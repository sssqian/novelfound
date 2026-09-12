# -*- coding: utf-8 -*-
"""主窗口：书架首页 + 书籍详情 + 内置阅读器，外加搜索/目录两个浮层。

P1 信息架构（见 ``docs/UI重构实施方案.md``）：

```
┌─ 应用窗口 ───────────────────────────────────────┐
│  📖 应用名                        已启用 N 个书源  🔍 ⚙ │
├──────────────────────────────────────────────────┤
│  ① 书架首页 ──点击书籍──▶ ② 书籍详情 ──继续阅读──▶ ③ 阅读器 │
│       ▲                      │                        │
│       └────────返回──────────┴────────返回─────────────┘ │
│  浮层：搜索面板（Ctrl+K）  目录抽屉（Ctrl+B）  轻提示        │
└──────────────────────────────────────────────────┘
```

要点：
* 左侧常驻栏与 ``QSplitter`` 已删除，搜索改成 Ctrl+K 浮层；
* 书源相关的导入/订阅/探测/网络找书源集中在「设置 → 书源」，
  搜索 0 结果时在搜索面板内给一个快捷入口；
* 抓取逻辑（``net`` / ``sources`` / ``cache`` / ``library`` / ``tasks``）没有改动。
"""
from __future__ import annotations

import base64
import subprocess
import sys
import time
from typing import Dict, List, Optional

from PyQt5.QtCore import QByteArray, Qt, QTimer
from PyQt5.QtGui import QKeySequence
from PyQt5.QtWidgets import (QFileDialog, QFrame, QHBoxLayout, QLabel, QMainWindow,
                             QPushButton, QShortcut, QStackedWidget, QVBoxLayout,
                             QWidget)

from ..cache import Cache
from ..config import APP_TITLE, APP_VERSION, AppConfig
from ..history import KIND_CHAPTER, KIND_DETAIL, BrowseHistory
from ..library import Library
from ..localbooks import LocalBooks
from .. import localbooks
from ..models import Book, BookDetail, Chapter, ChapterContent
from ..net import HttpSession
from ..sources import BaseSource, SourceStats, build_sources
from ..tasks import (ChapterTask, CoverTask, DetailTask, LocalImportTask,
                     SubscriptionTask, TaskManager)
from .book_view import BookView
from .catalog_drawer import CatalogDrawer
from .history_panel import HistoryPanel
from .image_gallery import ImageGalleryDialog
from .local_manager import LocalManagerDialog, human_size
from .library_view import LibraryView
from .reader import ReaderView
from .search_palette import SearchPalette
from .settings_dialog import SettingsDialog
from .source_discover_dialog import SourceDiscoverDialog
from .theme import reader_theme, theme_is_dark
from .widgets import Scrim, Toast


class MainWindow(QMainWindow):
    """应用主窗口。"""

    def __init__(self) -> None:
        super().__init__()
        self.config = AppConfig()
        self.http = HttpSession(timeout=float(self.config.get("timeout")),
                                retries=int(self.config.get("retries")),
                                host_interval=float(self.config.get("request_interval")))
        self.cache = Cache(enabled=bool(self.config.get("cache_enabled")),
                           ttl_days=int(self.config.get("cache_days")))
        self.library = Library()
        self.history = BrowseHistory()
        self.local_books = LocalBooks()
        self.stats = SourceStats()
        self.task_manager = TaskManager()
        self.sources: List[BaseSource] = build_sources(self.config, self.http)

        self.current_book: Optional[Book] = None
        self.current_detail: Optional[BookDetail] = None
        self.current_source: Optional[BaseSource] = None
        # 从浏览历史点进来时，目录就绪后要直接跳到的那一章
        self._pending_chapter: Optional[int] = None
        self._cover_cache: Dict[str, bytes] = {}
        self._cover_pending: List[tuple] = []        # [(url, apply, widget)]
        self._cover_active = 0
        self._cover_timer = QTimer(self)
        self._cover_timer.setSingleShot(True)
        self._cover_timer.setInterval(150)
        self._cover_timer.timeout.connect(self._pump_cover_queue)
        self._pending_position: Optional[dict] = None
        self._loading_chapter = False

        self.setWindowTitle(f"{APP_TITLE} v{APP_VERSION}")
        self.resize(1280, 820)
        self.setMinimumSize(980, 640)
        self.setAcceptDrops(True)          # 支持把 TXT / EPUB 拖进窗口导入

        self._build_ui()
        self._bind_shortcuts()
        self._restore_geometry()
        self._refresh_source_status()
        self._refresh_history_badge()
        self._refresh_library()

        # 启动后延迟检查订阅更新（不阻塞界面）
        QTimer.singleShot(1200, self._maybe_update_subscriptions)

    # ------------------------------------------------------------------ 界面
    def _build_ui(self) -> None:
        central = QWidget(self)
        central.setObjectName("centralArea")
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ------------------------------------------------------------ 顶部栏
        # 方案：首页不放搜索框，搜索入口只有右上角图标 + Ctrl+K
        top = QFrame(central)
        top.setObjectName("topBar")
        self.top_bar = top                      # 进阅读器时要跟着阅读主题换色
        top_layout = QHBoxLayout(top)
        top_layout.setContentsMargins(20, 10, 16, 10)
        top_layout.setSpacing(8)

        logo = QLabel("📖 小说搜索阅读器", top)
        logo.setObjectName("appTitle")
        top_layout.addWidget(logo)
        top_layout.addStretch(1)

        self.source_label = QLabel("", top)
        self.source_label.setObjectName("muted")
        top_layout.addWidget(self.source_label)

        self.history_button = QPushButton("🕘", top)
        self.history_button.setObjectName("iconButton")
        self.history_button.setToolTip("浏览历史（记录每一次打开详情 / 阅读章节）")
        self.history_button.clicked.connect(self.open_history_panel)
        top_layout.addWidget(self.history_button)

        self.search_button = QPushButton("🔍", top)
        self.search_button.setObjectName("iconButton")
        self.search_button.setToolTip("搜索小说（Ctrl+K）")
        self.search_button.clicked.connect(self.open_search_palette)
        top_layout.addWidget(self.search_button)

        self.settings_button = QPushButton("⚙", top)
        self.settings_button.setObjectName("iconButton")
        self.settings_button.setToolTip("设置（书源 / 网络 / 外观）")
        self.settings_button.clicked.connect(self.open_settings)
        top_layout.addWidget(self.settings_button)

        root.addWidget(top)

        # ------------------------------------------------------------ 页面栈
        self.stack = QStackedWidget(central)

        self.library_view = LibraryView(self.config, self.stack)
        self.library_view.book_opened.connect(self.on_book_clicked)
        self.library_view.search_requested.connect(self.open_search_palette)
        self.library_view.import_requested.connect(self.open_import_dialog)
        self.library_view.manage_requested.connect(self.open_local_manager)
        self.library_view.shelf_remove_requested.connect(self.remove_from_shelf)
        self.library_view.local_delete_requested.connect(self.on_local_delete_requested)
        self.library_view.reveal_requested.connect(self.reveal_local_file)
        self.stack.addWidget(self.library_view)

        self.book_view = BookView(self.stack)
        self.book_view.read_requested.connect(self.on_read_requested)
        self.book_view.shelf_toggled.connect(self.on_shelf_toggled)
        self.book_view.refresh_requested.connect(self.on_refresh_detail)
        self.book_view.back_requested.connect(self.on_book_back)
        self.book_view.catalog_requested.connect(self.open_catalog_drawer)
        self.book_view.local_delete_requested.connect(self.on_local_delete_requested)
        self.stack.addWidget(self.book_view)

        self.reader = ReaderView(self.config, self.stack)
        self.reader.chapter_requested.connect(self.load_chapter)
        self.reader.back_requested.connect(self.on_reader_back)
        self.reader.catalog_requested.connect(self.open_catalog_drawer)
        self.reader.position_changed.connect(self.on_position_changed)
        self.reader.settings_changed.connect(self._sync_shell_theme)
        self.reader.theme_changed.connect(self._sync_shell_theme)
        self.stack.addWidget(self.reader)

        root.addWidget(self.stack, 1)

        # ------------------------------------------------------------ 浮层
        # 遮罩、搜索面板、目录抽屉、轻提示都是中央控件的子控件（不参与布局）
        self.scrim = Scrim(central)
        self.scrim.clicked.connect(self._on_scrim_clicked)

        self.search_palette = SearchPalette(self.config, self.http, self.task_manager,
                                            self.stats, self._load_cover, central)
        self.search_palette.book_chosen.connect(self.on_book_clicked)
        self.search_palette.closed.connect(self._on_palette_closed)
        self.search_palette.discover_requested.connect(self.open_discover_dialog)
        self.search_palette.search_finished.connect(
            lambda *_: self._auto_disable_failing_sources())

        self.catalog_drawer = CatalogDrawer(central)
        self.catalog_drawer.chapter_activated.connect(self.on_catalog_chapter)
        self.catalog_drawer.closed.connect(self._on_drawer_closed)
        self.catalog_drawer.images_requested.connect(self.open_image_gallery)

        self.history_panel = HistoryPanel(self._load_cover, central)
        self.history_panel.entry_chosen.connect(self.on_history_entry)
        self.history_panel.closed.connect(self._on_history_closed)
        self.history_panel.cleared.connect(self.on_history_cleared)

        self.toast = Toast(central)
        self.toast.set_duration(float(self.config.get("toast_seconds") or 6))
        self.toast.shown.connect(self._position_toast)

        self.statusBar().showMessage("就绪")
        self.stack.setCurrentWidget(self.library_view)
        self._layout_overlays()

    def _bind_shortcuts(self) -> None:
        QShortcut(QKeySequence("Ctrl+K"), self, activated=self.open_search_palette)
        QShortcut(QKeySequence("Ctrl+F"), self, activated=self.open_search_palette)
        QShortcut(QKeySequence("Ctrl+H"), self, activated=self.open_history_panel)
        QShortcut(QKeySequence("Ctrl+B"), self, activated=self.toggle_catalog_drawer)
        QShortcut(QKeySequence("F5"), self, activated=self._reload_current_chapter)
        QShortcut(QKeySequence("Ctrl+="), self, activated=lambda: self.reader.change_font_size(1))
        QShortcut(QKeySequence("Ctrl++"), self, activated=lambda: self.reader.change_font_size(1))
        QShortcut(QKeySequence("Ctrl+-"), self, activated=lambda: self.reader.change_font_size(-1))
        QShortcut(QKeySequence("F11"), self, activated=self._toggle_fullscreen)

    def _toggle_fullscreen(self) -> None:
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    # -------------------------------------------------------------- 浮层定位
    def _layout_overlays(self) -> None:
        """把遮罩 / 浮层 / 抽屉 / 轻提示摆到中央控件的对应位置。"""
        central = self.centralWidget()
        if central is None:
            return
        width = max(1, central.width())
        height = max(1, central.height())

        self.scrim.setGeometry(0, 0, width, height)

        palette_width = min(660, max(420, width - 80))
        palette_height = min(520, max(260, height - 200))
        palette_geometry = ((width - palette_width) // 2, max(48, height // 7),
                            palette_width, palette_height)
        self.search_palette.setGeometry(*palette_geometry)
        self.history_panel.setGeometry(*palette_geometry)

        drawer_width = min(340, max(260, width // 3))
        self.catalog_drawer.setGeometry(0, 0, drawer_width, height)

        self._position_toast()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._layout_overlays()

    def _position_toast(self) -> None:
        """把 Toast 摆到底部居中（它是浮层，不参与布局）。"""
        central = self.centralWidget()
        if central is None or not self.toast.isVisible():
            return
        self.toast.reposition(central.width(), central.height())
        self.toast.raise_()

    # ------------------------------------------------------------ 浮层开合
    def _show_scrim(self) -> None:
        """淡入遮罩（200ms），保证它压在浮层下面。"""
        self.scrim.show_scrim()

    def _hide_scrim(self) -> None:
        self.scrim.hide_scrim()

    def open_search_palette(self, keyword: str = "") -> None:
        """打开搜索浮层（Ctrl+K / 点右上角 🔍）。"""
        if not keyword:
            keyword = self.search_palette.keyword() or self.config.get("last_search") or ""
        self.catalog_drawer.close_drawer()
        self.history_panel.close_panel()
        self._layout_overlays()
        self._show_scrim()
        self.search_palette.open(keyword)
        self.search_palette.raise_()
        self._cover_timer.start()

    def close_search_palette(self) -> None:
        self.search_palette.close_palette()

    def _on_palette_closed(self) -> None:
        if not (self.catalog_drawer.is_open() or self.history_panel.is_open()):
            self._hide_scrim()

    def open_catalog_drawer(self) -> None:
        """打开目录抽屉（Ctrl+B / 详情页与阅读器的「目录」按钮）。"""
        detail = self.current_detail
        if detail is None or not detail.chapters:
            self.toast.show_message("还没有目录：先打开一本书的详情。")
            return
        index = (self.reader.chapter_index
                 if self.stack.currentWidget() is self.reader
                 else self.book_view.reading_index())
        self.search_palette.close_palette()
        self.history_panel.close_panel()
        self._layout_overlays()          # 先把抽屉摆到最终位置，再从左侧滑入
        self._show_scrim()
        self.catalog_drawer.open_drawer(detail, index,
                                        images=self.book_image_count(detail.book))
        self.catalog_drawer.raise_()

    def book_image_count(self, book: Book) -> int:
        """这本书有多少张插图（只对本地 EPUB 有意义）。"""
        if not localbooks.is_local_url(book.url):
            return 0
        item = self.local_books.get(localbooks.book_id_from_url(book.url))
        return len((item or {}).get("images") or [])

    # ------------------------------------------------------------- 本地书管理
    def local_record(self, book: Book) -> Optional[dict]:
        """取这本书对应的本地库记录（不是本地书则为 None）。"""
        if not localbooks.is_local_url(book.url):
            return None
        return self.local_books.get(localbooks.book_id_from_url(book.url))

    def local_book_details(self, item: dict) -> dict:
        """删除前给用户看的明细：文件多大、缓存几行、历史/进度有没有。"""
        path = self.local_books.file_path(item)
        size = path.stat().st_size if path.is_file() else 0
        cover = item.get("cover_file")
        if cover and (self.local_books.files_dir / cover).is_file():
            size += (self.local_books.files_dir / cover).stat().st_size
        book = self.local_books.to_book(item)
        return {
            "size": size,
            "size_text": human_size(size),
            "chapters": len(item.get("chapters") or []),
            "cache_rows": (self.cache.count_for_book(localbooks.SOURCE_KEY, book.url)
                           if self.cache else 0),
            "in_history": any(i.get("key") == book.key for i in self.history.items()),
            "in_library": self.library.contains(book.key),
            "in_progress": bool(self.library.progress(book.key).get("chapter_url")),
            "book": book,
        }

    def remove_from_shelf(self, book: Book) -> None:
        """「移出书架」：只从书架隐藏；本地书额外提示文件仍保留。"""
        if self.library.contains(book.key):
            self.library.remove(book.key)
        if localbooks.is_local_url(book.url):
            self.toast.show_message(
                "已移出书架；导入的文件仍在本地库里（搜索能找到）。"
                "要连文件一起删，用「本地书管理 → 删除选中」。")
        else:
            self.toast.show_message("已移出书架")
        self._refresh_library()
        if self.current_book is not None and self.current_book.key == book.key:
            self.book_view.set_shelf_state(False)

    def delete_local_book(self, item: dict, confirm: bool = True) -> bool:
        """彻底删除一本导入的本地书：文件 + 记录 + 缓存 + 浏览历史 + 阅读进度。"""
        info = self.local_book_details(item)
        book = info["book"]
        if confirm:
            lines = [f"《{book.title}》（{str(item.get('format', '')).upper()}，"
                     f"{info['chapters']} 章）", "",
                     f"· 本地文件：{info['size_text']}",
                     f"· 阅读缓存：{info['cache_rows']} 行"]
            extras = [name for name, flag in (("书架", info["in_library"]),
                                              ("阅读进度", info["in_progress"]),
                                              ("浏览历史", info["in_history"])) if flag]
            lines.append(f"· 记录：{'、'.join(extras) if extras else '无'}")
            lines += ["", "删除后无法恢复（要看得重新导入文件）。"]
            box = QMessageBox(self)
            box.setWindowTitle("删除本地书")
            box.setIcon(QMessageBox.Warning)
            box.setText("\n".join(lines))
            yes = box.addButton("删除", QMessageBox.DestructiveRole)
            box.addButton("取消", QMessageBox.RejectRole)
            box.exec_()
            if box.clickedButton() is not yes:
                return False

        removed_cache = (self.cache.delete_book(localbooks.SOURCE_KEY, book.url)
                         if self.cache else 0)
        removed_history = self.history.remove_book(book.key)
        self.library.forget(book.key)
        self.local_books.remove(item.get("id", ""))
        self.sources = build_sources(self.config, self.http)
        self._refresh_source_status()
        self._refresh_library()
        self._refresh_history_badge()
        if self.current_book is not None and self.current_book.key == book.key:
            self.show_library()
        self.toast.show_message(
            f"已删除《{book.title}》：文件 + 缓存 {removed_cache} 行"
            f" + 历史 {removed_history} 条 + 进度记录")
        return True

    def cleanup_stale_records(self) -> dict:
        """清理指向"已不存在的本地书"的记录（书架/进度/历史/缓存）。"""
        valid_ids = [item.get("id") for item in self.local_books.all()]
        valid_urls = [localbooks.local_url(i) for i in valid_ids]
        result = {
            "library": self.library.purge_stale_local(valid_ids),
            "history": self.history.purge_stale_local(valid_ids),
            "cache": self.cache.purge_stale_local(valid_urls) if self.cache else 0,
        }
        self._refresh_library()
        self._refresh_history_badge()
        summary = (f"清理失效记录：书架/进度 {result['library']} 条、"
                   f"历史 {result['history']} 条、缓存 {result['cache']} 行")
        self.toast.show_message(summary)
        self.statusBar().showMessage(summary)
        return result

    def on_local_delete_requested(self, book: Book) -> None:
        """详情页按钮 / 书架右键点了「删除本地书」。"""
        item = self.local_record(book)
        if item is None:
            self.toast.show_message("这本书不在本地库里（可能已被删除）。")
            return
        self.delete_local_book(item, confirm=True)

    def open_local_manager(self) -> None:
        """打开「本地书管理」窗口。"""
        dialog = LocalManagerDialog(
            self.local_books, self.library, self.history, self.cache,
            on_delete=lambda item: self.delete_local_book(item, confirm=True),
            on_cleanup=self.cleanup_stale_records,
            on_reimport=self.reimport_local_book,
            parent=self)
        self.local_manager_dialog = dialog
        dialog.exec_()

    def reimport_local_book(self, item: dict) -> None:
        """重新导入：选一个新文件替换这本书。"""
        paths, _ = QFileDialog.getOpenFileNames(
            self, f"重新导入《{item.get('title', '')}》", "",
            "电子书 (*.txt *.epub);;所有文件 (*)")
        if paths:
            self.import_local_books(paths)

    def reveal_local_file(self, book: Book) -> None:
        """在资源管理器里定位导入的文件。"""
        item = self.local_record(book)
        if not item:
            return
        path = self.local_books.file_path(item)
        if not path.is_file():
            self.toast.show_message("文件已不在，可能已被删除。")
            return
        if sys.platform.startswith("win"):
            subprocess.Popen(["explorer", "/select,", str(path)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path.parent)])

    def open_image_gallery(self) -> None:
        """打开「本书插图」：列出 EPUB 包里的所有图片（含正文没引用的）。"""
        if book is None or not localbooks.is_local_url(book.url):
            self.toast.show_message("这本书没有插图可看（只有本地 EPUB 支持）。")
            return
        source = self._source_for(book)
        if source is None or not hasattr(source, "list_images"):
            self.toast.show_message("本地书源不可用，无法读取插图。")
            return
        try:
            items = source.list_images(book)
        except Exception as exc:      # noqa: BLE001 - 读清单失败给友好提示
            self.toast.show_message(f"读取插图清单失败：{exc}", "error")
            return
        if not items:
            self.toast.show_message("这本书里没有图片。")
            return
        dialog = ImageGalleryDialog(
            items, lambda path: source.image_bytes(book, path), self,
            title=f"《{book.title}》插图")
        dialog.exec_()

    def close_catalog_drawer(self) -> None:
        self.catalog_drawer.close_drawer()

    def toggle_catalog_drawer(self) -> None:
        if self.catalog_drawer.is_open():
            self.close_catalog_drawer()
        else:
            self.open_catalog_drawer()

    def _on_drawer_closed(self) -> None:
        if not (self.search_palette.is_open() or self.history_panel.is_open()):
            self._hide_scrim()

    # ------------------------------------------------------------ 浏览历史
    def open_history_panel(self) -> None:
        """打开浏览历史浮层（右上角 🕘 / Ctrl+H）。"""
        self.catalog_drawer.close_drawer()
        self.search_palette.close_palette()
        self._layout_overlays()
        self._show_scrim()
        self.history_panel.open_panel(self.history)
        self.history_panel.raise_()
        self._cover_timer.start()

    def close_history_panel(self) -> None:
        self.history_panel.close_panel()

    def _on_history_closed(self) -> None:
        if not (self.search_palette.is_open() or self.catalog_drawer.is_open()):
            self._hide_scrim()

    def on_history_entry(self, entry: dict) -> None:
        """点历史里的一条：回到当时那本书（阅读记录直接跳到那一章）。"""
        book = self.history.book_of(entry)
        if not book.url:
            return
        index = int(entry.get("chapter_index", -1))
        jump = index if (entry.get("chapter_url") and index >= 0) else None
        self._open_book(book, chapter_index=jump)

    def on_history_cleared(self) -> None:
        self.history.clear()
        self._refresh_history_badge()
        self.toast.show_message("浏览历史已清空。")

    def _refresh_history_badge(self) -> None:
        count = self.history.count()
        self.history_button.setToolTip(
            f"浏览历史（{count} 条）" if count else "浏览历史（暂无记录）")

    def _on_scrim_clicked(self) -> None:
        """点遮罩：关掉当前打开的浮层。"""
        if self.search_palette.is_open():
            self.search_palette.close_palette()
        if self.history_panel.is_open():
            self.history_panel.close_panel()
        if self.catalog_drawer.is_open():
            self.catalog_drawer.close_drawer()

    def _close_overlays(self) -> None:
        self.search_palette.close_palette()
        self.history_panel.close_panel()
        self.catalog_drawer.close_drawer()
        self._hide_scrim()

    def on_catalog_chapter(self, index: int) -> None:
        """目录抽屉里点了某一章：直接跳过去读。"""
        self.close_catalog_drawer()
        if self.current_detail is None:
            return
        if self.stack.currentWidget() is not self.reader:
            self.on_read_requested(self.current_detail, index)
        else:
            self.load_chapter(index)

    # ------------------------------------------------------------------ 本地书籍
    def open_import_dialog(self) -> None:
        """选文件导入本地 TXT / EPUB（也可以把文件直接拖进窗口）。"""
        paths, _ = QFileDialog.getOpenFileNames(
            self, "导入本地电子书（可多选）", "",
            "电子书 (*.txt *.epub);;文本文件 (*.txt);;EPUB 电子书 (*.epub);;所有文件 (*)")
        if paths:
            self.import_local_books(paths)

    def import_local_books(self, paths) -> None:
        """把一批本地文件导入书架（解析在工作线程里做）。"""
        files = [str(p) for p in paths if str(p).lower().endswith((".txt", ".epub"))]
        skipped = len(list(paths)) - len(files)
        if not files:
            self.toast.show_message("只支持 .txt 和 .epub 文件。")
            return
        self.toast.show_message(f"正在导入 {len(files)} 个文件…")
        task = LocalImportTask(files, self.local_books)
        task.signals.progress.connect(self._on_progress)
        task.signals.finished.connect(self._on_local_imported)
        task.signals.failed.connect(
            lambda msg, detail: self.toast.show_message(f"导入失败：{msg}", "error"))
        self.task_manager.start(task)
        if skipped:
            self.statusBar().showMessage(f"已跳过 {skipped} 个不支持的文件")

    def _on_local_imported(self, result) -> None:
        """导入完成：加入书架 + 刷新首页（这样就能直接点进去读）。"""
        imported = list((result or {}).get("imported") or [])
        failures = list((result or {}).get("failures") or [])
        for record in imported:
            book = self.local_books.to_book(record)
            count = len(record.get("chapters") or [])
            detail = BookDetail(book=book,
                                chapters=[Chapter(index=i) for i in range(count)])
            self.library.add(book, detail)      # 记下章节数，书架格子才有百分比
            self.statusBar().showMessage(
                f"已导入《{book.title}》：{count} 章")
        self.sources = build_sources(self.config, self.http)
        self._refresh_source_status()
        self._refresh_library()
        self.show_library()
        if imported:
            self.toast.show_message(f"已导入 {len(imported)} 本，点封面即可阅读。")
        if failures:
            self.toast.show_message("部分文件导入失败：" + "；".join(failures[:2]), "error")

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        """允许把 TXT / EPUB 拖进窗口导入。"""
        if self._drop_paths(event):
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802
        paths = self._drop_paths(event)
        if paths:
            event.acceptProposedAction()
            self.import_local_books(paths)

    @staticmethod
    def _drop_paths(event) -> list:
        if not event.mimeData().hasUrls():
            return []
        return [u.toLocalFile() for u in event.mimeData().urls()
                if u.isLocalFile() and u.toLocalFile().lower().endswith((".txt", ".epub"))]

    # ------------------------------------------------------------------ 页面
    def _sync_shell_theme(self) -> None:
        """进入阅读器时**收起顶部应用栏**，并让状态栏跟随阅读主题。

        阅读器自己的顶部浮条（鼠标靠近才出现）已经提供了「返回 / 书名 / 目录 / Aa」，
        再留一条「📖 小说搜索阅读器」横栏既占地方，夜间模式下还会和深色正文打架。
        所以：阅读时隐藏顶部栏，状态栏按当前阅读主题着色；离开阅读器时全部还原。
        """
        in_reader = self.stack.currentWidget() is self.reader
        self.top_bar.setVisible(not in_reader)
        if in_reader:
            theme = reader_theme(self.reader._theme_key)
            line = ("rgba(255,255,255,0.14)" if theme_is_dark(theme)
                    else "rgba(0,0,0,0.08)")
            self.statusBar().setStyleSheet(
                f"QStatusBar {{ background: {theme['bg']}; color: {theme['muted']}; "
                f"border-top: 1px solid {line}; }}")
        else:
            self.statusBar().setStyleSheet("")      # 空样式表 → 回到 app 级暖白 QSS

    def show_library(self) -> None:
        """回到书架首页（顺便刷新进度与封面）。"""
        self._refresh_library()
        self.stack.setCurrentWidget(self.library_view)
        self._sync_shell_theme()
        self._cover_timer.start()

    def on_book_back(self) -> None:
        self.show_library()

    def on_reader_back(self) -> None:
        self._record_reading_history()      # 结束阅读这本书 → 写一条（一本书只留一条）
        if self.current_detail is not None:
            self.book_view.set_reading_progress(self.reader.chapter_index)
            self.stack.setCurrentWidget(self.book_view)
        else:
            self.stack.setCurrentWidget(self.library_view)
        self._sync_shell_theme()

    def _refresh_library(self) -> None:
        self.library_view.refresh(self.library, self._load_cover)

    # ------------------------------------------------------------------ 详情
    def on_book_clicked(self, book: Book) -> None:
        """打开一本书的详情（搜索面板 / 书架首页都会走这里）。"""
        self._open_book(book)

    def _open_book(self, book: Book, chapter_index: Optional[int] = None) -> None:
        source = self._source_for(book)
        if source is None:
            self.toast.show_message(
                f"该书源（{book.source_name or book.source}）已被禁用，"
                "请在「设置 → 书源」中重新启用。", "error")
            return
        self._close_overlays()
        self.current_book = book
        self.current_source = source
        self._pending_chapter = chapter_index
        self.statusBar().showMessage(f"正在获取《{book.title}》的目录…")
        self.stack.setCurrentWidget(self.book_view)
        self.book_view.title_label.setText(book.title)
        self.book_view.meta_label.setText("正在加载目录…")
        self.book_view.progress_label.setText("")
        self.book_view.intro_label.setText("")
        self.book_view.source_label.setText(
            f"来源：{book.source_name}" if book.source_name else "")

        task = DetailTask(source, book, self.cache, stats=self.stats)
        task.signals.progress.connect(self._on_progress)
        task.signals.finished.connect(self._on_detail_ready)
        task.signals.failed.connect(self._on_detail_failed)
        self.task_manager.start(task)

    def _on_detail_ready(self, detail: BookDetail) -> None:
        self.current_detail = detail
        book = detail.book
        self.current_book = book
        self.current_source = self._source_for(book) or self.current_source
        cached = self.cache.has_chapter(book.source, book.url)
        self.book_view.set_detail(detail, in_shelf=self.library.contains(book.key),
                                  cached_count=cached,
                                  progress=self.library.progress(book.key))
        self.book_view.set_local(is_local=localbooks.is_local_url(book.url))
        if self.config.get("auto_load_cover") and book.cover_url:
            self._load_cover(book.cover_url, self.book_view.set_cover,
                             self.book_view.cover)
        self.statusBar().showMessage(f"《{book.title}》共 {len(detail.chapters)} 章")
        # 浏览历史：详情加载成功才算"浏览过"
        self._record_history(book, KIND_DETAIL)
        # 从浏览历史点进来时，直接回到当时读的那一章
        if self._pending_chapter is not None:
            index, self._pending_chapter = self._pending_chapter, None
            if 0 <= index < len(detail.chapters):
                self.on_read_requested(detail, index)

    def _on_detail_failed(self, message: str, detail: str) -> None:
        self._pending_chapter = None
        self.statusBar().showMessage("目录获取失败")
        self.toast.show_message(f"目录获取失败：{message}", "error")
        self.book_view.meta_label.setText(f"加载失败：{message}")
        self.book_view.intro_label.setText(
            "可能原因：站点结构变化、网络不通、或该书源需要人机验证。\n"
            "建议：换一个书源，或在设置中检测书源可用性。")

    def on_refresh_detail(self, book: Book) -> None:
        source = self._source_for(book)
        if source is None:
            return
        self.statusBar().showMessage("正在刷新目录…")
        task = DetailTask(source, book, self.cache, force=True, stats=self.stats)
        task.signals.finished.connect(self._on_detail_ready)
        task.signals.failed.connect(self._on_detail_failed)
        self.task_manager.start(task)

    # ------------------------------------------------------------------ 阅读
    def on_read_requested(self, detail: BookDetail, index: int) -> None:
        self._close_overlays()
        self.current_detail = detail
        self.current_book = detail.book
        self.current_source = self._source_for(detail.book) or self.current_source
        self.reader.set_book(len(detail.chapters), index)
        self.reader.apply_settings()
        self.stack.setCurrentWidget(self.reader)
        self._sync_shell_theme()          # 顶栏/状态栏跟随阅读主题
        # 上下控制条：默认自动隐藏，靠近边缘才出现
        if not self.config.get("auto_hide_bars", True):
            self.reader.show_bars()
        else:
            self.reader.hide_bars()
        self.library.mark_read(detail.book)
        self.load_chapter(index)

    def load_chapter(self, index: int) -> None:
        if self.current_detail is None or self.current_source is None:
            return
        chapters = self.current_detail.chapters
        if not 0 <= index < len(chapters):
            return
        if self._loading_chapter:
            return
        self._loading_chapter = True
        chapter = chapters[index]
        self.reader.set_loading(chapter.display_title)
        self.statusBar().showMessage(f"正在加载：{chapter.display_title}")
        progress = self.library.progress(self.current_detail.book.key)
        position = self.reader.position_for(chapter.url)
        if not position and progress.get("index") == index:
            position = {
                "scroll_pos": progress.get("scroll_pos", 0),
                "block_index": progress.get("block_index", -1),
                "page_index": progress.get("page_index", -1),
                "char_offset": progress.get("char_offset", -1),
            }
        self._pending_position = position

        task = ChapterTask(self.current_source, self.current_detail.book, chapter,
                           self.cache, stats=self.stats)
        task.signals.progress.connect(self._on_progress)
        task.signals.finished.connect(lambda content: self._on_chapter_ready(content, index))
        task.signals.failed.connect(lambda msg, detail: self._on_chapter_failed(msg, detail, index))
        self.task_manager.start(task)

    def _on_chapter_ready(self, content: ChapterContent, index: int) -> None:
        self._loading_chapter = False
        position = self._pending_position
        self._pending_position = None
        self.reader.set_content(content, index=index, position=position)
        self.book_view.set_reading_progress(index)
        if self.catalog_drawer.is_open():
            self.catalog_drawer.set_current(index)
        book = self.current_detail.book
        # 浏览历史不在这里写：切章太频繁，会在历史里堆出同一本书的很多条。
        # 改为离开阅读器 / 关闭应用时各写一次（见 _record_reading_history）。
        self.library.update_progress(book, content.url, content.title, index,
                                     **self.reader.reading_position())
        self.statusBar().showMessage(
            f"《{book.title}》 · 第 {index + 1} 章 / 共 {self.reader.chapter_count} 章"
            + ("（缓存）" if content.from_cache else ""))

    def _on_chapter_failed(self, message: str, detail: str, index: int) -> None:
        self._loading_chapter = False
        self.reader.set_error(message)
        self.toast.show_message(f"章节加载失败：{message}", "error")
        self.statusBar().showMessage("章节加载失败")

    def _reload_current_chapter(self) -> None:
        """F5：忽略缓存重新抓取当前章节。"""
        if self.current_detail is None or self.current_source is None:
            return
        index = self.reader.chapter_index
        if not 0 <= index < len(self.current_detail.chapters):
            return
        chapter = self.current_detail.chapters[index]
        self.reader.set_loading(chapter.display_title)
        self.statusBar().showMessage("正在重新抓取（忽略缓存）…")
        task = ChapterTask(self.current_source, self.current_detail.book, chapter,
                           self.cache, force=True, stats=self.stats)
        task.signals.finished.connect(lambda content: self._on_chapter_ready(content, index))
        task.signals.failed.connect(lambda msg, d: self._on_chapter_failed(msg, d, index))
        self.task_manager.start(task)

    def on_position_changed(self, index: int, position: dict) -> None:
        if self.current_detail is None:
            return
        chapters = self.current_detail.chapters
        if not 0 <= index < len(chapters):
            return
        chapter = chapters[index]
        self.library.update_progress(self.current_detail.book, chapter.url,
                                     chapter.title, index, **(position or {}))

    # ------------------------------------------------------------------ 书架
    def on_shelf_toggled(self, book: Book) -> None:
        added = self.library.toggle(book, self.current_detail)
        self.book_view.shelf_button.setText("★ 已在书架" if added else "☆ 加入书架")
        self._refresh_library()
        self.statusBar().showMessage("已加入书架" if added else "已移出书架")

    # ------------------------------------------------------------ 浏览历史
    def _record_history(self, book: Book, kind: str, index: int = -1,
                        chapter=None) -> None:
        """写入/刷新这本书的历史记录（**一本书只留一条**）。"""
        self.history.record(
            book, kind=kind,
            chapter_index=index if chapter is not None else -1,
            chapter_title=getattr(chapter, "title", "") if chapter is not None else "",
            chapter_url=getattr(chapter, "url", "") if chapter is not None else "")
        self._refresh_history_badge()

    def _record_reading_history(self) -> None:
        """离开阅读器（或关闭应用）时，把"这本书读到哪"写进历史。

        只在**结束阅读**时写一次：既不随每次切章刷记录，也不会让历史里
        同一本书出现多条。进度本身仍由 `library.update_progress()` 实时保存。
        """
        if self.current_detail is None:
            return
        index = self.reader.chapter_index
        chapters = self.current_detail.chapters
        chapter = chapters[index] if 0 <= index < len(chapters) else None
        self._record_history(self.current_detail.book, KIND_CHAPTER,
                             index=index, chapter=chapter)

    # ------------------------------------------------------------------ 封面
    def _load_cover(self, url: str, apply, widget=None) -> None:
        """登记一次封面加载。

        封面是"每张卡片一个请求"，一次搜索可能有几十张卡片；
        如果全部立刻并发下载，同一站点瞬间被打几十次，很容易被判成
        "访问过于频繁"。所以这里改成**排队 + 限并发 + 只下可见卡片**：
        卡片滚到可见区域时才真正发起请求。
        """
        if not url:
            return
        if url.startswith(localbooks.COVER_PREFIX):
            # 本地 EPUB 封面：直接读文件，不走网络队列
            item = self.local_books.get(url[len(localbooks.COVER_PREFIX):])
            data = self.local_books.cover_bytes(item) if item else b""
            if data:
                self._apply_cover(apply, data)
            return
        cached = self._cover_cache.get(url)
        if cached:
            self._apply_cover(apply, cached)
            return
        self._cover_pending.append((url, apply, widget))
        self._cover_timer.start()

    def _pump_cover_queue(self) -> None:
        """按并发上限从队列里取任务；不可见的卡片留到滚动时再处理。"""
        self._prune_covers()
        limit = max(1, int(self.config.get("cover_max_concurrent") or 2))
        while self._cover_pending and self._cover_active < limit:
            index = self._next_visible_cover()
            if index is None:
                break
            url, apply, widget = self._cover_pending.pop(index)
            if url in self._cover_cache:
                self._apply_cover(apply, self._cover_cache[url])
                continue
            self._cover_active += 1
            task = CoverTask(self.http, url, self.cache)
            task.signals.finished.connect(
                lambda data, u=url, a=apply: self._on_cover_ready(u, data, a))
            task.signals.failed.connect(
                lambda *_args, u=url: self._on_cover_failed(u))
            self.task_manager.start(task)

    def _prune_covers(self) -> None:
        """丢掉控件已销毁的封面任务（列表清空后这些不该再占队列）。"""
        alive = []
        for item in self._cover_pending:
            widget = item[2]
            if widget is not None:
                try:
                    widget.isVisible()      # 访问已销毁对象会抛 RuntimeError
                except RuntimeError:
                    continue
            alive.append(item)
        self._cover_pending = alive

    def _next_visible_cover(self) -> Optional[int]:
        """找出队列里第一个可见的封面；都不可见时返回 None。"""
        for index, (_url, _apply, widget) in enumerate(self._cover_pending):
            if widget is None or self._widget_visible(widget):
                return index
        return None

    @staticmethod
    def _widget_visible(widget) -> bool:
        """判断控件是否在滚动区域内可见（离屏测试环境也能用）。"""
        try:
            if not widget.isVisible():
                return False
            viewport = widget.parentWidget()
            if viewport is None or viewport.height() <= 0:
                return True          # 拿不到视口信息时按可见处理
            top = widget.mapTo(viewport, widget.rect().topLeft()).y()
            return -widget.height() < top < viewport.height()
        except RuntimeError:
            return False             # 控件已销毁

    def _apply_cover(self, apply, data: bytes) -> None:
        try:
            apply(data)
        except RuntimeError:
            pass   # 控件已被销毁

    def _on_cover_ready(self, url: str, data: bytes, apply) -> None:
        self._cover_active = max(0, self._cover_active - 1)
        if data:
            self._cover_cache[url] = data
            self._apply_cover(apply, data)
        self._pump_cover_queue()

    def _on_cover_failed(self, url: str) -> None:
        """封面失败不重试（避免反复打同一站点），仅释放并发位。"""
        self._cover_active = max(0, self._cover_active - 1)
        self._pump_cover_queue()

    # ------------------------------------------------------------------ 其它
    def _source_for(self, book: Book) -> Optional[BaseSource]:
        for source in build_sources(self.config, self.http):
            if source.key == book.source:
                return source
        return None

    # ---------------------------------------------------------- 书源健康度
    def _auto_disable_failing_sources(self) -> None:
        """连续失败达到阈值的书源自动禁用，并在界面上说明原因。

        这样下次搜索不必再为已经挂掉的站点白白等待超时。
        """
        threshold = int(self.config.get("auto_disable_after") or 0)
        if threshold <= 0:
            return
        keys = [s.key for s in self.sources]
        failing = self.stats.failing_sources(keys, threshold)
        if not failing:
            return
        names = []
        for key in failing:
            source = next((s for s in self.sources if s.key == key), None)
            if source is None:
                continue
            self.config.set_source_enabled(key, False)
            self.stats.mark_auto_disabled(key)
            names.append(source.name)
        if not names:
            return
        self.sources = build_sources(self.config, self.http)
        self._refresh_source_status()
        self.toast.show_message(
            "以下书源连续失败已自动禁用：" + "、".join(names)
            + "。可在「设置 → 书源」里重新勾选，或点「重置评分」后重试。")

    def _maybe_update_subscriptions(self) -> None:
        """启动时按设定间隔自动更新订阅书源（后台静默进行）。"""
        if not self.config.get("auto_update_sources"):
            return
        subscriptions = self.config.subscriptions()
        if not subscriptions:
            return
        interval = max(1, int(self.config.get("subscription_update_days") or 7)) * 86400
        now = time.time()
        stale = [s for s in subscriptions
                 if now - float(s.get("last_sync") or 0) >= interval]
        if not stale:
            return
        self.statusBar().showMessage("正在更新书源订阅…")
        task = SubscriptionTask(self.http, stale)
        task.signals.finished.connect(self._on_subscriptions_updated)
        task.signals.failed.connect(lambda *_: self.statusBar().showMessage("就绪"))
        self.task_manager.start(task)

    def _on_subscriptions_updated(self, result) -> None:
        parsed, failures = result
        rules = [item.rule for item in parsed]
        if rules:
            self.config.add_custom_sources(rules)
        now = time.time()
        for item in self.config.subscriptions():
            count = sum(1 for p in parsed if p.rule.get("imported_from") == item["url"])
            self.config.update_subscription(item["url"], last_sync=now, count=count)
        self.sources = build_sources(self.config, self.http)
        self._refresh_source_status()
        if rules:
            self.toast.show_message(f"已从订阅更新 {len(rules)} 个书源。")
        elif failures:
            self.toast.show_message("书源订阅更新失败：" + "；".join(failures[:2]), "error")
        self.statusBar().showMessage("就绪")

    def _on_progress(self, text: str) -> None:
        self.statusBar().showMessage(text)

    def _refresh_source_status(self) -> None:
        enabled = build_sources(self.config, self.http)
        self.source_label.setText(f"已启用 {len(enabled)} 个书源")

    def open_settings(self) -> None:
        dialog = SettingsDialog(self.config, self.http, self.cache,
                                build_sources(self.config, self.http, enabled_only=False),
                                self.task_manager, self, stats=self.stats)
        dialog.sources_changed.connect(self._on_settings_sources_changed)
        dialog.reader_settings_changed.connect(self._apply_reader_settings)
        dialog.exec_()

    def _apply_reader_settings(self) -> None:
        """设置对话框保存后，把新的字号/配色/模式应用到阅读器。"""
        self.reader.apply_settings()
        self.reader.render()

    def open_discover_dialog(self) -> None:
        """打开「网络找书源」对话框（方案 C）。"""
        keyword = (self.search_palette.keyword()
                   or self.config.get("last_search") or "")
        dialog = SourceDiscoverDialog(self.config, self.http, self.task_manager,
                                      book_title=keyword, parent=self)
        dialog.sources_changed.connect(self._on_discovered_sources)
        dialog.exec_()

    def _on_discovered_sources(self) -> None:
        """发现并保存了新书源后：刷新书源列表，并用同一个关键词重搜一次。"""
        self.sources = build_sources(self.config, self.http)
        self._refresh_source_status()
        self.toast.hide_banner()
        keyword = (self.search_palette.keyword()
                   or self.config.get("last_search") or "")
        if keyword:
            self.statusBar().showMessage("已添加新书源，正在重新搜索…")
            self.open_search_palette(keyword)

    def _on_settings_sources_changed(self) -> None:
        self.sources = build_sources(self.config, self.http)
        self._refresh_source_status()

    # ------------------------------------------------------------- 窗口状态
    def _restore_geometry(self) -> None:
        geometry = self.config.get("window_geometry")
        if geometry:
            try:
                self.restoreGeometry(QByteArray(base64.b64decode(geometry)))
            except Exception:
                pass
        state = self.config.get("window_state")
        if state:
            try:
                self.restoreState(QByteArray(base64.b64decode(state)))
            except Exception:
                pass

    def closeEvent(self, event) -> None:  # noqa: N802
        # 保存当前阅读位置，避免刚翻页就关闭导致进度丢失
        try:
            self.reader._emit_position()
        except Exception:
            pass
        # 直接关应用时也要把"这本书读到哪"写进浏览历史（一本书只留一条）
        try:
            if self.stack.currentWidget() is self.reader:
                self._record_reading_history()
        except Exception:
            pass
        try:
            self.config.set("window_geometry",
                            base64.b64encode(bytes(self.saveGeometry())).decode("ascii"),
                            autosave=False)
            self.config.set("window_state",
                            base64.b64encode(bytes(self.saveState())).decode("ascii"),
                            autosave=False)
            self.config.save()
        except Exception:
            pass
        self.task_manager.cancel_all()
        self.task_manager.wait(1500)   # 给工作线程一点时间收尾，避免关闭时崩溃
        self.cache.close()
        self.http.close()
        super().closeEvent(event)

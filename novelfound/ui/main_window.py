# -*- coding: utf-8 -*-
"""主窗口：搜索栏 + 结果/书架列表 + 详情页 + 内置阅读器。"""
from __future__ import annotations

import base64
import time
from typing import Dict, List, Optional

from PyQt5.QtCore import QByteArray, Qt, QTimer
from PyQt5.QtGui import QKeySequence
from PyQt5.QtWidgets import (QFrame, QHBoxLayout, QLabel, QLineEdit, QMainWindow,
                             QPushButton, QScrollArea, QShortcut, QSplitter,
                             QStackedWidget, QToolButton, QVBoxLayout, QWidget)

from ..cache import Cache
from ..config import APP_TITLE, APP_VERSION, AppConfig
from ..library import Library
from ..models import Book, BookDetail, ChapterContent, SearchOutcome
from ..net import HttpSession
from ..sources import BaseSource, SourceStats, build_sources
from ..tasks import (ChapterTask, CoverTask, DetailTask, SearchTask, SubscriptionTask,
                     TaskManager)
from .book_card import BookCard
from .detail_panel import DetailPanel
from .reader import ReaderView
from .settings_dialog import SettingsDialog
from .source_discover_dialog import SourceDiscoverDialog
from .widgets import Banner, EmptyState


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
        self.stats = SourceStats()
        self.task_manager = TaskManager()
        self.sources: List[BaseSource] = build_sources(self.config, self.http)

        self.current_book: Optional[Book] = None
        self.current_detail: Optional[BookDetail] = None
        self.current_source: Optional[BaseSource] = None
        self._cards: List[BookCard] = []
        self._shelf_cards: List[BookCard] = []
        self._cover_cache: Dict[str, bytes] = {}
        self._cover_pending: List[tuple] = []      # [(url, apply, widget)]
        self._cover_active = 0
        self._cover_timer = QTimer(self)
        self._cover_timer.setSingleShot(True)
        self._cover_timer.setInterval(150)
        self._cover_timer.timeout.connect(self._pump_cover_queue)
        self._pending_restore: Optional[int] = None
        self._loading_chapter = False
        self._sidebar_restore = False

        self.setWindowTitle(f"{APP_TITLE} v{APP_VERSION}")
        self.resize(1280, 820)
        self.setMinimumSize(980, 640)

        self._build_ui()
        self._bind_shortcuts()
        self._restore_geometry()
        self._refresh_source_status()

        last = self.config.get("last_search")
        if last:
            self.search_box.setText(last)

        # 应用上次的侧栏状态（可能是收起的）
        if not self.config.get("sidebar_visible", True):
            self._set_sidebar_visible(False)

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
        top = QFrame(central)
        top.setObjectName("topBar")
        top_layout = QHBoxLayout(top)
        top_layout.setContentsMargins(16, 10, 16, 10)
        top_layout.setSpacing(10)

        logo = QLabel("📖 小说搜索阅读器", top)
        logo.setStyleSheet("font-size: 15px; font-weight: 700;")
        top_layout.addWidget(logo)

        self.search_box = QLineEdit(top)
        self.search_box.setObjectName("searchBox")
        self.search_box.setPlaceholderText("输入小说名称后按回车，例如：斗罗大陆")
        self.search_box.setClearButtonEnabled(True)
        self.search_box.returnPressed.connect(self.on_search)
        top_layout.addWidget(self.search_box, 1)

        self.search_button = QPushButton("搜索", top)
        self.search_button.setObjectName("primary")
        self.search_button.clicked.connect(self.on_search)
        top_layout.addWidget(self.search_button)

        self.source_label = QLabel("", top)
        self.source_label.setObjectName("muted")
        top_layout.addWidget(self.source_label)

        # 侧栏收放（阅读时尤其有用）
        self.sidebar_button = QPushButton("☰ 侧栏", top)
        self.sidebar_button.setCheckable(True)
        self.sidebar_button.setChecked(bool(self.config.get("sidebar_visible", True)))
        self.sidebar_button.setToolTip("显示 / 隐藏左侧栏（Ctrl+B）")
        self.sidebar_button.toggled.connect(self._on_sidebar_toggled)
        top_layout.addWidget(self.sidebar_button)

        settings_button = QPushButton("设置", top)
        settings_button.clicked.connect(self.open_settings)
        top_layout.addWidget(settings_button)

        root.addWidget(top)

        # ------------------------------------------------------------ 提示条
        self.banner = Banner(central)
        root.addWidget(self.banner)

        # ------------------------------------------------------------ 主体
        splitter = QSplitter(Qt.Horizontal, central)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(1)
        root.addWidget(splitter, 1)

        self.side_panel = self._build_side_panel(splitter)
        splitter.addWidget(self.side_panel)

        self.stack = QStackedWidget(splitter)
        self.welcome = EmptyState(
            "开始搜索你的下一本小说",
            "在顶部输入书名后回车。搜索会同时查询所有已启用的书源，\n"
            "点击结果卡片即可查看简介与完整目录，并直接在应用内阅读。")
        self.stack.addWidget(self.welcome)

        self.detail_panel = DetailPanel(self.stack)
        self.detail_panel.read_requested.connect(self.on_read_requested)
        self.detail_panel.shelf_toggled.connect(self.on_shelf_toggled)
        self.detail_panel.refresh_requested.connect(self.on_refresh_detail)
        self.detail_panel.back_requested.connect(lambda: self.stack.setCurrentWidget(self.welcome))
        self.stack.addWidget(self.detail_panel)

        self.reader = ReaderView(self.config, self.stack)
        self.reader.chapter_requested.connect(self.load_chapter)
        self.reader.back_requested.connect(self.on_reader_back)
        self.reader.position_changed.connect(self.on_position_changed)
        self.stack.addWidget(self.reader)

        splitter.addWidget(self.stack)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        sizes = self.config.get("splitter_sizes") or []
        splitter.setSizes(sizes if len(sizes) == 2 else [380, 900])
        self.splitter = splitter

        self.statusBar().showMessage("就绪")
        self.stack.setCurrentWidget(self.welcome)

    def _build_side_panel(self, parent: QWidget) -> QWidget:
        panel = QFrame(parent)
        panel.setObjectName("sidePanel")
        panel.setMinimumWidth(300)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QFrame(panel)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(10, 8, 10, 4)
        header_layout.setSpacing(6)

        self.results_tab = QToolButton(header)
        self.results_tab.setText("搜索结果")
        self.results_tab.setCheckable(True)
        self.results_tab.setChecked(True)
        self.results_tab.clicked.connect(lambda: self._switch_side(0))
        header_layout.addWidget(self.results_tab)

        self.shelf_tab = QToolButton(header)
        self.shelf_tab.setText("我的书架")
        self.shelf_tab.setCheckable(True)
        self.shelf_tab.clicked.connect(lambda: self._switch_side(1))
        header_layout.addWidget(self.shelf_tab)
        header_layout.addStretch(1)

        self.side_hint = QLabel("", header)
        self.side_hint.setObjectName("muted")
        header_layout.addWidget(self.side_hint)
        layout.addWidget(header)

        self.side_stack = QStackedWidget(panel)

        # 搜索结果页
        self.results_scroll = QScrollArea(panel)
        self.results_scroll.setWidgetResizable(True)
        self.results_container = QWidget(self.results_scroll)
        self.results_layout = QVBoxLayout(self.results_container)
        self.results_layout.setContentsMargins(10, 4, 10, 12)
        self.results_layout.setSpacing(8)
        self.results_layout.addStretch(1)
        self.results_scroll.setWidget(self.results_container)
        # 滚动时补下载"刚滚到可见区域"的封面
        self.results_scroll.verticalScrollBar().valueChanged.connect(
            lambda *_: self._cover_timer.start())
        self.side_stack.addWidget(self.results_scroll)

        # 书架页
        self.shelf_scroll = QScrollArea(panel)
        self.shelf_scroll.setWidgetResizable(True)
        self.shelf_container = QWidget(self.shelf_scroll)
        self.shelf_layout = QVBoxLayout(self.shelf_container)
        self.shelf_layout.setContentsMargins(10, 4, 10, 12)
        self.shelf_layout.setSpacing(8)
        self.shelf_layout.addStretch(1)
        self.shelf_scroll.setWidget(self.shelf_container)
        self.shelf_scroll.verticalScrollBar().valueChanged.connect(
            lambda *_: self._cover_timer.start())
        self.side_stack.addWidget(self.shelf_scroll)

        layout.addWidget(self.side_stack, 1)
        self._refresh_shelf()
        return panel

    def _bind_shortcuts(self) -> None:
        QShortcut(QKeySequence("Ctrl+F"), self, activated=self.search_box.setFocus)
        QShortcut(QKeySequence("Ctrl+L"), self, activated=self.search_box.setFocus)
        QShortcut(QKeySequence("Ctrl+Return"), self, activated=self.on_search)
        QShortcut(QKeySequence("F5"), self, activated=self._reload_current_chapter)
        QShortcut(QKeySequence("Ctrl+B"), self,
                  activated=lambda: self.sidebar_button.toggle())
        QShortcut(QKeySequence("Ctrl+="), self, activated=lambda: self.reader.change_font_size(1))
        QShortcut(QKeySequence("Ctrl++"), self, activated=lambda: self.reader.change_font_size(1))
        QShortcut(QKeySequence("Ctrl+-"), self, activated=lambda: self.reader.change_font_size(-1))
        QShortcut(QKeySequence("F11"), self, activated=self._toggle_fullscreen)

    # ---------------------------------------------------------------- 侧栏
    def _on_sidebar_toggled(self, visible: bool) -> None:
        """用户手动切换侧栏（记到配置里）。"""
        self._sidebar_restore = False      # 手动操作后不再自动恢复
        self._set_sidebar_visible(visible, persist=True)

    def _set_sidebar_visible(self, visible: bool, persist: bool = False) -> None:
        """显示/隐藏左侧栏；重新显示时恢复原来的宽度。"""
        self.side_panel.setVisible(visible)
        if self.sidebar_button.isChecked() != visible:
            self.sidebar_button.blockSignals(True)
            self.sidebar_button.setChecked(visible)
            self.sidebar_button.blockSignals(False)
        if visible:
            sizes = self.config.get("splitter_sizes") or [380, 900]
            if len(sizes) == 2 and sizes[0] > 0:
                QTimer.singleShot(0, lambda: self.splitter.setSizes(sizes))
        if persist:
            self.config.set("sidebar_visible", visible)

    def _toggle_fullscreen(self) -> None:
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    # ------------------------------------------------------------------ 搜索
    def on_search(self) -> None:
        keyword = self.search_box.text().strip()
        if not keyword:
            self.banner.show_message("请先输入要搜索的小说名称。")
            self.search_box.setFocus()
            return
        self.config.set("last_search", keyword)
        self.sources = build_sources(self.config, self.http)
        if not self.sources:
            self.banner.show_message(
                "当前没有启用任何书源。请打开「设置 → 书源」勾选至少一个书源。", "error")
            return

        self._clear_results()
        self.banner.hide_banner()
        self.search_button.setEnabled(False)
        self.search_button.setText("搜索中…")
        self._switch_side(0)
        self.side_hint.setText(f"搜索：{keyword}")
        self.statusBar().showMessage(f"正在 {len(self.sources)} 个书源上搜索「{keyword}」…")

        task = SearchTask(self.http, self.sources, keyword,
                          limit=int(self.config.get("search_limit")), stats=self.stats)
        task.signals.progress.connect(self._on_progress)
        task.signals.partial.connect(self._on_search_partial)
        task.signals.finished.connect(self._on_search_finished)
        task.signals.failed.connect(self._on_task_failed)
        self.task_manager.start(task)

    def _on_search_partial(self, outcome: SearchOutcome) -> None:
        if outcome.error:
            self.banner.show_message(
                f"书源「{outcome.source_name}」搜索失败：{outcome.error}", "error")
            return
        if not outcome.books:
            return
        for book in outcome.books:
            self._add_result_card(book)

    def _on_search_finished(self, outcomes: List[SearchOutcome]) -> None:
        self.search_button.setEnabled(True)
        self.search_button.setText("搜索")
        total = sum(len(o.books) for o in outcomes)
        failed = [o for o in outcomes if o.error]
        if total == 0:
            self.banner.show_message(
                "没有找到匹配的小说。可以换一个关键词，或让程序去网络上找一个新书源。",
                action_text="网络找书源", action=self.open_discover_dialog)
            self.statusBar().showMessage("搜索完成，没有结果")
        else:
            self.statusBar().showMessage(
                f"搜索完成：共 {total} 条结果"
                + (f"，{len(failed)} 个书源失败" if failed else ""))
        self.side_hint.setText(f"共 {total} 条")
        self._auto_disable_failing_sources()

    def _add_result_card(self, book: Book) -> None:
        if len(self._cards) >= 150:
            return
        card = BookCard(book, self.results_container)
        card.clicked.connect(self.on_card_clicked)
        self.results_layout.insertWidget(self.results_layout.count() - 1, card)
        self._cards.append(card)
        if self.config.get("auto_load_cover") and book.cover_url:
            self._load_cover(book.cover_url, card.cover.set_image, card.cover)

    def _clear_results(self) -> None:
        for card in self._cards:
            card.setParent(None)
            card.deleteLater()
        self._cards.clear()
        self._cover_pending.clear()
        self.side_hint.setText("")

    # ------------------------------------------------------------------ 详情
    def on_card_clicked(self, book: Book) -> None:
        for card in self._cards:
            card.set_selected(card.book.key == book.key)
        for card in self._shelf_cards:
            card.set_selected(card.book.key == book.key)
        source = self._source_for(book)
        if source is None:
            self.banner.show_message(
                f"该书源（{book.source_name or book.source}）已被禁用，"
                "请在「设置 → 书源」中重新启用。", "error")
            return
        self.current_book = book
        self.current_source = source
        self.banner.hide_banner()
        self.statusBar().showMessage(f"正在获取《{book.title}》的目录…")
        self.stack.setCurrentWidget(self.detail_panel)
        self.detail_panel.title_label.setText(book.title)
        self.detail_panel.meta_label.setText("正在加载目录…")
        self.detail_panel.intro_label.setText("")
        self.detail_panel.catalog.clear()

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
        self.detail_panel.set_detail(detail, in_shelf=self.library.contains(book.key),
                                     cached_count=cached,
                                     progress=self.library.progress(book.key))
        if self.config.get("auto_load_cover") and book.cover_url:
            self._load_cover(book.cover_url, self.detail_panel.set_cover,
                             self.detail_panel.cover)
        self.statusBar().showMessage(f"《{book.title}》共 {len(detail.chapters)} 章")

    def _on_detail_failed(self, message: str, detail: str) -> None:
        self.statusBar().showMessage("目录获取失败")
        self.banner.show_message(f"目录获取失败：{message}", "error")
        self.detail_panel.meta_label.setText(f"加载失败：{message}")
        self.detail_panel.intro_label.setText(
            "可能原因：站点结构变化、网络不通、或该书源需要人机验证。\n"
            "建议：点击「返回搜索结果」换一个书源，或在设置中检测书源可用性。")

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
        self.current_detail = detail
        self.current_book = detail.book
        self.current_source = self._source_for(detail.book) or self.current_source
        self.reader.set_book(len(detail.chapters), index)
        self.reader.apply_settings()
        # 阅读时自动收起左侧栏（读完返回目录时会自动恢复）
        if self.config.get("auto_hide_sidebar", True) and self.side_panel.isVisible():
            self._sidebar_restore = True
            self._set_sidebar_visible(False)
        self.stack.setCurrentWidget(self.reader)
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
        restore = self.reader.position_for(chapter.url)
        if not restore and progress.get("index") == index:
            restore = progress.get("scroll_pos") or 0
        self._pending_restore = restore

        task = ChapterTask(self.current_source, self.current_detail.book, chapter,
                           self.cache, stats=self.stats)
        task.signals.progress.connect(self._on_progress)
        task.signals.finished.connect(lambda content: self._on_chapter_ready(content, index))
        task.signals.failed.connect(lambda msg, detail: self._on_chapter_failed(msg, detail, index))
        self.task_manager.start(task)

    def _on_chapter_ready(self, content: ChapterContent, index: int) -> None:
        self._loading_chapter = False
        restore = self._pending_restore or 0
        self._pending_restore = None
        self.reader.set_content(content, index=index, restore_pos=restore)
        self.detail_panel.set_reading_progress(index)
        book = self.current_detail.book
        self.library.update_progress(book, content.url, content.title, index, restore)
        self.statusBar().showMessage(
            f"《{book.title}》 · 第 {index + 1} 章 / 共 {self.reader.chapter_count} 章"
            + ("（缓存）" if content.from_cache else ""))
        self._refresh_shelf_card_progress(book.key, index)

    def _on_chapter_failed(self, message: str, detail: str, index: int) -> None:
        self._loading_chapter = False
        self.reader.set_error(message)
        self.banner.show_message(f"章节加载失败：{message}", "error")
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

    def on_reader_back(self) -> None:
        if self.current_detail is not None:
            self.stack.setCurrentWidget(self.detail_panel)
        else:
            self.stack.setCurrentWidget(self.welcome)
        # 恢复进入阅读器前被自动收起的侧栏
        if getattr(self, "_sidebar_restore", False):
            self._sidebar_restore = False
            self._set_sidebar_visible(True)

    def on_position_changed(self, index: int, scroll_pos: int) -> None:
        if self.current_detail is None:
            return
        chapters = self.current_detail.chapters
        if not 0 <= index < len(chapters):
            return
        chapter = chapters[index]
        self.library.update_progress(self.current_detail.book, chapter.url,
                                     chapter.title, index, scroll_pos)

    # ------------------------------------------------------------------ 书架
    def on_shelf_toggled(self, book: Book) -> None:
        added = self.library.toggle(book, self.current_detail)
        self.detail_panel.shelf_button.setText("移出书架" if added else "加入书架")
        self._refresh_shelf()
        self.statusBar().showMessage("已加入书架" if added else "已移出书架")

    def _refresh_shelf(self) -> None:
        for card in self._shelf_cards:
            card.setParent(None)
            card.deleteLater()
        self._shelf_cards.clear()
        while self.shelf_layout.count() > 1:
            item = self.shelf_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

        books = self.library.books()
        if not books:
            empty = QLabel("书架还是空的。\n搜索到喜欢的小说后，在详情页点击「加入书架」。",
                           self.shelf_container)
            empty.setObjectName("muted")
            empty.setWordWrap(True)
            empty.setAlignment(Qt.AlignCenter)
            empty.setContentsMargins(12, 24, 12, 12)
            self.shelf_layout.insertWidget(0, empty)
            self.shelf_tab.setText("我的书架")
        else:
            for record in books:
                self._add_shelf_card(record, self.shelf_layout.count() - 1)
            self.shelf_tab.setText(f"我的书架({len(books)})")

        # 最近阅读（含未加入书架的书），方便继续上次的进度
        shelf_keys = {r.get("key") for r in books}
        history = [h for h in self.library.history() if h.get("key") not in shelf_keys][:10]
        if history:
            title = QLabel("最近阅读", self.shelf_container)
            title.setObjectName("sideHeader")
            self.shelf_layout.insertWidget(self.shelf_layout.count() - 1, title)
            for record in history:
                self._add_shelf_card(record, self.shelf_layout.count() - 1)

    def _add_shelf_card(self, record: Dict, position: int) -> None:
        """把一条书架/历史记录渲染成卡片。"""
        book = Book(
            title=record.get("title", ""), author=record.get("author", ""),
            url=record.get("url", ""), cover_url=record.get("cover_url", ""),
            intro=record.get("intro", ""), source=record.get("source", ""),
            source_name=record.get("source_name", ""),
            category=record.get("category", ""), status=record.get("status", ""),
            latest_chapter=record.get("latest_chapter", ""))
        card = BookCard(book, self.shelf_container)
        if record.get("last_chapter_title"):
            card.meta_label.setText(
                f"{card.meta_label.text()}　·　读到：{record['last_chapter_title']}")
        card.clicked.connect(self.on_card_clicked)
        self.shelf_layout.insertWidget(position, card)
        self._shelf_cards.append(card)
        if self.config.get("auto_load_cover") and book.cover_url:
            self._load_cover(book.cover_url, card.cover.set_image, card.cover)

    def _refresh_shelf_card_progress(self, key: str, index: int) -> None:
        record = self.library.get(key)
        if record is None:
            return
        for card in self._shelf_cards:
            if card.book.key == key and record.get("last_chapter_title"):
                base = card.meta_label.text().split("　·　读到：")[0]
                card.meta_label.setText(
                    f"{base}　·　读到：{record['last_chapter_title']}")

    def _switch_side(self, index: int) -> None:
        self.side_stack.setCurrentIndex(index)
        self.results_tab.setChecked(index == 0)
        self.shelf_tab.setChecked(index == 1)
        if index == 1:
            self._refresh_shelf()

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
        self.banner.show_message(
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
            self.banner.show_message(f"已从订阅更新 {len(rules)} 个书源。")
        elif failures:
            self.banner.show_message("书源订阅更新失败：" + "；".join(failures[:2]), "error")
        self.statusBar().showMessage("就绪")

    def _on_progress(self, text: str) -> None:
        self.statusBar().showMessage(text)

    def _on_task_failed(self, message: str, detail: str) -> None:
        self.search_button.setEnabled(True)
        self.search_button.setText("搜索")
        self.banner.show_message(message, "error")
        self.statusBar().showMessage("操作失败")

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
        keyword = self.search_box.text().strip() or self.config.get("last_search") or ""
        dialog = SourceDiscoverDialog(self.config, self.http, self.task_manager,
                                      book_title=keyword, parent=self)
        dialog.sources_changed.connect(self._on_discovered_sources)
        dialog.exec_()

    def _on_discovered_sources(self) -> None:
        """发现并保存了新书源后：刷新书源列表，并用同一个关键词重搜一次。"""
        self.sources = build_sources(self.config, self.http)
        self._refresh_source_status()
        self.banner.hide_banner()
        keyword = self.search_box.text().strip() or self.config.get("last_search") or ""
        if keyword:
            self.statusBar().showMessage("已添加新书源，正在重新搜索…")
            self.on_search()

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
        try:
            self.config.set("window_geometry",
                            base64.b64encode(bytes(self.saveGeometry())).decode("ascii"),
                            autosave=False)
            self.config.set("window_state",
                            base64.b64encode(bytes(self.saveState())).decode("ascii"),
                            autosave=False)
            self.config.set("splitter_sizes", self.splitter.sizes(), autosave=False)
            self.config.save()
        except Exception:
            pass
        self.task_manager.cancel_all()
        self.task_manager.wait(1500)   # 给工作线程一点时间收尾，避免关闭时崩溃
        self.cache.close()
        self.http.close()
        super().closeEvent(event)

# -*- coding: utf-8 -*-
"""Ctrl+K 搜索浮层。

替代原来的"常驻左侧结果栏"：居中的一张卡片，上面是输入框，下面是结果列表。

* 输入 300ms 防抖后自动搜索（``config.search_debounce_ms``）；
* 结果行：封面缩略 + 书名 + 作者 + 来源 + 最新章节；
* 键盘：``↑/↓`` 选择、``Enter`` 打开详情、``Esc`` 关闭；
* 搜索进度 / 失败的书源 / 0 结果提示都显示在面板内，
  0 结果时给出「网络找书源」快捷入口。
"""
from __future__ import annotations

from typing import Callable, List, Optional

from PyQt5.QtCore import QEvent, Qt, QTimer, pyqtSignal
from PyQt5.QtWidgets import (QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                             QListWidgetItem, QPushButton, QVBoxLayout, QWidget)

from ..models import Book, SearchOutcome
from ..sources import build_sources
from ..tasks import SearchTask
from .widgets import FADE_MS, CoverLabel, fade


class SearchResultRow(QFrame):
    """搜索面板里的一行结果。"""

    def __init__(self, book: Book, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.book = book
        self.setObjectName("paletteRow")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 8, 12, 8)
        layout.setSpacing(12)

        self.cover = CoverLabel(44, 60, self)
        self.cover.set_title(book.title)
        layout.addWidget(self.cover, 0, Qt.AlignVCenter)

        info = QVBoxLayout()
        info.setSpacing(2)
        self.title_label = QLabel(book.title or "未知书名", self)
        self.title_label.setObjectName("bookTitle")
        info.addWidget(self.title_label)

        parts = []
        if book.author:
            parts.append(f"作者：{book.author}")
        if book.status:
            parts.append(book.status)
        if book.category:
            parts.append(book.category)
        self.meta_label = QLabel("　·　".join(parts) or "作者：未知", self)
        self.meta_label.setObjectName("bookMeta")
        info.addWidget(self.meta_label)

        if book.latest_chapter:
            latest = QLabel(f"最新：{book.latest_chapter}", self)
            latest.setObjectName("bookMeta")
            info.addWidget(latest)
        layout.addLayout(info, 1)

        if book.source_name:
            tag = QLabel(book.source_name, self)
            tag.setObjectName("sourceTag")
            layout.addWidget(tag, 0, Qt.AlignTop)

    def set_cover(self, data: bytes) -> None:
        self.cover.set_image(data)


class SearchPalette(QFrame):
    """搜索浮层（由主窗口负责定位与遮罩）。"""

    book_chosen = pyqtSignal(object)      # Book
    closed = pyqtSignal()
    discover_requested = pyqtSignal()     # 0 结果时的「网络找书源」
    search_finished = pyqtSignal(object)  # 一次搜索完成（outcomes），供主窗口做健康度统计

    def __init__(self, config, http, task_manager, stats=None,
                 cover_loader: Optional[Callable] = None,
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.config = config
        self.http = http
        self.task_manager = task_manager
        self.stats = stats
        self._cover_loader = cover_loader
        self._rows: List[SearchResultRow] = []
        self._searching = False
        self._queued_keyword = ""
        self._last_keyword = ""
        self._seq = 0
        self._shown_state = False          # 真实开合状态（动画只负责视觉）

        self.setObjectName("paletteCard")
        self.setAttribute(Qt.WA_StyledBackground, True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ---------------------------------------------------------- 输入行
        input_row = QHBoxLayout()
        input_row.setContentsMargins(16, 14, 12, 10)
        input_row.setSpacing(10)
        icon = QLabel("🔍", self)
        icon.setObjectName("paletteIcon")
        input_row.addWidget(icon)

        self.input = QLineEdit(self)
        self.input.setObjectName("paletteInput")
        self.input.setPlaceholderText("输入小说名称，回车或稍等片刻自动搜索")
        self.input.setClearButtonEnabled(True)
        self.input.textChanged.connect(self._on_text_changed)
        self.input.returnPressed.connect(self._choose_current)
        self.input.installEventFilter(self)
        input_row.addWidget(self.input, 1)

        self.close_button = QPushButton("Esc", self)
        self.close_button.setObjectName("ghost")
        self.close_button.setFixedWidth(46)
        self.close_button.setToolTip("关闭搜索面板")
        self.close_button.clicked.connect(self.close_palette)
        input_row.addWidget(self.close_button)
        layout.addLayout(input_row)

        # ---------------------------------------------------------- 结果列表
        self.results = QListWidget(self)
        self.results.setObjectName("paletteResults")
        self.results.setFrameShape(QFrame.NoFrame)
        self.results.setUniformItemSizes(False)
        self.results.itemClicked.connect(self._on_item_activated)
        self.results.itemActivated.connect(self._on_item_activated)
        layout.addWidget(self.results, 1)

        # ---------------------------------------------------------- 底部提示
        footer = QHBoxLayout()
        footer.setContentsMargins(16, 8, 12, 12)
        footer.setSpacing(8)
        self.hint = QLabel("输入书名开始搜索（Ctrl+K 随时唤起）", self)
        self.hint.setObjectName("paletteHint")
        self.hint.setWordWrap(True)
        footer.addWidget(self.hint, 1)
        self.discover_button = QPushButton("网络找书源", self)
        self.discover_button.setObjectName("ghost")
        self.discover_button.setVisible(False)
        self.discover_button.clicked.connect(self._on_discover_clicked)
        footer.addWidget(self.discover_button)
        layout.addLayout(footer)

        # 防抖计时器
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(int(self.config.get("search_debounce_ms") or 300))
        self._debounce.timeout.connect(self._run_search)

        self.hide()

    # ------------------------------------------------------------------ 开关
    def open(self, keyword: str = "") -> None:  # noqa: A003 - 与 Qt 习惯一致
        """淡入显示面板；带新关键词时直接搜一次（同一关键词不重复搜）。"""
        if keyword and keyword != self.input.text():
            self.input.setText(keyword)
        self._shown_state = True
        fade(self, True, FADE_MS)          # 200ms 淡入
        self.raise_()
        self.input.setFocus()
        self.input.selectAll()
        keyword = keyword.strip() or self.keyword()
        if keyword and not (keyword == self._last_keyword and self._rows):
            self._debounce.stop()
            self._run_search()

    def close_palette(self) -> None:
        """淡出隐藏；状态立即置为关闭（不依赖动画跑完）。"""
        self._debounce.stop()
        was_open = self._shown_state
        self._shown_state = False
        if self.isVisible():
            fade(self, False, FADE_MS)
        if was_open or self.isVisible():
            self.closed.emit()

    def is_open(self) -> bool:
        return self._shown_state

    def keyword(self) -> str:
        return self.input.text().strip()

    def rows(self) -> List[SearchResultRow]:
        """结果行（自检用）。"""
        return list(self._rows)

    # ------------------------------------------------------------------ 搜索
    def _on_text_changed(self, text: str) -> None:
        self.hint.setText("")
        self.discover_button.setVisible(False)
        if not text.strip():
            self._clear_rows()
            self.hint.setText("输入书名开始搜索（Ctrl+K 随时唤起）")
            return
        self._debounce.start()

    def _run_search(self) -> None:
        keyword = self.input.text().strip()
        if not keyword:
            return
        if self._searching:
            self._queued_keyword = keyword       # 搜完再补一次，避免并发堆积
            return
        if keyword == self._last_keyword:
            return
        sources = build_sources(self.config, self.http)
        if not sources:
            self.hint.setText("当前没有启用任何书源，请在「设置 → 书源」里勾选。")
            return
        self._last_keyword = keyword
        self._searching = True
        self._seq += 1
        seq = self._seq
        self._clear_rows()
        self.hint.setText(f"正在 {len(sources)} 个书源上搜索「{keyword}」…")
        self.discover_button.setVisible(False)

        task = SearchTask(self.http, sources, keyword,
                          limit=int(self.config.get("search_limit")), stats=self.stats)
        task.signals.progress.connect(
            lambda text, s=seq: self._on_progress(text, s))
        task.signals.partial.connect(lambda o, s=seq: self._on_partial(o, s))
        task.signals.finished.connect(lambda outs, s=seq: self._on_finished(outs, s))
        task.signals.failed.connect(lambda msg, d, s=seq: self._on_failed(msg, s))
        self.task_manager.start(task)

    def _on_progress(self, text: str, seq: int) -> None:
        if seq == self._seq and self._searching:
            self.hint.setText(text)

    def _on_partial(self, outcome: SearchOutcome, seq: int) -> None:
        if seq != self._seq:
            return
        if outcome.error:
            self.hint.setText(f"书源「{outcome.source_name}」搜索失败：{outcome.error}")
            return
        for book in outcome.books:
            self._add_row(book)

    def _on_finished(self, outcomes: List[SearchOutcome], seq: int) -> None:
        if seq != self._seq:
            return
        self._searching = False
        self.search_finished.emit(outcomes)
        total = sum(len(o.books) for o in outcomes)
        failed = [o for o in outcomes if o.error]
        if total == 0:
            self.hint.setText("没有找到匹配的小说。可以换个关键词，"
                              "或者让程序去网络上找一个新书源。")
            self.discover_button.setVisible(True)
        else:
            text = f"共 {total} 条结果"
            if failed:
                text += f"，{len(failed)} 个书源失败"
            self.hint.setText(text)
        if self._queued_keyword and self._queued_keyword != self._last_keyword:
            queued, self._queued_keyword = self._queued_keyword, ""
            self.input.setText(queued)
            self._run_search()
        else:
            self._queued_keyword = ""

    def _on_failed(self, message: str, seq: int) -> None:
        if seq != self._seq:
            return
        self._searching = False
        self.hint.setText(message)
        self.discover_button.setVisible(True)

    # ------------------------------------------------------------------ 结果
    def _add_row(self, book: Book) -> None:
        if len(self._rows) >= 150:
            return
        row = SearchResultRow(book, self.results)
        item = QListWidgetItem(self.results)
        item.setData(Qt.UserRole, book)
        item.setSizeHint(row.sizeHint())
        self.results.addItem(item)
        self.results.setItemWidget(item, row)
        self._rows.append(row)
        if self._rows and self.results.currentRow() < 0:
            self.results.setCurrentRow(0)
        if book.cover_url and self._cover_loader:
            self._cover_loader(book.cover_url, row.set_cover, row.cover)

    def _clear_rows(self) -> None:
        self.results.clear()
        self._rows.clear()

    def _current_book(self) -> Optional[Book]:
        item = self.results.currentItem()
        if item is None:
            return None
        return item.data(Qt.UserRole)

    def _choose_current(self) -> None:
        book = self._current_book()
        if book is None and self._rows:
            book = self._rows[0].book
        if book is not None:
            self.book_chosen.emit(book)

    def _on_item_activated(self, item: QListWidgetItem) -> None:
        book = item.data(Qt.UserRole)
        if book is not None:
            self.book_chosen.emit(book)

    def _on_discover_clicked(self) -> None:
        self.discover_requested.emit()

    # ------------------------------------------------------------------ 键盘
    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        """输入框里也要能用 ↑/↓/Esc（焦点不在列表上）。"""
        if obj is self.input and event.type() == QEvent.KeyPress:
            key = event.key()
            if key == Qt.Key_Escape:
                self.close_palette()
                return True
            if key in (Qt.Key_Down, Qt.Key_Up):
                self._move_selection(1 if key == Qt.Key_Down else -1)
                return True
        return super().eventFilter(obj, event)

    def _move_selection(self, delta: int) -> None:
        count = self.results.count()
        if count == 0:
            return
        row = self.results.currentRow()
        row = 0 if row < 0 else max(0, min(count - 1, row + delta))
        self.results.setCurrentRow(row)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key_Escape:
            self.close_palette()
            return
        super().keyPressEvent(event)

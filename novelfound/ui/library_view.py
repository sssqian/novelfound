# -*- coding: utf-8 -*-
"""书架首页（启动首屏）。

按方案 ①「书架首页」实现，自上而下三段：

1. **继续阅读** —— 大卡片，直接接着上次的章节读；
2. **最近阅读** —— 横排小封面（含未加入书架的书）；
3. **我的书架** —— 封面网格，每格只有封面 + 书名 + 百分比文字（按反馈不放进度条）。

书架为空时显示引导语 + 「搜索小说」按钮（唤起搜索浮层）。
封面沿用主窗口的节流队列（只下可见项、并发上限），通过 ``cover_loader`` 回调注入。
"""
from __future__ import annotations

from typing import Callable, Dict, List, Optional

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel, QScrollArea,
                             QSizePolicy, QVBoxLayout, QWidget)

from ..models import Book
from .widgets import CoverLabel, CoverTile, EmptyState, SectionTitle


def progress_text(record: Dict, progress: Dict) -> str:
    """书架格子上那行小字：百分比 / 未读 / 第 N 章。

    方案要求：**只显示百分比文字，不放进度条**；没读过的显示「未读」。
    """
    index = int((progress or {}).get("index", 0) or 0)
    if not (progress or {}).get("chapter_url"):
        return "未读"
    total = int((record or {}).get("chapter_count") or 0)
    if total > 0:
        percent = max(1, min(100, round((index + 1) / total * 100)))
        return f"{percent}%"
    return f"第 {index + 1} 章"


class ContinueCard(QFrame):
    """「继续阅读」大卡片：封面 + 书名 + 上次读到的章节 + 进度文字。"""

    clicked = pyqtSignal(object)      # Book

    def __init__(self, book: Book, record: Dict, progress: Dict,
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.book = book
        self.setObjectName("continueCard")
        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 14, 18, 14)
        layout.setSpacing(16)

        self.cover = CoverLabel(72, 100, self)
        self.cover.set_title(book.title)
        layout.addWidget(self.cover, 0, Qt.AlignVCenter)

        info = QVBoxLayout()
        info.setSpacing(6)
        title = QLabel(book.title or "未知书名", self)
        title.setObjectName("continueTitle")
        info.addWidget(title)

        index = int((progress or {}).get("index", 0) or 0)
        chapter_title = (progress or {}).get("chapter_title") or ""
        subtitle = (f"第 {index + 1} 章 · {chapter_title}" if chapter_title
                    else "还没开始读，点一下从第一章开始")
        self.chapter_label = QLabel(subtitle, self)
        self.chapter_label.setObjectName("bookMeta")
        self.chapter_label.setWordWrap(True)
        info.addWidget(self.chapter_label)
        info.addStretch(1)
        layout.addLayout(info, 1)

        right = QVBoxLayout()
        right.setSpacing(4)
        self.percent_label = QLabel(progress_text(record, progress), self)
        self.percent_label.setObjectName("continuePercent")
        self.percent_label.setAlignment(Qt.AlignRight | Qt.AlignTop)
        right.addWidget(self.percent_label)
        right.addStretch(1)
        hint = QLabel("继续阅读 →", self)
        hint.setObjectName("bookMeta")
        right.addWidget(hint, 0, Qt.AlignRight)
        layout.addLayout(right, 0)

    def set_cover(self, data: bytes) -> None:
        self.cover.set_image(data)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.book)
        super().mouseReleaseEvent(event)


class LibraryView(QWidget):
    """书架首页。"""

    book_opened = pyqtSignal(object)          # Book：打开详情
    continue_requested = pyqtSignal(object)   # Book：直接接着上次读
    search_requested = pyqtSignal()           # 空状态里的「搜索小说」

    def __init__(self, config, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.config = config
        self.setObjectName("libraryView")
        self._cover_loader: Optional[Callable] = None
        self._tiles: List[CoverTile] = []
        self._recent_tiles: List[CoverTile] = []
        self.continue_card: Optional[ContinueCard] = None
        self._columns = 0

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 空状态（书架与历史都为空时显示）
        self.empty = EmptyState(
            "书架还是空的",
            "搜索到喜欢的小说后，在详情页点「加入书架」，\n"
            "之后就能从这里一键继续阅读。")
        self.empty_button = self.empty.add_action("搜索小说")
        self.empty_button.clicked.connect(self.search_requested.emit)
        root.addWidget(self.empty, 1)

        # 正常内容
        self.scroll = QScrollArea(self)
        self.scroll.setObjectName("pageScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.container = QWidget(self.scroll)
        self.container.setObjectName("pageBody")
        self.body = QVBoxLayout(self.container)
        self.body.setContentsMargins(32, 24, 32, 32)
        self.body.setSpacing(14)
        self.scroll.setWidget(self.container)
        root.addWidget(self.scroll, 1)

        # 继续阅读
        self.continue_title = SectionTitle("继续阅读", self.container)
        self.body.addWidget(self.continue_title)
        self.continue_host = QVBoxLayout()
        self.continue_host.setSpacing(10)
        self.body.addLayout(self.continue_host)

        # 最近阅读
        self.recent_title = SectionTitle("最近阅读", self.container)
        self.body.addWidget(self.recent_title)
        self.recent_host = QHBoxLayout()
        self.recent_host.setSpacing(10)
        self.recent_host.addStretch(1)
        self.body.addLayout(self.recent_host)

        # 我的书架
        self.shelf_title = SectionTitle("我的书架", self.container)
        self.body.addWidget(self.shelf_title)
        self.grid_host = QWidget(self.container)
        self.grid = QGridLayout(self.grid_host)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(12)
        self.grid.setVerticalSpacing(16)
        self.body.addWidget(self.grid_host)
        self.body.addStretch(1)

        self._books: List[Book] = []

    # ------------------------------------------------------------------ 数据
    def refresh(self, library, cover_loader: Optional[Callable] = None) -> None:
        """按当前书架/历史重建首页内容。"""
        self._cover_loader = cover_loader or self._cover_loader
        self._clear()
        records = library.books()
        history = [h for h in library.history()
                   if h.get("key") not in {r.get("key") for r in records}]
        self._books = [Book.from_record(r) for r in records]

        if not records and not history:
            self.empty.show()
            self.scroll.hide()
            return
        self.empty.hide()
        self.scroll.show()

        # ---- 继续阅读：最近读过的一本（书架优先）
        latest = None
        for record in records + history:
            if library.progress(record.get("key", "")).get("chapter_url"):
                latest = record
                break
        if latest is None and (records or history):
            latest = (records + history)[0]
        if latest is not None:
            book = Book.from_record(latest)
            progress = library.progress(latest.get("key", ""))
            card = ContinueCard(book, latest, progress, self.container)
            card.clicked.connect(self.continue_requested.emit)
            self.continue_host.addWidget(card)
            self.continue_card = card
            if book.cover_url and self._cover_loader:
                self._cover_loader(book.cover_url, card.set_cover, card.cover)
            self.continue_title.show()
        else:
            self.continue_title.hide()

        # ---- 最近阅读（不含已进书架的书）
        if history:
            for record in history[:12]:
                book = Book.from_record(record)
                tile = CoverTile(book, progress_text(record, library.progress(book.key)),
                                 cover_w=72, cover_h=100, parent=self.container)
                tile.clicked.connect(self.book_opened.emit)
                self.recent_host.insertWidget(self.recent_host.count() - 1, tile)
                self._recent_tiles.append(tile)
                if book.cover_url and self._cover_loader:
                    self._cover_loader(book.cover_url, tile.set_cover, tile.cover)
            self.recent_title.show()
        else:
            self.recent_title.hide()

        # ---- 我的书架网格
        self.shelf_title.setText(f"我的书架（{len(records)}）")
        for record in records:
            book = Book.from_record(record)
            tile = CoverTile(book, progress_text(record, library.progress(book.key)),
                             parent=self.container)
            tile.clicked.connect(self.book_opened.emit)
            self._tiles.append(tile)
            if book.cover_url and self._cover_loader:
                self._cover_loader(book.cover_url, tile.set_cover, tile.cover)
        self._columns = 0
        self._relayout_grid()

    def _clear(self) -> None:
        for tile in self._tiles + self._recent_tiles:
            tile.setParent(None)
            tile.deleteLater()
        self._tiles.clear()
        self._recent_tiles.clear()
        if self.continue_card is not None:
            self.continue_card.setParent(None)
            self.continue_card.deleteLater()
            self.continue_card = None
        while self.continue_host.count():
            item = self.continue_host.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        while self.grid.count():
            item = self.grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)

    # ------------------------------------------------------------------ 布局
    def _relayout_grid(self) -> None:
        """按可用宽度决定列数，窗口变宽时自动多排一列。"""
        width = max(320, self.scroll.viewport().width() - 64)
        columns = max(2, min(10, width // 176))
        if columns == self._columns:
            return
        self._columns = columns
        while self.grid.count():
            self.grid.takeAt(0)
        for i, tile in enumerate(self._tiles):
            self.grid.addWidget(tile, i // columns, i % columns,
                                1, 1, Qt.AlignTop | Qt.AlignLeft)
        for column in range(columns):
            self.grid.setColumnStretch(column, 0)
        self.grid.setColumnStretch(columns, 1)   # 右侧留白

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._relayout_grid()

    # ------------------------------------------------------------------ 测试
    def tiles(self) -> List[CoverTile]:
        """书架网格里的格子（自检用）。"""
        return list(self._tiles)

    def recent(self) -> List[CoverTile]:
        return list(self._recent_tiles)

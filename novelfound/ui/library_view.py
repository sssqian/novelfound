# -*- coding: utf-8 -*-
"""书架首页（启动首屏）。

只有一屏**书架封面网格**：每格 = 封面 + 书名 + 进度文字。
按用户反馈，首页不再放「继续阅读」「最近阅读」两个栏目——
想接着上次读，点封面进详情页，主按钮本身就是「继续阅读」。

* 网格列数按可用宽度自动算（宽屏多排一列），重排时只挪动已有格子、不重建控件；
* 未加入书架的书不会出现在这里（阅读历史仍由 `library.py` 记录，只是不上首页）；
* 书架为空时显示引导语 + 「搜索小说」按钮（唤起搜索浮层）；
* 封面沿用主窗口的节流队列（只下可见项、并发上限），通过 ``cover_loader`` 回调注入。
"""
from __future__ import annotations

from typing import Callable, List, Optional

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QPushButton,
                             QScrollArea, QVBoxLayout, QWidget)

from ..models import Book
from .widgets import CoverTile, EmptyState, SectionTitle


def progress_text(record: dict, progress: dict) -> str:
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


class LibraryView(QWidget):
    """书架首页：一屏书架网格。"""

    book_opened = pyqtSignal(object)          # Book：打开详情
    search_requested = pyqtSignal()           # 空状态里的「搜索小说」
    import_requested = pyqtSignal()           # 「导入 TXT / EPUB」按钮

    def __init__(self, config, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.config = config
        self.setObjectName("libraryView")
        self._cover_loader: Optional[Callable] = None
        self._tiles: List[CoverTile] = []
        self._columns = 0

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 空状态（书架为空时显示）
        self.empty = EmptyState(
            "书架还是空的",
            "搜索到喜欢的小说后，在详情页点「☆ 加入书架」；\n"
            "也可以把本地的 TXT / EPUB 文件拖进窗口直接导入。")
        self.empty_button = self.empty.add_action("搜索小说")
        self.empty_button.clicked.connect(self.search_requested.emit)
        self.empty_import_button = self.empty.add_secondary_action("导入本地书籍")
        self.empty_import_button.clicked.connect(self.import_requested.emit)
        root.addWidget(self.empty, 1)

        # 书架网格
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

        # 书架标题行：标题 + 「导入本地书籍」按钮（也支持把文件拖到窗口里）
        head = QHBoxLayout()
        head.setSpacing(10)
        self.shelf_title = SectionTitle("我的书架", self.container)
        head.addWidget(self.shelf_title)
        head.addStretch(1)
        self.import_button = QPushButton("＋ 导入 TXT / EPUB", self.container)
        self.import_button.setToolTip("把本地的 TXT / EPUB 电子书导入书架（也可以直接拖文件到窗口）")
        self.import_button.clicked.connect(self.import_requested.emit)
        head.addWidget(self.import_button)
        self.body.addLayout(head)

        self.grid_host = QWidget(self.container)
        self.grid = QGridLayout(self.grid_host)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(12)
        self.grid.setVerticalSpacing(16)
        self.body.addWidget(self.grid_host)
        self.body.addStretch(1)

    # ------------------------------------------------------------------ 数据
    def refresh(self, library, cover_loader: Optional[Callable] = None) -> None:
        """按当前书架重建首页（只展示已加入书架的书）。"""
        self._cover_loader = cover_loader or self._cover_loader
        self._clear()
        records = library.books()

        if not records:
            self.empty.show()
            self.scroll.hide()
            return
        self.empty.hide()
        self.scroll.show()

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
        while self.grid.count():
            self.grid.takeAt(0)
        for tile in self._tiles:
            tile.setParent(None)
            tile.deleteLater()
        self._tiles.clear()

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

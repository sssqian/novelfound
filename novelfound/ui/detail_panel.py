# -*- coding: utf-8 -*-
"""书籍详情面板：封面、简介、完整目录。"""
from __future__ import annotations

from typing import List, Optional

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (QHBoxLayout, QLabel, QLineEdit, QListWidget,
                             QListWidgetItem, QPushButton, QSplitter,
                             QVBoxLayout, QWidget)

from ..models import Book, BookDetail, Chapter
from .widgets import CoverLabel, SectionTitle


class DetailPanel(QWidget):
    """展示一本书的详情与目录，并触发阅读。"""

    read_requested = pyqtSignal(object, int)     # (BookDetail, chapter_index)
    shelf_toggled = pyqtSignal(object)           # (Book)
    refresh_requested = pyqtSignal(object)       # (Book)
    back_requested = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.detail: Optional[BookDetail] = None
        self._in_shelf = False
        # 上次读到的章节序号（-1 = 没有进度）；「继续阅读」在用户没点目录时用它
        self._progress_index = -1

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(10)

        # ---------------------------------------------------------- 顶部信息区
        header = QHBoxLayout()
        header.setSpacing(16)

        self.cover = CoverLabel(120, 160, self)
        header.addWidget(self.cover, 0, Qt.AlignTop)

        info = QVBoxLayout()
        info.setSpacing(6)
        self.title_label = QLabel("", self)
        self.title_label.setObjectName("sectionTitle")
        self.title_label.setWordWrap(True)
        info.addWidget(self.title_label)

        self.meta_label = QLabel("", self)
        self.meta_label.setObjectName("bookMeta")
        self.meta_label.setWordWrap(True)
        info.addWidget(self.meta_label)

        self.progress_label = QLabel("", self)
        self.progress_label.setObjectName("bookMeta")
        info.addWidget(self.progress_label)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.read_button = QPushButton("开始阅读", self)
        self.read_button.setObjectName("primary")
        self.read_button.clicked.connect(self._on_read_clicked)
        buttons.addWidget(self.read_button)

        self.shelf_button = QPushButton("加入书架", self)
        self.shelf_button.clicked.connect(self._on_shelf_clicked)
        buttons.addWidget(self.shelf_button)

        self.refresh_button = QPushButton("刷新目录", self)
        self.refresh_button.clicked.connect(
            lambda: self.detail and self.refresh_requested.emit(self.detail.book))
        buttons.addWidget(self.refresh_button)

        self.back_button = QPushButton("返回搜索结果", self)
        self.back_button.clicked.connect(self.back_requested.emit)
        buttons.addWidget(self.back_button)
        buttons.addStretch(1)
        info.addLayout(buttons)
        info.addStretch(1)
        header.addLayout(info, 1)
        root.addLayout(header)

        # ------------------------------------------------------------ 简介区
        self.intro_title = SectionTitle("简介", self)
        root.addWidget(self.intro_title)
        self.intro_label = QLabel("", self)
        self.intro_label.setObjectName("introText")
        self.intro_label.setWordWrap(True)
        self.intro_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        root.addWidget(self.intro_label)

        # ------------------------------------------------------------ 目录区
        catalog_header = QHBoxLayout()
        self.catalog_title = SectionTitle("目录", self)
        catalog_header.addWidget(self.catalog_title)
        catalog_header.addStretch(1)
        self.filter_box = QLineEdit(self)
        self.filter_box.setObjectName("plain")
        self.filter_box.setPlaceholderText("筛选章节（输入关键词或章节号）")
        self.filter_box.setFixedWidth(260)
        self.filter_box.textChanged.connect(self._apply_filter)
        catalog_header.addWidget(self.filter_box)
        root.addLayout(catalog_header)

        self.catalog = QListWidget(self)
        self.catalog.setUniformItemSizes(True)
        self.catalog.setAlternatingRowColors(False)
        self.catalog.itemActivated.connect(self._on_item_activated)
        self.catalog.itemClicked.connect(self._on_item_activated)
        root.addWidget(self.catalog, 1)

    # ------------------------------------------------------------------ 数据
    def set_detail(self, detail: BookDetail, in_shelf: bool = False,
                   cached_count: int = 0, progress: Optional[dict] = None) -> None:
        self.detail = detail
        book = detail.book
        self._in_shelf = in_shelf

        self.cover.clear_image()
        self.cover.set_title(book.title)
        self.title_label.setText(book.title or "未知书名")

        parts = []
        if book.author:
            parts.append(f"作者：{book.author}")
        if book.status:
            parts.append(book.status)
        if book.category:
            parts.append(book.category)
        parts.append(f"{len(detail.chapters)} 章")
        if cached_count:
            parts.append(f"已缓存 {cached_count} 章")
        if book.source_name:
            parts.append(f"来源：{book.source_name}")
        self.meta_label.setText("　·　".join(parts))

        progress = progress or {}
        self._progress_index = int(progress.get("index", -1))
        if progress.get("chapter_title"):
            self.progress_label.setText(
                f"上次读到：第 {progress.get('index', 0) + 1} 章 · "
                f"{progress.get('chapter_title')}")
            self.read_button.setText("继续阅读")
        else:
            self.progress_label.setText("")
            self.read_button.setText("开始阅读")

        self.shelf_button.setText("移出书架" if in_shelf else "加入书架")
        self.intro_label.setText(book.intro or "（该书源未提供简介）")

        self._fill_catalog(detail.chapters, progress.get("index", -1))
        self.catalog_title.setText(f"目录（{len(detail.chapters)} 章）")

    def _fill_catalog(self, chapters: List[Chapter], current_index: int) -> None:
        self.catalog.clear()
        progress_item = None
        for chapter in chapters:
            item = QListWidgetItem(chapter.display_title)
            item.setData(Qt.UserRole, chapter.index)
            item.setToolTip(chapter.title)
            if chapter.index == current_index:
                item.setText(f"▶ {chapter.display_title}")
                progress_item = item
            self.catalog.addItem(item)
        # 关键：把"上次读到的那一章"设为当前行。
        # 否则 currentRow() 是 -1，点「继续阅读」会回落到第 1 章。
        if progress_item is not None:
            self.catalog.setCurrentItem(progress_item)
            self.catalog.scrollToItem(progress_item, QListWidget.PositionAtCenter)

    def set_cover(self, data: bytes) -> None:
        self.cover.set_image(data)

    def set_reading_progress(self, index: int) -> None:
        """高亮当前阅读章节。"""
        if self.detail is None:
            return
        for i in range(self.catalog.count()):
            item = self.catalog.item(i)
            chapter = self.detail.chapters[item.data(Qt.UserRole)]
            item.setText(chapter.display_title if chapter.index != index
                         else f"▶ {chapter.display_title}")
            if chapter.index == index:
                self.catalog.setCurrentItem(item)

    # ------------------------------------------------------------------ 交互
    def _apply_filter(self, text: str) -> None:
        if self.detail is None:
            return
        text = text.strip()
        if not text:
            for i in range(self.catalog.count()):
                self.catalog.item(i).setHidden(False)
            return
        lowered = text.lower()
        for i in range(self.catalog.count()):
            item = self.catalog.item(i)
            item.setHidden(lowered not in item.text().lower())

    def _on_item_activated(self, item: QListWidgetItem) -> None:
        if self.detail is None:
            return
        self.read_requested.emit(self.detail, int(item.data(Qt.UserRole)))

    def _on_read_clicked(self) -> None:
        if self.detail is None or not self.detail.chapters:
            return
        current = self.catalog.currentRow()
        if current >= 0:
            index = current
        elif self._progress_index >= 0:
            # 目录没有被选中时，用"上次读到的章节"，而不是硬编码第 1 章
            index = self._progress_index
        else:
            index = 0
        self.read_requested.emit(self.detail, index)

    def _on_shelf_clicked(self) -> None:
        if self.detail is not None:
            self.shelf_toggled.emit(self.detail.book)

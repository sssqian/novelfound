# -*- coding: utf-8 -*-
"""搜索结果卡片。"""
from __future__ import annotations

from typing import Optional

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout,
                             QWidget)

from ..models import Book
from .widgets import CoverLabel


class BookCard(QFrame):
    """单本小说卡片：封面 + 书名 + 作者 + 来源 + 最新章节。"""

    clicked = pyqtSignal(object)   # Book

    def __init__(self, book: Book, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.book = book
        self.setObjectName("card")
        self.setProperty("selected", "false")
        self.setCursor(Qt.PointingHandCursor)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 10, 12, 10)
        layout.setSpacing(12)

        self.cover = CoverLabel(76, 100, self)
        self.cover.set_title(book.title)
        layout.addWidget(self.cover, 0, Qt.AlignTop)

        right = QVBoxLayout()
        right.setSpacing(4)

        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        self.title_label = QLabel(book.title or "未知书名", self)
        self.title_label.setObjectName("bookTitle")
        self.title_label.setWordWrap(True)
        title_row.addWidget(self.title_label, 1)
        if book.source_name:
            tag = QLabel(book.source_name, self)
            tag.setObjectName("sourceTag")
            title_row.addWidget(tag, 0, Qt.AlignTop)
        right.addLayout(title_row)

        meta_parts = []
        if book.author:
            meta_parts.append(f"作者：{book.author}")
        if book.status:
            meta_parts.append(book.status)
        if book.category:
            meta_parts.append(book.category)
        self.meta_label = QLabel("　·　".join(meta_parts) or "作者：未知", self)
        self.meta_label.setObjectName("bookMeta")
        right.addWidget(self.meta_label)

        if book.latest_chapter:
            latest = QLabel(f"最新：{book.latest_chapter}", self)
            latest.setObjectName("bookMeta")
            latest.setWordWrap(True)
            right.addWidget(latest)

        if book.intro:
            intro = QLabel(book.intro[:110] + ("…" if len(book.intro) > 110 else ""), self)
            intro.setObjectName("bookMeta")
            intro.setWordWrap(True)
            right.addWidget(intro)

        right.addStretch(1)
        layout.addLayout(right, 1)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    # ------------------------------------------------------------------ 交互
    def set_selected(self, selected: bool) -> None:
        self.setProperty("selected", "true" if selected else "false")
        self.style().unpolish(self)
        self.style().polish(self)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.book)
        super().mouseReleaseEvent(event)

# -*- coding: utf-8 -*-
"""书籍详情页（由旧的 ``detail_panel.py`` 演化而来）。

按方案 ③ 精简：

* 顶部只有一行「← 返回」；
* 左边大封面（160×220），右边书名 / 元信息 / 进度；
* 按钮**只保留一个主按钮**（开始阅读 / 继续阅读），
  「加入书架」「目录」「刷新目录」都是次级按钮；
* 简介默认只显示 3 行，点「展开」看全文；
* 目录不再是常驻列表，改由主窗口的目录抽屉承载（点「目录」按钮）。

**保留**原来的「继续阅读索引」逻辑：主按钮优先用上次读到的章节序号，
不会因为目录没被选中就跳回第 1 章。
"""
from __future__ import annotations

from typing import Optional

from PyQt5.QtCore import QRect, Qt, pyqtSignal
from PyQt5.QtWidgets import (QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget)

from ..models import BookDetail
from .widgets import CoverLabel, SectionTitle

INTRO_LINES = 3


class BookView(QWidget):
    """一本书的详情页。"""

    read_requested = pyqtSignal(object, int)     # (BookDetail, chapter_index)
    shelf_toggled = pyqtSignal(object)           # (Book)
    refresh_requested = pyqtSignal(object)       # (Book)
    back_requested = pyqtSignal()
    catalog_requested = pyqtSignal()             # 打开目录抽屉
    local_delete_requested = pyqtSignal(object)  # (Book) 删除本地书

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("bookView")
        self.detail: Optional[BookDetail] = None
        self._in_shelf = False
        self._progress_index = -1
        self._intro_expanded = False

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 16, 28, 20)
        root.setSpacing(12)

        # ------------------------------------------------------------ 返回行
        top_row = QHBoxLayout()
        self.back_button = QPushButton("← 返回", self)
        self.back_button.setObjectName("link")
        self.back_button.setCursor(Qt.PointingHandCursor)
        self.back_button.clicked.connect(self.back_requested.emit)
        top_row.addWidget(self.back_button, 0, Qt.AlignLeft)
        top_row.addStretch(1)
        self.source_label = QLabel("", self)
        self.source_label.setObjectName("muted")
        top_row.addWidget(self.source_label, 0, Qt.AlignRight)
        root.addLayout(top_row)

        # ------------------------------------------------------------ 头部信息
        header = QHBoxLayout()
        header.setSpacing(22)

        self.cover = CoverLabel(160, 220, self)
        header.addWidget(self.cover, 0, Qt.AlignTop)

        info = QVBoxLayout()
        info.setSpacing(8)
        self.title_label = QLabel("", self)
        self.title_label.setObjectName("pageTitle")
        self.title_label.setWordWrap(True)
        info.addWidget(self.title_label)

        self.meta_label = QLabel("", self)
        self.meta_label.setObjectName("bookMeta")
        self.meta_label.setWordWrap(True)
        info.addWidget(self.meta_label)

        self.progress_label = QLabel("", self)
        self.progress_label.setObjectName("bookMeta")
        self.progress_label.setWordWrap(True)
        info.addWidget(self.progress_label)

        info.addSpacing(6)
        buttons = QHBoxLayout()
        buttons.setSpacing(10)
        self.read_button = QPushButton("开始阅读", self)
        self.read_button.setObjectName("primary")
        self.read_button.setMinimumWidth(150)
        self.read_button.clicked.connect(self._on_read_clicked)
        buttons.addWidget(self.read_button)

        self.catalog_button = QPushButton("目录", self)
        self.catalog_button.clicked.connect(self.catalog_requested.emit)
        buttons.addWidget(self.catalog_button)

        self.shelf_button = QPushButton("☆ 加入书架", self)
        self.shelf_button.clicked.connect(self._on_shelf_clicked)
        buttons.addWidget(self.shelf_button)

        self.refresh_button = QPushButton("刷新目录", self)
        self.refresh_button.setObjectName("ghost")
        self.refresh_button.clicked.connect(
            lambda: self.detail and self.refresh_requested.emit(self.detail.book))
        buttons.addWidget(self.refresh_button)

        # 本地导入的书才有「删除本地书」（连文件、缓存、历史一起清）
        self.delete_button = QPushButton("🗑 删除本地书…", self)
        self.delete_button.setObjectName("ghost")
        self.delete_button.setToolTip("删除导入的文件、缓存、浏览历史与阅读进度（不可恢复）")
        self.delete_button.clicked.connect(self._on_delete_clicked)
        self.delete_button.setVisible(False)
        buttons.addWidget(self.delete_button)
        buttons.addStretch(1)
        info.addLayout(buttons)
        info.addStretch(1)
        header.addLayout(info, 1)
        root.addLayout(header)

        # -------------------------------------------------------------- 简介
        intro_row = QHBoxLayout()
        intro_row.setSpacing(8)
        self.intro_title = SectionTitle("简介", self)
        intro_row.addWidget(self.intro_title)
        intro_row.addStretch(1)
        self.intro_toggle = QPushButton("展开", self)
        self.intro_toggle.setObjectName("link")
        self.intro_toggle.setCursor(Qt.PointingHandCursor)
        self.intro_toggle.setVisible(False)
        self.intro_toggle.clicked.connect(self._toggle_intro)
        intro_row.addWidget(self.intro_toggle)
        root.addLayout(intro_row)

        self.intro_label = QLabel("", self)
        self.intro_label.setObjectName("introText")
        self.intro_label.setWordWrap(True)
        self.intro_label.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.intro_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        root.addWidget(self.intro_label)
        root.addStretch(1)

    # ------------------------------------------------------------------ 数据
    def set_local(self, is_local: bool) -> None:
        """是不是本地导入的书（决定「删除本地书」按钮是否出现）。"""
        self.delete_button.setVisible(bool(is_local))

    def _on_delete_clicked(self) -> None:
        if self.detail is not None:
            self.local_delete_requested.emit(self.detail.book)

    def set_shelf_state(self, in_shelf: bool) -> None:
        self._in_shelf = in_shelf
        self.shelf_button.setText("★ 已在书架" if in_shelf else "☆ 加入书架")

    def set_detail(self, detail: BookDetail, in_shelf: bool = False,
                   cached_count: int = 0, progress: Optional[dict] = None) -> None:
        self.detail = detail
        book = detail.book
        self._in_shelf = in_shelf
        self._intro_expanded = False
        # 切换书籍时先按"是否本地书"决定删除按钮，避免沿用上一本的可见状态
        self.delete_button.setVisible(False)

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
        self.meta_label.setText("　·　".join(parts))
        self.source_label.setText(
            f"来源：{book.source_name}" if book.source_name else "")

        progress = progress or {}
        self._progress_index = int(progress.get("index", -1))
        if progress.get("chapter_title"):
            self.progress_label.setText(
                f"上次读到：第 {progress.get('index', 0) + 1} 章 · "
                f"{progress.get('chapter_title')}")
            self.read_button.setText("继续阅读")
        else:
            self.progress_label.setText("还没有开始阅读")
            self.read_button.setText("开始阅读")

        self.shelf_button.setText("★ 已在书架" if in_shelf else "☆ 加入书架")
        self.catalog_button.setText(f"目录（{len(detail.chapters)} 章）")
        self.intro_label.setText(book.intro or "（该书源未提供简介）")
        self._apply_intro_limit()

    def set_cover(self, data: bytes) -> None:
        self.cover.set_image(data)

    def set_reading_progress(self, index: int) -> None:
        """从阅读器返回时更新"上次读到"与主按钮文案。"""
        if self.detail is None or not 0 <= index < len(self.detail.chapters):
            return
        self._progress_index = index
        self.progress_label.setText(
            f"上次读到：第 {index + 1} 章 · {self.detail.chapters[index].display_title}")
        self.read_button.setText("继续阅读")

    def reading_index(self) -> int:
        """主按钮要跳到哪一章：上次读到的章节，没有则第 1 章。"""
        if self.detail is None or not self.detail.chapters:
            return 0
        if 0 <= self._progress_index < len(self.detail.chapters):
            return self._progress_index
        return 0

    # ------------------------------------------------------------------ 简介
    def _apply_intro_limit(self) -> None:
        """默认只显示 3 行，超出时才显示「展开」。

        用 ``QFontMetrics.boundingRect`` 按当前宽度量一次全文高度，
        比 ``sizeHint()`` 可靠（后者受最大高度限制影响）。
        """
        text = self.intro_label.text()
        width = self.intro_label.width() or self.width() or 600
        metrics = self.intro_label.fontMetrics()
        full_height = metrics.boundingRect(
            QRect(0, 0, max(120, width), 10000),
            Qt.TextWordWrap | Qt.AlignTop, text).height()
        limit = metrics.lineSpacing() * INTRO_LINES + 4
        too_long = full_height > limit
        self.intro_toggle.setVisible(too_long or self._intro_expanded)
        if self._intro_expanded or not too_long:
            self.intro_label.setMaximumHeight(16777215)
        else:
            self.intro_label.setMaximumHeight(limit)

    def _toggle_intro(self) -> None:
        self._intro_expanded = not self._intro_expanded
        self.intro_toggle.setText("收起" if self._intro_expanded else "展开")
        self._apply_intro_limit()

    # ------------------------------------------------------------------ 交互
    def _on_read_clicked(self) -> None:
        if self.detail is None or not self.detail.chapters:
            return
        self.read_requested.emit(self.detail, self.reading_index())

    def _on_shelf_clicked(self) -> None:
        if self.detail is not None:
            self.shelf_toggled.emit(self.detail.book)

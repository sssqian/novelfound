# -*- coding: utf-8 -*-
"""目录抽屉：从左侧滑出的章节列表。

按方案：目录不再常驻在详情页/阅读器里，而是点「目录」按钮（或 ``Ctrl+B``）
从左侧滑出，带章节筛选框，当前章高亮，点击即跳转。
遮罩点击、``Esc``、右上角 ✕ 都能关闭。

抽屉是**中央控件的子浮层**（用 ``setGeometry`` 定位，不参与布局），
这样动画和定位都不受布局系统影响。
"""
from __future__ import annotations

from typing import List, Optional

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                             QListWidgetItem, QPushButton, QVBoxLayout, QWidget)

from ..models import BookDetail, Chapter
from .widgets import DRAWER_MS, slide_in, slide_out


class CatalogDrawer(QFrame):
    """目录抽屉（250ms 从左侧滑出）。"""

    chapter_activated = pyqtSignal(int)      # 章节序号
    closed = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("catalogDrawer")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.detail: Optional[BookDetail] = None
        self._current = -1
        self._shown_state = False          # 真实开合状态（动画只负责视觉）

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ------------------------------------------------------------ 标题行
        header = QFrame(self)
        header.setObjectName("drawerHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(16, 12, 10, 10)
        header_layout.setSpacing(8)
        self.title_label = QLabel("目录", header)
        self.title_label.setObjectName("drawerTitle")
        header_layout.addWidget(self.title_label, 1)
        self.close_button = QPushButton("✕", header)
        self.close_button.setObjectName("ghost")
        self.close_button.setFixedWidth(28)
        self.close_button.setToolTip("关闭目录（Esc）")
        self.close_button.clicked.connect(self.close_drawer)
        header_layout.addWidget(self.close_button)
        layout.addWidget(header)

        # ------------------------------------------------------------ 筛选框
        self.filter_box = QLineEdit(self)
        self.filter_box.setObjectName("plain")
        self.filter_box.setPlaceholderText("筛选章节（关键词或章节号）")
        self.filter_box.setClearButtonEnabled(True)
        self.filter_box.textChanged.connect(self._apply_filter)
        filter_wrap = QHBoxLayout()
        filter_wrap.setContentsMargins(14, 4, 14, 10)
        filter_wrap.addWidget(self.filter_box)
        layout.addLayout(filter_wrap)

        # ------------------------------------------------------------ 章节列表
        self.catalog = QListWidget(self)
        self.catalog.setObjectName("drawerList")
        self.catalog.setFrameShape(QFrame.NoFrame)
        self.catalog.setUniformItemSizes(True)
        self.catalog.itemActivated.connect(self._on_item_activated)
        self.catalog.itemClicked.connect(self._on_item_activated)
        layout.addWidget(self.catalog, 1)

        self.hide()

    # ------------------------------------------------------------------ 开关
    def open_drawer(self, detail: BookDetail, current_index: int = -1) -> None:
        """填充并从左侧滑出（当前章高亮）。"""
        self.detail = detail
        self.title_label.setText(f"目录（{len(detail.chapters)} 章）")
        self.filter_box.clear()
        self._fill(detail.chapters, current_index)
        self._shown_state = True
        self.show()
        self.raise_()
        # 从"屏幕左外侧"滑到最终位置（最终 x 由主窗口 setGeometry 决定，固定为 0）
        end_x = self.x()
        slide_in(self, end_x - max(80, self.width()), end_x, DRAWER_MS)
        self.filter_box.setFocus()

    def close_drawer(self) -> None:
        """滑出后隐藏；状态立即置为关闭。"""
        was_open = self._shown_state
        self._shown_state = False
        if self.isVisible():
            slide_out(self, self.x() - max(80, self.width()), DRAWER_MS)
        if was_open:
            self.closed.emit()

    def is_open(self) -> bool:
        return self._shown_state

    def set_current(self, index: int) -> None:
        """高亮当前阅读章节（切章时调用）。"""
        self._current = index
        if self.detail is None:
            return
        for row in range(self.catalog.count()):
            item = self.catalog.item(row)
            chapter_index = int(item.data(Qt.UserRole))
            chapter = self.detail.chapters[chapter_index]
            item.setText(chapter.display_title if chapter.index != index
                         else f"▶ {chapter.display_title}")
            if chapter.index == index:
                self.catalog.setCurrentItem(item)

    # ------------------------------------------------------------------ 内部
    def _fill(self, chapters: List[Chapter], current_index: int) -> None:
        self.catalog.clear()
        current_item = None
        for chapter in chapters:
            item = QListWidgetItem(chapter.display_title)
            item.setData(Qt.UserRole, chapter.index)
            item.setToolTip(chapter.title)
            if chapter.index == current_index:
                item.setText(f"▶ {chapter.display_title}")
                current_item = item
            self.catalog.addItem(item)
        self._current = current_index
        if current_item is not None:
            # 关键：把"上次读到的那一章"设为当前行，避免主按钮回落第 1 章
            self.catalog.setCurrentItem(current_item)
            self.catalog.scrollToItem(current_item, QListWidget.PositionAtCenter)

    def _apply_filter(self, text: str) -> None:
        text = text.strip().lower()
        for row in range(self.catalog.count()):
            item = self.catalog.item(row)
            item.setHidden(bool(text) and text not in item.text().lower())

    def _on_item_activated(self, item: QListWidgetItem) -> None:
        if self.detail is None:
            return
        self.chapter_activated.emit(int(item.data(Qt.UserRole)))

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key_Escape:
            self.close_drawer()
            return
        super().keyPressEvent(event)

# -*- coding: utf-8 -*-
"""目录抽屉：从左侧滑出的章节树。

按方案：目录不再常驻在详情页/阅读器里，而是点「目录」按钮（或 ``Ctrl+B``）
从左侧滑出，带章节筛选框，当前章高亮，点击即跳转。

**分组（部/卷）**：本地书从 TXT 的"第X部/卷"行或 EPUB 的 NCX 目录拿到层级，
折叠成可展开的组节点（默认收起；**当前章所在的组自动展开**，免得找不到自己）。
章节索引不变，所以阅读进度、"继续阅读"、位置记忆都不受影响。

遮罩点击、``Esc``、右上角 ✕ 都能关闭；抽屉是**中央控件的子浮层**
（用 ``setGeometry`` 定位，不参与布局），动画与定位都不受布局系统影响。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton,
                             QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from ..models import BookDetail, Chapter
from .widgets import DRAWER_MS, slide_in, slide_out

GROUP_ROLE = Qt.UserRole + 1        # 标记"这是分组节点"
CURRENT_MARK = "▶ "


class CatalogDrawer(QFrame):
    """目录抽屉（250ms 从左侧滑出，支持"部/卷"折叠）。"""

    chapter_activated = pyqtSignal(int)      # 章节序号
    images_requested = pyqtSignal()          # 点「本书插图」
    closed = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("catalogDrawer")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.detail: Optional[BookDetail] = None
        self._current = -1
        self._shown_state = False          # 真实开合状态（动画只负责视觉）
        self._group_items: Dict[str, QTreeWidgetItem] = {}
        self._chapter_items: Dict[int, QTreeWidgetItem] = {}
        self._all_expanded = False

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

        # ------------------------------------------------------------ 工具行
        tools = QHBoxLayout()
        tools.setContentsMargins(14, 0, 14, 8)
        tools.setSpacing(8)
        self.images_button = QPushButton("本书插图", self)
        self.images_button.setObjectName("link")
        self.images_button.setToolTip("查看这本书里的所有插图（含正文里没有引用的）")
        self.images_button.clicked.connect(self.images_requested.emit)
        self.images_button.setVisible(False)
        tools.addWidget(self.images_button)
        tools.addStretch(1)
        self.expand_button = QPushButton("全部展开", self)
        self.expand_button.setObjectName("link")
        self.expand_button.setToolTip("展开 / 收起所有分卷")
        self.expand_button.clicked.connect(self.toggle_all_groups)
        self.expand_button.setVisible(False)
        tools.addWidget(self.expand_button)
        layout.addLayout(tools)

        # ------------------------------------------------------------ 筛选框
        self.filter_box = QLineEdit(self)
        self.filter_box.setObjectName("plain")
        self.filter_box.setPlaceholderText("筛选章节（关键词或章节号）")
        self.filter_box.setClearButtonEnabled(True)
        self.filter_box.textChanged.connect(self._apply_filter)
        filter_wrap = QHBoxLayout()
        filter_wrap.setContentsMargins(14, 0, 14, 10)
        filter_wrap.addWidget(self.filter_box)
        layout.addLayout(filter_wrap)

        # ------------------------------------------------------------ 章节树
        self.catalog = QTreeWidget(self)
        self.catalog.setObjectName("drawerList")
        self.catalog.setHeaderHidden(True)
        self.catalog.setFrameShape(QFrame.NoFrame)
        self.catalog.setUniformRowHeights(True)
        self.catalog.setIndentation(14)
        self.catalog.itemActivated.connect(self._on_item_activated)
        self.catalog.itemClicked.connect(self._on_item_activated)
        layout.addWidget(self.catalog, 1)

        self.hide()

    # ------------------------------------------------------------------ 开关
    def open_drawer(self, detail: BookDetail, current_index: int = -1,
                    images: int = 0) -> None:
        """填充并从左侧滑出（当前章高亮，所在分组自动展开）。"""
        self.detail = detail
        self.title_label.setText(f"目录（{len(detail.chapters)} 章）")
        self.filter_box.clear()
        self.images_button.setText(f"本书插图（{images}）")
        self.images_button.setVisible(images > 0)
        self._fill(detail.chapters, current_index)
        self._shown_state = True
        self.show()
        self.raise_()
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
        """高亮当前阅读章节（切章时调用），并保证它的分组是展开的。"""
        self._current = index
        if self.detail is None:
            return
        self._mark_current(index, scroll=True)

    # ------------------------------------------------------------------ 构建
    def _fill(self, chapters: List[Chapter], current_index: int) -> None:
        self.catalog.clear()
        self._group_items.clear()
        self._chapter_items.clear()
        self._all_expanded = False
        self._current = current_index

        last_group = None
        parent: Optional[QTreeWidgetItem] = None
        for chapter in chapters:
            group = (getattr(chapter, "group", "") or "").strip()
            if group != last_group:
                parent = None
                if group:
                    parent = QTreeWidgetItem(self.catalog)
                    parent.setText(0, group)
                    parent.setData(0, GROUP_ROLE, True)
                    parent.setExpanded(False)          # 默认收起
                    parent.setToolTip(0, f"{group}（点击展开/收起）")
                    self._group_items[group] = parent
                last_group = group
            item = QTreeWidgetItem(parent or self.catalog)
            item.setText(0, chapter.display_title)
            item.setToolTip(0, chapter.title)
            item.setData(0, Qt.UserRole, chapter.index)
            self._chapter_items[chapter.index] = item

        self.expand_button.setVisible(bool(self._group_items))
        self.expand_button.setText("全部展开")
        self._mark_current(current_index, scroll=True)

    def _mark_current(self, index: int, scroll: bool = False) -> None:
        """打 ▶ 标记 + 选中 + 展开所在分组。"""
        for chapter_index, item in self._chapter_items.items():
            text = item.text(0)
            bare = text[len(CURRENT_MARK):] if text.startswith(CURRENT_MARK) else text
            item.setText(0, (CURRENT_MARK + bare) if chapter_index == index else bare)
        item = self._chapter_items.get(index)
        if item is None:
            return
        parent = item.parent()
        if parent is not None:
            parent.setExpanded(True)               # 当前章所在组自动展开
        self.catalog.setCurrentItem(item)
        if scroll:
            self.catalog.scrollToItem(item, QTreeWidget.PositionAtCenter)

    # ------------------------------------------------------------------ 分组
    def group_titles(self) -> List[str]:
        """所有分组名（自检用）。"""
        return list(self._group_items.keys())

    def group_item(self, title: str) -> Optional[QTreeWidgetItem]:
        return self._group_items.get(title)

    def is_group_expanded(self, title: str) -> bool:
        item = self._group_items.get(title)
        return bool(item is not None and item.isExpanded())

    def chapter_count(self) -> int:
        return len(self._chapter_items)

    def chapter_item(self, index: int) -> Optional[QTreeWidgetItem]:
        return self._chapter_items.get(index)

    def visible_chapter_count(self) -> int:
        """当前没被筛掉的章节数（自检用）。"""
        return sum(1 for item in self._chapter_items.values() if not item.isHidden())

    def current_chapter_index(self) -> int:
        item = self.catalog.currentItem()
        if item is None or item.data(0, GROUP_ROLE):
            return -1
        return int(item.data(0, Qt.UserRole))

    def toggle_all_groups(self) -> None:
        """一键展开 / 收起所有分组。"""
        self._all_expanded = not self._all_expanded
        for item in self._group_items.values():
            item.setExpanded(self._all_expanded)
        self.expand_button.setText("全部收起" if self._all_expanded else "全部展开")

    # ------------------------------------------------------------------ 筛选
    def _apply_filter(self, text: str) -> None:
        text = text.strip().lower()
        for item in self._chapter_items.values():
            item.setHidden(bool(text) and text not in item.text(0).lower())
        if not text:
            # 清空筛选 → 回到"默认收起 + 当前章所在组展开"
            for item in self._group_items.values():
                item.setExpanded(False)
            self._all_expanded = False
            self.expand_button.setText("全部展开")
            if self._current >= 0:
                self._mark_current(self._current)
            for item in self._group_items.values():
                item.setHidden(False)
            return
        for item in self._group_items.values():
            children = [item.child(i) for i in range(item.childCount())]
            visible = any(not c.isHidden() for c in children)
            item.setHidden(not visible)
            if visible:
                item.setExpanded(True)             # 筛选时自动展开命中的组
        # 根层的章节（不属于任何组）本来就按 hidden 处理，无需额外操作

    # ------------------------------------------------------------------ 交互
    def _on_item_activated(self, item: QTreeWidgetItem, _column: int = 0) -> None:
        if item.data(0, GROUP_ROLE):
            item.setExpanded(not item.isExpanded())    # 点组标题 = 展开/收起
            return
        if self.detail is None:
            return
        self.chapter_activated.emit(int(item.data(0, Qt.UserRole)))

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key_Escape:
            self.close_drawer()
            return
        super().keyPressEvent(event)

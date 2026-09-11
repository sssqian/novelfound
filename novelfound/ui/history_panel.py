# -*- coding: utf-8 -*-
"""浏览历史浮层（右上角 🕘 按钮 / 与搜索面板同样的居中卡片）。

按时间倒序展示每一次浏览动作：

* 「浏览」= 打开过这本书的详情页；
* 「阅读」= 读过这一章（带章节名）。

点一条即可回到当时那本书（阅读记录会直接跳到那一章）；
右上角「清空」可一键清空历史，`Esc` / 点遮罩 / ✕ 关闭。
"""
from __future__ import annotations

import time
from typing import Dict, List, Optional

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (QFrame, QHBoxLayout, QLabel, QListWidget,
                             QListWidgetItem, QPushButton, QVBoxLayout, QWidget)

from ..history import KIND_CHAPTER
from .widgets import FADE_MS, CoverLabel, fade


def format_time(timestamp: float, now: Optional[float] = None) -> str:
    """把时间戳转成"今天 14:03 / 昨天 09:20 / 09-08 21:15"这种好读的形式。"""
    now = time.time() if now is None else now
    moment = time.localtime(timestamp or 0)
    today = time.localtime(now)
    clock = time.strftime("%H:%M", moment)
    if (moment.tm_year, moment.tm_yday) == (today.tm_year, today.tm_yday):
        return f"今天 {clock}"
    if moment.tm_year == today.tm_year and moment.tm_yday == today.tm_yday - 1:
        return f"昨天 {clock}"
    if moment.tm_year == today.tm_year:
        return time.strftime("%m-%d %H:%M", moment)
    return time.strftime("%Y-%m-%d %H:%M", moment)


class HistoryRow(QFrame):
    """历史里的一行：时间 + 动作标签 + 书名 + 章节。"""

    def __init__(self, entry: Dict, cover_loader=None,
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.entry = entry
        self.setObjectName("paletteRow")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 8, 12, 8)
        layout.setSpacing(12)

        self.cover = CoverLabel(36, 48, self)
        self.cover.set_title(entry.get("title", ""))
        layout.addWidget(self.cover, 0, Qt.AlignVCenter)

        info = QVBoxLayout()
        info.setSpacing(2)
        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        self.title_label = QLabel(entry.get("title") or "未知书名", self)
        self.title_label.setObjectName("bookTitle")
        title_row.addWidget(self.title_label, 0)
        self.kind_label = QLabel("阅读" if entry.get("kind") == KIND_CHAPTER else "浏览",
                                 self)
        self.kind_label.setObjectName("sourceTag")
        title_row.addWidget(self.kind_label, 0)
        title_row.addStretch(1)
        self.time_label = QLabel(format_time(float(entry.get("at") or 0)), self)
        self.time_label.setObjectName("bookMeta")
        title_row.addWidget(self.time_label, 0)
        info.addLayout(title_row)

        detail = entry.get("chapter_title") or ""
        if detail and entry.get("chapter_index", -1) >= 0:
            detail = f"第 {int(entry['chapter_index']) + 1} 章 · {detail}"
        elif not detail:
            detail = entry.get("author") and f"作者：{entry['author']}" or "打开过详情页"
        self.detail_label = QLabel(detail, self)
        self.detail_label.setObjectName("bookMeta")
        info.addWidget(self.detail_label)
        layout.addLayout(info, 1)

        if entry.get("source_name"):
            tag = QLabel(entry["source_name"], self)
            tag.setObjectName("sourceTag")
            layout.addWidget(tag, 0, Qt.AlignTop)

        if cover_loader and entry.get("cover_url"):
            cover_loader(entry["cover_url"], self.set_cover, self.cover)

    def set_cover(self, data: bytes) -> None:
        self.cover.set_image(data)


class HistoryPanel(QFrame):
    """浏览历史浮层（由主窗口负责定位与遮罩）。"""

    entry_chosen = pyqtSignal(object)      # dict 历史条目
    closed = pyqtSignal()
    cleared = pyqtSignal()

    def __init__(self, cover_loader=None, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._cover_loader = cover_loader
        self._rows: List[HistoryRow] = []
        self._shown_state = False          # 真实开合状态（动画只负责视觉）

        self.setObjectName("paletteCard")
        self.setAttribute(Qt.WA_StyledBackground, True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ------------------------------------------------------------ 标题行
        head = QHBoxLayout()
        head.setContentsMargins(16, 12, 12, 10)
        head.setSpacing(8)
        title = QLabel("浏览历史", self)
        title.setObjectName("drawerTitle")
        head.addWidget(title)
        self.count_label = QLabel("", self)
        self.count_label.setObjectName("paletteHint")
        head.addWidget(self.count_label)
        head.addStretch(1)
        self.clear_button = QPushButton("清空", self)
        self.clear_button.setObjectName("ghost")
        self.clear_button.setToolTip("清空全部浏览历史")
        self.clear_button.clicked.connect(self._on_clear)
        head.addWidget(self.clear_button)
        self.close_button = QPushButton("Esc", self)
        self.close_button.setObjectName("ghost")
        self.close_button.setFixedWidth(46)
        self.close_button.clicked.connect(self.close_panel)
        head.addWidget(self.close_button)
        layout.addLayout(head)

        # ------------------------------------------------------------ 列表
        self.list = QListWidget(self)
        self.list.setObjectName("paletteResults")
        self.list.setFrameShape(QFrame.NoFrame)
        self.list.itemClicked.connect(self._on_item_activated)
        self.list.itemActivated.connect(self._on_item_activated)
        layout.addWidget(self.list, 1)

        self.hint = QLabel("还没有浏览记录：打开一本书的详情就会记下来。", self)
        self.hint.setObjectName("paletteHint")
        self.hint.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.hint)

        self.hide()

    # ------------------------------------------------------------------ 开关
    def open_panel(self, history) -> None:
        """填充并淡入显示（每次打开都重新读一遍历史）。"""
        self.refresh(history)
        self._shown_state = True
        fade(self, True, FADE_MS)          # 200ms 淡入
        self.raise_()
        self.list.setFocus()

    def close_panel(self) -> None:
        """淡出隐藏；状态立即置为关闭。"""
        was_open = self._shown_state
        self._shown_state = False
        if self.isVisible():
            fade(self, False, FADE_MS)
        if was_open:
            self.closed.emit()

    def is_open(self) -> bool:
        return self._shown_state

    # ------------------------------------------------------------------ 数据
    def refresh(self, history) -> None:
        items = history.items()
        self.list.clear()
        self._rows = []
        for entry in items:
            row = HistoryRow(entry, self._cover_loader, self.list)
            item = QListWidgetItem(self.list)
            item.setData(Qt.UserRole, entry)
            item.setSizeHint(row.sizeHint())
            self.list.addItem(item)
            self.list.setItemWidget(item, row)
            self._rows.append(row)
        if items and self.list.currentRow() < 0:
            self.list.setCurrentRow(0)
        self.count_label.setText(f"共 {len(items)} 条" if items else "")
        self.hint.setVisible(not items)
        self.clear_button.setEnabled(bool(items))

    def rows(self) -> List[HistoryRow]:
        """历史行（自检用）。"""
        return list(self._rows)

    # ------------------------------------------------------------------ 交互
    def _on_item_activated(self, item: QListWidgetItem) -> None:
        entry = item.data(Qt.UserRole)
        if entry:
            self.entry_chosen.emit(entry)

    def _on_clear(self) -> None:
        self.cleared.emit()
        self.list.clear()
        self._rows = []
        self.count_label.setText("")
        self.hint.setVisible(True)
        self.clear_button.setEnabled(False)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key_Escape:
            self.close_panel()
            return
        super().keyPressEvent(event)

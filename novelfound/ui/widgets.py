# -*- coding: utf-8 -*-
"""通用小部件：封面、提示条、空状态占位。"""
from __future__ import annotations

from typing import Optional

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QPainter, QPixmap
from PyQt5.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy,
                             QVBoxLayout, QWidget)

from .theme import BORDER, MUTED


class CoverLabel(QLabel):
    """封面显示控件：无图时绘制占位图（书名首字）。"""

    clicked = pyqtSignal()

    def __init__(self, width: int = 84, height: int = 112, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._w = width
        self._h = height
        self._title = ""
        self._pixmap: Optional[QPixmap] = None
        self.setFixedSize(width, height)
        self.setAlignment(Qt.AlignCenter)
        self.setStyleSheet(f"border: 1px solid {BORDER}; border-radius: 4px; "
                           f"background: #f7f8fa; color: {MUTED};")
        self.setCursor(Qt.PointingHandCursor)

    # ------------------------------------------------------------------ 数据
    def set_title(self, title: str) -> None:
        self._title = title or ""
        if self._pixmap is None:
            self.update()

    def set_image(self, data: bytes) -> None:
        """设置封面图片（失败时保持占位图）。"""
        if not data:
            return
        pixmap = QPixmap()
        if not pixmap.loadFromData(data):
            return
        self._pixmap = pixmap
        self.update()

    def clear_image(self) -> None:
        self._pixmap = None
        self.update()

    # ------------------------------------------------------------------ 绘制
    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 接口
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        rect = self.rect().adjusted(1, 1, -1, -1)
        if self._pixmap is not None and not self._pixmap.isNull():
            scaled = self._pixmap.scaled(self._w - 2, self._h - 2,
                                         Qt.KeepAspectRatioByExpanding,
                                         Qt.SmoothTransformation)
            # 居中裁剪
            x = (scaled.width() - (self._w - 2)) // 2
            y = (scaled.height() - (self._h - 2)) // 2
            painter.drawPixmap(0, 0, scaled.copy(x, y, self._w - 2, self._h - 2))
            painter.end()
            return
        painter.fillRect(rect, QColor("#f2f4f7"))
        text = (self._title or "书")[:2]
        font = QFont(self.font())
        font.setPointSize(max(11, self._w // 6))
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor("#9aa1ab"))
        painter.drawText(rect, Qt.AlignCenter, text)
        painter.end()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class Banner(QFrame):
    """可关闭的提示条，用于展示错误 / 警告 / 信息，可带一个操作按钮。"""

    closed = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("banner")
        self.setVisible(False)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 8, 8)
        layout.setSpacing(8)
        self.label = QLabel("", self)
        self.label.setWordWrap(True)
        self.label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        layout.addWidget(self.label, 1)
        self.action_button = QPushButton("", self)
        self.action_button.setVisible(False)
        layout.addWidget(self.action_button, 0)
        self.close_button = QPushButton("✕", self)
        self.close_button.setObjectName("ghost")
        self.close_button.setFixedWidth(28)
        self.close_button.clicked.connect(self.hide_banner)
        layout.addWidget(self.close_button, 0)

    def show_message(self, text: str, level: str = "info", action_text: str = "",
                     action=None) -> None:
        self.setObjectName("bannerError" if level == "error" else "banner")
        self.style().unpolish(self)
        self.style().polish(self)
        self.label.setText(text)
        if action_text and action is not None:
            self.action_button.setText(action_text)
            self.action_button.setVisible(True)
            try:
                self.action_button.clicked.disconnect()
            except TypeError:
                pass
            self.action_button.clicked.connect(action)
        else:
            self.action_button.setVisible(False)
        self.setVisible(True)

    def hide_banner(self) -> None:
        self.setVisible(False)
        self.closed.emit()


class EmptyState(QWidget):
    """空状态占位（首次打开、无结果时展示）。"""

    def __init__(self, title: str, hint: str, parent: Optional[QWidget] = None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 40, 40, 40)
        layout.setSpacing(10)
        layout.addStretch(1)
        title_label = QLabel(title, self)
        title_label.setObjectName("emptyTitle")
        title_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(title_label)
        hint_label = QLabel(hint, self)
        hint_label.setObjectName("emptyHint")
        hint_label.setAlignment(Qt.AlignCenter)
        hint_label.setWordWrap(True)
        layout.addWidget(hint_label)
        layout.addStretch(2)

    def set_text(self, title: str, hint: str) -> None:
        labels = self.findChildren(QLabel)
        if len(labels) >= 2:
            labels[0].setText(title)
            labels[1].setText(hint)


class SectionTitle(QLabel):
    """小节标题。"""

    def __init__(self, text: str, parent: Optional[QWidget] = None):
        super().__init__(text, parent)
        self.setObjectName("sectionTitle")

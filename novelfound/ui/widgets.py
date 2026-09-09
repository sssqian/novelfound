# -*- coding: utf-8 -*-
"""通用小部件：封面、提示条、空状态、自动隐藏浮条、细进度线、轻提示。

设计取向见 docs/UI设计方案：界面尽量安静，控件按需出现而不是常驻。
"""
from __future__ import annotations

from typing import Optional

from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QFontMetrics, QPainter, QPixmap
from PyQt5.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy,
                             QVBoxLayout, QWidget)

from .theme import BORDER, DIVIDER, MUTED, TEXT_FAINT, TEXT_MAIN, TEXT_SUB


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
    """空状态占位（首次打开、书架为空时展示）。"""

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
        self._layout = layout
        self.action_button: Optional[QPushButton] = None

    def add_action(self, text: str) -> QPushButton:
        """在提示下方加一个按钮（如「搜索小说」）。"""
        if self.action_button is None:
            row = QHBoxLayout()
            row.addStretch(1)
            self.action_button = QPushButton(text, self)
            self.action_button.setObjectName("primary")
            self.action_button.setMinimumWidth(140)
            row.addWidget(self.action_button)
            row.addStretch(1)
            self._layout.insertLayout(self._layout.count() - 1, row)
        else:
            self.action_button.setText(text)
        return self.action_button

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


class AutoHideBar(QFrame):
    """阅读器顶部/底部浮条：平时隐藏，鼠标靠近边缘才出现，静止后自动隐藏。

    为了可测试，显示/隐藏是**显式状态机**（``show_bar`` / ``hide_bar`` /
    ``maybe_auto_hide``），不依赖真实鼠标事件。
    """

    def __init__(self, parent: Optional[QWidget] = None, edge: str = "top",
                 auto_hide_ms: int = 2000):
        super().__init__(parent)
        self.edge = edge
        self.setObjectName("autoHideBar")
        self.setProperty("edge", edge)
        self._auto_hide_ms = auto_hide_ms
        self._hovered = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(auto_hide_ms)
        self._timer.timeout.connect(self.maybe_auto_hide)
        self.hide()

    # ------------------------------------------------------------------ 状态
    def show_bar(self) -> None:
        """显示并重新开始计时。"""
        self.show()
        self.raise_()
        self._timer.start(self._auto_hide_ms)

    def hide_bar(self) -> None:
        self._timer.stop()
        self.hide()

    def set_hovered(self, hovered: bool) -> None:
        """鼠标是否停在浮条上（停住时不要自动隐藏）。"""
        self._hovered = hovered
        if hovered:
            self._timer.stop()
        else:
            self._timer.start(self._auto_hide_ms)

    def maybe_auto_hide(self) -> None:
        if not self._hovered:
            self.hide_bar()

    def is_bar_visible(self) -> bool:
        return self.isVisible()

    # ------------------------------------------------------------ 鼠标进出
    def enterEvent(self, event) -> None:  # noqa: N802
        self.set_hovered(True)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self.set_hovered(False)
        super().leaveEvent(event)


class ProgressLine(QWidget):
    """常驻的 2px 细进度线（不占布局高度，直接贴在窗口底部）。"""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._ratio = 0.0
        self.setFixedHeight(2)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._track = QColor(DIVIDER)
        self._fill = QColor(TEXT_FAINT)

    def set_progress(self, ratio: float) -> None:
        self._ratio = max(0.0, min(1.0, float(ratio)))
        self.update()

    def progress(self) -> float:
        return self._ratio

    def set_colors(self, track: str, fill: str) -> None:
        self._track = QColor(track)
        self._fill = QColor(fill)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.fillRect(self.rect(), self._track)
        width = int(self.width() * self._ratio)
        if width > 0:
            painter.fillRect(0, 0, width, self.height(), self._fill)
        painter.end()


class Toast(QFrame):
    """底部轻提示：出现几秒后自动消失，不打断操作。

    接口与 :class:`Banner` 保持一致（``show_message`` / ``hide_banner`` / ``label``），
    方便替换。它是浮层，需要由父窗口在 resize 时调用 ``reposition()``。
    """

    closed = pyqtSignal()
    shown = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None, seconds: float = 6.0):
        super().__init__(parent)
        self.setObjectName("toast")
        self.setVisible(False)
        self._seconds = max(1.0, float(seconds))
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide_banner)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 10, 10, 10)
        layout.setSpacing(8)
        self.label = QLabel("", self)
        self.label.setWordWrap(True)
        self.label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        layout.addWidget(self.label, 1)
        self.action_button = QPushButton("", self)
        self.action_button.setObjectName("ghost")
        self.action_button.setVisible(False)
        layout.addWidget(self.action_button, 0)
        self.close_button = QPushButton("✕", self)
        self.close_button.setObjectName("ghost")
        self.close_button.setFixedWidth(24)
        self.close_button.clicked.connect(self.hide_banner)
        layout.addWidget(self.close_button, 0)

    def show_message(self, text: str, level: str = "info", action_text: str = "",
                     action=None) -> None:
        self.setObjectName("toastError" if level == "error" else "toast")
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
        self.raise_()
        self._timer.start(int(self._seconds * 1000))

    def hide_banner(self) -> None:
        self._timer.stop()
        self.setVisible(False)
        self.closed.emit()

    def set_duration(self, seconds: float) -> None:
        self._seconds = max(1.0, float(seconds))

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.shown.emit()

    def reposition(self, area_width: int, area_height: int,
                   margin_bottom: int = 24, max_width: int = 760) -> None:
        """把提示条摆到底部居中（由父窗口在显示/改变尺寸时调用）。"""
        self.adjustSize()
        hint = self.sizeHint()
        width = min(max(320, hint.width()), max(320, area_width - 80), max_width)
        height = hint.height()
        self.setGeometry((area_width - width) // 2,
                         max(0, area_height - height - margin_bottom),
                         width, height)


class Scrim(QWidget):
    """半透明遮罩：抽屉/浮层打开时盖住主界面，点击即关闭。"""

    clicked = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("scrim")
        self.setAttribute(Qt.WA_StyledBackground, True)   # 让 QSS 背景生效
        self.setCursor(Qt.ArrowCursor)
        self.hide()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class CoverTile(QFrame):
    """书架网格单元：封面 + 书名 + 进度文字（不画进度条）。

    进度按方案要求只显示百分比文字；没读过的显示「未读」。
    """

    clicked = pyqtSignal(object)     # 传回 Book

    def __init__(self, book, status: str = "", cover_w: int = 104,
                 cover_h: int = 146, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.book = book
        self.setObjectName("coverTile")
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedWidth(cover_w + 8)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 6)
        layout.setSpacing(6)

        self.cover = CoverLabel(cover_w, cover_h, self)
        self.cover.set_title(book.title)
        layout.addWidget(self.cover, 0, Qt.AlignHCenter)

        self.title_label = QLabel(book.title or "未知书名", self)
        self.title_label.setObjectName("tileTitle")
        self.title_label.setAlignment(Qt.AlignHCenter)
        self.title_label.setFixedWidth(cover_w)
        self.title_label.setToolTip(book.title or "")
        layout.addWidget(self.title_label, 0, Qt.AlignHCenter)

        self.status_label = QLabel(status or "未读", self)
        self.status_label.setObjectName("tileMeta")
        self.status_label.setAlignment(Qt.AlignHCenter)
        layout.addWidget(self.status_label, 0, Qt.AlignHCenter)

        self.setToolTip(f"{book.title}\n{book.author or '作者未知'}")

    # ------------------------------------------------------------------ 数据
    def set_status(self, text: str) -> None:
        self.status_label.setText(text or "未读")

    def set_cover(self, data: bytes) -> None:
        self.cover.set_image(data)

    def _elide_title(self) -> None:
        metrics = QFontMetrics(self.title_label.font())
        self.title_label.setText(metrics.elidedText(
            self.book.title or "未知书名", Qt.ElideRight, self.title_label.width()))

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._elide_title()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._elide_title()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.book)
        super().mouseReleaseEvent(event)


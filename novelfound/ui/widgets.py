# -*- coding: utf-8 -*-
"""通用小部件：封面、提示条、空状态、自动隐藏浮条、细进度线、轻提示。

设计取向见 docs/UI设计方案：界面尽量安静，控件按需出现而不是常驻。
"""
from __future__ import annotations

from typing import Optional

from PyQt5.QtCore import (QEasingCurve, QPoint, QPropertyAnimation, Qt, QTimer,
                          pyqtSignal)
from PyQt5.QtGui import QColor, QFont, QFontMetrics, QPainter, QPixmap
from PyQt5.QtWidgets import (QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel,
                             QPushButton, QSizePolicy, QVBoxLayout, QWidget)

from .theme import (BADGE_BG, BADGE_FG, BORDER, DIVIDER, MUTED, PLACEHOLDER_BG,
                    PLACEHOLDER_FG, TEXT_FAINT,
                    TEXT_MAIN, TEXT_SUB)

# ---------------------------------------------------------------------------
# 动画时长（方案 P2：所有动画落在 200–300ms，克制、不做花哨效果）
# ---------------------------------------------------------------------------
FADE_MS = 200        # 浮层 / 浮条淡入淡出
DRAWER_MS = 250      # 抽屉滑出滑入


def _keep(widget, animation: QPropertyAnimation) -> QPropertyAnimation:
    """把动画挂在控件上，避免被 Python 垃圾回收（否则动画瞬间消失）。"""
    widget._animation = animation          # noqa: SLF001 - 有意挂载
    return animation


def fade(widget, show: bool, duration: int = FADE_MS,
         on_finished=None) -> QPropertyAnimation:
    """淡入 / 淡出。

    注意：**显示状态由调用方自己维护**（如 ``AutoHideBar._shown``、``is_open()``），
    动画只负责视觉。这样"是否可见"是确定性的，测试不用等动画跑完。
    """
    effect = widget.graphicsEffect()
    if not isinstance(effect, QGraphicsOpacityEffect):
        effect = QGraphicsOpacityEffect(widget)
        widget.setGraphicsEffect(effect)
    if show:
        effect.setOpacity(0.0)
        widget.show()
    animation = QPropertyAnimation(effect, b"opacity", widget)
    animation.setDuration(max(1, int(duration)))
    animation.setStartValue(effect.opacity())
    animation.setEndValue(1.0 if show else 0.0)
    animation.setEasingCurve(QEasingCurve.OutCubic)
    if show:
        if on_finished is not None:
            animation.finished.connect(on_finished)
    else:
        animation.finished.connect(lambda: _finish_hide(widget, on_finished))
    animation.start()
    return _keep(widget, animation)


def _finish_hide(widget, on_finished) -> None:
    """淡出结束后再真正 hide；若中途又被显示（``_shown_state`` 为真）就跳过。"""
    if on_finished is not None:
        on_finished()
        return
    if getattr(widget, "_shown_state", True) is False:
        widget.hide()


def slide_in(widget, from_x: int, to_x: int, duration: int = DRAWER_MS,
             on_finished=None) -> QPropertyAnimation:
    """水平滑入（抽屉用；只动 x，不碰 y，避免和布局定位打架）。"""
    start = QPoint(int(from_x), widget.y())
    end = QPoint(int(to_x), widget.y())
    animation = QPropertyAnimation(widget, b"pos", widget)
    animation.setDuration(max(1, int(duration)))
    animation.setStartValue(start)
    animation.setEndValue(end)
    animation.setEasingCurve(QEasingCurve.OutCubic)
    if on_finished is not None:
        animation.finished.connect(on_finished)
    widget.move(start)
    animation.start()
    return _keep(widget, animation)


def slide_out(widget, to_x: int, duration: int = DRAWER_MS,
              on_finished=None) -> QPropertyAnimation:
    """水平滑出（动画结束后再 hide）。"""
    animation = QPropertyAnimation(widget, b"pos", widget)
    animation.setDuration(max(1, int(duration)))
    animation.setStartValue(widget.pos())
    animation.setEndValue(QPoint(int(to_x), widget.y()))
    animation.setEasingCurve(QEasingCurve.OutCubic)
    animation.finished.connect(lambda: _finish_hide(widget, on_finished))
    animation.start()
    return _keep(widget, animation)


class CoverLabel(QLabel):
    """封面显示控件：无图时绘制占位图（书名首字）。"""

    clicked = pyqtSignal()

    def __init__(self, width: int = 84, height: int = 112, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._w = width
        self._h = height
        self._title = ""
        self._pixmap: Optional[QPixmap] = None
        self._hover = False
        self._hover_hint = ""          # 非空时，鼠标悬停会给封面盖一层薄纱 + 提示文字
        self.setFixedSize(width, height)
        self.setAlignment(Qt.AlignCenter)
        # 占位底色用暖色 token（原来是硬编码冷灰 #f7f8fa/#f2f4f7，在暖白背景上发蓝）
        self.setStyleSheet(f"border: 1px solid {BORDER}; border-radius: 4px; "
                           f"background: {PLACEHOLDER_BG}; color: {PLACEHOLDER_FG};")
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

    # ------------------------------------------------------------------ 悬停
    def set_hover_hint(self, text: str) -> None:
        """开启悬停效果，并指定盖在封面上的一行提示（如「查看详情」）。"""
        self._hover_hint = text or ""
        self.update()

    def set_hover(self, hovered: bool) -> None:
        if not self._hover_hint or hovered == self._hover:
            return
        self._hover = bool(hovered)
        self.update()

    def is_hovered(self) -> bool:
        return self._hover

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
        else:
            painter.fillRect(rect, QColor(PLACEHOLDER_BG))
            text = (self._title or "书")[:2]
            font = QFont(self.font())
            font.setPointSize(max(11, self._w // 6))
            font.setBold(True)
            painter.setFont(font)
            painter.setPen(QColor(PLACEHOLDER_FG))
            painter.drawText(rect, Qt.AlignCenter, text)

        # 悬停：盖一层薄纱 + 提示文字（书架网格用；其它地方不开这个效果）
        if self._hover and self._hover_hint:
            painter.fillRect(self.rect(), QColor(40, 36, 30, 90))
            font = QFont(self.font())
            font.setPointSize(10)
            painter.setFont(font)
            painter.setPen(QColor("#FBF9F3"))
            painter.drawText(self.rect(), Qt.AlignCenter, self._hover_hint)
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
        self._action_row: Optional[QHBoxLayout] = None
        self.action_button: Optional[QPushButton] = None

    def _append_action(self, text: str, primary: bool = True) -> QPushButton:
        if self._action_row is None:
            self._action_row = QHBoxLayout()
            self._action_row.addStretch(1)
            self._layout.insertLayout(self._layout.count() - 1, self._action_row)
            self._action_row.addStretch(1)
        button = QPushButton(text, self)
        if primary:
            button.setObjectName("primary")
        button.setMinimumWidth(140)
        self._action_row.insertWidget(self._action_row.count() - 1, button)
        return button

    def add_action(self, text: str) -> QPushButton:
        """在提示下方加一个主按钮（如「搜索小说」）。"""
        if self.action_button is None:
            self.action_button = self._append_action(text)
        else:
            self.action_button.setText(text)
        return self.action_button

    def add_secondary_action(self, text: str) -> QPushButton:
        """在同一行再加一个次级按钮（如「导入本地书籍」）。"""
        return self._append_action(text, primary=False)

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
        self._shown_state = False        # 真实状态（不依赖动画是否跑完）
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(auto_hide_ms)
        self._timer.timeout.connect(self.maybe_auto_hide)
        self.hide()

    # ------------------------------------------------------------------ 状态
    def show_bar(self) -> None:
        """淡入显示，并重新开始"静止 2 秒自动隐藏"的计时。"""
        self._shown_state = True
        self.raise_()
        fade(self, True, FADE_MS)
        self._timer.start(self._auto_hide_ms)

    def hide_bar(self) -> None:
        """淡出隐藏（状态立即置为隐藏，动画只是视觉过渡）。"""
        self._timer.stop()
        self._shown_state = False
        fade(self, False, FADE_MS)

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
        return self._shown_state

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
    """半透明遮罩：抽屉/浮层打开时盖住主界面（200ms 淡入），点击即关闭。"""

    clicked = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("scrim")
        self.setAttribute(Qt.WA_StyledBackground, True)   # 让 QSS 背景生效
        self.setCursor(Qt.ArrowCursor)
        self._shown_state = False
        self.hide()

    def show_scrim(self) -> None:
        self._shown_state = True
        fade(self, True, FADE_MS)
        self.raise_()

    def hide_scrim(self) -> None:
        self._shown_state = False
        if self.isVisible():
            fade(self, False, FADE_MS)

    def is_shown(self) -> bool:
        return self._shown_state

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class CoverTile(QFrame):
    """书架网格单元：封面（含右下角进度角标）+ 书名 + 进度文字（不画进度条）。

    进度有两处展示（按用户要求"都做，先看效果"）：
    * 封面右下角的**角标**（半透明小圆角标签）；
    * 书名下方的百分比文字。
    没读过的书两处都显示「未读」。鼠标悬停时封面会盖一层薄纱并提示「查看详情」。
    """

    clicked = pyqtSignal(object)     # 传回 Book

    def __init__(self, book, status: str = "", cover_w: int = 104,
                 cover_h: int = 146, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.book = book
        self.setObjectName("coverTile")
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedWidth(cover_w + 8)
        self.setMouseTracking(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 6)
        layout.setSpacing(6)

        self.cover = CoverLabel(cover_w, cover_h, self)
        self.cover.setObjectName("tileCover")
        self.cover.set_title(book.title)
        self.cover.setToolTip(book.title or "")
        self.cover.set_hover_hint("查看详情")     # 悬停盖纱 + 提示
        layout.addWidget(self.cover, 0, Qt.AlignHCenter)

        # 角标：贴在封面右下角，显示百分比 / 未读
        # 注意：`CoverLabel` 自己 setStyleSheet 过（无选择器的声明会**连子控件一起**生效），
        # 它会盖掉 app 级 QSS 里的 `#coverBadge`，导致角标只剩文字、没有深色药丸底。
        # 所以这里给角标单独设一条带选择器的样式表（更具体 → 一定胜出）。
        self.badge = QLabel(status or "未读", self.cover)
        self.badge.setObjectName("coverBadge")
        self.badge.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.badge.setStyleSheet(
            f"#coverBadge {{ background: {BADGE_BG}; color: {BADGE_FG}; "
            f"border-radius: 9px; padding: 1px 7px; font-size: 11px; }}")

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
        self._place_badge()

    # ------------------------------------------------------------------ 数据
    def set_status(self, text: str) -> None:
        text = text or "未读"
        self.status_label.setText(text)
        self.badge.setText(text)
        self._place_badge()

    def set_cover(self, data: bytes) -> None:
        self.cover.set_image(data)

    def _place_badge(self) -> None:
        """把角标摆到封面右下角（文字变化后要重新摆）。"""
        self.badge.adjustSize()
        margin = 5
        self.badge.move(max(0, self.cover.width() - self.badge.width() - margin),
                        max(0, self.cover.height() - self.badge.height() - margin))
        self.badge.raise_()

    def _elide_title(self) -> None:
        metrics = QFontMetrics(self.title_label.font())
        self.title_label.setText(metrics.elidedText(
            self.book.title or "未知书名", Qt.ElideRight, self.title_label.width()))

    # ------------------------------------------------------------------ 悬停
    def enterEvent(self, event) -> None:  # noqa: N802
        self.cover.set_hover(True)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self.cover.set_hover(False)
        super().leaveEvent(event)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._elide_title()
        self._place_badge()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._elide_title()
        self._place_badge()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.book)
        super().mouseReleaseEvent(event)


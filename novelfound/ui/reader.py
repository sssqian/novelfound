# -*- coding: utf-8 -*-
"""内置阅读器。

特点：
* 纯文本渲染（HTML 只用于排版），不会加载任何原站脚本、图片或广告；
* 字号 / 行距 / 段距 / 字体可调；
* 5 套配色（日间、护眼绿、羊皮纸、夜间、深灰）；
* 两种翻页方式：连续滚动、整页翻页（PageUp / PageDown / 空格）；
* 章节内进度与整本书进度实时显示，退出后自动记住位置。
"""
from __future__ import annotations

from typing import List, Optional

from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtGui import (QColor, QFont, QFontMetrics, QTextBlockFormat,
                         QTextCharFormat, QTextCursor)
from PyQt5.QtWidgets import (QComboBox, QFrame, QHBoxLayout, QLabel, QSizePolicy,
                             QTextBrowser, QToolButton, QVBoxLayout, QWidget)

from ..config import AppConfig
from ..models import ChapterContent
from .theme import READER_THEMES, reader_theme


class ReaderView(QWidget):
    """阅读器主体。"""

    chapter_requested = pyqtSignal(int)        # 请求加载第 index 章
    back_requested = pyqtSignal()              # 返回详情
    settings_changed = pyqtSignal()            # 字号/主题变化，需要写回配置
    position_changed = pyqtSignal(int, int)    # (chapter_index, scroll_pos)

    def __init__(self, config: AppConfig, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.config = config
        self.chapter_index = 0
        self.chapter_count = 0
        self.chapter_title = ""
        self._scroll_pos = 0
        self._paragraphs: List[str] = []
        self._positions: Dict[str, int] = {}   # 章节地址 -> 滚动位置（本次会话内记忆）
        self._current_url = ""
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(600)
        self._save_timer.timeout.connect(self._emit_position)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # -------------------------------------------------------------- 工具栏
        self.toolbar = QFrame(self)
        self.toolbar.setObjectName("readerToolBar")
        bar = QHBoxLayout(self.toolbar)
        bar.setContentsMargins(10, 6, 10, 6)
        bar.setSpacing(6)

        self.back_button = QToolButton(self)
        self.back_button.setText("‹ 目录")
        self.back_button.setToolTip("返回书籍详情 / 目录")
        self.back_button.clicked.connect(self.back_requested.emit)
        bar.addWidget(self.back_button)

        self.title_label = QLabel("", self)
        self.title_label.setObjectName("readerTitle")
        self.title_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        bar.addWidget(self.title_label)
        bar.addStretch(1)

        self.prev_button = QToolButton(self)
        self.prev_button.setText("上一章")
        self.prev_button.clicked.connect(lambda: self._step(-1))
        bar.addWidget(self.prev_button)

        self.next_button = QToolButton(self)
        self.next_button.setText("下一章")
        self.next_button.clicked.connect(lambda: self._step(1))
        bar.addWidget(self.next_button)

        bar.addWidget(_separator(self))

        self.font_down = QToolButton(self)
        self.font_down.setText("A−")
        self.font_down.setToolTip("减小字号（Ctrl+-）")
        self.font_down.clicked.connect(lambda: self.change_font_size(-1))
        bar.addWidget(self.font_down)

        self.font_up = QToolButton(self)
        self.font_up.setText("A+")
        self.font_up.setToolTip("增大字号（Ctrl+=）")
        self.font_up.clicked.connect(lambda: self.change_font_size(1))
        bar.addWidget(self.font_up)

        self.theme_box = QComboBox(self)
        for key, theme in READER_THEMES.items():
            self.theme_box.addItem(theme["name"], key)
        self.theme_box.setToolTip("阅读背景色（护眼模式）")
        self.theme_box.currentIndexChanged.connect(self._on_theme_changed)
        bar.addWidget(self.theme_box)

        self.mode_button = QToolButton(self)
        self.mode_button.setCheckable(True)
        self.mode_button.setText("翻页模式")
        self.mode_button.setToolTip("切换 滚动 / 整页翻页（PageUp、PageDown、空格）")
        self.mode_button.toggled.connect(self._on_mode_toggled)
        bar.addWidget(self.mode_button)

        self.fullscreen_button = QToolButton(self)
        self.fullscreen_button.setText("全屏")
        self.fullscreen_button.setCheckable(True)
        self.fullscreen_button.setToolTip("沉浸阅读（F11）")
        self.fullscreen_button.toggled.connect(self._on_fullscreen_toggled)
        bar.addWidget(self.fullscreen_button)

        root.addWidget(self.toolbar)

        # -------------------------------------------------------------- 正文区
        self.body = QWidget(self)
        body_layout = QVBoxLayout(self.body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)

        self.view = QTextBrowser(self.body)
        self.view.setFrameShape(QFrame.NoFrame)
        self.view.setOpenExternalLinks(False)
        self.view.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
        self.view.setFocusPolicy(Qt.StrongFocus)
        self.view.verticalScrollBar().valueChanged.connect(self._on_scrolled)
        body_layout.addWidget(self.view, 1)

        self.status = QLabel("", self.body)
        self.status.setObjectName("muted")
        self.status.setContentsMargins(16, 4, 16, 6)
        body_layout.addWidget(self.status, 0)

        root.addWidget(self.body, 1)

        self.apply_settings()
        self._update_buttons()

    # ------------------------------------------------------------------ 设置
    def apply_settings(self) -> None:
        """把配置应用到阅读器（字号、字体、行距、主题、模式）。"""
        self._font_size = int(self.config.get("font_size"))
        self._font_family = self.config.get("font_family") or ""
        self._line_height = float(self.config.get("line_height"))
        self._para_spacing = int(self.config.get("paragraph_spacing"))
        self._indent_chars = float(self.config.get("first_line_indent") or 0)
        self._theme_key = self.config.get("reader_theme")
        self._page_mode = self.config.get("reader_mode") == "page"

        theme = reader_theme(self._theme_key)
        self.view.setStyleSheet(
            f"QTextBrowser {{ background: {theme['bg']}; border: none; "
            f"padding: 18px 28px; color: {theme['fg']}; }}")

        index = self.theme_box.findData(self._theme_key)
        if index >= 0 and index != self.theme_box.currentIndex():
            self.theme_box.blockSignals(True)
            self.theme_box.setCurrentIndex(index)
            self.theme_box.blockSignals(False)
        if self.mode_button.isChecked() != self._page_mode:
            self.mode_button.blockSignals(True)
            self.mode_button.setChecked(self._page_mode)
            self.mode_button.setText("滚动模式" if self._page_mode else "翻页模式")
            self.mode_button.blockSignals(False)
        self.view.setVerticalScrollBarPolicy(
            Qt.ScrollBarAlwaysOff if self._page_mode else Qt.ScrollBarAsNeeded)

    def change_font_size(self, delta: int) -> None:
        size = max(12, min(48, int(self.config.get("font_size")) + delta))
        if size == int(self.config.get("font_size")):
            return
        self.config.set("font_size", size)
        self.apply_settings()
        self.render()
        self.settings_changed.emit()

    def _on_theme_changed(self) -> None:
        key = self.theme_box.currentData()
        if not key or key == self.config.get("reader_theme"):
            return
        self.config.set("reader_theme", key)
        self.apply_settings()
        self.render()          # 文字颜色写在字符格式里，换肤后必须重绘
        self.settings_changed.emit()

    def _on_mode_toggled(self, checked: bool) -> None:
        self._page_mode = checked
        self.mode_button.setText("滚动模式" if checked else "翻页模式")
        self.config.set("reader_mode", "page" if checked else "scroll")
        self.apply_settings()
        self.settings_changed.emit()

    def _on_fullscreen_toggled(self, checked: bool) -> None:
        window = self.window()
        if checked:
            window.showFullScreen()
        else:
            window.showNormal()

    # ------------------------------------------------------------------ 内容
    def set_book(self, chapter_count: int, chapter_index: int = 0) -> None:
        self.chapter_count = max(0, chapter_count)
        self.chapter_index = max(0, chapter_index)
        self._update_buttons()

    def remember_position(self) -> None:
        """把当前滚动位置记到当前章节名下（切章/清空视图前调用）。"""
        if self._current_url:
            self._positions[self._current_url] = self._scroll_pos

    def set_loading(self, title: str = "") -> None:
        self.remember_position()
        self.title_label.setText(title or "加载中…")
        self.status.setText("正在获取章节内容…")
        self.view.setPlainText("")

    def set_error(self, message: str) -> None:
        theme = reader_theme(self._theme_key)
        self.view.setHtml(
            f"<div style='color:{theme['fg']}; font-size:15px; padding:24px;'>"
            f"<p style='color:#c0392b; font-weight:600;'>加载失败</p>"
            f"<p>{message}</p>"
            f"<p style='color:{theme['muted']};'>可以尝试：切换书源、点击「上一章 / 下一章」重试、"
            f"或在工具栏切换阅读模式后再试。</p></div>")
        self.status.setText("加载失败")

    def set_content(self, content: ChapterContent, index: Optional[int] = None,
                    restore_pos: Optional[int] = None) -> None:
        """展示章节内容。"""
        # 同一章重新渲染（换字号/换主题）时保留位置；换章的位置已在 set_loading 里记过
        if self._current_url and self._current_url == content.url:
            self._positions[content.url] = self._scroll_pos
        self._current_url = content.url
        if index is not None:
            self.chapter_index = index
        self.chapter_title = content.title
        self.title_label.setText(content.title)
        self.render(content.paragraphs)
        if restore_pos is None:
            restore_pos = self._positions.get(content.url, 0)
        if restore_pos:
            self._restore_scroll(restore_pos)
        else:
            self.view.verticalScrollBar().setValue(0)
        self._update_buttons()
        suffix = "　·　来自缓存" if content.from_cache else ""
        self.status.setText(
            f"第 {self.chapter_index + 1} / {self.chapter_count} 章　·　"
            f"{content.char_count} 字　·　{len(content.paragraphs)} 段{suffix}")

    def position_for(self, chapter_url: str) -> int:
        """该章节上次读到的滚动位置（本次会话记忆）。"""
        return self._positions.get(chapter_url, 0)

    def _restore_scroll(self, target: int, attempts: int = 12) -> None:
        """恢复滚动位置。

        刚 setHtml 完时文档还没完成布局，滚动条最大值可能是 0，
        直接 setValue 会被钳制到 0，所以要轮询等待布局完成。
        """
        def attempt(remaining: int) -> None:
            bar = self.view.verticalScrollBar()
            if bar.maximum() >= target or remaining <= 0:
                bar.setValue(min(target, bar.maximum()))
                return
            QTimer.singleShot(40, lambda: attempt(remaining - 1))

        attempt(attempts)

    def _indent_width(self, font: QFont) -> float:
        """按当前字体计算首行缩进宽度（像素）。

        用「一个汉字的宽度 × 缩进字符数」来算，这样字号、字体变化时缩进会同步变化，
        不会出现"字变大了但缩进还是老样子"的错位。
        """
        if self._indent_chars <= 0:
            return 0.0
        metrics = QFontMetrics(font)
        advance = metrics.horizontalAdvance("汉")
        if advance <= 0:                      # 极端字体兜底
            advance = max(font.pointSizeF(), 1.0) * 1.2
        return advance * self._indent_chars

    def render(self, paragraphs: Optional[List[str]] = None) -> None:
        """按当前字体/配色把段落渲染进文档。

        不传 paragraphs 时重绘当前章节（切换主题 / 调整字号后必须重绘，
        因为颜色和字号是写在字符格式里的）。
        """
        if paragraphs is None:
            paragraphs = self._paragraphs
        if not paragraphs:
            return
        theme = reader_theme(self._theme_key)
        font = QFont()
        if self._font_family:
            font.setFamily(self._font_family)
        font.setPointSize(self._font_size)

        block = QTextBlockFormat()
        block.setLineHeight(self._line_height * 100, QTextBlockFormat.ProportionalHeight)
        block.setBottomMargin(self._para_spacing)
        block.setTopMargin(0)
        block.setTextIndent(self._indent_width(font))   # 首行缩进（0 = 不缩进）
        char = QTextCharFormat()
        char.setFont(font)
        char.setForeground(QColor(theme["fg"]))

        document = self.view.document()
        document.setDefaultFont(font)
        cursor = QTextCursor(document)
        cursor.beginEditBlock()
        cursor.select(QTextCursor.Document)
        cursor.removeSelectedText()
        for i, para in enumerate(paragraphs):
            if i:
                cursor.insertBlock()
            cursor.setBlockFormat(block)
            cursor.setCharFormat(char)
            cursor.insertText(para)
        cursor.endEditBlock()
        self.view.moveCursor(QTextCursor.Start)
        self._paragraphs = paragraphs

    # ------------------------------------------------------------------ 导航
    def _update_buttons(self) -> None:
        self.prev_button.setEnabled(self.chapter_index > 0)
        self.next_button.setEnabled(self.chapter_index < self.chapter_count - 1)
        if self.chapter_count:
            self.status.setToolTip(f"第 {self.chapter_index + 1} 章 / 共 {self.chapter_count} 章")

    def _step(self, delta: int) -> None:
        target = self.chapter_index + delta
        if 0 <= target < max(self.chapter_count, 1):
            self.chapter_requested.emit(target)

    def goto(self, index: int) -> None:
        if 0 <= index < self.chapter_count:
            self.chapter_requested.emit(index)

    # ------------------------------------------------------------------ 翻页
    def page_step(self) -> int:
        return max(60, self.view.viewport().height() - 40)

    def next_page(self) -> None:
        bar = self.view.verticalScrollBar()
        if bar.value() >= bar.maximum() - 4:
            if self.chapter_index < self.chapter_count - 1:
                self.chapter_requested.emit(self.chapter_index + 1)
            return
        bar.setValue(min(bar.maximum(), bar.value() + self.page_step()))

    def prev_page(self) -> None:
        bar = self.view.verticalScrollBar()
        if bar.value() <= 4:
            if self.chapter_index > 0:
                self.chapter_requested.emit(self.chapter_index - 1)
            return
        bar.setValue(max(0, bar.value() - self.page_step()))

    def keyPressEvent(self, event) -> None:  # noqa: N802
        key = event.key()
        modifiers = event.modifiers()
        if modifiers & (Qt.ControlModifier | Qt.AltModifier):
            if modifiers & Qt.ControlModifier:
                if key in (Qt.Key_Plus, Qt.Key_Equal):
                    self.change_font_size(1)
                    return
                if key == Qt.Key_Minus:
                    self.change_font_size(-1)
                    return
            super().keyPressEvent(event)
            return

        if key in (Qt.Key_PageDown, Qt.Key_Space):
            self.next_page()
        elif key == Qt.Key_PageUp:
            self.prev_page()
        elif key == Qt.Key_Right:
            self._step(1)
        elif key == Qt.Key_Left:
            self._step(-1)
        elif key == Qt.Key_Home:
            self.view.verticalScrollBar().setValue(0)
        elif key == Qt.Key_End:
            bar = self.view.verticalScrollBar()
            bar.setValue(bar.maximum())
        elif key == Qt.Key_Escape:
            if self.fullscreen_button.isChecked():
                self.fullscreen_button.setChecked(False)
            else:
                self.back_requested.emit()
        else:
            super().keyPressEvent(event)

    def wheelEvent(self, event) -> None:  # noqa: N802
        # 滚动模式下正常滚动；翻页模式下滚轮也整页跳动，手感更接近翻页
        if self._page_mode:
            if event.angleDelta().y() < 0:
                self.next_page()
            else:
                self.prev_page()
            event.accept()
            return
        super().wheelEvent(event)

    # ------------------------------------------------------------ 位置记忆
    def _on_scrolled(self, value: int) -> None:
        self._scroll_pos = value
        self._save_timer.start()

    def _emit_position(self) -> None:
        self.position_changed.emit(self.chapter_index, self._scroll_pos)

    def current_position(self) -> int:
        return self._scroll_pos


def _separator(parent: QWidget) -> QFrame:
    line = QFrame(parent)
    line.setFrameShape(QFrame.VLine)
    line.setFixedWidth(1)
    line.setStyleSheet("color: #e2e5ea;")
    line.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
    return line

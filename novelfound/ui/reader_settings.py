# -*- coding: utf-8 -*-
"""阅读设置浮层（Aa）：字号 / 字体 / 行距 / 段距 / 页宽 / 缩进 / 主题 / 翻页方式。

替代原来摊在阅读器顶部工具栏里的那一排控件——沉浸阅读时它们太吵了。
浮层是阅读器画布的子控件（**不参与布局**，由 `ReaderView._layout_overlays()`
定位到右上角），改动**即时生效**：每个控件改完就写进 config 并发出 ``changed``，
由 `ReaderView` 负责重排重绘。

用法：
    popover = ReaderSettingsPopover(config, parent)
    popover.changed.connect(on_settings_changed)     # 阅读器重绘
    popover.closed.connect(on_closed)
    popover.sync_from_config()                       # 从配置刷新控件值
"""
from __future__ import annotations

from typing import Optional

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (QComboBox, QDoubleSpinBox, QFrame, QGridLayout,
                             QHBoxLayout, QLabel, QPushButton, QSpinBox,
                             QToolButton, QVBoxLayout, QWidget)

from .theme import READER_THEMES

# 常见的中文正文字体（空 = 跟随系统）
FONT_CHOICES = [
    ("跟随系统", ""),
    ("思源宋体 / 宋体", "Source Han Serif SC"),
    ("宋体", "SimSun"),
    ("楷体", "KaiTi"),
    ("黑体", "SimHei"),
    ("微软雅黑", "Microsoft YaHei UI"),
]


class ReaderSettingsPopover(QFrame):
    """阅读设置浮层（`Aa` 按钮弹出）。"""

    changed = pyqtSignal()      # 任一设置变了 → 阅读器重排重绘
    closed = pyqtSignal()

    def __init__(self, config, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.config = config
        self._syncing = False
        self.setObjectName("settingsPopover")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedWidth(300)

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(10)

        # ------------------------------------------------------------ 标题行
        head = QHBoxLayout()
        title = QLabel("阅读设置", self)
        title.setObjectName("drawerTitle")
        head.addWidget(title)
        head.addStretch(1)
        self.close_button = QPushButton("✕", self)
        self.close_button.setObjectName("ghost")
        self.close_button.setFixedWidth(26)
        self.close_button.setToolTip("关闭设置（Esc）")
        self.close_button.clicked.connect(self.close_popover)
        head.addWidget(self.close_button)
        root.addLayout(head)

        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(8)
        grid.setColumnStretch(1, 1)

        # ------------------------------------------------------------ 字号
        self.font_down = QToolButton(self)
        self.font_down.setText("A−")
        self.font_down.setToolTip("减小字号（Ctrl+-）")
        self.font_down.clicked.connect(lambda: self._step_font(-1))
        self.font_up = QToolButton(self)
        self.font_up.setText("A+")
        self.font_up.setToolTip("增大字号（Ctrl+=）")
        self.font_up.clicked.connect(lambda: self._step_font(1))
        self.font_size_label = QLabel("", self)
        self.font_size_label.setObjectName("muted")
        self.font_size_label.setAlignment(Qt.AlignCenter)
        font_row = QHBoxLayout()
        font_row.setSpacing(6)
        font_row.addWidget(self.font_down)
        font_row.addWidget(self.font_size_label, 1)
        font_row.addWidget(self.font_up)
        grid.addWidget(QLabel("字号", self), 0, 0)
        grid.addLayout(font_row, 0, 1)

        # ------------------------------------------------------------ 字体
        self.font_family_box = QComboBox(self)
        for name, family in FONT_CHOICES:
            self.font_family_box.addItem(name, family)
        self.font_family_box.currentIndexChanged.connect(self._on_font_family)
        grid.addWidget(QLabel("字体", self), 1, 0)
        grid.addWidget(self.font_family_box, 1, 1)

        # ------------------------------------------------------------ 行距 / 段距
        self.line_height_box = QDoubleSpinBox(self)
        self.line_height_box.setRange(1.0, 3.0)
        self.line_height_box.setSingleStep(0.1)
        self.line_height_box.setDecimals(1)
        self.line_height_box.valueChanged.connect(self._on_line_height)
        grid.addWidget(QLabel("行距", self), 2, 0)
        grid.addWidget(self.line_height_box, 2, 1)

        self.para_spacing_box = QSpinBox(self)
        self.para_spacing_box.setRange(0, 40)
        self.para_spacing_box.setSuffix(" px")
        self.para_spacing_box.valueChanged.connect(self._on_para_spacing)
        grid.addWidget(QLabel("段距", self), 3, 0)
        grid.addWidget(self.para_spacing_box, 3, 1)

        self.indent_box = QSpinBox(self)
        self.indent_box.setRange(0, 4)
        self.indent_box.setSuffix(" 字")
        self.indent_box.valueChanged.connect(self._on_indent)
        grid.addWidget(QLabel("首行缩进", self), 4, 0)
        grid.addWidget(self.indent_box, 4, 1)

        self.content_width_box = QSpinBox(self)
        self.content_width_box.setRange(600, 1200)
        self.content_width_box.setSingleStep(20)
        self.content_width_box.setSuffix(" px")
        self.content_width_box.valueChanged.connect(self._on_content_width)
        grid.addWidget(QLabel("正文宽度", self), 5, 0)
        grid.addWidget(self.content_width_box, 5, 1)

        # ------------------------------------------------------------ 主题
        self.theme_box = QComboBox(self)
        for key, theme in READER_THEMES.items():
            self.theme_box.addItem(theme["name"], key)
        self.theme_box.currentIndexChanged.connect(self._on_theme_changed)
        grid.addWidget(QLabel("背景", self), 6, 0)
        grid.addWidget(self.theme_box, 6, 1)

        # ------------------------------------------------------------ 翻页方式
        self.mode_button = QToolButton(self)
        self.mode_button.setCheckable(True)
        self.mode_button.setText("翻页模式")
        self.mode_button.setToolTip("切换 滚动 / 整页翻页（PageUp、PageDown、空格）")
        self.mode_button.toggled.connect(self._on_mode_toggled)
        grid.addWidget(QLabel("方式", self), 7, 0)
        grid.addWidget(self.mode_button, 7, 1)

        self.columns_box = QComboBox(self)
        self.columns_box.addItem("单页", 1)
        self.columns_box.addItem("左右双页", 2)
        self.columns_box.setToolTip("翻页模式下的排版；选「左右双页」会自动切到翻页模式")
        self.columns_box.currentIndexChanged.connect(self._on_columns_changed)
        grid.addWidget(QLabel("排版", self), 8, 0)
        grid.addWidget(self.columns_box, 8, 1)

        root.addLayout(grid)

        self.fullscreen_button = QToolButton(self)
        self.fullscreen_button.setText("沉浸式全屏（F11）")
        self.fullscreen_button.setCheckable(True)
        self.fullscreen_button.toggled.connect(self._on_fullscreen_toggled)
        root.addWidget(self.fullscreen_button)

        self.hint = QLabel("改动即时生效，不用点确定。", self)
        self.hint.setObjectName("paletteHint")
        root.addWidget(self.hint)

        self.sync_from_config()
        self.hide()

    # ------------------------------------------------------------------ 开关
    def open_popover(self) -> None:
        self.sync_from_config()
        self.show()
        self.raise_()

    def close_popover(self) -> None:
        if not self.isVisible():
            return
        self.hide()
        self.closed.emit()

    def toggle(self) -> None:
        if self.isVisible():
            self.close_popover()
        else:
            self.open_popover()

    # ------------------------------------------------------------------ 同步
    def sync_from_config(self) -> None:
        """把配置值刷到控件上（期间屏蔽信号，避免回写触发重绘）。"""
        self._syncing = True
        try:
            self.font_size_label.setText(f"{int(self.config.get('font_size'))} px")
            family_index = self.font_family_box.findData(
                self.config.get("font_family") or "")
            self.font_family_box.setCurrentIndex(max(0, family_index))
            self.line_height_box.setValue(float(self.config.get("line_height")))
            self.para_spacing_box.setValue(int(self.config.get("paragraph_spacing")))
            self.indent_box.setValue(int(self.config.get("first_line_indent") or 0))
            self.content_width_box.setValue(int(self.config.get("content_width") or 820))
            theme_index = self.theme_box.findData(self.config.get("reader_theme"))
            if theme_index >= 0:
                self.theme_box.setCurrentIndex(theme_index)
            page_mode = self.config.get("reader_mode") == "page"
            self.mode_button.setChecked(page_mode)
            self.mode_button.setText("翻页模式" if page_mode else "滚动模式")
            col_index = self.columns_box.findData(int(self.config.get("page_columns") or 1))
            if col_index >= 0:
                self.columns_box.setCurrentIndex(col_index)
            self.fullscreen_button.setChecked(
                bool(self.window() and self.window().isFullScreen()))
        finally:
            self._syncing = False

    # ------------------------------------------------------------------ 回调
    def _emit(self) -> None:
        if not self._syncing:
            self.changed.emit()

    def _step_font(self, delta: int) -> None:
        size = max(12, min(48, int(self.config.get("font_size")) + delta))
        if size == int(self.config.get("font_size")):
            return
        self.config.set("font_size", size)
        self.font_size_label.setText(f"{size} px")
        self._emit()

    def set_font_size(self, size: int) -> None:
        """外部改字号后同步显示（Ctrl+= 快捷键、工具栏按钮都会走这里）。"""
        self.font_size_label.setText(f"{max(12, min(48, int(size)))} px")

    def _on_font_family(self) -> None:
        self.config.set("font_family", self.font_family_box.currentData() or "")
        self._emit()

    def _on_line_height(self) -> None:
        self.config.set("line_height", round(float(self.line_height_box.value()), 2))
        self._emit()

    def _on_para_spacing(self) -> None:
        self.config.set("paragraph_spacing", int(self.para_spacing_box.value()))
        self._emit()

    def _on_indent(self) -> None:
        self.config.set("first_line_indent", int(self.indent_box.value()))
        self._emit()

    def _on_content_width(self) -> None:
        self.config.set("content_width", int(self.content_width_box.value()))
        self._emit()

    def _on_theme_changed(self) -> None:
        key = self.theme_box.currentData()
        if not key or key == self.config.get("reader_theme"):
            return
        self.config.set("reader_theme", key)
        self._emit()

    def _on_mode_toggled(self, checked: bool) -> None:
        self.mode_button.setText("翻页模式" if checked else "滚动模式")
        self.config.set("reader_mode", "page" if checked else "scroll")
        self._emit()

    def _on_columns_changed(self) -> None:
        """切换单页 / 左右双页；选双页时自动切到翻页模式。"""
        columns = int(self.columns_box.currentData() or 1)
        if columns == int(self.config.get("page_columns") or 1) and \
                not (columns == 2 and self.config.get("reader_mode") != "page"):
            return
        self.config.set("page_columns", columns)
        if columns == 2 and self.config.get("reader_mode") != "page":
            self.mode_button.setChecked(True)      # 会触发 _on_mode_toggled
            return
        self._emit()

    def _on_fullscreen_toggled(self, checked: bool) -> None:
        window = self.window()
        if window is None:
            return
        if checked:
            window.showFullScreen()
        else:
            window.showNormal()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key_Escape:
            self.close_popover()
            return
        super().keyPressEvent(event)

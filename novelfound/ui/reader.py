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

import time
from typing import List, Optional

from PyQt5.QtCore import QEvent, QPoint, QUrl, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import (QColor, QFont, QFontMetrics, QImage, QTextBlockFormat,
                         QTextCharFormat, QTextCursor, QTextDocument, QTextImageFormat)
from PyQt5.QtWidgets import (QComboBox, QFrame, QHBoxLayout, QLabel, QSizePolicy,
                             QTextBrowser, QToolButton, QVBoxLayout, QWidget)

from ..config import AppConfig
from ..models import ChapterContent
from .reader_settings import ReaderSettingsPopover
from .theme import READER_THEMES, reader_selection, reader_theme, theme_is_dark
from .widgets import AutoHideBar, ProgressLine

# 滚轮翻页参数：机械滚轮/触摸板常把"一格"拆成多个小事件发出来，
# 按事件翻页会一次跳好几屏（内容整段丢失），所以按格累计、一次只翻一屏。
WHEEL_NOTCH = 120            # 一格滚轮的 angleDelta
WHEEL_PIXEL_NOTCH = 60       # 像素级滚动（触摸板）折算成一格的像素数
WHEEL_COOLDOWN = 0.12        # 两次翻页的最小间隔（秒）
MAX_PAGES_PER_CHAPTER = 4000 # 分页循环的安全上限（防止异常输入死循环）

# 插图的显示高度上限（占页面高度的比例）：章首大图这类不能占满整页
IMAGE_CHAR = "\ufffc"          # 插图占位字符（与 local_parse.IMAGE_CHAR 一致）
IMAGE_MAX_HEIGHT_RATIO = 0.62


class ReaderView(QWidget):
    """阅读器主体。"""

    chapter_requested = pyqtSignal(int)        # 请求加载第 index 章
    back_requested = pyqtSignal()              # 返回详情
    catalog_requested = pyqtSignal()           # 打开目录抽屉（Ctrl+B）
    settings_changed = pyqtSignal()            # 字号/主题变化，需要写回配置
    theme_changed = pyqtSignal()               # 阅读背景主题变了（主窗口顶栏/状态栏要跟着换色）
    position_changed = pyqtSignal(int, object)  # (chapter_index, 进度字典)

    def __init__(self, config: AppConfig, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.config = config
        self.chapter_index = 0
        self.chapter_count = 0
        self.chapter_title = ""
        self._scroll_pos = 0
        self._paragraphs: List[str] = []
        self._images: Dict[int, bytes] = {}      # 内嵌插图（段落序号 → 图片字节）
        self._image_cache: Dict[tuple, object] = {}
        self._wheel_accum = 0          # 滚轮累计当量（凑够一格才翻页）
        self._wheel_pixels = 0         # 触摸板像素累计
        self._last_turn = 0.0          # 上次翻页时间（抑制驱动连发）
        self._positions: Dict[str, int] = {}   # 章节地址 -> 滚动位置（本次会话内记忆）
        self._current_url = ""
        # 分页/双页状态
        self._page_offsets: List[int] = []     # 每页的起始字符偏移（按行分页）
        self._text_length = 0                  # 整章显示文本的总长度
        self._spread = 0                       # 当前"屏"（单页=1 页，双页=2 页）
        self._two_page = False                 # 是否左右双页
        self._gutter = 64                      # 双页时两栏之间的间距
        self._relayout_timer = QTimer(self)
        self._relayout_timer.setSingleShot(True)
        self._relayout_timer.setInterval(160)
        self._relayout_timer.timeout.connect(self._relayout_pages)
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(600)
        self._save_timer.timeout.connect(self._emit_position)

        # -------------------------------------------------------------- 布局
        # 结构：canvas（占满） ├─ page（正文居中限宽）
        #                      ├─ top_bar / bottom_bar（浮条，不进布局，手动定位）
        #                      └─ progress_line（底部 2px 细线）
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.canvas = QWidget(self)
        root.addWidget(self.canvas, 1)
        canvas_layout = QVBoxLayout(self.canvas)
        canvas_layout.setContentsMargins(0, 0, 0, 0)
        canvas_layout.setSpacing(0)

        self.page = QWidget(self.canvas)
        page_layout = QHBoxLayout(self.page)
        page_layout.setContentsMargins(0, 0, 0, 0)
        page_layout.setSpacing(0)
        page_layout.addStretch(1)

        self.view = QTextBrowser(self.page)
        self.view.setFrameShape(QFrame.NoFrame)
        self.view.setOpenExternalLinks(False)
        # 只用 TextSelectableByMouse：**不要** TextSelectableByKeyboard——
        # 那个标志会让 Qt 在正文左上角画一个文本光标（用户看到的"待输入的竖线"），
        # 而它永远不会动、也不参与输入。鼠标选中/复制照常。
        self.view.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.view.setFocusPolicy(Qt.StrongFocus)
        self.view.setMouseTracking(True)
        self.view.viewport().setMouseTracking(True)
        self.view.verticalScrollBar().valueChanged.connect(self._on_scrolled)
        self.view.viewport().installEventFilter(self)
        self.view.installEventFilter(self)          # 按键要在这里拦（见 eventFilter）
        page_layout.addWidget(self.view, 0)

        # 右页整列（含栏间距）作为一个整体显示/隐藏，单页时完全不占宽度
        self.right_column = QWidget(self.page)
        right_layout = QHBoxLayout(self.right_column)
        right_layout.setContentsMargins(self._gutter, 0, 0, 0)
        right_layout.setSpacing(0)

        self.view2 = QTextBrowser(self.right_column)
        self.view2.setFrameShape(QFrame.NoFrame)
        self.view2.setOpenExternalLinks(False)
        self.view2.setTextInteractionFlags(Qt.TextSelectableByMouse)   # 同上：不要键盘光标
        self.view2.setFocusPolicy(Qt.StrongFocus)
        self.view2.setMouseTracking(True)
        self.view2.viewport().setMouseTracking(True)
        self.view2.viewport().installEventFilter(self)
        self.view2.installEventFilter(self)          # 按键要在这里拦
        right_layout.addWidget(self.view2)
        self.right_column.hide()
        page_layout.addWidget(self.right_column, 0)

        page_layout.addStretch(1)
        canvas_layout.addWidget(self.page, 1)

        # 章节标题（常驻在正文上方，安静的一行小字，切章时立刻能看出来）
        self.chapter_header = QLabel("", self.canvas)
        self.chapter_header.setObjectName("chapterHeader")
        self.chapter_header.setAlignment(Qt.AlignCenter)
        self.chapter_header.setTextInteractionFlags(Qt.TextSelectableByMouse)
        canvas_layout.insertWidget(0, self.chapter_header, 0)
        self._header_text = ""
        # 鼠标移到自己身上时也要能唤出顶部浮条（章节标题栏占住了顶部区域）
        for widget in (self.canvas, self.page, self.chapter_header):
            widget.setMouseTracking(True)
            widget.installEventFilter(self)

        # ------------------------------------------------------------ 顶部浮条
        self.top_bar = AutoHideBar(self.canvas, edge="top")
        bar = QHBoxLayout(self.top_bar)
        bar.setContentsMargins(10, 6, 10, 6)
        bar.setSpacing(6)

        self.back_button = QToolButton(self.top_bar)
        self.back_button.setText("‹ 返回")
        self.back_button.setToolTip("返回书籍详情 / 目录")
        self.back_button.clicked.connect(self.back_requested.emit)
        bar.addWidget(self.back_button)

        self.title_label = QLabel("", self.top_bar)
        self.title_label.setObjectName("readerTitle")
        self.title_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        bar.addWidget(self.title_label)
        bar.addStretch(1)

        self.catalog_button = QToolButton(self.top_bar)
        self.catalog_button.setText("目录")
        self.catalog_button.setToolTip("打开目录抽屉（Ctrl+B）")
        self.catalog_button.clicked.connect(self.catalog_requested.emit)
        bar.addWidget(self.catalog_button)

        # 阅读设置（字号/字体/行距/段距/页宽/缩进/主题/翻页方式）收进 Aa 浮层，
        # 沉浸阅读时顶部只剩「返回 · 书名 · 目录 · Aa · 全屏」，安静很多。
        self.settings_button = QToolButton(self.top_bar)
        self.settings_button.setText("Aa")
        self.settings_button.setToolTip("阅读设置（字号 / 主题 / 排版）")
        self.settings_button.clicked.connect(self.toggle_settings_popover)
        bar.addWidget(self.settings_button)

        self.fullscreen_button = QToolButton(self.top_bar)
        self.fullscreen_button.setText("全屏")
        self.fullscreen_button.setCheckable(True)
        self.fullscreen_button.setToolTip("沉浸阅读（F11）")
        self.fullscreen_button.toggled.connect(self._on_fullscreen_toggled)
        bar.addWidget(self.fullscreen_button)

        # ------------------------------------------------------------ 底部浮条
        self.bottom_bar = AutoHideBar(self.canvas, edge="bottom")
        bottom = QHBoxLayout(self.bottom_bar)
        bottom.setContentsMargins(12, 6, 12, 6)
        bottom.setSpacing(10)

        self.prev_button = QToolButton(self.bottom_bar)
        self.prev_button.setText("‹ 上一章")
        self.prev_button.clicked.connect(lambda: self._step(-1))
        bottom.addWidget(self.prev_button)

        self.status = QLabel("", self.bottom_bar)
        self.status.setObjectName("muted")
        self.status.setAlignment(Qt.AlignCenter)
        bottom.addWidget(self.status, 1)

        self.catalog_button = QToolButton(self.bottom_bar)
        self.catalog_button.setText("目录")
        self.catalog_button.setToolTip("打开目录抽屉（Ctrl+B）")
        self.catalog_button.clicked.connect(self.catalog_requested.emit)
        bottom.addWidget(self.catalog_button)

        self.next_button = QToolButton(self.bottom_bar)
        self.next_button.setText("下一章 ›")
        self.next_button.clicked.connect(lambda: self._step(1))
        bottom.addWidget(self.next_button)

        # ------------------------------------------------------------ 进度细线
        self.progress_line = ProgressLine(self.canvas)

        # 阅读设置浮层（Aa）：改完即时生效
        self.settings_popover = ReaderSettingsPopover(self.config, self.canvas)
        self.settings_popover.changed.connect(self._on_settings_changed)
        self.settings_popover.closed.connect(self._on_settings_closed)

        # 兼容旧名字（原来叫 toolbar）
        self.toolbar = self.top_bar
        self.body = self.canvas

        self._edge_zone = 24          # 鼠标距边缘多少像素内唤出浮条
        self.apply_settings()
        self._update_buttons()
        self._layout_overlays()

    # ------------------------------------------------------------------ 设置
    def _on_fullscreen_toggled(self, checked: bool) -> None:
        """顶部浮条上的「全屏」按钮（和设置浮层里那个同步）。"""
        window = self.window()
        if window is None:
            return
        if checked:
            window.showFullScreen()
        else:
            window.showNormal()

    def toggle_settings_popover(self) -> None:
        """点 Aa：开/关阅读设置浮层。"""
        if self.settings_popover.isVisible():
            self.settings_popover.close_popover()
        else:
            self.settings_popover.open_popover()
            self._layout_overlays()

    def _on_settings_changed(self) -> None:
        """浮层里改了任何设置：应用 + 重绘 + 通知主窗口写配置。"""
        self.apply_settings()
        self.render()
        self.settings_changed.emit()

    def _on_settings_closed(self) -> None:
        self.settings_button.setChecked(False)

    def apply_settings(self) -> None:
        """把配置应用到阅读器（字号、字体、行距、主题、模式、正文宽度）。"""
        previous_theme = getattr(self, "_applied_theme", None)
        self._font_size = int(self.config.get("font_size"))
        self._font_family = self.config.get("font_family") or ""
        self._line_height = float(self.config.get("line_height"))
        self._para_spacing = int(self.config.get("paragraph_spacing"))
        self._indent_chars = float(self.config.get("first_line_indent") or 0)
        self._theme_key = self.config.get("reader_theme")
        self._page_mode = self.config.get("reader_mode") == "page"
        self._two_page = int(self.config.get("page_columns") or 1) == 2
        self._content_width = max(480, int(self.config.get("content_width") or 820))

        theme = reader_theme(self._theme_key)
        # 选中高亮也跟着主题走，避免用 Qt 默认的系统蓝
        sel_bg, sel_fg = reader_selection(theme)
        view_css = (f"QTextBrowser {{ background: {theme['bg']}; border: none; "
                    f"color: {theme['fg']}; "
                    f"selection-background-color: {sel_bg}; "
                    f"selection-color: {sel_fg}; }}")
        self.view.setStyleSheet(view_css)
        self.view2.setStyleSheet(view_css)
        # 用 documentMargin 控制内边距（而不是 CSS padding），
        # 这样"段落坐标"与滚动位置一一对应，分页计算才准。
        for view in (self.view, self.view2):
            view.document().setDocumentMargin(26)
        # 阅读区整体跟随主题：正文限宽后，左右两侧也要是同一底色，
        # 否则夜间模式会出现一圈亮边。
        self.canvas.setStyleSheet(f"background: {theme['bg']};")
        self.page.setStyleSheet(f"background: {theme['bg']};")
        self._apply_content_width()

        # 浮条跟随阅读主题配色
        dark = theme_is_dark(theme)
        line = "rgba(255,255,255,0.14)" if dark else "rgba(0,0,0,0.08)"
        for bar in (self.top_bar, self.bottom_bar):
            border = ("border-bottom" if bar is self.top_bar else "border-top")
            bar.setStyleSheet(
                f"#autoHideBar {{ background: {theme['bg']}; {border}: 1px solid {line}; }}"
                f"#autoHideBar QToolButton {{ color: {theme['fg']}; }}"
                f"#autoHideBar QToolButton:hover {{ background: {line}; }}"
                f"#autoHideBar QLabel {{ color: {theme['muted']}; }}")
        self.progress_line.set_colors(line, theme["fg"])

        # 字号/主题/排版控件都收进了 Aa 浮层，这里只负责同步浮层显示
        self.view.setVerticalScrollBarPolicy(
            Qt.ScrollBarAlwaysOff if self._page_mode else Qt.ScrollBarAsNeeded)
        self.settings_popover.sync_from_config()
        self._applied_theme = self._theme_key
        if previous_theme is not None and previous_theme != self._theme_key:
            # 通知主窗口：顶栏/状态栏的底色要跟着换，否则夜间模式会出现"浅色外壳"
            self.theme_changed.emit()

    def _column_widths(self) -> tuple:
        """返回 (每栏宽度, 是否双页)。

        双页时**每栏都按"单页正文宽度"来**（默认 820），窗口不够宽才等比缩小，
        这样在宽屏上两栏都是完整的阅读宽度，而不是各占一半。
        """
        available = max(320, self.canvas.width() - 80) if self.canvas else 820
        two_page = bool(self._two_page and self._page_mode)
        if two_page:
            col = min(self._content_width, (available - self._gutter) // 2)
            return max(240, col), True
        return min(self._content_width, available), False

    def _apply_content_width(self) -> None:
        """把正文列宽设为 min(content_width, 可用宽度)，两侧留白由布局居中。"""
        width, two_page = self._column_widths()
        self.view.setFixedWidth(width)
        self.view2.setFixedWidth(width)
        if two_page:
            self.right_column.show()
        else:
            self.right_column.hide()

    def _apply_page_scrollbars(self) -> None:
        """翻页模式下**始终隐藏滚动条**（宽度必须恒定）。

        这一条是真踩过坑的：早先做"某页比视口高就打开滚动条"的兜底，结果
        滚动条占掉 ~10px → viewport 变窄 → 文字重排成更多行 → 更溢出 →
        滚动条一直开着（**粘住**）；而分页是按"没有滚动条"的宽度算的，
        于是底部几行被裁掉、看起来像**中间丢了一段内容**。
        现在一律 AlwaysOff：宽度稳定，分页量与显示量一致。
        真遇到比一页还高的单块（超大插图），分页会给它单独一页，
        插图本身也有高度上限（``IMAGE_MAX_HEIGHT_RATIO``）。
        """
        for view in (self.view, self.view2):
            if self._page_mode:
                view.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            else:
                view.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)

    def _layout_overlays(self) -> None:
        """把上下浮条、进度线、阅读设置浮层贴到画布边缘（它们都不参与布局）。"""
        width = max(1, self.canvas.width())
        height = max(1, self.canvas.height())
        top_h = max(38, self.top_bar.sizeHint().height())
        bottom_h = max(38, self.bottom_bar.sizeHint().height())
        self.top_bar.setGeometry(0, 0, width, top_h)
        self.bottom_bar.setGeometry(0, height - bottom_h - 2, width, bottom_h)
        self.progress_line.setGeometry(0, height - 2, width, 2)
        self.progress_line.raise_()

        # 阅读设置浮层：挂在右上角（Aa 按钮下方）
        popover = self.settings_popover
        popover.adjustSize()
        pop_h = min(popover.sizeHint().height(), max(200, height - top_h - 24))
        popover.setGeometry(max(8, width - popover.width() - 12),
                            top_h + 6, popover.width(), pop_h)
        if popover.isVisible():
            popover.raise_()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._apply_content_width()
        self._apply_header_text()
        self._layout_overlays()
        # 尺寸变了，分页要重算（防抖，避免拖动窗口时反复计算）
        if self._page_mode and self._paragraphs:
            self._relayout_timer.start()

    def showEvent(self, event) -> None:  # noqa: N802
        """切到阅读页时重算一次列宽（此时才知道真实可用宽度）。"""
        super().showEvent(event)
        self._apply_content_width()
        self._layout_overlays()

    # ------------------------------------------------------------ 浮条交互
    def show_bars(self) -> None:
        """显示上下浮条（鼠标靠近边缘时调用）。"""
        self.top_bar.show_bar()
        self.bottom_bar.show_bar()

    def hide_bars(self) -> None:
        """立即隐藏上下浮条。"""
        self.top_bar.hide_bar()
        self.bottom_bar.hide_bar()

    def bars_visible(self) -> bool:
        return self.top_bar.is_bar_visible() or self.bottom_bar.is_bar_visible()

    def notify_mouse(self, y: float) -> None:
        """鼠标在正文区移动：靠近上下边缘则唤出对应浮条。"""
        height = max(1, self.canvas.height())
        if y <= self._edge_zone:
            self.top_bar.show_bar()
        elif y >= height - self._edge_zone:
            self.bottom_bar.show_bar()
        else:
            self.top_bar.maybe_auto_hide()
            self.bottom_bar.maybe_auto_hide()

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        views = {self.view.viewport(): self.view, self.view2.viewport(): self.view2}
        view = views.get(obj)
        # 按键：正文控件（view/view2 本身）会吃掉方向键，必须在它之前拦下来
        if event.type() == QEvent.KeyPress and obj in (self.view, self.view2):
            if self._handle_key(event.key(), event.modifiers()):
                event.accept()
                return True
        if event.type() == QEvent.MouseMove and (
                view is not None or obj in (self.canvas, self.page, self.chapter_header)):
            try:
                self.notify_mouse(obj.mapTo(self.canvas, event.pos()).y())
            except RuntimeError:
                return super().eventFilter(obj, event)
        elif event.type() == QEvent.Leave:
            self.top_bar.maybe_auto_hide()
            self.bottom_bar.maybe_auto_hide()
        elif event.type() == QEvent.MouseButtonPress and \
                self.settings_popover.isVisible():
            # 点正文/画布任意处即收起设置浮层（浮层自己的点击事件传不到这里，
            # 所以点浮层内部不会误关）。Esc 也能关。
            self.settings_popover.close_popover()
        elif view is not None and event.type() == QEvent.Wheel and self._page_mode:
            # 翻页模式下，滚轮在正文上也应该整屏翻页；
            # 只有"某段比整页还高、需要页内滚动"时才让视图自己滚（安全阀，避免卡住）
            bar = view.verticalScrollBar()
            delta = event.angleDelta().y()
            if bar.maximum() > 0 and ((delta < 0 and bar.value() < bar.maximum())
                                      or (delta > 0 and bar.value() > 0)):
                return False
            return self._turn_from_wheel(delta, event.pixelDelta().y())
        return super().eventFilter(obj, event)

    def change_font_size(self, delta: int) -> None:
        """快捷键 / 浮层按钮改字号（Ctrl+= / Ctrl+-）。"""
        size = max(12, min(48, int(self.config.get("font_size")) + delta))
        if size == int(self.config.get("font_size")):
            return
        self.config.set("font_size", size)
        self.apply_settings()
        self.settings_popover.set_font_size(size)
        self.render()
        self.settings_changed.emit()

    # ------------------------------------------------------------------ 内容
    def set_book(self, chapter_count: int, chapter_index: int = 0) -> None:
        self.chapter_count = max(0, chapter_count)
        self.chapter_index = max(0, chapter_index)
        self._update_buttons()

    def remember_position(self) -> None:
        """把当前阅读位置记到当前章节名下（切章/清空视图前调用）。"""
        if self._current_url:
            self._positions[self._current_url] = self.reading_position()

    def position_for(self, chapter_url: str) -> Optional[dict]:
        """该章节上次读到的位置（本次会话记忆）。"""
        return self._positions.get(chapter_url)

    def set_loading(self, title: str = "") -> None:
        self.remember_position()
        self._set_header(title)
        self.title_label.setText(title or "加载中…")
        self.status.setText("正在获取章节内容…")
        self.view.setPlainText("")
        self.view2.setPlainText("")
        self._page_offsets = []
        self._spread = 0

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
                    position: Optional[dict] = None) -> None:
        """展示章节内容。

        ``position`` 是进度字典（``scroll_pos`` / ``block_index`` / ``page_index``）：
        滚动模式看 scroll_pos，翻页/双页模式看 block_index（跨尺寸、跨排版都能用）。
        """
        # 同一章重新渲染（换字号/换主题）时保留位置；换章的位置已在 set_loading 里记过
        if self._current_url and self._current_url == content.url:
            self._positions[self._current_url] = self.reading_position()
        self._current_url = content.url
        if index is not None:
            self.chapter_index = index
        self.chapter_title = content.title
        self.title_label.setText(content.title)
        self._set_header(content.title, index)
        # 章首在正文里也放一个标题（大字号居中），只有本章第一段是它
        paragraphs = list(content.paragraphs)
        images = dict(getattr(content, "images", {}) or {})
        title = (content.title or "").strip()
        if title:
            paragraphs = [title] + paragraphs
            # 注意：插图的键是"段落序号"，插入标题后要整体 +1，否则会错位
            images = {index + 1: data for index, data in images.items()}
        self._paragraphs = paragraphs
        self._images = images
        self._image_cache.clear()
        self._block_count = len(self._paragraphs)
        self._char_count = content.char_count
        self._from_cache = content.from_cache

        self._render_full()
        self._restore_position(position)
        self._update_buttons()
        self._update_status()
        if self.chapter_count:
            self.progress_line.set_progress((self.chapter_index + 1) / self.chapter_count)

    # ------------------------------------------------------------ 位置读写
    def reading_position(self) -> dict:
        """当前阅读位置（用于持久化）。

        ``char_offset`` 是跨排版、跨尺寸都稳定的锚点（整章显示文本的字符偏移），
        另外保留 ``block_index`` / ``page_index`` 便于兼容旧进度与调试。
        """
        offset = self._current_offset()
        return {"scroll_pos": self._scroll_pos if not self._page_mode else 0,
                "char_offset": offset,
                "block_index": self._block_for_offset(offset),
                "page_index": self._spread if self._page_mode else -1}

    def _restore_position(self, position: Optional[dict]) -> None:
        position = position or {}
        if self._page_mode:
            self._show_spread(self._spread_for_position(position))
            return
        scroll = int(position.get("scroll_pos", 0) or 0)
        offset = int(position.get("char_offset", -1) or -1)
        if scroll > 0:
            self._restore_scroll(scroll)
        elif offset > 0:
            self._scroll_to_offset(offset)
        else:
            self.view.verticalScrollBar().setValue(0)

    def _update_status(self) -> None:
        parts = [f"第 {self.chapter_index + 1} / {self.chapter_count} 章"]
        if self._page_mode and self._page_offsets:
            parts.append(f"{self._spread + 1} / {self.spread_count()} 页")
        parts.append(f"{self._char_count} 字")
        if self._from_cache:
            parts.append("来自缓存")
        self.status.setText("　·　".join(parts))

    def _set_header(self, title: str, index: Optional[int] = None) -> None:
        """更新正文上方的章节标题（太长时按宽度省略）。"""
        index = self.chapter_index if index is None else index
        title = (title or "").strip()
        if title.startswith("第") or not title:
            text = title or f"第 {index + 1} 章"
        else:
            text = f"第 {index + 1} 章 · {title}"
        self._header_text = text
        self._apply_header_text()
        self.chapter_header.setToolTip(text)

    def _apply_header_text(self) -> None:
        metrics = QFontMetrics(self.chapter_header.font())
        available = max(120, self.canvas.width() - 48) if self.canvas else 600
        self.chapter_header.setText(
            metrics.elidedText(self._header_text, Qt.ElideRight, available))

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
        """重绘当前章节（切换主题 / 调整字号 / 换排版后调用）。

        不传 paragraphs 时用已缓存的段落；重绘后尽量停在原来读到的位置。
        """
        if paragraphs is not None:
            self._paragraphs = list(paragraphs)
        if not self._paragraphs:
            return
        keep_offset = self._current_offset()
        self._render_full()
        if self._page_mode:
            self._show_spread(self._spread_for_offset(keep_offset))
        else:
            self._scroll_to_offset(keep_offset)
        self._update_status()

    # ------------------------------------------------------------ 渲染与分页
    def _render_full(self) -> None:
        """整章渲染进主视图（同时为分页提供测量数据）。"""
        self._block_count = len(self._paragraphs)
        self._text_length = (sum(len(p) for p in self._paragraphs)
                             + max(0, len(self._paragraphs) - 1))
        # 整章的第一段就是章节大标题
        self._render_into(self.view, self._paragraphs, heading_first=True)
        if self._page_mode:
            self._paginate()

    def _block_formats(self, font: QFont, theme: Dict[str, str]) -> tuple:
        """构造正文 / 续段 / 章节标题三种块格式与字符格式。"""
        body = QTextBlockFormat()
        body.setLineHeight(self._line_height * 100, QTextBlockFormat.ProportionalHeight)
        body.setBottomMargin(self._para_spacing)
        body.setTopMargin(0)
        body.setTextIndent(self._indent_width(font))

        # 续页片段：段首缩进要去掉，否则每页开头都会缩进
        continuation = QTextBlockFormat(body)
        continuation.setTextIndent(0)

        # 插图段落：居中、不带缩进，**并且不能套用比例行距**。
        # 图片那"一行"的 line.height() 就是图片高度（600px），
        # 若按比例行距 190% 算，这一行的步进会变成 1140px：图片下方凭空多出
        # 540px 空白，而且分页以为它高 1140 → 图片被挤到单独一页（落到右栏）。
        image_block = QTextBlockFormat(body)
        image_block.setAlignment(Qt.AlignCenter)
        image_block.setTextIndent(0)
        image_block.setTopMargin(0)
        image_block.setBottomMargin(self._para_spacing)
        image_block.setLineHeight(100, QTextBlockFormat.SingleHeight)

        heading = QTextBlockFormat(body)
        heading.setAlignment(Qt.AlignCenter)
        heading.setTextIndent(0)
        heading.setBottomMargin(self._para_spacing * 3)

        char = QTextCharFormat()
        char.setFont(font)
        char.setForeground(QColor(theme["fg"]))

        heading_font = QFont(font)
        heading_font.setPointSize(max(self._font_size + 4, 14))
        heading_font.setBold(True)
        heading_char = QTextCharFormat()
        heading_char.setFont(heading_font)
        heading_char.setForeground(QColor(theme["fg"]))
        return body, continuation, image_block, heading, char, heading_char

    def _render_into(self, view, paragraphs: List[str],
                     heading_first: bool = False) -> None:
        """把整段段落写进视图（用于整章测量）。"""
        fragments = [(i, 0, len(p)) for i, p in enumerate(paragraphs)]
        self._render_fragments(view, fragments, paragraphs, heading_first)

    def _render_fragments(self, view, fragments: List[tuple],
                          paragraphs: List[str], heading_first: bool = False) -> None:
        """把若干"段落片段"写进视图。

        片段是 ``(段落序号, 段内起始字符, 段内结束字符)``：
        按行分页时一段可能被拆到两页，续页部分不带首行缩进。
        """
        theme = reader_theme(self._theme_key)
        font = QFont()
        if self._font_family:
            font.setFamily(self._font_family)
        font.setPointSize(self._font_size)
        (body, continuation, image_block, heading, char, heading_char) = \
            self._block_formats(font, theme)

        document = view.document()
        document.setDefaultFont(font)
        cursor = QTextCursor(document)
        cursor.beginEditBlock()
        cursor.select(QTextCursor.Document)
        cursor.removeSelectedText()
        for index, (block_index, start, end) in enumerate(fragments):
            if index:
                cursor.insertBlock()
            image = self._image_for(block_index, start, end)
            if image is not None:
                # 内嵌插图：整段就一个对象替换符（只占 1 个字符，偏移不受影响）
                cursor.setBlockFormat(image_block)
                cursor.setCharFormat(char)
                name = self._image_key(block_index)
                # **必须把图片注册成文档资源**：QTextImageFormat 里只放名字，
                # 文档按名字查资源，查不到就画"图片缺失"的破图图标（真实踩过）。
                document.addResource(QTextDocument.ImageResource, QUrl(name), image)
                fmt = QTextImageFormat()
                fmt.setName(name)
                scale = self._image_scale(image)
                fmt.setWidth(max(1.0, image.width() * scale))
                fmt.setHeight(max(1.0, image.height() * scale))
                cursor.insertImage(fmt)
                continue
            if heading_first and block_index == 0 and start == 0:
                cursor.setBlockFormat(heading)
                cursor.setCharFormat(heading_char)
            elif start > 0:
                cursor.setBlockFormat(continuation)   # 续页片段
                cursor.setCharFormat(char)
            else:
                cursor.setBlockFormat(body)
                cursor.setCharFormat(char)
            cursor.insertText(paragraphs[block_index][start:end])
        cursor.endEditBlock()
        view.moveCursor(QTextCursor.Start)
        view.verticalScrollBar().setValue(0)

    # -------------------------------------------------------------- 内嵌插图
    def _image_for(self, block_index: int, start: int, end: int):
        """这一片段是不是"整段一张图"？是就返回 QImage（带缓存）。"""
        if not self._images or block_index not in self._images:
            return None
        if end - start != 1:
            return None                     # 被拆开的图片片段不画（正常不会发生）
        return self._cached_image(block_index)

    def _image_key(self, block_index: int) -> str:
        return f"local-image://{self._current_url}/{block_index}"

    def _cached_image(self, block_index: int):
        """按图片字节缓存解码结果（同一张图可能被上千章引用，不能重复解码）。"""
        key = (self._current_url, block_index)
        cached = self._image_cache.get(key)
        if cached is not None:
            return cached
        data = self._images.get(block_index) or b""
        image = QImage()
        if data:
            image.loadFromData(data)
        self._image_cache[key] = image
        # 同一张图在不同章节里会重复出现，按数据指纹再缓存一份
        if data and len(self._image_cache) < 64:
            digest = hash(data)
            self._image_cache.setdefault(("__digest__", digest), image)
        return image

    def _image_scale(self, image) -> float:
        """图片显示比例：小图原尺寸，大图缩到**列宽与页面高度**以内。

        高度上限很重要：有些书每章开头都放一张大图（实测一本书 600×600 的章首图
        出现在 1394 章里），不限制就会占满整页、甚至比一页还高。
        """
        if image.isNull() or image.width() <= 0 or image.height() <= 0:
            return 1.0
        avail_w = max(120, self._column_widths()[0] - 16)
        avail_h = max(120, int(self.view.viewport().height() * IMAGE_MAX_HEIGHT_RATIO))
        return min(1.0, avail_w / float(image.width()), avail_h / float(image.height()))

    def _line_advance(self, block_layout, line_index: int,
                      block_span: float = 0.0, is_image: bool = False) -> float:
        """第 line_index 行占用的高度（含行距）。

        Qt 的比例行距下，``QTextLine.height()`` 只是**文字本身**的高度，
        行与行之间真正的步进是它乘以行距倍数。

        * **图片行**（``is_image=True``）：直接返回 ``line.height()``，就是图片高度。
          图片不是文字，**绝不能乘行距倍数**——否则 600px 的插图会被算成 1140px：
          图片下方凭空多出 540px 空白，分页还以为它放不下、把它挤到单独一页
          （表现就是"图标跑到右栏、左栏下半截空着"）。
        * 文字中间行：用相邻行的 ``y`` 差（最准）。
        * 文字块内最后一行：用行距模型。**不要**用"块占位反推"——
          ``blockBoundingRect`` 在比例行距下不含行距，而文档末尾那块还会用到
          **可能过期的** ``document.size()``，反推值偏小 → 一页多塞一两行、
          底部被裁掉（用户实测"内容缺了一段"就是这么来的）。
          模型值（实测 55 vs 真实 51）略偏保守，宁可少放一行也不裁字。
        """
        count = block_layout.lineCount()
        line = block_layout.lineAt(line_index)
        if is_image:
            return line.height()
        if line_index + 1 < count:
            return block_layout.lineAt(line_index + 1).y() - line.y()
        return line.height() * max(1.0, float(self._line_height))

    def _block_span(self, document, layout, block_index: int) -> float:
        """一个块真正占用的高度（不含它自己的下间距）。

        = 下一块的顶部 - 本块顶部 - 本块下间距；最后一块用文档高度反推。
        """
        block = document.findBlockByNumber(block_index)
        top = layout.blockBoundingRect(block).top()
        if block_index + 1 < document.blockCount():
            nxt = document.findBlockByNumber(block_index + 1)
            span = (layout.blockBoundingRect(nxt).top() - top
                    - block.blockFormat().bottomMargin())
            if span > 0:
                return span
        margin = document.documentMargin()
        span = document.size().height() - margin - top
        if span > 0:
            return span
        return max(0.0, layout.blockBoundingRect(block).height())

    def _paginate(self) -> None:
        """按行分页：**渲染候选页并量它自己的行**，得出每页起始偏移。

        为什么不用"整篇文档的行坐标"推算（老做法）：整篇文档里排的是**完整段落**，
        而页面上是**段落片段**（续页片段不带首行缩进 → 断行位置可能不同），
        图片块的真实高度也只有渲染出来才知道。实测这种差异会让个别页面多塞
        ~100px（约 2 行），底部被裁掉——用户看到的就是"中间少了一段内容"。
        所以这里逐页渲染、逐行量：一页渲染一次，代价可控，但结果**与实际显示一致**。
        """
        document = self.view.document()
        margin = document.documentMargin()
        available = self.view.viewport().height() - 2 * margin - 1
        total = self._text_length
        if total <= 0 or available < 60:
            self._page_offsets = [0]
            return

        offsets = [0]
        # 各段在"全章文本"里的起始偏移（段落之间用 \n 连接，和 _fragments_for_range 一致）
        para_starts: List[int] = []
        position = 0
        for para in self._paragraphs:
            para_starts.append(position)
            position += len(para) + 1

        chars_guess = max(400, int(available * 1.2))
        while offsets[-1] < total and len(offsets) <= MAX_PAGES_PER_CHAPTER:
            start = offsets[-1]
            # 先渲染一个"足够宽"的候选范围（按上一页的字数外推，多给一倍量），
            # 再在渲染结果上找真正放得下的最后一行
            window = min(total, start + max(64, int(chars_guess * 2)))
            fragments = self._fragments_for_range(start, window)
            if not fragments:
                break
            # heading_first 必须与实际显示页一致：页面渲染时每页都传 True
            # （内部条件 start == 0 才真的用章首格式）。不一致的话分页按正文字体
            # 量、实际按大字号画，最后一页就会溢出被裁。
            self._render_fragments(self.view, fragments, self._paragraphs,
                                   heading_first=True)
            page_end = self._last_fitting_offset(fragments, para_starts, start,
                                                 available, margin)
            if page_end <= start:
                page_end = start + 1        # 单行/单图都放不下时至少前进一个字，避免死循环
            if page_end >= total:
                break                       # 本页已经到章末：**不要再加一个等于总长的偏移**，
                                            # 否则会多出一个 [总长, 总长) 的空白尾页
            offsets.append(page_end)
            chars_guess = max(64, int((offsets[-1] - start) * 1.1))
        self._page_offsets = offsets

    def _last_fitting_offset(self, fragments: List[tuple], para_starts: List[int],
                             start: int, available: float, margin: float) -> int:
        """在当前**已渲染页面**上，返回最后一行放得下时的**全章字符偏移**。

        页顶就是 ``documentMargin``（每页都是独立渲染的文档）。
        注意坐标系：页面文档里的 ``block.position()`` 是**页内坐标**，
        要按"第几个片段 + 片段在段落里的起点"映射回全章偏移。
        """
        document = self.view.document()
        layout = document.documentLayout()
        best = start
        for index, (para_index, frag_start, _frag_end) in enumerate(fragments):
            block = document.findBlockByNumber(index)
            if block is None:
                break
            # 先量 blockBoundingRect：Qt 布局是按需的，这一步才会算出行坐标
            rect = layout.blockBoundingRect(block)
            block_layout = block.layout()
            if block_layout is None or not block_layout.lineCount():
                continue
            span = self._block_span(document, layout, index)
            base = para_starts[para_index] + frag_start
            is_image = block.text() == IMAGE_CHAR
            for line_index in range(block_layout.lineCount()):
                line = block_layout.lineAt(line_index)
                # 图片那一行的高度就是图片本身的高度，**不乘行距倍数**
                advance = self._line_advance(block_layout, line_index, span, is_image)
                if rect.top() + line.y() + advance - margin > available:
                    return best
                best = base + line.textStart() + line.textLength()
        return best

    def _page_size(self) -> int:
        """一屏几页：单页 1，左右双页 2。"""
        return 2 if (self._two_page and self._page_mode) else 1

    def spread_count(self) -> int:
        """一屏的总数（单页 = 页数，双页 = 页数/2 向上取整）。"""
        size = self._page_size()
        pages = len(self._page_offsets)
        return max(1, (pages + size - 1) // size) if pages else 1

    def _page_range(self, page: int) -> tuple:
        """第 page 页的字符区间 [start, end)。"""
        offsets = self._page_offsets
        start = offsets[page] if 0 <= page < len(offsets) else self._text_length
        end = offsets[page + 1] if 0 <= page + 1 < len(offsets) else self._text_length
        return start, end

    def _fragments_for_range(self, start: int, end: int) -> List[tuple]:
        """把字符区间切成段落片段，保留段落结构。"""
        fragments: List[tuple] = []
        pos = 0
        for i, para in enumerate(self._paragraphs):
            para_end = pos + len(para)
            if para_end > start and pos < end:
                s = max(0, start - pos)
                e = min(len(para), end - pos)
                if e > s:
                    fragments.append((i, s, e))
            pos = para_end + 1
            if pos >= end:
                break
        return fragments

    def _show_spread(self, spread: int) -> None:
        """显示第 spread 屏（左页 = spread*size，右页 = 左页 + 1）。"""
        if not self._page_offsets:
            return
        size = self._page_size()
        self._spread = max(0, min(spread, self.spread_count() - 1))
        left = self._spread * size
        start, end = self._page_range(left)
        self._render_fragments(self.view, self._fragments_for_range(start, end),
                               self._paragraphs, heading_first=True)
        if size == 2:
            if left + 1 < len(self._page_offsets):
                start2, end2 = self._page_range(left + 1)
                self._render_fragments(self.view2,
                                       self._fragments_for_range(start2, end2),
                                       self._paragraphs, heading_first=True)
            else:
                self._render_into(self.view2, [])   # 章末：右页留空
        self._apply_page_scrollbars()
        self._update_status()
        self._save_timer.start()

    def _page_for_offset(self, offset: int) -> int:
        """包含指定字符偏移的页序号。"""
        page = 0
        for i, start in enumerate(self._page_offsets):
            if start <= offset:
                page = i
            else:
                break
        return page

    def _spread_for_offset(self, offset: int) -> int:
        return self._page_for_offset(offset) // self._page_size()

    def _spread_for_position(self, position: Optional[dict]) -> int:
        position = position or {}
        offset = int(position.get("char_offset", -1) or -1)
        if offset >= 0:
            return self._spread_for_offset(offset)
        # 兼容旧进度：用段落序号换算成字符偏移
        block = int(position.get("block_index", -1) or -1)
        if block >= 0:
            return self._spread_for_offset(self._offset_for_block(block))
        page = int(position.get("page_index", -1) or -1)
        return page if page >= 0 else 0

    def _offset_for_block(self, block: int) -> int:
        """段落序号 → 该段首字符的全局偏移。"""
        if block <= 0:
            return 0
        block = min(block, len(self._paragraphs))
        return sum(len(p) + 1 for p in self._paragraphs[:block])

    def _block_for_offset(self, offset: int) -> int:
        """全局字符偏移 → 所在段落序号。"""
        pos = 0
        for i, para in enumerate(self._paragraphs):
            if pos + len(para) > offset:
                return i
            pos += len(para) + 1
        return max(0, len(self._paragraphs) - 1)

    def _current_offset(self) -> int:
        """当前屏最上方的字符偏移。"""
        if self._page_mode and self._page_offsets:
            return self._page_offsets[min(self._spread * self._page_size(),
                                          len(self._page_offsets) - 1)]
        return self._first_visible_offset()

    def _first_visible_offset(self) -> int:
        try:
            return self.view.cursorForPosition(QPoint(2, 2)).position()
        except Exception:  # noqa: BLE001 - 文档未就绪
            return 0

    def _offset_top(self, offset: int) -> int:
        """字符偏移在文档中的 y 坐标（按行对齐）。"""
        document = self.view.document()
        offset = max(0, min(offset, max(0, document.characterCount() - 1)))
        cursor = QTextCursor(document)
        cursor.setPosition(offset)
        block = cursor.block()
        rect = document.documentLayout().blockBoundingRect(block)
        y = rect.top()
        block_layout = block.layout()
        if block_layout is not None and block_layout.lineCount():
            line = block_layout.lineForTextPosition(offset - block.position())
            if line.isValid():
                y += line.y()
        return int(y)

    def _scroll_to_offset(self, offset: int) -> None:
        self._restore_scroll(max(0, self._offset_top(offset)))

    def _relayout_pages(self) -> None:
        """窗口/字号变化后重新分页，并停在原来读到的位置。"""
        if not (self._page_mode and self._paragraphs):
            return
        offset = self._current_offset()
        self._render_full()
        self._show_spread(self._spread_for_offset(offset))

    def current_spread_offsets(self) -> tuple:
        """当前屏左右两页的起始字符偏移（右页为 -1 表示没有右页）。"""
        if not self._page_offsets:
            return (0, -1)
        size = self._page_size()
        left = self._spread * size
        left_start = (self._page_offsets[left]
                      if left < len(self._page_offsets) else -1)
        right_start = (self._page_offsets[left + 1]
                       if size == 2 and left + 1 < len(self._page_offsets) else -1)
        return (left_start, right_start)

    def paragraphs_in_page(self, page: int) -> int:
        """第 page 页涉及的段落数量（用于自检）。"""
        start, end = self._page_range(page)
        return len(self._fragments_for_range(start, end))

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
        """翻页模式：走下一屏（双页一次两页）；到章末则进入下一章。"""
        if self._page_mode:
            if self._spread + 1 < self.spread_count():
                self._show_spread(self._spread + 1)
            elif self.chapter_index < self.chapter_count - 1:
                self.chapter_requested.emit(self.chapter_index + 1)
            return
        bar = self.view.verticalScrollBar()
        if bar.value() >= bar.maximum() - 4:
            if self.chapter_index < self.chapter_count - 1:
                self.chapter_requested.emit(self.chapter_index + 1)
            return
        bar.setValue(min(bar.maximum(), bar.value() + self.page_step()))

    def prev_page(self) -> None:
        """翻页模式：走上一屏；到章首则进入上一章。"""
        if self._page_mode:
            if self._spread > 0:
                self._show_spread(self._spread - 1)
            elif self.chapter_index > 0:
                self.chapter_requested.emit(self.chapter_index - 1)
            return
        bar = self.view.verticalScrollBar()
        if bar.value() <= 4:
            if self.chapter_index > 0:
                self.chapter_requested.emit(self.chapter_index - 1)
            return
        bar.setValue(max(0, bar.value() - self.page_step()))

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if self._handle_key(event.key(), event.modifiers()):
            event.accept()
            return
        super().keyPressEvent(event)

    def _handle_key(self, key: int, modifiers) -> bool:
        """阅读器按键：**只保留方向键翻页 + Ctrl+方向键换章**，返回是否已处理。

        为什么要在 :meth:`eventFilter` 里也调用它：正文控件（``QTextBrowser``）
        持有焦点时，按键会先被它吃掉（它在 read-only 下也会消费方向键/翻页键），
        父控件的 ``keyPressEvent`` 根本收不到 —— 实测把 PageDown 发给正文控件，
        页码一动不动。所以按键必须在**控件这一层**拦下来转发给阅读器。
        """
        ctrl = bool(modifiers & Qt.ControlModifier)
        alt = bool(modifiers & Qt.AltModifier)
        if alt:
            return False
        if ctrl:
            # 换章：Ctrl+← / Ctrl+→
            if key == Qt.Key_Left:
                self._step(-1)
                return True
            if key == Qt.Key_Right:
                self._step(1)
                return True
            return False
        if key in (Qt.Key_Left, Qt.Key_Up):
            self.prev_page()
            return True
        if key in (Qt.Key_Right, Qt.Key_Down):
            self.next_page()
            return True
        if key == Qt.Key_Escape:
            if self.settings_popover.isVisible():
                self.settings_popover.close_popover()      # 先收设置浮层
            elif self.fullscreen_button.isChecked():
                self.fullscreen_button.setChecked(False)
            else:
                self.back_requested.emit()
            return True
        return False

    def wheelEvent(self, event) -> None:  # noqa: N802
        # 滚动模式下正常滚动；翻页模式下滚轮也整页跳动，手感更接近翻页
        if self._page_mode:
            self._turn_from_wheel(event.angleDelta().y(), event.pixelDelta().y())
            event.accept()
            return
        super().wheelEvent(event)

    def _turn_from_wheel(self, delta: int, pixel: int = 0) -> bool:
        """把滚轮量**按"格"累计**后翻页，返回是否消费了这次事件。

        为什么不能"来一个事件就翻一屏"：滚轮驱动和触摸板经常把**一次滚动
        拆成多个小事件**（-60 + -60，甚至 -30×4），按事件翻页就会一次跳过
        好几屏——表现就是"滑一下过去了两页、中间整段内容不见了"（真实踩过，
        TXT / EPUB 都会）。所以：
        * 累计到一格（``WHEEL_NOTCH``）才翻**一屏**，多余的当量直接丢掉（绝不连跳）；
        * 同一格被拆成的连发事件用 ``WHEEL_COOLDOWN`` 抑制，避免重复翻转。
        """
        if delta:
            self._wheel_accum += delta
            steps = int(self._wheel_accum / WHEEL_NOTCH)
            self._wheel_accum -= steps * WHEEL_NOTCH
        elif pixel:
            self._wheel_pixels += pixel
            if abs(self._wheel_pixels) < WHEEL_PIXEL_NOTCH:
                return True                     # 触摸板：攒够一格像素再翻
            steps = 1 if self._wheel_pixels > 0 else -1
            self._wheel_pixels = 0
        else:
            return True
        if not steps:
            return True                         # 还没凑够一格，先攒着
        now = time.monotonic()
        if now - self._last_turn < WHEEL_COOLDOWN:
            return True                         # 同一格的连发事件，只翻一次
        self._last_turn = now
        if steps < 0:
            self.next_page()
        else:
            self.prev_page()
        return True

    # ------------------------------------------------------------ 位置记忆
    def _on_scrolled(self, value: int) -> None:
        if self._page_mode:
            return          # 翻页模式下滚动条不用（每屏独立渲染）
        self._scroll_pos = value
        self._save_timer.start()

    def _emit_position(self) -> None:
        self.position_changed.emit(self.chapter_index, self.reading_position())

    def current_position(self) -> int:
        return self._scroll_pos


def _separator(parent: QWidget) -> QFrame:
    line = QFrame(parent)
    line.setFrameShape(QFrame.VLine)
    line.setFixedWidth(1)
    line.setStyleSheet("color: #e2e5ea;")
    line.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
    return line

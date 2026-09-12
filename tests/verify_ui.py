# -*- coding: utf-8 -*-
"""界面自检脚本（联网，可选）。

它会真实跑一遍「搜索 → 详情 → 阅读 → 书架 → 设置」流程，并做两类校验：
1. 程序化断言：控件尺寸、目录条数、主题配色、字号、翻页、进度记忆、错误提示……
2. 截图色彩统计：确认渲染结果不是空白、深色主题确实变暗、护眼绿确实偏绿。

运行（无需真实显示器，使用 Qt 离屏平台）：
    Windows:  set QT_QPA_PLATFORM=offscreen && python tests\verify_ui.py
    macOS/Linux:  QT_QPA_PLATFORM=offscreen python tests/verify_ui.py

说明：脚本需要联网（会访问内置书源），并会使用独立的数据目录 tests/.uicheck，
不会影响你自己的书架与配置。截图保存在 tests/shots/。
"""
from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("NOVELFOUND_HOME", str(ROOT / "tests" / ".uicheck"))
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

# 离屏平台默认**不加载任何字体**（实测字体族 = 0），结果是 QLabel / QPushButton /
# QTextBrowser 的文字全部绘制为空——截图里只有封面图和色块，看不到一个字。
# 指定系统字体目录后，Qt 的 basic font database 才会去扫描字体（实测 0 → 107 个族）。
# 这一步必须在 QApplication 创建之前完成，所以放在所有 PyQt5 导入之前。
if not os.environ.get("QT_QPA_FONTDIR"):
    for candidate in (Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts",
                      Path("/System/Library/Fonts"),
                      Path("/usr/share/fonts")):
        if candidate.is_dir():
            os.environ["QT_QPA_FONTDIR"] = str(candidate)
            break

try:
    sys.stdout.reconfigure(encoding="utf-8")
except AttributeError:  # pragma: no cover
    pass

# 每次自检都从干净状态开始，避免历史配置影响断言
shutil.rmtree(os.environ["NOVELFOUND_HOME"], ignore_errors=True)

from PyQt5.QtCore import QEvent, QEventLoop, QPoint, QUrl, Qt  # noqa: E402
from PyQt5.QtGui import (QColor, QFont, QFontDatabase, QImage, QKeyEvent,  # noqa: E402
                         QMouseEvent, QPainter, QTextDocument)
from PyQt5.QtWidgets import QApplication, QLabel, QPushButton  # noqa: E402

from novelfound.ui.main_window import MainWindow  # noqa: E402
from novelfound.ui.theme import app_stylesheet  # noqa: E402

# 兜底：万一 QT_QPA_FONTDIR 没生效（非 Windows 或路径不存在），手工注册几个字体文件，
# 保证截图里的文字能画出来——否则"截图验收"会变成一次看不见字的空转。
FALLBACK_FONTS = (
    r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\simsun.ttc",
    r"C:\Windows\Fonts\simhei.ttf", r"C:\Windows\Fonts\segoeui.ttf",
    "/System/Library/Fonts/PingFang.ttc", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
)

try:
    from PIL import Image
    HAVE_PIL = True
except ImportError:      # 没装 Pillow 也能跑，只是跳过截图统计
    HAVE_PIL = False

OUT = ROOT / "tests" / "shots"
OUT.mkdir(parents=True, exist_ok=True)
FAILS = []
CHECKS = []


def check(name: str, ok: bool, extra: str = "") -> None:
    CHECKS.append((name, ok, extra))
    if not ok:
        FAILS.append(f"{name} {extra}")
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {extra}", flush=True)


def pump(seconds: float) -> None:
    deadline = time.time() + seconds
    while time.time() < deadline:
        app.processEvents(QEventLoop.AllEvents, 50)
        time.sleep(0.02)


def wait_for(predicate, seconds: float = 30.0) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        app.processEvents(QEventLoop.AllEvents, 50)
        time.sleep(0.03)
        if predicate():
            return True
    return False


def shot(name: str) -> str:
    pump(0.35)
    path = OUT / f"{name}.png"
    window.grab().save(str(path))
    return str(path)


def image_stats(path: str) -> dict:
    """截图统计：平均亮度、平均颜色、颜色数量（用于判断渲染是否正常）。"""
    if not HAVE_PIL:
        return {"size": (0, 0), "mean": (255, 255, 255), "lum": 255, "colors": 999}
    with Image.open(path) as img:
        img = img.convert("RGB")
        width, height = img.size
        small = img.resize((160, 100))
        pixels = list(small.getdata())
    count = len(pixels)
    mean = tuple(sum(p[i] for p in pixels) / count for i in range(3))
    lum = sum(0.299 * p[0] + 0.587 * p[1] + 0.114 * p[2] for p in pixels) / count
    return {"size": (width, height), "mean": mean, "lum": lum,
            "colors": len(set(pixels))}


app = QApplication(sys.argv)
app.setStyleSheet(app_stylesheet())

# 字体兜底 + 断言：没字体就没有文字，截图也就失去意义（这正是"截图全对但看不见字"的坑）
if len(QFontDatabase().families()) == 0:
    for font_path in FALLBACK_FONTS:
        if Path(font_path).is_file():
            QFontDatabase.addApplicationFont(font_path)
FONT_FAMILIES = len(QFontDatabase().families())

window = MainWindow()
window.resize(1360, 880)
window.show()
pump(0.6)

check("离屏环境已加载字体（否则截图里没有文字）", FONT_FAMILIES > 0,
      f"{FONT_FAMILIES} 个字体族 / QT_QPA_FONTDIR="
      f"{os.environ.get('QT_QPA_FONTDIR', '未设置')}")

# 直接验证"文字真的能画出来"——比事后统计截图颜色硬得多：
# 之前那批 Pillow 统计（colors>30 / lum>150）在"全图只有色块、一个字都没有"时照样通过。
_probe = QImage(240, 60, QImage.Format_RGB32)
_probe.fill(QColor("#F7F3E9"))
_painter = QPainter(_probe)
_painter.setFont(QFont("Microsoft YaHei UI", 20))
_painter.setPen(QColor("#333333"))
_painter.drawText(_probe.rect(), Qt.AlignCenter, "斗罗大陆 ABC")
_painter.end()
_bg = QColor("#F7F3E9").rgb()
_text_px = sum(1 for y in range(60) for x in range(240)
               if _probe.pixel(x, y) != _bg)
check("离屏渲染能画出文字（截图验收的前提）", _text_px > 200, f"{_text_px} 个文字像素")

# ---------------------------------------------------------------- 1. 初始界面
check("窗口尺寸", window.width() >= 1200 and window.height() >= 700,
      f"{window.width()}x{window.height()}")
check("初始显示书架首页", window.stack.currentWidget() is window.library_view,
      type(window.stack.currentWidget()).__name__)
check("左侧常驻栏已移除", not hasattr(window, "side_panel"), "")
check("书源状态文本", "书源" in window.source_label.text(), window.source_label.text())
stats = image_stats(shot("01_library_home"))
check("初始界面非空白", stats["colors"] > 30 and stats["lum"] > 150,
      f"colors={stats['colors']} lum={stats['lum']:.1f}")

# ---- P1：搜索入口只有右上角图标 + Ctrl+K ----
window.open_search_palette()
pump(0.3)
check("Ctrl+K 唤起搜索面板", window.search_palette.is_open(), "")
check("搜索面板输入框获得焦点", window.search_palette.input.hasFocus(), "")
check("搜索面板打开时遮罩可见", window.scrim.isVisible(), "")
shot("02_search_palette")
QApplication.sendEvent(window.search_palette.input,
                       QKeyEvent(QKeyEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier))
pump(0.3)
check("Esc 关闭搜索面板", not window.search_palette.is_open(), "")
check("关闭后遮罩隐藏", not window.scrim.isVisible(), "")

# ---- P0：暖白配色（方案指定 #F7F3E9），不应出现纯白大面积背景 ----
check("主界面使用暖白背景", "#F7F3E9" in app.styleSheet(), "")
mean = stats["mean"]
check("背景色调为暖白（R≥G≥B 且明亮）",
      mean[0] >= mean[1] >= mean[2] and mean[0] > 200,
      f"mean={tuple(round(v) for v in mean)}")

# ---- P0：轻提示是浮层（不占布局），会自动消失 ----
window.toast.set_duration(0.6)
window.toast.show_message("测试提示")
pump(0.3)
check("轻提示可见", window.toast.isVisible(), "")
check("轻提示是浮层（父控件为中央部件）",
      window.toast.parent() is window.centralWidget(), "")
pump(1.2)
check("轻提示自动消失", not window.toast.isVisible(), "")
window.toast.set_duration(6)

# 轻提示带操作按钮（原来"网络找书源"入口就靠它）
clicked = {}
window.toast.show_message("没有结果", action_text="网络找书源",
                          action=lambda: clicked.update(hit=True))
pump(0.3)
check("轻提示操作按钮可见", window.toast.action_button.isVisible(), "")
window.toast.action_button.click()
pump(0.2)
check("轻提示操作按钮可点击", clicked.get("hit") is True, "")
window.toast.hide_banner()

# ------------------------------------------------------------------ 2. 搜索
keyword = sys.argv[1] if len(sys.argv) > 1 else "斗罗大陆"
window.open_search_palette(keyword)
ok = wait_for(lambda: len(window.search_palette.rows()) > 0, 60)
check("搜索返回结果", ok, f"结果行={len(window.search_palette.rows())}")
check("搜索面板输入框有关键词", window.search_palette.keyword() == keyword,
      window.search_palette.keyword())

if window.search_palette.rows():
    first = window.search_palette.rows()[0]
    check("结果行书名非空", bool(first.title_label.text()),
          first.title_label.text()[:24])
    check("结果行尺寸合理", first.width() > 200 and first.height() > 40,
          f"{first.width()}x{first.height()}")
    check("结果行有来源标签",
          bool([lbl for lbl in first.findChildren(QLabel)
                if lbl.objectName() == "sourceTag"]), "")
    stats = image_stats(shot("02_search_palette"))
    check("搜索面板渲染正常", stats["colors"] > 40, f"colors={stats['colors']}")

    # 键盘导航：↓ 之后 Enter 也能打开
    before = window.search_palette.results.currentRow()
    QApplication.sendEvent(window.search_palette.input,
                           QKeyEvent(QKeyEvent.KeyPress, Qt.Key_Down, Qt.NoModifier))
    pump(0.2)
    after = window.search_palette.results.currentRow()
    check("↓ 键移动结果选择", after == min(before + 1,
                                           window.search_palette.results.count() - 1),
          f"{before} -> {after}")
    window.search_palette.results.setCurrentRow(0)

# ------------------------------------------------------------------ 3. 详情
window.on_book_clicked(window.search_palette.rows()[0].book)
ok = wait_for(lambda: window.current_detail is not None, 40)
check("详情加载成功", ok,
      f"章节数={len(window.current_detail.chapters) if ok else 0}")
if ok:
    detail = window.current_detail
    check("详情页已显示", window.stack.currentWidget() is window.book_view, "")
    check("搜索面板已关闭", not window.search_palette.is_open(), "")
    check("简介非空", len(window.book_view.intro_label.text()) > 10,
          window.book_view.intro_label.text()[:30])
    check("详情元信息", "章" in window.book_view.meta_label.text(),
          window.book_view.meta_label.text()[:60])
    check("元信息无重复作者",
          window.book_view.meta_label.text().count("作者：") <= 1,
          window.book_view.meta_label.text()[:80])
    check("目录按钮带章节数",
          str(len(detail.chapters)) in window.book_view.catalog_button.text(),
          window.book_view.catalog_button.text())

    primary_buttons = [b for b in window.book_view.findChildren(QPushButton)
                       if b.objectName() == "primary"]
    check("详情页只有一个主按钮", len(primary_buttons) == 1,
          f"primary={len(primary_buttons)}")

    window.book_view.shelf_button.click()
    pump(0.4)
    check("加入书架生效", window.library.contains(window.current_book.key), "")
    check("书架按钮状态", "已在书架" in window.book_view.shelf_button.text(),
          window.book_view.shelf_button.text())

    # ---- 目录抽屉（原来的常驻目录列表）----
    check("目录抽屉默认关闭", not window.catalog_drawer.is_open(), "")
    window.open_catalog_drawer()
    pump(0.3)
    check("目录抽屉可打开", window.catalog_drawer.is_open(), "")
    check("目录列表已填充",
          window.catalog_drawer.chapter_count() == len(detail.chapters),
          f"{window.catalog_drawer.chapter_count()} / {len(detail.chapters)}")
    window.catalog_drawer.filter_box.setText("第一章")
    pump(0.3)
    visible = window.catalog_drawer.visible_chapter_count()
    check("目录筛选生效", 0 < visible < len(detail.chapters), f"可见 {visible} 条")
    window.catalog_drawer.filter_box.clear()
    pump(0.2)
    window.close_catalog_drawer()
    pump(0.2)
    check("目录抽屉可关闭", not window.catalog_drawer.is_open(), "")
    stats = image_stats(shot("03_detail"))
    check("详情页渲染正常", stats["colors"] > 40, f"colors={stats['colors']}")

    # -------------------------------------------------------------- 4. 阅读
    window.on_read_requested(detail, 2)
    ok = wait_for(lambda: bool(window.reader._paragraphs), 40)
    check("章节正文加载", ok, f"段落={len(window.reader._paragraphs)}")
    if ok:
        check("阅读器状态栏", "第 3 /" in window.reader.status.text(),
              window.reader.status.text())
        bar = window.reader.view.verticalScrollBar()
        # 网络章节长度随机（实测遇到过整章只有一屏），太短就往后换一章再断言
        if bar.maximum() == 0:
            for candidate in range(3, min(8, len(detail.chapters))):
                window.on_read_requested(detail, candidate)
                if wait_for(lambda: window.reader.view.verticalScrollBar().maximum() > 0, 40):
                    break
            bar = window.reader.view.verticalScrollBar()
        check("正文可滚动", bar.maximum() > 0, f"max={bar.maximum()}")

        # ---- P0：默认主题应为暖白 #F7F3E9 ----
        from novelfound.ui.theme import reader_theme as _reader_theme
        current_bg = _reader_theme(window.config.get("reader_theme"))["bg"]
        check("默认阅读主题为暖白", current_bg.upper() == "#F7F3E9", f"bg={current_bg}")
        check("暖白主题已应用", current_bg.lower() in
              window.reader.view.styleSheet().lower(),
              window.reader.view.styleSheet()[:60])

        # ---- P0：正文居中限宽（方案要求 700–900px）----
        width_limit = int(window.config.get("content_width"))
        view_width = window.reader.view.width()
        check("正文宽度受限", view_width <= width_limit + 4,
              f"view={view_width} limit={width_limit}")
        check("正文宽度接近设定值", view_width >= min(width_limit, 700),
              f"view={view_width}")
        left_gap = window.reader.view.geometry().left()
        right_gap = window.reader.page.width() - window.reader.view.geometry().right() - 1
        check("正文左右留白对称", abs(left_gap - right_gap) <= 8,
              f"left={left_gap} right={right_gap}")

        # ---- 阅读时收起顶部应用栏（沉浸阅读）----
        check("阅读时隐藏顶部应用栏", not window.top_bar.isVisible(), "")

        # ---- P0：上下浮条默认隐藏，可显式唤出 ----
        check("阅读器浮条默认隐藏", not window.reader.bars_visible(), "")
        window.reader.show_bars()
        pump(0.2)
        check("调用 show_bars 后浮条可见", window.reader.bars_visible(), "")
        window.reader.hide_bars()
        pump(0.2)
        check("调用 hide_bars 后浮条隐藏", not window.reader.bars_visible(), "")
        window.reader.notify_mouse(2)          # 模拟鼠标到顶部
        pump(0.2)
        check("鼠标靠近顶部唤出浮条", window.reader.top_bar.is_bar_visible(), "")
        window.reader.notify_mouse(400)        # 回到正文中间
        pump(0.2)
        window.reader.top_bar.maybe_auto_hide()
        check("鼠标离开后浮条自动隐藏", not window.reader.top_bar.is_bar_visible(), "")

        # ---- P0：底部细进度线 ----
        check("进度线高度 ≤ 3px", window.reader.progress_line.height() <= 3,
              f"h={window.reader.progress_line.height()}")
        check("进度线数值合理", 0 < window.reader.progress_line.progress() <= 1,
              f"ratio={window.reader.progress_line.progress():.3f}")

        # ---- P1：阅读器里的目录抽屉 ----
        check("阅读器有目录按钮", window.reader.catalog_button.text() == "目录", "")
        window.reader.catalog_button.click()
        pump(0.4)
        check("阅读器可打开目录抽屉", window.catalog_drawer.is_open(), "")
        check("抽屉高亮当前阅读章",
              window.catalog_drawer.current_chapter_index() ==
              window.reader.chapter_index,
              f"当前={window.catalog_drawer.current_chapter_index()} "
              f"index={window.reader.chapter_index}")
        _current_item = window.catalog_drawer.chapter_item(window.reader.chapter_index)
        check("抽屉当前章有 ▶ 标记",
              _current_item is not None and _current_item.text(0).startswith("▶"),
              _current_item.text(0) if _current_item is not None else "无")
        window.close_catalog_drawer()
        pump(0.5)                       # 等 200ms 淡出动画跑完
        check("关闭抽屉后遮罩隐藏", not window.scrim.isVisible(), "")

        # ---- P2：阅读设置浮层（Aa，改动即时生效）----
        check("常驻的设置控件已收进浮层",
              not hasattr(window.reader, "theme_box")
              and not hasattr(window.reader, "columns_box"), "")
        check("浮层不参与布局（画布的子控件）",
              window.reader.settings_popover.parent() is window.reader.canvas, "")
        window.reader.settings_button.click()
        pump(0.3)
        check("点 Aa 打开阅读设置浮层", window.reader.settings_popover.isVisible(), "")
        check("浮层含字号/字体/行距/段距/页宽/缩进/主题/方式/排版",
              all(hasattr(window.reader.settings_popover, name) for name in
                  ("font_up", "font_down", "font_family_box", "line_height_box",
                   "para_spacing_box", "indent_box", "content_width_box",
                   "theme_box", "mode_button", "columns_box")), "")
        shot("16_reader_settings")
        cursor = window.reader.view.textCursor()
        cursor.setPosition(1)
        size_before = cursor.charFormat().font().pointSize()
        window.reader.settings_popover.font_up.click()
        pump(0.6)
        cursor = window.reader.view.textCursor()
        cursor.setPosition(1)
        size_after = cursor.charFormat().font().pointSize()
        check("浮层改字号后正文立即重绘", size_after == size_before + 1,
              f"{size_before} -> {size_after}")
        line_before = window.reader._line_height
        window.reader.settings_popover.line_height_box.setValue(
            round(line_before + 0.2, 1))
        pump(0.6)
        check("浮层改行距后立即生效",
              abs(window.reader._line_height - (line_before + 0.2)) < 0.01,
              f"{line_before} -> {window.reader._line_height}")
        window.reader.settings_popover.line_height_box.setValue(line_before)
        window.reader.settings_popover.font_down.click()
        pump(0.5)
        window.reader.settings_popover.close_popover()
        pump(0.2)
        check("浮层可关闭", not window.reader.settings_popover.isVisible(), "")

        # ---- 点正文任意处应收起设置浮层（回归：以前点了正文浮层还在）----
        window.reader.settings_button.click()
        pump(0.3)
        check("浮层可再次打开", window.reader.settings_popover.isVisible(), "")
        _body = window.reader.view.viewport()
        app.sendEvent(_body, QMouseEvent(QEvent.MouseButtonPress, QPoint(10, 10),
                                         Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
        pump(0.3)
        check("点正文收起阅读设置浮层",
              not window.reader.settings_popover.isVisible(), "")
        window.reader.settings_button.click()
        pump(0.3)
        window.reader.keyPressEvent(
            QKeyEvent(QKeyEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier))
        pump(0.2)
        check("Esc 也收起阅读设置浮层",
              not window.reader.settings_popover.isVisible(), "")

        # ---- P2：浮条是"显式状态机 + 200ms 淡入淡出" ----
        check("浮条隐藏状态立即生效",
              not window.reader.bars_visible(), "")
        window.reader.show_bars()
        check("show_bars 后状态即可见（不等动画）", window.reader.bars_visible(), "")
        bar_anim = getattr(window.reader.top_bar, "_animation", None)
        check("浮条淡入动画时长 200ms",
              bar_anim is not None and bar_anim.duration() == 200,
              f"{bar_anim.duration() if bar_anim else 'none'}ms")
        window.reader.top_bar.maybe_auto_hide()
        window.reader.bottom_bar.maybe_auto_hide()
        check("maybe_auto_hide 后状态即隐藏", not window.reader.bars_visible(), "")

        # ---- 分页相关断言改用"内容量确定"的合成正文 ----
        # 真实章节偶尔很短（比如只有一屏的卷首说明），"翻屏前进""恢复位置"这类
        # 相对断言就会失真。这里换成固定长度的合成正文，断言才有意义。
        from novelfound.models import ChapterContent  # noqa: E402
        synthetic = ChapterContent(
            title="分页自检章节",
            paragraphs=[f"第 {i} 段：这是用于分页自检的正文内容，长度大致固定，"
                        f"方便验证一页能装下多少行文字。" for i in range(80)],
            url="about:pagination-selftest")
        window.reader.set_content(synthetic, index=window.reader.chapter_index)
        pump(0.5)
        check("合成正文已加载（分页断言的可重复前提）",
              len(window.reader._paragraphs) > 60,
              f"段落={len(window.reader._paragraphs)}")

        # ---- 分栏（左右双页）----
        window.reader.settings_popover.mode_button.setChecked(True)      # 切到翻页模式
        pump(0.8)
        check("翻页模式已启用", window.reader._page_mode, "")
        check("单页模式右页隐藏", not window.reader.view2.isVisible(), "")
        single_pages = len(window.reader._page_offsets)
        check("单页模式已分页", single_pages > 1, f"pages={single_pages}")

        window.reader.settings_popover.columns_box.setCurrentIndex(
            window.reader.settings_popover.columns_box.findData(2))
        pump(1.0)
        check("双页模式已开启", window.reader._two_page, "")
        check("双页模式右页可见", window.reader.right_column.isVisible(), "")
        left_offset, right_offset = window.reader.current_spread_offsets()
        check("右页接着左页（不是同一处）", 0 <= left_offset < right_offset,
              f"left={left_offset} right={right_offset}")
        check("双页分页数量减半",
              window.reader.spread_count() ==
              max(1, (len(window.reader._page_offsets) + 1) // 2),
              f"spreads={window.reader.spread_count()} "
              f"pages={len(window.reader._page_offsets)}")
        check("状态栏显示页码", "页" in window.reader.status.text(),
              window.reader.status.text())
        check("章节标题常驻显示", bool(window.reader.chapter_header.text()),
              window.reader.chapter_header.text())
        shot("14_reader_two_page")

        # ---- 按行分页：每页应装下多个段落片段（回归：修复前一段一页）----
        page_sizes = [window.reader.paragraphs_in_page(i)
                      for i in range(len(window.reader._page_offsets))]
        check("每页包含多个段落", max(page_sizes) >= 2,
              f"每页段数={page_sizes[:6]}")
        check("后续页也装满（不是一段一页）",
              sum(1 for s in page_sizes[1:] if s >= 2) >= len(page_sizes[1:]) // 2,
              f"后续页段数={page_sizes[1:7]}")

        # ---- 行级分页的结构性保证：所有页都不溢出，且每页都装满 ----
        # 注意：这里的"溢出"用的是 document().size()，只是个辅助信号（比例行距下
        # 这个高度本身不准、还可能滞后）；**权威判据是下面那段像素级检查**。
        overflow = []
        fills = []
        doc_margin = window.reader.view.document().documentMargin()
        usable = window.reader.view.viewport().height() - 2 * doc_margin
        for i in range(len(window.reader._page_offsets)):
            start, end = window.reader._page_range(i)
            window.reader._render_fragments(
                window.reader.view,
                window.reader._fragments_for_range(start, end),
                window.reader._paragraphs, heading_first=True)
            height = window.reader.view.document().size().height()
            if height > window.reader.view.viewport().height() + 2:
                overflow.append(i)
            fills.append((height - 2 * doc_margin) / max(1.0, usable))
        check("所有页均不溢出（行级分页）", not overflow, f"溢出页={overflow}")
        # 最后一页是本章剩余内容，天然不满，只校验前面各页。
        # 门槛 0.75：分页现在对"每块最后一行"用偏保守的行距模型（宁可少放一行，
        # 也不让底部被裁），填充率会比理论极限低一点，这是有意的取舍。
        body_fills = fills[:-1] or fills
        check("每页都装满（不是只塞一行）", min(body_fills) >= 0.75,
              f"最低填充率={min(body_fills):.3f} "
              f"页号={body_fills.index(min(body_fills))}")
        window.reader._show_spread(0)
        pump(0.3)

        # ---- 章首大标题：字号更大且居中 ----
        heading_cursor = window.reader.view.textCursor()
        heading_cursor.setPosition(1)
        heading_size = heading_cursor.charFormat().font().pointSize()
        body_size = int(window.config.get("font_size"))
        check("章首标题字号大于正文", heading_size > body_size,
              f"heading={heading_size} body={body_size}")
        check("章首标题居中",
              window.reader.view.document().firstBlock().blockFormat().alignment()
              == Qt.AlignCenter, "")

        # ---- 鼠标移到章节标题栏上也能唤出顶部浮条（回归）----
        from PyQt5.QtGui import QMouseEvent  # noqa: E402
        window.reader.hide_bars()
        pump(0.2)
        header = window.reader.chapter_header
        point = QPoint(header.width() // 2, 2)
        move = QMouseEvent(QEvent.MouseMove, point, header.mapToGlobal(point),
                           Qt.NoButton, Qt.NoButton, Qt.NoModifier)
        app.sendEvent(header, move)
        pump(0.3)
        check("鼠标移到标题栏唤出浮条", window.reader.top_bar.is_bar_visible(), "")
        window.reader.hide_bars()
        pump(0.2)

        # ---- 每页不裁切：正文高度必须放得下（这是双页模式的关键修复）----
        def _page_fits(view) -> bool:
            return (view.document().size().height()
                    <= view.viewport().height() + 2)

        check("左页内容未被裁切", _page_fits(window.reader.view),
              f"doc={window.reader.view.document().size().height():.0f} "
              f"viewport={window.reader.view.viewport().height()}")
        check("右页内容未被裁切", _page_fits(window.reader.view2),
              f"doc={window.reader.view2.document().size().height():.0f} "
              f"viewport={window.reader.view2.viewport().height()}")

        # ---- 双页时每栏按"单页宽度"取（窗口够宽才给满）----
        content_width = int(window.config.get("content_width"))
        col_width = window.reader.view.width()
        check("双页每栏宽度合理",
              360 <= col_width <= content_width and
              col_width == window.reader.view2.width(),
              f"col={col_width} content_width={content_width}")

        # ---- 滚轮在正文上也应整屏翻页 ----
        from PyQt5.QtGui import QWheelEvent  # noqa: E402

        def _send_wheel(angle: int) -> None:
            wheel_viewport = window.reader.view.viewport()
            wheel_point = QPoint(wheel_viewport.width() // 2, wheel_viewport.height() // 2)
            app.sendEvent(wheel_viewport, QWheelEvent(
                wheel_point, wheel_viewport.mapToGlobal(wheel_point), QPoint(0, 0),
                QPoint(0, angle), Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False))

        spread_before = window.reader._spread
        viewport = window.reader.view.viewport()
        point = QPoint(viewport.width() // 2, viewport.height() // 2)
        wheel = QWheelEvent(point, viewport.mapToGlobal(point), QPoint(0, 0),
                            QPoint(0, -120), Qt.NoButton, Qt.NoModifier,
                            Qt.NoScrollPhase, False)
        app.sendEvent(viewport, wheel)
        pump(0.4)
        check("滚轮在正文上翻页", window.reader._spread == spread_before + 1,
              f"{spread_before} -> {window.reader._spread} "
              f"（屏数 {window.reader.spread_count()}）")

        # ---- 方向键翻页：焦点在正文控件上时也必须生效 ----
        # 回归背景：早先按键处理挂在 ReaderView 上，而正文控件（QTextBrowser）
        # 持焦点时会自己吃掉方向键/翻页键，父控件根本收不到 —— 用户按了没反应。
        from PyQt5.QtGui import QKeyEvent  # noqa: E402

        def _press(key, modifiers=Qt.NoModifier):
            window.reader.view.setFocus()
            pump(0.1)
            before = window.reader._spread
            app.sendEvent(window.reader.view,
                          QKeyEvent(QKeyEvent.KeyPress, key, modifiers))
            pump(0.25)
            return before, window.reader._spread

        check("正文控件只允许鼠标选择（没有待输入光标）",
              int(window.reader.view.textInteractionFlags())
              == int(Qt.TextSelectableByMouse),
              f"flags={int(window.reader.view.textInteractionFlags())}")
        window.reader._show_spread(0)
        pump(0.2)
        _, spread = _press(Qt.Key_Right)
        check("→ 翻下一页（焦点在正文控件上）", spread == 1, f"屏={spread}")
        _, spread = _press(Qt.Key_Down)
        check("↓ 翻下一页", spread == 2, f"屏={spread}")
        _, spread = _press(Qt.Key_Up)
        check("↑ 翻上一页", spread == 1, f"屏={spread}")
        _, spread = _press(Qt.Key_Left)
        check("← 翻上一页", spread == 0, f"屏={spread}")
        _, spread = _press(Qt.Key_PageDown)
        check("PageDown 已解绑（按了不翻页）", spread == 0, f"屏={spread}")
        window.reader._show_spread(1)
        pump(0.2)

        # ---- 回归：一次物理滚动只翻一屏 ----
        # 滚轮驱动/触摸板会把一格拆成多个小事件（-60+-60、-30x4），
        # 按事件翻页就会一次跳好几屏、中间内容整段丢失（用户实测反馈）。
        window.reader._show_spread(0)
        window.reader._wheel_accum = 0
        window.reader._last_turn = 0.0
        pump(0.2)
        before = window.reader._spread
        for _ in range(2):
            _send_wheel(-60)
            pump(0.05)
        pump(0.3)
        check("一格被拆成两个事件也只翻一屏", window.reader._spread == before + 1,
              f"{before} -> {window.reader._spread}")
        window.reader._wheel_accum = 0
        window.reader._last_turn = 0.0
        before = window.reader._spread
        for _ in range(4):
            _send_wheel(-30)
            pump(0.04)
        pump(0.3)
        check("一格被拆成四个事件也只翻一屏", window.reader._spread == before + 1,
              f"{before} -> {window.reader._spread}")

        # ---- 回归：逐屏翻完不能丢内容 ----
        window.reader._show_spread(0)
        pump(0.2)
        pieces = []
        total_spreads = window.reader.spread_count()
        for spread_index in range(total_spreads):
            pieces.append(window.reader.view.toPlainText())
            if window.reader._two_page:
                pieces.append(window.reader.view2.toPlainText())
            # 最后一屏不能再翻：next_page() 会跳到下一章，后面所有断言都会跟着乱
            if spread_index < total_spreads - 1:
                window.reader.next_page()
                pump(0.12)
        walked = "".join("".join(pieces).split())
        original = "".join("".join(window.reader._paragraphs).split())
        check("逐屏翻完全程不丢内容", walked == original,
              f"收集 {len(walked)} 字 / 原文 {len(original)} 字")

        # ---- 翻页模式下没有一页比视口高（否则滚轮会先去滚视口）----
        too_tall = []
        for spread_index in range(window.reader.spread_count()):
            window.reader._show_spread(spread_index)
            pump(0.06)
            for side, view in (("左", window.reader.view), ("右", window.reader.view2)):
                if not view.isVisible():
                    continue
                if view.document().size().height() > view.viewport().height() + 2:
                    too_tall.append((spread_index, side))
        check("翻页模式没有溢出的页", not too_tall,
              f"{len(too_tall)} 页溢出：{too_tall[:4]}")

        # ---- 像素级：每一页的文字都不能贴着/超出底边（真·裁切检测）----
        # 之前用 document().size() 判断"没溢出"是循环验证——分页和自检用了同一个
        # 高度模型，所以一直是绿的，而实际渲染把底部几行裁掉了。这里改数像素。
        # 注意要抓**稳定帧**：刚翻页时 grab() 可能抓到上一帧，会误报"贴底"。
        def stable_grab(widget):
            previous = None
            image = None
            for _ in range(8):
                image = widget.viewport().grab().toImage()
                data = bytes(image.bits().asstring(image.byteCount()))
                if previous == data:
                    return image
                previous = data
                pump(0.12)
            return image

        clipped = []
        page_widths = set()
        scrollbars_on = []
        for spread_index in range(window.reader.spread_count()):
            window.reader._show_spread(spread_index)
            pump(0.2)
            for side, view in (("左", window.reader.view), ("右", window.reader.view2)):
                if not view.isVisible():
                    continue
                page_widths.add(view.viewport().width())
                if view.verticalScrollBarPolicy() != Qt.ScrollBarAlwaysOff:
                    scrollbars_on.append((spread_index, side))
                page_image = stable_grab(view)
                width, height = page_image.width(), page_image.height()
                if width < 8 or height < 8:
                    continue
                background = page_image.pixelColor(2, 2)
                lowest = 0
                for y in range(height - 3, 2, -1):
                    hits = 0
                    for x in range(4, width - 4, 3):
                        color = page_image.pixelColor(x, y)
                        if (abs(color.red() - background.red())
                                + abs(color.green() - background.green())
                                + abs(color.blue() - background.blue())) > 36:
                            hits += 1
                    if hits >= 4:          # 成行的文字才算，单像素噪点不算
                        lowest = y
                        break
                if lowest and height - lowest < 6:
                    clipped.append((spread_index, side, lowest, height))
        check("每页底部都留有余量（文字没被裁掉）", not clipped,
              f"{len(clipped)} 页贴底：{clipped[:4]}")
        # 翻页模式下滚动条必须始终关闭：早先"溢出就打开滚动条"做兜底，
        # 结果滚动条挤窄视口 → 文字重排更多行 → 更溢出 → 滚动条粘住，
        # 而分页是按没有滚动条的宽度算的 → 底部被裁、内容看着像丢了一段。
        check("翻页模式视口宽度恒定（不会越翻越窄）", len(page_widths) == 1,
              f"出现过的宽度={sorted(page_widths)}")
        check("翻页模式不显示滚动条", not scrollbars_on,
              f"{len(scrollbars_on)} 页开了滚动条：{scrollbars_on[:4]}")
        window.reader._show_spread(0)
        pump(0.2)

        # 翻一屏：左右两页都往前走
        before_spread = window.reader._spread
        before_offset = left_offset
        window.reader.next_page()
        pump(0.4)
        after_left, after_right = window.reader.current_spread_offsets()
        check("双页翻屏生效", window.reader._spread == before_spread + 1,
              f"{before_spread} -> {window.reader._spread}")
        check("翻屏后左页内容前进", after_left > before_offset,
              f"{before_offset} -> {after_left}")
        check("翻屏后右页仍然衔接", after_right > after_left,
              f"left={after_left} right={after_right}")
        position = window.reader.reading_position()
        check("位置记录为字符偏移",
              position["char_offset"] ==
              window.reader._page_offsets[window.reader._spread * 2],
              f"offset={position['char_offset']} spread={position['page_index']}")

        # 位置恢复：给一个字符偏移，应回到包含它的那一屏
        target_offset = window.reader._page_offsets[
            min(4, len(window.reader._page_offsets) - 1)]
        window.reader._show_spread(window.reader._spread_for_offset(target_offset))
        pump(0.3)
        restored_left, _ = window.reader.current_spread_offsets()
        check("按字符偏移可定位到对应屏", restored_left <= target_offset,
              f"first={restored_left} target={target_offset}")

        # 切回单页
        window.reader.settings_popover.columns_box.setCurrentIndex(
            window.reader.settings_popover.columns_box.findData(1))
        pump(0.6)
        check("切回单页后右页隐藏", not window.reader.right_column.isVisible(), "")

        stats = image_stats(shot("04_reader_warm"))
        mean = stats["mean"]
        check("暖白配色可见（R>G>B 且偏亮）",
              mean[0] >= mean[1] >= mean[2] and mean[0] > 200,
              f"mean={tuple(round(v) for v in mean)}")

        before = window.config.get("font_size")
        window.reader.change_font_size(3)
        after = window.config.get("font_size")
        check("字号可调", after == before + 3, f"{before} -> {after}")
        cursor = window.reader.view.textCursor()
        cursor.setPosition(1)
        check("字号已应用到正文",
              cursor.charFormat().font().pointSize() == after,
              f"doc={cursor.charFormat().font().pointSize()}")

        # 首行缩进
        # 注意：不能只看"当前页第一块"——翻页模式下第一块可能是**续页片段**
        # （continuation 格式，缩进按设计就是 0），于是断言会随分页边界随机飘。
        # 这里取本页所有块里最大的缩进：只要页内有段落起始，就应当 > 0。
        def page_indent() -> float:
            document = window.reader.view.document()
            return max((document.findBlockByNumber(i).blockFormat().textIndent()
                        for i in range(document.blockCount())), default=0.0)

        indent = page_indent()
        check("默认首行缩进生效（2 字符）", indent > 0, f"indent={indent:.1f}px")
        window.config.set("first_line_indent", 0)
        window.reader.apply_settings()
        window.reader.render()
        pump(0.3)
        indent_off = page_indent()
        check("首行缩进可关闭", indent_off == 0, f"indent={indent_off}")
        window.config.set("first_line_indent", 2)
        window.reader.apply_settings()
        window.reader.render()
        pump(0.3)
        indent_on = page_indent()
        check("首行缩进可恢复", indent_on > 0, f"indent={indent_on:.1f}px")

        window.config.set("reader_theme", "night")
        window.reader.apply_settings()
        window.reader.render()
        stats = image_stats(shot("05_reader_night"))
        check("夜间主题渲染为深色", stats["lum"] < 110, f"lum={stats['lum']:.1f}")
        # 状态栏要跟着阅读主题走（回归：曾是"浅色外壳夹着深色正文"）
        check("阅读时顶部应用栏保持隐藏", not window.top_bar.isVisible(), "")
        _status = window.statusBar().grab().toImage()
        _status_pixel = _status.pixelColor(_status.width() // 2, _status.height() // 2)
        check("夜间主题下状态栏跟随变暗", _status_pixel.lightness() < 110,
              f"#{_status_pixel.name()}")

        window.config.set("reader_theme", "sepia")
        window.reader.apply_settings()
        window.reader.settings_popover.mode_button.setChecked(True)
        window.reader.render()
        pump(0.3)
        check("翻页模式隐藏滚动条",
              window.reader.view.verticalScrollBarPolicy() == Qt.ScrollBarAlwaysOff, "")
        start_spread = window.reader._spread
        window.reader.next_page()
        pump(0.3)
        check("翻页可前进", window.reader._spread == start_spread + 1,
              f"{start_spread} -> {window.reader._spread}")
        window.reader.prev_page()
        pump(0.3)
        check("翻页可后退", window.reader._spread == start_spread,
              f"回到 {window.reader._spread}")
        stats = image_stats(shot("06_reader_page"))
        check("羊皮纸配色可见", stats["mean"][0] > stats["mean"][2],
              f"mean={tuple(round(v) for v in stats['mean'])}")

        index_before = window.reader.chapter_index
        window.reader.next_button.click()
        ok = wait_for(lambda: window.reader.chapter_index == index_before + 1, 40)
        check("下一章可切换", ok, f"{index_before} -> {window.reader.chapter_index}")
        window.reader.prev_button.click()
        ok = wait_for(lambda: window.reader.chapter_index == index_before, 40)
        check("上一章可切换", ok, f"回到 {window.reader.chapter_index}")

        night_index = window.reader.settings_popover.theme_box.findData("night")
        window.reader.settings_popover.theme_box.setCurrentIndex(night_index)
        pump(0.4)
        cursor = window.reader.view.textCursor()
        cursor.setPosition(1)
        color = cursor.charFormat().foreground().color().name()
        check("下拉切换主题后文字颜色同步", color.lower() == "#c2c9d1", f"color={color}")
        eye_index = window.reader.settings_popover.theme_box.findData("eye")
        window.reader.settings_popover.theme_box.setCurrentIndex(eye_index)
        pump(0.3)

        # 滚动位置恢复属于滚动模式，先切回去
        window.reader.settings_popover.mode_button.setChecked(False)
        pump(0.6)
        check("切回滚动模式", not window.reader._page_mode, "")

        # 滚动恢复断言需要一章"足够长的真实正文"：短章节（卷首说明之类）就往后翻几章找
        for candidate in range(index_before,
                               min(index_before + 4, window.reader.chapter_count)):
            window.load_chapter(candidate)
            wait_for(lambda: window.reader.chapter_index == candidate
                     and bool(window.reader._paragraphs), 40)
            pump(0.4)
            if window.reader.view.verticalScrollBar().maximum() >= 700:
                break
        index_before = window.reader.chapter_index
        check("已定位到足够长的一章（用于滚动恢复断言）",
              window.reader.view.verticalScrollBar().maximum() >= 700,
              f"第 {index_before + 1} 章 max="
              f"{window.reader.view.verticalScrollBar().maximum()}")

        bar = window.reader.view.verticalScrollBar()
        bar.setValue(600)
        pump(1.0)
        saved = window.library.progress(window.current_book.key)
        check("滚动位置已记录", saved.get("scroll_pos", 0) >= 600,
              f"pos={saved.get('scroll_pos')}")
        window.reader.next_button.click()
        wait_for(lambda: window.reader.chapter_index == index_before + 1, 30)
        window.reader.prev_button.click()
        wait_for(lambda: window.reader.chapter_index == index_before, 30)
        pump(1.0)
        restored = window.reader.view.verticalScrollBar().value()
        check("返回章节恢复滚动位置", abs(restored - 600) < 200, f"restored={restored}")

        record = window.library.get(window.current_book.key)
        check("阅读进度已保存", bool(record) and bool(record.get("last_chapter_url")),
              (record or {}).get("last_chapter_title", "")[:24])

        window.on_reader_back()
        pump(0.4)
        check("返回详情页", window.stack.currentWidget() is window.book_view, "")
        check("离开阅读器后顶部应用栏恢复", window.top_bar.isVisible(), "")
        _back_shell = window.top_bar.grab().toImage()
        _back_pixel = _back_shell.pixelColor(_back_shell.width() // 2,
                                             _back_shell.height() // 2)
        check("恢复后的顶栏是暖白", _back_pixel.lightness() > 200,
              f"#{_back_pixel.name()}")
        check("返回后主按钮仍是继续阅读",
              window.book_view.read_button.text() == "继续阅读",
              window.book_view.read_button.text())
        shot("07_detail_after_reading")

# ------------------------------------------------------------------ 5. 书架首页
from novelfound.ui.widgets import ProgressLine  # noqa: E402

check("已自动加入书架", bool(window.library.books()), f"{len(window.library.books())} 本")
window.show_library()
pump(0.6)
check("书架首页显示", window.stack.currentWidget() is window.library_view, "")
check("书架网格渲染", len(window.library_view.tiles()) >= 1,
      f"{len(window.library_view.tiles())} 个格子")
check("首页不再有「继续阅读」栏目",
      not hasattr(window.library_view, "continue_card"), "")
check("首页不再有「最近阅读」栏目",
      not hasattr(window.library_view, "recent_title"), "")
check("首页只有书架网格 + 空状态",
      window.library_view.scroll.isVisible() and
      not window.library_view.empty.isVisible(), "")
saved_progress = window.library.progress(window.current_book.key)
tile_texts = [t.status_label.text() for t in window.library_view.tiles()]
check("书架格子显示百分比文字",
      all(t.endswith("%") or t == "未读" for t in tile_texts), str(tile_texts))
check("书架格子不放进度条",
      not any(t.findChildren(ProgressLine) for t in window.library_view.tiles()), "")
# P2：封面角标 + 悬停效果
badge_texts = [t.badge.text() for t in window.library_view.tiles()]
check("封面右下角有进度角标",
      len(badge_texts) == len(tile_texts) and badge_texts == tile_texts,
      str(badge_texts))
first_tile = window.library_view.tiles()[0]
check("角标贴在封面内（是封面的子控件）", first_tile.badge.parent() is first_tile.cover, "")
check("角标位置在封面右下角",
      first_tile.badge.x() + first_tile.badge.width() <= first_tile.cover.width()
      and first_tile.badge.y() + first_tile.badge.height() <= first_tile.cover.height()
      and first_tile.badge.x() + first_tile.badge.width()
      >= first_tile.cover.width() * 0.75
      and first_tile.badge.y() + first_tile.badge.height()
      >= first_tile.cover.height() * 0.75,
      f"badge=({first_tile.badge.x()},{first_tile.badge.y()}) "
      f"{first_tile.badge.width()}x{first_tile.badge.height()} "
      f"cover={first_tile.cover.width()}x{first_tile.cover.height()}")
check("角标不挡鼠标（点击落到卡片上）",
      first_tile.badge.testAttribute(Qt.WA_TransparentForMouseEvents), "")
# 角标必须有深色药丸底（回归：CoverLabel 的样式表会连子控件一起生效，
# 曾把 #coverBadge 的背景盖掉，角标只剩文字、白底上看不出是标签）
_cover_img = first_tile.cover.grab().toImage()
_badge_rect = first_tile.badge.geometry()
_dark = _light = 0
for _y in range(_badge_rect.top(), min(_badge_rect.bottom() + 1, _cover_img.height())):
    for _x in range(_badge_rect.left(), min(_badge_rect.right() + 1, _cover_img.width())):
        if _cover_img.pixelColor(_x, _y).lightness() < 150:
            _dark += 1
        else:
            _light += 1
check("角标有深色药丸底", _dark > _light,
      f"深色占比={_dark / max(1, _dark + _light):.0%}")
check("封面默认没有悬停薄纱", not first_tile.cover.is_hovered(), "")
first_tile.enterEvent(QMouseEvent(QEvent.Enter, QPoint(5, 5), Qt.NoButton,
                                  Qt.NoButton, Qt.NoModifier))
check("鼠标移入后封面出现悬停提示", first_tile.cover.is_hovered(), "")
first_tile.leaveEvent(QMouseEvent(QEvent.Leave, QPoint(5, 5), Qt.NoButton,
                                  Qt.NoButton, Qt.NoModifier))
check("鼠标移出后悬停提示消失", not first_tile.cover.is_hovered(), "")
shot("17_library_tiles")
check("书架标题带数量",
      f"（{len(window.library.books())}）" in window.library_view.shelf_title.text(),
      window.library_view.shelf_title.text())
check("首页只显示书架里的书（不含仅历史记录的书）",
      len(window.library_view.tiles()) == len(window.library.books()),
      f"格子={len(window.library_view.tiles())} 书架={len(window.library.books())} "
      f"历史={len(window.library.history())}")
stats = image_stats(shot("08_library_home"))
check("书架首页非空白", stats["colors"] > 30, f"colors={stats['colors']}")

# ------------------------------------------- 5b. 「继续阅读」要跳到上次那一章
from novelfound.ui.book_view import BookView  # noqa: E402

check("进度里记录了章节序号", saved_progress.get("index", -1) >= 0,
      f"index={saved_progress.get('index')}")
fresh_view = BookView(window)
fresh_view.set_detail(window.current_detail, in_shelf=True, cached_count=0,
                      progress=saved_progress)
pump(0.3)
check("详情页主按钮指向上次读到的章节",
      fresh_view.reading_index() == saved_progress["index"],
      f"reading_index={fresh_view.reading_index()} / 期望 {saved_progress['index']}")
captured = {}
fresh_view.read_requested.connect(lambda detail, index: captured.update(index=index))
fresh_view.read_button.click()
pump(0.2)
check("点「继续阅读」打开的是上次那一章",
      captured.get("index") == saved_progress["index"],
      f"实际打开 index={captured.get('index')}")
fresh_view.deleteLater()

# ------------------------------------------------------ 5c. 目录抽屉
check("目录抽屉默认关闭", not window.catalog_drawer.is_open(), "")
# P2：动画时长（方案要求 200–300ms）
from novelfound.ui.widgets import DRAWER_MS, FADE_MS  # noqa: E402

check("淡入淡出时长在 200–300ms", 200 <= FADE_MS <= 300, f"{FADE_MS}ms")
check("抽屉滑出时长在 200–300ms", 200 <= DRAWER_MS <= 300, f"{DRAWER_MS}ms")
window.open_catalog_drawer()
pump(0.15)
drawer_anim = getattr(window.catalog_drawer, "_animation", None)
check("抽屉滑出动画确实在跑",
      drawer_anim is not None and drawer_anim.duration() == DRAWER_MS,
      f"{drawer_anim.duration() if drawer_anim else 'none'}ms")
pump(0.4)
check("抽屉可打开", window.catalog_drawer.is_open(), "")
check("抽屉打开时遮罩可见", window.scrim.isVisible(), "")
shot("13_catalog_drawer")
window.scrim.clicked.emit()          # 点遮罩关闭
pump(0.5)
check("点遮罩关闭抽屉", not window.catalog_drawer.is_open(), "")
check("关闭抽屉后遮罩隐藏", not window.scrim.isVisible(), "")
window.on_read_requested(window.current_detail, 2)
pump(2.5)
check("进入阅读器时浮层全部关闭",
      not window.catalog_drawer.is_open() and not window.search_palette.is_open(), "")
window.on_reader_back()
pump(0.5)
check("返回详情页正常", window.stack.currentWidget() is window.book_view, "")

# ------------------------------------------- 5d. 封面节流（并发上限 + 懒加载）
window.show_library()
pump(0.4)
window.open_search_palette(keyword)
pump(2.0)
limit = int(window.config.get("cover_max_concurrent") or 2)
check("封面并发不超过上限", window._cover_active <= limit,
      f"active={window._cover_active} limit={limit}")
rows = window.search_palette.rows()
for _ in range(6):
    bar = window.search_palette.results.verticalScrollBar()
    bar.setValue(min(bar.maximum(), bar.value() + 400))
    pump(0.6)
check("滚动后封面队列被消费", len(window._cover_pending) < len(rows),
      f"pending={len(window._cover_pending)} rows={len(rows)}")
window.close_search_palette()
pump(0.3)

# ---------------------------------------------------- 5e. 浏览历史（右上角 🕘）
check("右上角有浏览历史按钮", window.history_button.text() == "🕘",
      window.history_button.text())
history_items = window.history.items()
_book_key = window.current_book.key
check("这本书在历史里只有一条记录",
      sum(1 for i in history_items if i["key"] == _book_key) == 1,
      f"共 {len(history_items)} 条 / 本书 "
      f"{sum(1 for i in history_items if i['key'] == _book_key)} 条")
check("读完返回后记录为「阅读」且带章节",
      any(i["key"] == _book_key and i["kind"] == "chapter" for i in history_items),
      str([(i["kind"], i["chapter_index"]) for i in history_items
           if i["key"] == _book_key]))
check("历史里没有重复的书",
      len({i["key"] for i in history_items}) == len(history_items), "")
check("历史上限为 30 条", window.history.count() <= 30,
      f"{window.history.count()} 条")
check("历史条目带书名与时间戳",
      all(i.get("title") and i.get("at") for i in history_items), "")

window.open_history_panel()
pump(0.4)
check("点 🕘 打开浏览历史面板", window.history_panel.is_open(), "")
check("历史面板打开时遮罩可见", window.scrim.isVisible(), "")
check("历史面板行数与记录数一致",
      len(window.history_panel.rows()) == len(history_items),
      f"行={len(window.history_panel.rows())} 记录={len(history_items)}")
check("历史面板显示条数", "共" in window.history_panel.count_label.text(),
      window.history_panel.count_label.text())
panel_rows = window.history_panel.rows()
check("历史行显示动作标签与时间",
      panel_rows and panel_rows[0].kind_label.text() in ("浏览", "阅读")
      and bool(panel_rows[0].time_label.text()),
      f"{panel_rows[0].kind_label.text()} / {panel_rows[0].time_label.text()}"
      if panel_rows else "无行")
check("首行是最近一次动作（阅读）", panel_rows[0].kind_label.text() == "阅读",
      panel_rows[0].kind_label.text())
shot("15_history_panel")
# 键盘 Esc 关闭
QApplication.sendEvent(window.history_panel.list,
                       QKeyEvent(QKeyEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier))
window.history_panel.keyPressEvent(
    QKeyEvent(QKeyEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier))
pump(0.3)
check("Esc 关闭浏览历史面板", not window.history_panel.is_open(), "")
check("关闭历史面板后遮罩隐藏", not window.scrim.isVisible(), "")

# 点一条「阅读」记录 → 直接回到当时那一章
chapter_row = next((i for i, r in enumerate(window.history_panel.rows())
                    if r.entry.get("kind") == "chapter"), -1)
if chapter_row >= 0:
    window.open_history_panel()
    pump(0.3)
    target_entry = window.history_panel.rows()[chapter_row].entry
    window.history_panel.list.setCurrentRow(chapter_row)
    window.history_panel._on_item_activated(
        window.history_panel.list.item(chapter_row))
    ok = wait_for(lambda: window.stack.currentWidget() is window.reader
                  and window.reader.chapter_index == target_entry["chapter_index"], 40)
    check("点历史条目回到当时那一章", ok,
          f"期望第 {target_entry['chapter_index'] + 1} 章 / "
          f"当前第 {window.reader.chapter_index + 1} 章")
    check("点历史条目后浮层已关闭", not window.history_panel.is_open(), "")

# 再打开一次详情：仍然只有一条（按书去重），且不会丢掉"读到哪一章"
# 注意：这段必须在"清空历史"之前，否则历史已空、无从继承章节信息。
window.on_book_clicked(window.current_book)
wait_for(lambda: window.current_detail is not None, 40)
pump(0.8)
_entry_after = next((i for i in window.history.items() if i["key"] == _book_key), {})
check("重新打开同一本书详情不新增记录",
      sum(1 for i in window.history.items() if i["key"] == _book_key) == 1,
      f"本书 {sum(1 for i in window.history.items() if i['key'] == _book_key)} 条")
check("重开详情不会丢掉已读章节", _entry_after.get("chapter_index", -1) >= 0,
      f"chapter_index={_entry_after.get('chapter_index')} "
      f"kind={_entry_after.get('kind')}")

# 清空
before_clear = window.history.count()
window.open_history_panel()
pump(0.3)
window.history_panel.clear_button.click()
pump(0.4)
check("清空历史生效", window.history.count() == 0, f"{before_clear} -> 0")
check("清空后显示空提示", window.history_panel.hint.isVisible(), "")
check("清空后按钮提示更新", "暂无记录" in window.history_button.toolTip(),
      window.history_button.toolTip())
window.close_history_panel()
pump(0.2)

# ------------------------------------------- 5f. 本地 TXT / EPUB 导入与阅读
import zipfile as _zipfile  # noqa: E402

from novelfound.localbooks import COVER_PREFIX  # noqa: E402

_local_dir = ROOT / "tests" / ".tmp" / "localimport"
_local_dir.mkdir(parents=True, exist_ok=True)
_txt_path = _local_dir / "本地测试书.txt"
_txt_path.write_text(
    "书名：本地测试书\n作者：测试作者\n\n"
    "第一部 开端\n"
    "第一章 开端\n" + "第一段正文内容，用于本地导入自检。" * 8 + "\n"
    "第二部 继续\n"
    "第二章 继续\n" + "第二段正文内容，用于本地导入自检。" * 8 + "\n",
    encoding="utf-8")

_epub_path = _local_dir / "本地EPUB.epub"
# 仿真实 EPUB 的目录结构：正文在 Text/、插图在 Images/，src 用相对路径 ../Images/
# PNG 用 zlib + CRC 现场生成（手写十六进制那张是坏的：能过字节断言、解码时报错）
def _make_png(width: int = 4, height: int = 4, rgb: tuple = (200, 80, 80)) -> bytes:
    import struct as _struct
    import zlib as _zlib

    raw = b"".join(b"\x00" + bytes(rgb) * width for _ in range(height))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (_struct.pack(">I", len(data)) + tag + data
                + _struct.pack(">I", _zlib.crc32(tag + data) & 0xFFFFFFFF))

    ihdr = _struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", _zlib.compress(raw)) + chunk(b"IEND", b""))


_PNG_1PX = _make_png(400, 300)      # 图要够大：小图看不出"被行距放大"的空白
with _zipfile.ZipFile(_epub_path, "w") as _archive:
    _archive.writestr("mimetype", "application/epub+zip")
    _archive.writestr("META-INF/container.xml",
                      '<?xml version="1.0"?><container version="1.0" '
                      'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                      '<rootfiles><rootfile full-path="OEBPS/content.opf" '
                      'media-type="application/oebps-package+xml"/></rootfiles></container>')
    _archive.writestr("OEBPS/content.opf",
                      '<?xml version="1.0" encoding="utf-8"?>'
                      '<package xmlns="http://www.idpf.org/2007/opf" version="2.0" '
                      'unique-identifier="id"><metadata '
                      'xmlns:dc="http://purl.org/dc/elements/1.1/">'
                      '<dc:title>本地 EPUB 测试书</dc:title>'
                      '<dc:creator>EPUB 作者</dc:creator></metadata><manifest>'
                      '<item id="c1" href="Text/c1.xhtml" '
                      'media-type="application/xhtml+xml"/>'
                      '<item id="i1" href="Images/C1.png" media-type="image/png"/>'
                      '<item id="i2" href="Images/未引用插图.png" media-type="image/png"/>'
                      '</manifest><spine><itemref idref="c1"/></spine></package>')
    _archive.writestr("OEBPS/Text/c1.xhtml",
                      '<?xml version="1.0" encoding="utf-8"?><html '
                      'xmlns="http://www.w3.org/1999/xhtml"><body>'
                      '<div class="imgh"><img alt="alt" src="../Images/C1.png" '
                      'width="140"/></div>'
                      '<h1>第一章 EPUB</h1>'
                      '<p>EPUB 正文内容，用于本地导入自检。</p></body></html>')
    _archive.writestr("OEBPS/Images/C1.png", _PNG_1PX)
    _archive.writestr("OEBPS/Images/未引用插图.png", _PNG_1PX)

window.import_local_books([str(_txt_path), str(_epub_path)])
# 注意：等的是"真的进了书架"，不能只等 local_books.count()——那个在导入循环里就会变，
# 而 finished 回调（写书架 + 刷新首页）要等任务整体结束。
ok = wait_for(lambda: window.local_books.count() >= 2
              and any(t.book.title == "本地测试书" for t in window.library_view.tiles())
              and any(t.book.title == "本地 EPUB 测试书"
                      for t in window.library_view.tiles()), 90)
check("导入本地 TXT / EPUB 成功", ok, f"{window.local_books.count()} 本")
_lb_txt = next((i for i in window.local_books.all() if i["format"] == "txt"), None)
_lb_epub = next((i for i in window.local_books.all() if i["format"] == "epub"), None)
check("TXT 解析出章节", bool(_lb_txt) and len(_lb_txt["chapters"]) == 2,
      f"章节={len(_lb_txt['chapters']) if _lb_txt else 0}")
check("TXT 读到文件内的书名/作者",
      bool(_lb_txt) and _lb_txt["title"] == "本地测试书"
      and _lb_txt["author"] == "测试作者",
      f"{_lb_txt['title'] if _lb_txt else ''} / {_lb_txt['author'] if _lb_txt else ''}")
check("EPUB 解析出书名与章节",
      bool(_lb_epub) and _lb_epub["title"] == "本地 EPUB 测试书"
      and len(_lb_epub["chapters"]) == 1,
      f"{_lb_epub['title'] if _lb_epub else ''}")

_titles = [t.book.title for t in window.library_view.tiles()]
check("导入的书出现在书架首页", "本地测试书" in _titles, str(_titles[:4]))
check("导入的书已记入书架（有章节数）",
      window.library.contains(window.local_books.to_book(_lb_txt).key), "")

# 打开详情 → 目录 → 读正文（关键：不只能导入，还要能读）
_local_book = window.local_books.to_book(_lb_txt)
window.on_book_clicked(_local_book)
ok = wait_for(lambda: window.current_detail is not None
              and window.current_detail.book.key == _local_book.key, 40)
check("本地书能打开详情", ok, "")
check("本地书目录已生成", ok and len(window.current_detail.chapters) == 2,
      f"{len(window.current_detail.chapters) if ok else 0} 章")
if ok:
    window.on_read_requested(window.current_detail, 1)
    # 等"这一章的正文"出现，不能只等 _paragraphs 非空——它可能还是上一章的内容
    ok = wait_for(lambda: "第二段正文内容" in "".join(window.reader._paragraphs), 40)
    _text = "".join(window.reader._paragraphs)
    check("本地书能读正文（第二章）",
          ok and "第二段正文内容" in _text, f"{len(window.reader._paragraphs)} 段")
    check("本地书读的是本地书源",
          window.current_source is not None and window.current_source.key == "local",
          window.current_source.key if window.current_source else "无")
    shot("18_local_reader")
    window.on_reader_back()
    pump(0.4)

# EPUB 也要能读
_local_epub_book = window.local_books.to_book(_lb_epub)
window.on_book_clicked(_local_epub_book)
ok = wait_for(lambda: window.current_detail is not None
              and window.current_detail.book.key == _local_epub_book.key, 40)
if ok:
    window.on_read_requested(window.current_detail, 0)
    ok = wait_for(lambda: "EPUB 正文内容" in "".join(window.reader._paragraphs), 40)
    check("EPUB 能读正文", ok, f"{len(window.reader._paragraphs)} 段")
    # 正文内嵌插图：解析出图片字节，并且真的画进了文档
    check("EPUB 内嵌插图已解析", len(window.reader._images) >= 1,
          f"{len(window.reader._images)} 张")
    _html = window.reader.view.document().toHtml()
    check("EPUB 插图已画进正文", "<img" in _html.lower(), f"HTML 长度={len(_html)}")
    # 关键：图片要**注册成文档资源**，否则 Qt 画的是"图片缺失"的破图图标
    # （只断言 "<img" 是不够的——破图也满足它，真实踩过）
    _doc = window.reader.view.document()
    _registered = [index for index in window.reader._images
                   if not _doc.resource(
                       QTextDocument.ImageResource,
                       QUrl(window.reader._image_key(index))).isNull()]
    check("EPUB 插图已注册为文档资源", len(_registered) == len(window.reader._images),
          f"已注册 {len(_registered)}/{len(window.reader._images)}")

    # ---- 回归：插图那一行不能被"比例行距"放大 ----
    # 图片那"一行"的 line.height() 就是图片高度；若按行距 190% 算，
    # 600px 的插图会变成 1140px → 图片下方凭空多出 540px 空白，分页还会以为它
    # 放不下、把它挤到单独一页（表现："图标跑到右栏、左栏下半截空着"）。
    _doc_layout = _doc.documentLayout()
    _image_gap = None
    for _i in range(_doc.blockCount()):
        _block = _doc.findBlockByNumber(_i)
        if _block.text() == "\ufffc" and _i + 1 < _doc.blockCount():
            _rect = _doc_layout.blockBoundingRect(_block)
            _next_rect = _doc_layout.blockBoundingRect(_doc.findBlockByNumber(_i + 1))
            _image_gap = _next_rect.top() - (_rect.top() + _rect.height())
            break
    check("插图下方没有多余空白（未被行距放大）",
          _image_gap is not None and _image_gap <= 40,
          f"间隙={_image_gap if _image_gap is not None else '未找到图片块'}px")

    # ---- 回归：章末不能多出一个空白尾页 ----
    # 分页最后一页的起点若等于整章长度，那一页就是空的（用户实测看到空白页）。
    # 这里需要翻页模式（滚动模式下没有页的概念），测完切回滚动模式，
    # 免得影响后面依赖滚动模式的断言。
    window.reader.settings_popover.mode_button.setChecked(True)
    pump(0.8)
    check("章末没有空白尾页",
          bool(window.reader._page_offsets)
          and window.reader._text_length > window.reader._page_offsets[-1],
          f"末页起点={window.reader._page_offsets[-1] if window.reader._page_offsets else '无'} "
          f"文本长={window.reader._text_length}")
    window.reader._show_spread(window.reader.spread_count() - 1)
    pump(0.3)
    _last = window.reader.view.toPlainText()
    if window.reader._two_page:
        _last += window.reader.view2.toPlainText()
    check("最后一屏有内容", bool(_last.strip()), f"{len(_last.strip())} 字")
    window.reader._show_spread(0)
    pump(0.2)
    shot("21_epub_with_image")
    window.reader.settings_popover.mode_button.setChecked(False)
    pump(0.6)
    window.on_reader_back()
    pump(0.4)

# ---- 本书插图：清单 + 浏览窗口 + 抽屉入口 ----
_epub_src = window._source_for(_local_epub_book)
_items = _epub_src.list_images(_local_epub_book) if _epub_src else []
check("插图清单包含正文没引用的图", len(_items) >= 2,
      f"{len(_items)} 张：{[i['name'] for i in _items]}")
if _items:
    from novelfound.ui.image_gallery import ImageGalleryDialog  # noqa: E402

    _gallery = ImageGalleryDialog(
        _items, lambda path: _epub_src.image_bytes(_local_epub_book, path),
        window, title="《本地 EPUB 测试书》插图")
    _gallery.resize(880, 620)
    _gallery.show()
    pump(0.4)
    check("插图窗口列出全部图片",
          len(_gallery.image_paths()) == len(_items), f"{len(_gallery.image_paths())} 张")
    _size = _gallery.current_image_size()
    check("插图窗口能显示图片", _size[0] > 0 and _size[1] > 0, f"{_size[0]}x{_size[1]}")
    check("插图窗口可另存", _gallery.save_button.isEnabled(), "")
    _gallery.grab().save(str(OUT / "20_image_gallery.png"))
    _gallery.close()

window.on_book_clicked(_local_epub_book)
wait_for(lambda: window.current_detail is not None
         and window.current_detail.book.key == _local_epub_book.key, 40)
window.open_catalog_drawer()
pump(0.4)
check("目录抽屉有「本书插图（N）」入口",
      window.catalog_drawer.images_button.isVisible()
      and str(len(_items)) in window.catalog_drawer.images_button.text(),
      window.catalog_drawer.images_button.text())
window.close_catalog_drawer()
pump(0.3)

# ---- 超大插图必须缩到页面内（章首大图不能占满整页）----
from novelfound.models import ChapterContent  # noqa: E402

window.reader.set_content(ChapterContent(
    title="大图测试",
    paragraphs=["\ufffc", "正文内容，用于验证插图缩放与居中。"],
    images={0: _make_png(1600, 1200)}), index=0)
pump(0.8)
_doc = window.reader.view.document()
_doc_layout = _doc.documentLayout()
_viewport_h = window.reader.view.viewport().height()
_image_h = 0.0
_image_centered = False
for _i in range(_doc.blockCount()):
    _block = _doc.findBlockByNumber(_i)
    if _block.text() == "\ufffc":
        _image_h = _doc_layout.blockBoundingRect(_block).height()
        _image_centered = bool(_block.blockFormat().alignment() & Qt.AlignHCenter)
        break
check("超大插图缩到页面内",
      0 < _image_h <= _viewport_h * 0.62 + 2,
      f"高={_image_h:.0f} 上限={_viewport_h * 0.62:.0f}")
check("插图居中显示", _image_centered, "")

# ------------------------------------------------- 5g. 本地书的「部/卷」分组目录
window.on_book_clicked(window.local_books.to_book(_lb_txt))
ok = wait_for(lambda: window.current_detail is not None
              and window.current_detail.book.key == _local_book.key, 40)
if ok:
    _detail = window.current_detail
    _groups = [c.group for c in _detail.chapters if c.group]
    check("本地书章节带分组信息", bool(set(_groups)),
          f"{len(set(_groups))} 组：{sorted(set(_groups))}")
    window.open_catalog_drawer()
    pump(0.4)
    check("目录里出现分组节点",
          set(window.catalog_drawer.group_titles()) == set(_groups),
          str(window.catalog_drawer.group_titles()))
    _current = window.catalog_drawer.current_chapter_index()
    _current_group = (_detail.chapters[_current].group
                      if 0 <= _current < len(_detail.chapters) else "")
    _expanded = [g for g in window.catalog_drawer.group_titles()
                 if window.catalog_drawer.is_group_expanded(g)]
    check("默认只展开当前章所在的分组",
          _expanded == ([_current_group] if _current_group else []),
          f"展开={_expanded} 当前章所在组={_current_group!r}")
    _first = window.catalog_drawer.group_titles()[0] if _groups else ""
    if not _groups:
        check("目录分组断言可继续（需先有分组）", False, "这本书没有分组，后续断言跳过")
    else:
        window.catalog_drawer.toggle_all_groups()
        pump(0.2)
        check("可一键展开全部分组",
              all(window.catalog_drawer.is_group_expanded(g)
                  for g in window.catalog_drawer.group_titles()), "")
        window.catalog_drawer.toggle_all_groups()
        pump(0.2)
        check("可一键收起全部分组",
              not any(window.catalog_drawer.is_group_expanded(g)
                      for g in window.catalog_drawer.group_titles()), "")
        window.catalog_drawer._on_item_activated(
            window.catalog_drawer.group_item(_first))      # 点组标题
        pump(0.2)
        check("点分组标题可展开", window.catalog_drawer.is_group_expanded(_first), "")
        window.catalog_drawer._on_item_activated(
            window.catalog_drawer.group_item(_first))
        pump(0.2)
        check("再点分组标题可收起",
              not window.catalog_drawer.is_group_expanded(_first), "")
        window.catalog_drawer.filter_box.setText("第二章")
        pump(0.3)
        _visible = window.catalog_drawer.visible_chapter_count()
        _matched = next((c.group for c in _detail.chapters
                         if "第二章" in c.display_title), "")
        _others_hidden = all(
            window.catalog_drawer.group_item(g).isHidden()
            for g in window.catalog_drawer.group_titles() if g != _matched)
        check("筛选时展开命中的分组、隐藏没命中的",
              0 < _visible < len(_detail.chapters)
              and window.catalog_drawer.is_group_expanded(_matched)
              and _others_hidden,
              f"可见={_visible}/{len(_detail.chapters)} 命中组={_matched!r} "
              f"其它组全隐藏={_others_hidden}")
        window.catalog_drawer.filter_box.clear()
        pump(0.2)
        _expanded_after = [g for g in window.catalog_drawer.group_titles()
                           if window.catalog_drawer.is_group_expanded(g)]
        check("清空筛选后回到默认收起",
              _expanded_after == ([_current_group] if _current_group else []),
              f"展开={_expanded_after}")
    shot("19_catalog_groups")
    window.close_catalog_drawer()
    pump(0.4)

from novelfound.ui.local_manager import LocalManagerDialog  # noqa: E402

# ------------------------------------------------- 5h. 本地书管理（删除级联 / 清理失效记录）
# 用一本**临时**书来测删除：别把后面「本地书可被搜索到」要用的那两本删掉
_tmp_book_path = _local_dir / "临时删除测试书.txt"
_tmp_book_path.write_text("书名：临时删除测试书\n作者：测试\n\n第一章 开端\n"
                          + "正文内容，用于删除级联自检。" * 10 + "\n", encoding="utf-8")
_before_count = window.local_books.count()
window.import_local_books([str(_tmp_book_path)])
ok = wait_for(lambda: window.local_books.count() == _before_count + 1, 60)
check("导入临时测试书（删除用）", ok, f"{window.local_books.count()} 本")
_lb_record = next((i for i in window.local_books.all()
                   if i["title"] == "临时删除测试书"), None)
if _lb_record:
    _lb_book = window.local_books.to_book(_lb_record)
    _lb_path = window.local_books.file_path(_lb_record)
    # 造出"读过的本地书"：书架 + 进度 + 浏览历史 + 缓存
    window.library.add(_lb_book, window.current_detail
                       if window.current_detail and
                       window.current_detail.book.key == _lb_book.key else None)
    window.library.update_progress(_lb_book, f"{_lb_book.url}/0", "第一章 开端", 0)
    window.history.record(_lb_book, kind="detail")
    window.cache.put_chapter("local", _lb_book.url, f"{_lb_book.url}/0",
                             "第一章", ["正文"])
    _details = window.local_book_details(_lb_record)
    check("删除明细含文件大小与缓存行数",
          _details["size"] > 0 and _details["cache_rows"] >= 1,
          f"{_details['size_text']} / 缓存 {_details['cache_rows']} 行 / "
          f"书架={_details['in_library']} 历史={_details['in_history']}")

    ok = window.delete_local_book(_lb_record, confirm=False)   # 自检里跳过确认框
    check("删除本地书成功", ok, "")
    check("删除后文件已消失", not _lb_path.exists(), str(_lb_path))
    check("删除后记录已消失", window.local_books.get(_lb_record["id"]) is None, "")
    check("删除后书架条目已清", not window.library.contains(_lb_book.key), "")
    check("删除后阅读进度已清",
          not window.library.progress(_lb_book.key).get("chapter_url"), "")
    check("删除后浏览历史已清",
          not any(i.get("key") == _lb_book.key for i in window.history.items()), "")
    check("删除后缓存已清",
          window.cache.count_for_book("local", _lb_book.url) == 0, "")

    # 造一条"失效记录"（模拟删掉记录但历史/进度/缓存里还留着），验证清理按钮
    _ghost = "local|local://deadbeef0001"
    window.library._data["books"][_ghost] = {"key": _ghost, "title": "已删的书",
                                             "url": "local://deadbeef0001",
                                             "source": "local"}
    window.library.save()
    window.history._items.insert(0, {"key": _ghost, "title": "已删的书",
                                     "url": "local://deadbeef0001",
                                     "source": "local", "kind": "detail",
                                     "at": 0})
    window.history.save()
    window.cache.put_chapter("local", "local://deadbeef0001",
                             "local://deadbeef0001/0", "第一章", ["正文"])
    window.library._data["books"][_ghost] = {"key": _ghost, "title": "已删的书",
                                             "url": "local://deadbeef0001",
                                             "source": "local"}
    window.library.save()
    _clean = window.cleanup_stale_records()
    check("清理失效记录：书架/进度",
          _clean["library"] >= 1 and not window.library.contains(_ghost),
          str(_clean))
    check("清理失效记录：浏览历史", _clean["history"] >= 1, str(_clean))
    check("清理失效记录：缓存", _clean["cache"] >= 1, str(_clean))

    # 管理窗口能列出已导入的书
    _mgr = LocalManagerDialog(window.local_books, window.library, window.history,
                              window.cache,
                              on_delete=lambda item: None,
                              on_cleanup=lambda: {},
                              parent=window)
    _mgr.show()
    pump(0.3)
    check("本地书管理窗口列出已导入的书",
          len(_mgr.row_titles()) == window.local_books.count()
          and "占用" in _mgr.summary.text(),
          f"{_mgr.row_titles()} / {_mgr.summary.text()}")
    _mgr.close()

# 搜索里也能搜到本地书
window.open_search_palette("本地测试书")
ok = wait_for(lambda: any(r.book.title == "本地测试书"
                          for r in window.search_palette.rows()), 60)
check("本地书可被搜索到", ok,
      f"结果={[r.book.title for r in window.search_palette.rows()][:3]}")
window.close_search_palette()
pump(0.3)

# ------------------------------------------------- 6. 书源导入 / 评分 / 自动禁用
from novelfound.sources.importer import parse_payload  # noqa: E402
from novelfound.tasks import ImportTask  # noqa: E402
from novelfound.ui.source_import_dialog import SourceImportDialog  # noqa: E402

legado_text = (ROOT / "tests" / "fixtures" / "legado_sample.json").read_text(
    encoding="utf-8")
parsed = parse_payload(legado_text, "fixture")
usable_count = sum(1 for p in parsed if p.ok)
check("Legado 书源解析", len(parsed) == 3 and usable_count == 2,
      f"共 {len(parsed)} 条，可用 {usable_count} 条")
check("含 JS 规则的源被跳过",
      any((not p.ok) and "JS" in p.reason for p in parsed), "")

before_custom = len(window.config.custom_sources())
import_dialog = SourceImportDialog(window.config, window.http, window.task_manager, window)
import_dialog.resize(760, 640)
import_dialog.show()
import_task = ImportTask(window.http, text=legado_text)
import_task.signals.finished.connect(import_dialog._on_parsed)
window.task_manager.start(import_task)
wait_for(lambda: import_dialog.preview.count() > 0, 20)
check("导入对话框预览条目", import_dialog.preview.count() == 3,
      f"{import_dialog.preview.count()} 条")
check("导入按钮可用", import_dialog.import_button.isEnabled(), "")
import_dialog.grab().save(str(OUT / "10_import_dialog.png"))
import_dialog.on_import_selected()
pump(0.6)
after_custom = len(window.config.custom_sources())
check("导入写入配置", after_custom == before_custom + 2,
      f"{before_custom} -> {after_custom}")
imported_key = next(p.key for p in parsed if p.ok)
check("导入后自动启用", window.config.is_source_enabled(imported_key, False), imported_key)
window._on_settings_sources_changed()
check("主窗口书源列表刷新", any(s.key == imported_key for s in window.sources), "")
import_dialog.close()

# ------------------------------------------------- 6b. 探测新书源对话框（离线喂结果）
from novelfound.sources.probe import ProbeOutcome  # noqa: E402
from novelfound.ui.source_probe_dialog import SourceProbeDialog  # noqa: E402

probe_dialog = SourceProbeDialog(window.config, window.http, window.task_manager, window)
probe_dialog.resize(720, 620)
probe_dialog.show()
probe_rule = {
    "key": "probe_probe_example_com",
    "name": "probe.example.com（自动探测）",
    "base_url": "https://probe.example.com",
    "search_url": "/s?q={q}",
    "content": ["#content"],
    "enabled_by_default": True,
}
ok_outcome = ProbeOutcome(
    base_url="https://probe.example.com", keyword="示例", ok=True, stage="完成",
    template="经典笔趣阁模板", template_key="classic", search_url="/s?q={q}",
    books_found=10, chapter_count=337, char_count=2455, elapsed=1.4,
    preview={"title": "示例书", "author": "某作者",
             "chapters": ["第一章 开端", "第二章 冲突", "第三章 转折"],
             "snippet": "这是探测到的正文开头。"},
    rule=probe_rule)
before_probe = len(window.config.custom_sources())
probe_dialog._on_finished(ok_outcome)
pump(0.4)
check("探测成功显示预览", "匹配模板" in probe_dialog.preview_label.text(),
      probe_dialog.preview_label.text()[:40])
check("探测成功后保存按钮可用", probe_dialog.save_button.isEnabled(), "")
probe_dialog.grab().save(str(OUT / "11_probe_dialog.png"))
probe_dialog.on_save()
pump(0.4)
check("探测规则写入配置",
      len(window.config.custom_sources()) == before_probe + 1,
      f"{before_probe} -> {len(window.config.custom_sources())}")
check("探测书源自动启用",
      window.config.is_source_enabled("probe_probe_example_com", False), "")
probe_dialog.close()

fail_dialog = SourceProbeDialog(window.config, window.http, window.task_manager, window)
fail_dialog.show()
fail_dialog._on_finished(ProbeOutcome(
    base_url="https://half.example.com", ok=False, stage="目录解析",
    error="目录解析失败：站点结构可能已变化",
    partial_rule={"key": "probe_half_example_com", "base_url": "https://half.example.com",
                  "search_url": "/search/?keyword={q}"}))
pump(0.3)
check("探测失败显示卡在哪一关", "目录解析" in fail_dialog.preview_label.text(),
      fail_dialog.preview_label.text()[:40])
check("失败时给出半成品规则", bool(fail_dialog.editor.toPlainText().strip()), "")
fail_dialog.close()
# 清理探测产生的测试规则
window.config.remove_custom_source("probe_probe_example_com")
window._on_settings_sources_changed()

# --------------------- 6b-2. 点按钮的回归测试（离线注入假 HTTP，覆盖真实点击路径）
# 说明：之前的自检只调用了对话框内部方法，没点按钮，结果漏掉了
# 「网络找书源 → 开始查找」里的 AttributeError（导致整个程序 qFatal 退出）。
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_discover import FakeHttp  # noqa: E402
from novelfound.ui.source_discover_dialog import SourceDiscoverDialog  # noqa: E402

fake = FakeHttp({
    "/search": (ROOT / "tests" / "fixtures" / "bing_search.html").read_text(
        encoding="utf-8"),
    "/search/?keyword=": (ROOT / "tests" / "fixtures" / "hetushu_search.html").read_text(
        encoding="utf-8"),
    "/book/27/index.html": (ROOT / "tests" / "fixtures" / "hetushu_book.html").read_text(
        encoding="utf-8"),
    "/book/27/17756.html": (ROOT / "tests" / "fixtures"
                            / "hetushu_chapter.html").read_text(encoding="utf-8"),
})

click_discover = SourceDiscoverDialog(window.config, fake, window.task_manager,
                                      book_title="斗罗大陆", parent=window)
click_discover.show()
click_discover.title_edit.setText("斗罗大陆")
click_discover.start_button.click()          # ← 真正点按钮
ok = wait_for(lambda: click_discover.result_list.count() > 0, 30)
check("点击「开始查找」不崩溃且出结果", ok,
      f"列表 {click_discover.result_list.count()} 行")
check("点击开始查找后按钮恢复",
      wait_for(lambda: click_discover.start_button.isEnabled(), 20), "")
click_discover.close()

# 空书名点击也不能崩
empty_discover = SourceDiscoverDialog(window.config, fake, window.task_manager,
                                      book_title="", parent=window)
empty_discover.show()
empty_discover.title_edit.setText("")
empty_discover.start_button.click()
pump(0.3)
check("空书名点击给出提示不崩溃",
      "请先填写书名" in empty_discover.log.toPlainText(),
      empty_discover.log.toPlainText()[:30])
empty_discover.close()

# 探测对话框：同样走"点按钮"路径
click_probe = SourceProbeDialog(window.config, fake, window.task_manager, window)
click_probe.show()
click_probe.url_edit.setText("https://www.hetushu.com")
click_probe.keyword_edit.setText("斗罗大陆")
click_probe.probe_button.click()             # ← 真正点按钮
ok = wait_for(lambda: click_probe.save_button.isEnabled(), 30)
check("点击「开始探测」不崩溃且探测成功", ok,
      click_probe.preview_label.text().replace("<br>", " ")[:50])
click_probe.close()

# ------------------------------------------------- 6c. 网络找书源对话框（离线喂结果）
from novelfound.sources.discover import (Candidate, DiscoveredSource,  # noqa: E402
                                         DiscoveryOutcome)
from novelfound.ui.source_discover_dialog import SourceDiscoverDialog  # noqa: E402

discover_dialog = SourceDiscoverDialog(window.config, window.http, window.task_manager,
                                       book_title="示例书", parent=window)
discover_dialog.resize(780, 660)
discover_dialog.show()
good_candidate = Candidate(host="www.demo-biquge.com", url="https://www.demo-biquge.com/x",
                           title="示例书_最新章节免费阅读-笔趣阁", score=6)
skipped_candidate = Candidate(host="www.qidian.com", url="https://www.qidian.com/bqd",
                              title="示例书_起点中文网", skipped="官方/付费站点")
good_probe = ProbeOutcome(base_url="https://www.demo-biquge.com", keyword="示例书", ok=True,
                          stage="完成", template="经典笔趣阁模板",
                          search_url="/search/?searchkey={q}", books_found=8,
                          chapter_count=112, char_count=1500, elapsed=2.0,
                          search_titles=["示例书"],
                          preview={"title": "示例书", "author": "某作者",
                                   "chapters": ["第一章", "第二章", "第三章"],
                                   "snippet": "正文开头示例内容。"},
                          rule={"key": "probe_www_demo_biquge_com",
                                "name": "www.demo-biquge.com（自动探测）",
                                "base_url": "https://www.demo-biquge.com",
                                "search_url": "/search/?searchkey={q}",
                                "content": ["#content"], "enabled_by_default": True})
discovery = DiscoveryOutcome(query="示例书", book_title="示例书", engine="bing_cn",
                             candidates=[good_candidate, skipped_candidate],
                             results=[DiscoveredSource(candidate=good_candidate,
                                                       outcome=good_probe, matched=True)])
before_discover = len(window.config.custom_sources())
discover_dialog._on_finished(discovery)
pump(0.4)
check("发现结果列表渲染", discover_dialog.result_list.count() >= 2,
      f"{discover_dialog.result_list.count()} 行")
check("被过滤站点标注原因",
      any("官方" in discover_dialog.result_list.item(i).text()
          for i in range(discover_dialog.result_list.count())), "")
check("发现成功后保存按钮可用", discover_dialog.save_button.isEnabled(), "")
discover_dialog.grab().save(str(OUT / "12_discover_dialog.png"))
discover_dialog.on_save()
pump(0.4)
check("发现书源写入配置",
      len(window.config.custom_sources()) == before_discover + 1,
      f"{before_discover} -> {len(window.config.custom_sources())}")
check("发现书源自动启用",
      window.config.is_source_enabled("probe_www_demo_biquge_com", False), "")
discover_dialog.close()
window.config.remove_custom_source("probe_www_demo_biquge_com")
window._on_settings_sources_changed()

# 自动禁用：给第一个书源灌入连续失败
target = window.sources[0]
threshold = int(window.config.get("auto_disable_after"))
for _ in range(threshold):
    window.stats.record(target.key, ok=False, error="模拟失败")
window._auto_disable_failing_sources()
pump(0.3)
check("连续失败后自动禁用",
      not window.config.is_source_enabled(target.key, True), target.key)
check("自动禁用给出提示", window.toast.isVisible(), window.toast.label.text()[:40])
check("评分记录可读取", "连续失败" in window.stats.describe(target.key),
      window.stats.describe(target.key))
# 复原，避免影响后续运行
window.config.set_source_enabled(target.key, True)
window.stats.reset(target.key)
window._on_settings_sources_changed()

# ---------------------------------------------------------- 7. 广告过滤（离线）
from novelfound.cleaner import extract_paragraphs, make_soup, strip_noise  # noqa: E402
sample_html = """
<div id="content">
  <p>第一段正文内容，足够长以便通过长度检查。</p>
  <a href="http://ad.example.com">点击这里领取福利 http://ad.example.com</a>
  <p>第二段正文，包含正常叙述。</p>
  <p>推广</p>
  <p>本站永久域名 www.example.com 请记住收藏</p>
  <p>Third line in plain ascii only</p>
  <p>第三段正文内容。</p>
</div>
"""
plain = extract_paragraphs(strip_noise(make_soup(sample_html)).select_one("#content"))
strict = extract_paragraphs(strip_noise(make_soup(sample_html)).select_one("#content"),
                            strict=True)
check("普通模式过滤广告行",
      not any("http" in p or "永久域名" in p or p == "推广" for p in plain),
      f"{len(plain)} 段")
check("严格模式进一步剔除纯英文行",
      not any("Third line" in p for p in strict) and any("Third line" in p for p in plain),
      f"普通={len(plain)} 严格={len(strict)}")
check("正文内容未被误删",
      any("第一段正文" in p for p in strict) and any("第三段正文" in p for p in strict),
      str(strict[:3]))

# ------------------------------------------------------------------ 7. 错误容错
from novelfound.models import Book, Chapter  # noqa: E402
from novelfound.tasks import ChapterTask  # noqa: E402
bad_book = Book(title="不存在的书", url="https://www.hetushu.com/book/999999/index.html",
                source="hetushu", source_name="和图书")
bad_chapter = Chapter(title="第一章", url="https://www.hetushu.com/book/999999/1.html")
results = {}
task = ChapterTask(window.sources[0] if window.sources else None, bad_book, bad_chapter, None)
task.signals.failed.connect(lambda m, d: results.update({"msg": m, "detail": d}))
task.signals.finished.connect(lambda r: results.update({"msg": "unexpected"}))
window.task_manager.start(task)
wait_for(lambda: bool(results), 30)
check("无效链接给出友好错误",
      bool(results.get("msg")) and results.get("msg") != "unexpected",
      str(results.get("msg"))[:60])

# ------------------------------------------------------------------ 8. 设置对话框
from novelfound.ui.settings_dialog import SettingsDialog  # noqa: E402
dialog = SettingsDialog(window.config, window.http, window.cache, window.sources,
                        window.task_manager, window)
dialog.resize(780, 620)
dialog.show()
pump(0.6)
check("书源列表非空", dialog.source_list.count() >= 3, f"{dialog.source_list.count()} 条")
check("设置页不再有侧栏选项", not hasattr(dialog, "auto_hide_sidebar"), "")
check("设置页不再重复阅读项（已收进 Aa 浮层）",
      not any(hasattr(dialog, name) for name in
              ("font_size", "theme_box", "columns_box", "first_indent",
               "content_width", "line_height", "mode_box")), "")
check("阅读项只在阅读器浮层里", 
      window.reader.settings_popover.indent_box.value() ==
      int(window.config.get("first_line_indent") or 0),
      f"indent={window.reader.settings_popover.indent_box.value()}")
check("设置页含同站请求间隔与封面并发",
      dialog.request_interval.value() > 0 and dialog.cover_concurrent.value() >= 1,
      f"interval={dialog.request_interval.value()} cover={dialog.cover_concurrent.value()}")
check("设置页含探测入口",
      any("探测新书源" in b.text() for b in dialog.findChildren(QPushButton)), "")
dialog.grab().save(str(OUT / "09_settings.png"))
# 对话框底色也必须是暖白（回归：页签面板曾用卡片色 BG_SURFACE，看着比主窗口白一档）
_dialog_img = dialog.grab().toImage()
_dialog_pixel = _dialog_img.pixelColor(_dialog_img.width() // 2,
                                       _dialog_img.height() // 2)
check("设置对话框底色为暖白", _dialog_pixel.name().upper() == "#F7F3E9",
      f"#{_dialog_pixel.name()}")
check("设置项默认值", dialog.strict_filter is not None and dialog.timeout.value() >= 3,
      f"timeout={dialog.timeout.value()}")
dialog.close()

# ------------------------------------------------------------------ 汇总
print("\n================ 汇总 ================")
print(f"通过 {sum(1 for _, ok, _ in CHECKS if ok)} / {len(CHECKS)}")
for name, ok, extra in CHECKS:
    if not ok:
        print(f"  FAIL {name} {extra}")
print(f"截图目录：{OUT}")
sys.exit(1 if FAILS else 0)

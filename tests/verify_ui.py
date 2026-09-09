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

try:
    sys.stdout.reconfigure(encoding="utf-8")
except AttributeError:  # pragma: no cover
    pass

# 每次自检都从干净状态开始，避免历史配置影响断言
shutil.rmtree(os.environ["NOVELFOUND_HOME"], ignore_errors=True)

from PyQt5.QtCore import QEvent, QEventLoop, QPoint, Qt  # noqa: E402
from PyQt5.QtGui import QKeyEvent  # noqa: E402
from PyQt5.QtWidgets import QApplication, QLabel, QPushButton  # noqa: E402

from novelfound.ui.main_window import MainWindow  # noqa: E402
from novelfound.ui.theme import app_stylesheet  # noqa: E402

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
window = MainWindow()
window.resize(1360, 880)
window.show()
pump(0.6)

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
          window.catalog_drawer.catalog.count() == len(detail.chapters),
          f"{window.catalog_drawer.catalog.count()} / {len(detail.chapters)}")
    window.catalog_drawer.filter_box.setText("第一章")
    pump(0.3)
    visible = sum(1 for i in range(window.catalog_drawer.catalog.count())
                  if not window.catalog_drawer.catalog.item(i).isHidden())
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
              window.catalog_drawer.catalog.currentRow() ==
              window.reader.chapter_index,
              f"row={window.catalog_drawer.catalog.currentRow()} "
              f"index={window.reader.chapter_index}")
        check("抽屉当前章有 ▶ 标记",
              window.catalog_drawer.catalog.item(
                  window.catalog_drawer.catalog.currentRow()).text().startswith("▶"),
              "")
        window.close_catalog_drawer()
        pump(0.2)
        check("关闭抽屉后遮罩隐藏", not window.scrim.isVisible(), "")

        # ---- 分栏（左右双页）----
        window.reader.mode_button.setChecked(True)      # 切到翻页模式
        pump(0.8)
        check("翻页模式已启用", window.reader._page_mode, "")
        check("单页模式右页隐藏", not window.reader.view2.isVisible(), "")
        single_pages = len(window.reader._page_offsets)
        check("单页模式已分页", single_pages > 1, f"pages={single_pages}")

        window.reader.columns_box.setCurrentIndex(
            window.reader.columns_box.findData(2))
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
        # 溢出回归的根因：Qt 比例行距下 line.height() 只是文字高度，
        # 真实行步进要乘行距倍数（30 × 1.9 = 57），漏乘就会一页多塞两三行。
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
        # 最后一页是本章剩余内容，天然不满，只校验前面各页
        body_fills = fills[:-1] or fills
        check("每页都装满（不是只塞一行）", min(body_fills) >= 0.8,
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
        spread_before = window.reader._spread
        viewport = window.reader.view.viewport()
        point = QPoint(viewport.width() // 2, viewport.height() // 2)
        wheel = QWheelEvent(point, viewport.mapToGlobal(point), QPoint(0, 0),
                            QPoint(0, -120), Qt.NoButton, Qt.NoModifier,
                            Qt.NoScrollPhase, False)
        app.sendEvent(viewport, wheel)
        pump(0.3)
        check("滚轮在正文上翻页", window.reader._spread == spread_before + 1,
              f"{spread_before} -> {window.reader._spread}")

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
        window.reader.columns_box.setCurrentIndex(
            window.reader.columns_box.findData(1))
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
        indent = window.reader.view.document().firstBlock().blockFormat().textIndent()
        check("默认首行缩进生效（2 字符）", indent > 0, f"indent={indent:.1f}px")
        window.config.set("first_line_indent", 0)
        window.reader.apply_settings()
        window.reader.render()
        pump(0.3)
        indent_off = window.reader.view.document().firstBlock().blockFormat().textIndent()
        check("首行缩进可关闭", indent_off == 0, f"indent={indent_off}")
        window.config.set("first_line_indent", 2)
        window.reader.apply_settings()
        window.reader.render()
        pump(0.3)
        indent_on = window.reader.view.document().firstBlock().blockFormat().textIndent()
        check("首行缩进可恢复", indent_on > 0, f"indent={indent_on:.1f}px")

        window.config.set("reader_theme", "night")
        window.reader.apply_settings()
        window.reader.render()
        stats = image_stats(shot("05_reader_night"))
        check("夜间主题渲染为深色", stats["lum"] < 110, f"lum={stats['lum']:.1f}")

        window.config.set("reader_theme", "sepia")
        window.reader.apply_settings()
        window.reader.mode_button.setChecked(True)
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

        night_index = window.reader.theme_box.findData("night")
        window.reader.theme_box.setCurrentIndex(night_index)
        pump(0.4)
        cursor = window.reader.view.textCursor()
        cursor.setPosition(1)
        color = cursor.charFormat().foreground().color().name()
        check("下拉切换主题后文字颜色同步", color.lower() == "#c2c9d1", f"color={color}")
        eye_index = window.reader.theme_box.findData("eye")
        window.reader.theme_box.setCurrentIndex(eye_index)
        pump(0.3)

        # 滚动位置恢复属于滚动模式，先切回去
        window.reader.mode_button.setChecked(False)
        pump(0.6)
        check("切回滚动模式", not window.reader._page_mode, "")

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
check("最近阅读记录存在", bool(window.library.history()),
      f"{len(window.library.history())} 条")
saved_progress = window.library.progress(window.current_book.key)
check("继续阅读卡片存在", window.library_view.continue_card is not None, "")
if window.library_view.continue_card is not None:
    card = window.library_view.continue_card
    check("继续阅读显示上次章节",
          str(saved_progress.get("index", -1) + 1) in card.chapter_label.text(),
          card.chapter_label.text())
    check("继续阅读显示百分比文字",
          card.percent_label.text().endswith("%") or card.percent_label.text() == "未读",
          card.percent_label.text())
    check("继续阅读卡片不放进度条", not card.findChildren(ProgressLine), "")
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
window.open_catalog_drawer()
pump(0.4)
check("抽屉可打开", window.catalog_drawer.is_open(), "")
check("抽屉打开时遮罩可见", window.scrim.isVisible(), "")
shot("13_catalog_drawer")
window.scrim.clicked.emit()          # 点遮罩关闭
pump(0.3)
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
check("设置页含首行缩进项", dialog.first_indent.value() == 2,
      f"value={dialog.first_indent.value()}")
check("设置页不再有侧栏选项", not hasattr(dialog, "auto_hide_sidebar"), "")
check("设置页含同站请求间隔与封面并发",
      dialog.request_interval.value() > 0 and dialog.cover_concurrent.value() >= 1,
      f"interval={dialog.request_interval.value()} cover={dialog.cover_concurrent.value()}")
check("设置页含探测入口",
      any("探测新书源" in b.text() for b in dialog.findChildren(QPushButton)), "")
dialog.grab().save(str(OUT / "09_settings.png"))
check("设置项默认值", dialog.font_size.value() >= 12 and dialog.timeout.value() >= 3,
      f"font={dialog.font_size.value()} timeout={dialog.timeout.value()}")
dialog.close()

# ------------------------------------------------------------------ 汇总
print("\n================ 汇总 ================")
print(f"通过 {sum(1 for _, ok, _ in CHECKS if ok)} / {len(CHECKS)}")
for name, ok, extra in CHECKS:
    if not ok:
        print(f"  FAIL {name} {extra}")
print(f"截图目录：{OUT}")
sys.exit(1 if FAILS else 0)

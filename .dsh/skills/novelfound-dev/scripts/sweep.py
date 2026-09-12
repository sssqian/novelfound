# -*- coding: utf-8 -*-
"""全量扫描：把一本真实电子书**每一章**都逐屏走一遍，验证分页/翻页没有丢内容。

为什么要有这个脚本：分页"内容缺一段"那个 bug 修完后，我只抽查了 6 章就下了结论，
用户立刻反问"这能保证所有章节吗"。抽查 6/1408 章说明不了问题，而**逐章扫描很快**
（实测 12 章 1 秒 → 整本 1408 章约 2 分钟），所以凡是改分页 / 翻页 / 行距 / 正文宽度，
都应该跑一次全量。

检查项（全部盯"用户能看到的结果"，不看实现内部的量）：

1. **内容完整性**：逐屏收集的文字（去空白）与整章原文**逐字比较** → 抓"丢内容"
2. **页偏移合法**：`_page_offsets` 严格递增、不超界 → 抓"分页算错"
3. **视口宽度恒定**：翻页模式全程不变 → 抓"滚动条挤窄正文"的反馈环

用法：

    # 0) 一次性准备样本（沙箱不能写 %APPDATA%，所以复制进工作区；
    #    tests/.testdata/ 已被 .gitignore 忽略，且 verify.py 不会清它）
    $dst = "tests\\.testdata\\books"
    New-Item -ItemType Directory -Force "$dst\\local_books" | Out-Null
    Copy-Item "$env:APPDATA\\NovelFound\\local_books.json" $dst
    Copy-Item "$env:APPDATA\\NovelFound\\local_books\\*" "$dst\\local_books\\"

    # 1) 扫描（format = epub / txt）
    $env:NOVELFOUND_HOME = (Resolve-Path tests\\.testdata\\books)
    .\\.venv\\Scripts\\python.exe .dsh\\skills\\novelfound-dev\\scripts\\sweep.py epub
    .\\.venv\\Scripts\\python.exe .dsh\\skills\\novelfound-dev\\scripts\\sweep.py txt 0 500

退出码：0 全过；1 有失败章节；2 前置条件不满足（例如视口尺寸不合理 → 拒绝假绿）。
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from harness import make_app, make_window, pump, wait_for          # noqa: E402
from novelfound.localbooks import LocalBooks                       # noqa: E402
from novelfound.sources.local import LocalSource                   # noqa: E402

MIN_VIEWPORT_HEIGHT = 400      # 低于这个高度说明没切到阅读器页面，分页会走兜底
MIN_VIEWPORT_WIDTH = 300
PROGRESS_EVERY = 200


def _chapter_stub(book, index: int, title: str):
    """构造一个只带索引的 Chapter（扫描时不需要更多字段）。"""
    from novelfound.models import Chapter
    return Chapter(title=title, url=f"{book.url}/{index}", index=index)


def main() -> int:
    fmt = (sys.argv[1] if len(sys.argv) > 1 else "epub").lower()
    start = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    end = int(sys.argv[3]) if len(sys.argv) > 3 else 10 ** 9

    app = make_app(clean=False)
    window = make_window()
    library = LocalBooks()
    record = next((i for i in library.all() if i.get("format") == fmt), None)
    if record is None:
        print(f"!! 样本里没有 {fmt} 格式的书（NOVELFOUND_HOME={os.environ.get('NOVELFOUND_HOME')}）")
        return 2
    book = library.to_book(record)
    source = LocalSource(None, library)
    entries = record["chapters"]
    total = min(end, len(entries))
    print(f"《{book.title}》 {fmt.upper()} 共 {len(entries)} 章，扫描 {start}..{total - 1}",
          flush=True)

    # 必须真的切到阅读器页面：否则 viewport 只有几十像素高、分页走兜底，
    # 扫描会变成"每章 1 页、全部通过"的**假绿**（真实踩到过：视口 30px）。
    reader = window.reader
    window.library.add(book, source.fetch_detail(book))
    window.on_book_clicked(book)
    wait_for(app, lambda: window.current_detail is not None, 30)
    window.on_read_requested(window.current_detail, start)
    wait_for(app, lambda: int(reader.chapter_index) == start and bool(reader._paragraphs), 60)
    pump(app, 0.8)
    reader.settings_popover.mode_button.setChecked(True)          # 翻页模式
    pump(app, 0.5)
    reader.settings_popover.columns_box.setCurrentIndex(
        reader.settings_popover.columns_box.findData(2))          # 双页
    pump(app, 0.8)

    width = reader.view.viewport().width()
    height = reader.view.viewport().height()
    print(f"翻页模式={reader._page_mode} 双页={reader._two_page} 视口={width}x{height}",
          flush=True)
    if height < MIN_VIEWPORT_HEIGHT or width < MIN_VIEWPORT_WIDTH:
        print(f"!! 视口尺寸不合理（{width}x{height}）：分页会走兜底，这次扫描毫无意义")
        return 2

    failures: list = []
    widths = set()
    page_counts: list = []
    slowest = (0.0, -1)
    started = time.time()
    for index in range(start, total):
        title = entries[index].get("title", "")
        t0 = time.time()
        reader.set_content(source.fetch_chapter(book, _chapter_stub(book, index, title)),
                           index=index)
        spreads = reader.spread_count()
        pieces = []
        for spread in range(spreads):
            pieces.append(reader.view.toPlainText())
            if reader._two_page:
                pieces.append(reader.view2.toPlainText())
            if spread < spreads - 1:          # 最后一屏别再翻，否则会跳到下一章
                reader.next_page()
        elapsed = time.time() - t0
        if elapsed > slowest[0]:
            slowest = (elapsed, index)
        widths.add(reader.view.viewport().width())
        page_counts.append(len(reader._page_offsets))

        walked = "".join("".join(pieces).split())
        original = "".join("".join(reader._paragraphs).split())
        offsets = list(reader._page_offsets)
        problems = []
        if walked != original:
            problems.append(f"内容不符(差 {len(original) - len(walked)} 字)")
        if offsets != sorted(set(offsets)):
            problems.append("页偏移不单调")
        if offsets and reader._text_length and offsets[-1] > reader._text_length:
            problems.append("页偏移超界")
        if problems:
            failures.append((index, title, problems))
        if (index - start + 1) % PROGRESS_EVERY == 0:
            done = index - start + 1
            used = time.time() - started
            print(f"  …{done}/{total - start} 章，用时 {used:.0f}s，"
                  f"预计还需 {used / done * (total - start - done):.0f}s，"
                  f"失败 {len(failures)}", flush=True)

    used = time.time() - started
    print(f"\n===== 扫描完成：{total - start} 章，用时 {used:.0f}s =====", flush=True)
    print(f"失败章节数：{len(failures)}", flush=True)
    for index, title, problems in failures[:20]:
        print(f"  ✗ 第{index + 1}章《{title}》：{'；'.join(problems)}", flush=True)
    print(f"视口宽度集合：{sorted(widths)}（应当只有一个）", flush=True)
    print(f"每章页数：最少 {min(page_counts)}，最多 {max(page_counts)}，"
          f"平均 {sum(page_counts) / len(page_counts):.1f}", flush=True)
    print(f"最慢一章：第{slowest[1] + 1}章 {slowest[0]:.2f}s", flush=True)
    print("结论：", "全部章节通过" if not failures else "存在失败章节，见上", flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

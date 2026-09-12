# -*- coding: utf-8 -*-
"""离屏窗口脚手架：省掉每个一次性验证脚本的 40 行样板。

用法（**必须先 import harness，再 import novelfound**，因为环境变量要在
Qt/配置模块导入前设好）：

    import sys
    sys.path.insert(0, r"D:\\AI\\option\\novelfound\\.dsh\\skills\\novelfound-dev\\scripts")
    from harness import make_app, pump, wait_for, make_window, synthetic_chapter, fake_detail

    app = make_app()
    window = make_window()
    window.reader.set_content(synthetic_chapter(), index=0)
    pump(app, 0.4)

约定：
* 数据目录默认 `tests/.testdata/smoke`（每次 `make_app(clean=True)` 清空），
  不会碰到用户真实书架。放在 `.testdata/` 下是因为 `.gitignore` 已忽略它、
  且 `verify.py` 不会清（`tests/.tmp` 每轮都会被清掉，不适合放样本）。
* 要拿用户真实的书做验证：复制到 `tests/.testdata/books/`，再把
  `NOVELFOUND_HOME` 指过去（沙箱写不了 `%APPDATA%`）。见 `scripts/sweep.py`。
* 需要真实显示器时把 QT_QPA_PLATFORM 设为空字符串再调用 make_app()。
"""
from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path


# --------------------------------------------------------------------------- 环境
def repo_root() -> Path:
    for candidate in [Path(__file__).resolve(), *Path(__file__).resolve().parents]:
        if (candidate / "main.py").is_file() and (candidate / "novelfound").is_dir():
            return candidate
    raise SystemExit("找不到仓库根（需要 main.py 与 novelfound/）")


ROOT = repo_root()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("NOVELFOUND_HOME", str(ROOT / "tests" / ".testdata" / "smoke"))

# 离屏平台默认不加载字体 → 界面上的文字全都画不出来（截图里只有色块和图片）。
# 必须在 QApplication 创建之前指定系统字体目录，Qt 的 basic font database 才会扫描。
if not os.environ.get("QT_QPA_FONTDIR"):
    for _candidate in (Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts",
                       Path("/System/Library/Fonts"),
                       Path("/usr/share/fonts")):
        if _candidate.is_dir():
            os.environ["QT_QPA_FONTDIR"] = str(_candidate)
            break


# --------------------------------------------------------------------------- 应用
def make_app(clean: bool = True):
    """建一个离屏 QApplication（并清空测试数据目录）。"""
    if clean:
        shutil.rmtree(os.environ["NOVELFOUND_HOME"], ignore_errors=True)
    from PyQt5.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv[:1])
    return app


def make_window(width: int = 1360, height: int = 880, show: bool = True):
    """建并显示真主窗口（MainWindow）。"""
    from novelfound.ui.main_window import MainWindow
    from novelfound.ui.theme import app_stylesheet
    app = make_app(clean=False)
    app.setStyleSheet(app_stylesheet())
    window = MainWindow()
    window.resize(width, height)
    if show:
        window.show()
    return window


def pump(app, seconds: float) -> None:
    """跑事件循环 seconds 秒（动画/异步任务要靠这个推进）。"""
    from PyQt5.QtCore import QEventLoop
    deadline = time.time() + seconds
    while time.time() < deadline:
        app.processEvents(QEventLoop.AllEvents, 50)
        time.sleep(0.01)


def wait_for(app, predicate, seconds: float = 30.0, interval: float = 0.05) -> bool:
    """等条件成立（联网任务用）。"""
    from PyQt5.QtCore import QEventLoop
    deadline = time.time() + seconds
    while time.time() < deadline:
        if predicate():
            return True
        app.processEvents(QEventLoop.AllEvents, 50)
        time.sleep(interval)
    return predicate()


# --------------------------------------------------------------------------- 假数据
def synthetic_chapter(paragraphs: int = 80, url: str = "about:selftest",
                      title: str = "分页自检章节", chars: int = 48):
    """内容量确定的合成章节。

    联网自检偶尔会命中"整章只有 272 字"的短章节，让"翻页前进 / 滚动恢复"
    这类相对断言失真——分页相关断言一律用这个。
    """
    from novelfound.models import ChapterContent
    unit = "这是用于自检的正文内容，长度大致固定。"
    body = []
    for i in range(paragraphs):
        text = f"第 {i} 段：" + unit * 3
        body.append(text[:chars] if len(text) > chars else text)
    return ChapterContent(title=title, paragraphs=body, url=url)


def fake_book(title: str = "测试书", url: str = "http://example.test/book/1",
              source: str = "hetushu", source_name: str = "和图书"):
    from novelfound.models import Book
    return Book(title=title, author="测试作者", url=url, source=source,
                source_name=source_name, intro="这是测试简介。" * 6)


def fake_detail(chapters: int = 30, book=None):
    from novelfound.models import BookDetail, Chapter
    book = book or fake_book()
    return BookDetail(book=book, chapters=[
        Chapter(title=f"第{i + 1}章 测试章节", url=f"{book.url}/c{i}", index=i)
        for i in range(chapters)])


def add_to_shelf(window, title: str = "测试书", chapters: int = 30,
                 read_index: int = 9):
    """往书架塞一本"有章节数、有进度"的书（首页网格 / 百分比断言用）。"""
    book = fake_book(title=title, url=f"http://example.test/book/{title}")
    window.library.add(book, fake_detail(chapters=chapters, book=book))
    window.library.update_progress(book, f"{book.url}/c{read_index}",
                                   f"第{read_index + 1}章 测试章节", read_index,
                                   char_offset=100)
    window._refresh_library()
    return book

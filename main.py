# -*- coding: utf-8 -*-
"""程序入口。

运行：
    python main.py

自检（无需打开界面，用于确认打包后的 exe 抓取链路正常）：
    python main.py --selftest [关键词]
    dist\\NovelFound\\NovelFound.exe --selftest 斗罗大陆
    结果会打印到控制台，同时写入数据目录下的 selftest.txt

打包（Windows/macOS）：
    pip install pyinstaller
    pyinstaller --noconfirm --distpath dist --workpath build/pyi build/novelfound.spec
"""
from __future__ import annotations

import os
import sys

# 高 DPI 支持需要在导入 Qt 之前设置
os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")


def run_selftest(keyword: str = "斗罗大陆") -> int:
    """不打开界面，跑一遍 搜索 → 目录 → 正文 的完整链路。

    返回 0 表示成功。结果同时写入数据目录的 selftest.txt，
    方便在打包成无控制台的 exe 后查看。
    """
    from novelfound.config import data_dir
    from novelfound.net import HttpSession, NovelError
    from novelfound.sources import build_sources
    from novelfound.config import AppConfig

    lines = []

    def log(text: str) -> None:
        lines.append(text)
        try:
            print(text, flush=True)
        except Exception:      # 无控制台（windowed exe）时忽略
            pass

    log(f"NovelFound 自检开始，关键词：{keyword}")
    config = AppConfig()
    http = HttpSession(timeout=float(config.get("timeout")),
                       retries=int(config.get("retries")))
    sources = build_sources(config, http)
    log(f"启用书源：{', '.join(s.name for s in sources) or '（无）'}")
    ok = False
    for source in sources:
        try:
            books = source.search(keyword, limit=3)
            log(f"[{source.name}] 搜索到 {len(books)} 本")
            if not books:
                continue
            detail = source.fetch_detail(books[0])
            log(f"[{source.name}] 《{detail.book.title}》 作者 {detail.book.author} "
                f"目录 {len(detail.chapters)} 章")
            if not detail.chapters:
                continue
            content = source.fetch_chapter(detail.book, detail.chapters[0])
            log(f"[{source.name}] 正文《{content.title}》 {len(content.paragraphs)} 段 "
                f"{content.char_count} 字")
            if content.char_count < 20:
                log(f"[{source.name}] 正文过短，判定失败")
                continue
            ok = True
            break
        except NovelError as exc:
            log(f"[{source.name}] 失败：{exc}")
        except Exception as exc:  # noqa: BLE001
            log(f"[{source.name}] 异常：{type(exc).__name__} {exc}")
    log("自检结果：" + ("通过" if ok else "失败"))
    try:
        report = data_dir() / "selftest.txt"
        report.write_text("\n".join(lines), encoding="utf-8")
        print(f"报告已写入：{report}", flush=True)
    except Exception:
        pass
    return 0 if ok else 1


def main() -> int:
    # 先装崩溃兜底：槽函数里的异常会写进数据目录的 crash.log，
    # 否则窗口化的 exe 遇到这类错误会直接消失、什么都不留。
    from novelfound.errors import install_crash_handler
    install_crash_handler()

    if "--selftest" in sys.argv:
        args = [a for a in sys.argv[1:] if not a.startswith("-")]
        return run_selftest(args[0] if args else "斗罗大陆")

    from PyQt5.QtCore import Qt
    from PyQt5.QtGui import QFont
    from PyQt5.QtWidgets import QApplication

    from novelfound.config import APP_NAME
    from novelfound.ui.main_window import MainWindow
    from novelfound.ui.theme import app_stylesheet

    # Qt 5.14+ 需要显式开启高 DPI 缩放
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setStyleSheet(app_stylesheet())

    # 中文字体优先，避免部分系统默认字体显示发虚
    font = QFont()
    font.setFamily("Microsoft YaHei UI" if sys.platform.startswith("win") else "")
    font.setPointSize(10)
    app.setFont(font)

    window = MainWindow()
    window.show()
    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())

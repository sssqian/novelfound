# -*- coding: utf-8 -*-
"""崩溃与异常兜底。

PyQt5 有个要命的行为：**Qt 槽函数里抛出的未捕获异常会调用 qFatal() → abort()**，
窗口化打包的程序（`console=False`）因此会"直接消失"，且看不到任何提示。
本模块提供两层保护：

1. :func:`install_crash_handler`：把未捕获异常（主线程 / 子线程）连同 traceback
   写入数据目录的 ``crash.log``，并打开 faulthandler 以便原生崩溃也能留下 Python 栈；
2. :func:`guard_slot`：给"按钮点击"这类槽函数套一层保护——出错时记日志并返回 None，
   让程序继续活着（用户会在界面上看到失败提示，而不是整个程序消失）。
"""
from __future__ import annotations

import faulthandler
import functools
import inspect
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Any, Callable, Optional

_installed = False
_fault_file = None
_log_path: Optional[Path] = None
_lock = threading.Lock()


def crash_log_path() -> Path:
    """崩溃日志路径（放在用户数据目录）。"""
    global _log_path
    if _log_path is None:
        from .config import data_dir
        _log_path = data_dir() / "crash.log"
    return _log_path


def write_crash_log(title: str, exc_type=None, exc=None, tb=None,
                    extra: str = "") -> Optional[Path]:
    """追加一条崩溃记录，返回日志路径（写失败则返回 None）。"""
    try:
        path = crash_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with _lock, open(path, "a", encoding="utf-8") as f:
            f.write("=" * 70 + "\n")
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {title}\n")
            if extra:
                f.write(extra.rstrip() + "\n")
            if exc is not None:
                f.write("".join(traceback.format_exception(exc_type, exc, tb)))
            f.write("\n")
        return path
    except OSError:
        return None


def _excepthook(exc_type, exc, tb) -> None:
    write_crash_log("未捕获异常（主线程）", exc_type, exc, tb)
    traceback.print_exception(exc_type, exc, tb)


def _thread_excepthook(args) -> None:
    write_crash_log(f"未捕获异常（线程 {getattr(args.thread, 'name', '?')}）",
                    args.exc_type, args.exc_value, args.exc_traceback)


def install_crash_handler() -> None:
    """安装全局异常钩子（可重复调用）。"""
    global _installed, _fault_file
    if _installed:
        return
    _installed = True
    sys.excepthook = _excepthook
    try:
        threading.excepthook = _thread_excepthook
    except AttributeError:      # Python < 3.8
        pass
    try:
        path = crash_log_path()
        if not path.exists() or path.stat().st_size == 0:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write("NovelFound 崩溃日志：正常情况下这里是空的。\n"
                        "程序异常退出或某个操作报内部错误时，完整堆栈会追加在下面。\n\n")
        _fault_file = open(path, "a", encoding="utf-8")
        faulthandler.enable(file=_fault_file, all_threads=True)
    except (OSError, ValueError):
        pass


def guard_slot(func: Callable) -> Callable:
    """装饰 Qt 槽函数：异常不再让整个程序崩溃，而是记日志 + 返回 None。

    注意：PyQt 会把信号自带的参数一起传进槽（例如 ``clicked(bool)``），
    而我们的槽通常只接受 ``self``，所以这里按原函数签名裁剪多余参数。

    用法::

        @guard_slot
        def on_start(self) -> None: ...
    """
    try:
        signature = inspect.signature(func)
    except (TypeError, ValueError):        # pragma: no cover - 内置函数等
        signature = None
    if signature is not None:
        params = list(signature.parameters.values())
        accepts_varargs = any(p.kind == p.VAR_POSITIONAL for p in params)
        max_positional = len([p for p in params
                              if p.kind in (p.POSITIONAL_ONLY,
                                            p.POSITIONAL_OR_KEYWORD)])
    else:                                   # pragma: no cover
        accepts_varargs, max_positional = True, None

    @functools.wraps(func)
    def wrapper(*args, **kwargs) -> Any:
        call_args = args if accepts_varargs else args[:max_positional]
        try:
            return func(*call_args, **kwargs)
        except Exception as exc:        # noqa: BLE001 - 这里就是要兜住一切
            path = write_crash_log(f"槽函数异常：{func.__qualname__}",
                                   type(exc), exc, exc.__traceback__)
            traceback.print_exc()
            # 尽量在界面上给个提示，而不是静默
            for candidate in call_args:
                banner = getattr(candidate, "banner", None)
                if banner is not None and hasattr(banner, "show_message"):
                    try:
                        banner.show_message(
                            f"操作失败（内部错误）：{exc}。详情已写入 {path}", "error")
                    except Exception:   # noqa: BLE001
                        pass
                    break
            return None

    return wrapper

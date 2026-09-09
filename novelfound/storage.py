# -*- coding: utf-8 -*-
"""小型持久化工具。

统一"先写临时文件再原子替换"的写法，避免各处重复实现。

注意：这里刻意**不用** ``tempfile.mkstemp``——它在某些受限环境下会卡住
（本次开发中就遇到了），而"同目录 + 固定后缀 + 进程号"的临时文件同样安全：
写入方持有各自的锁，且 ``os.replace`` 在同一文件系统内是原子的。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def _temp_path(path: Path) -> Path:
    return path.with_name(f"{path.name}.{os.getpid()}.tmp")


def atomic_write_text(path: Path, text: str) -> bool:
    """原子写入文本，成功返回 True。"""
    tmp = _temp_path(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        return True
    except OSError:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        return False


def atomic_write_json(path: Path, data: Any, indent: int = 1) -> bool:
    """原子写入 JSON（UTF-8、不转义中文）。"""
    try:
        text = json.dumps(data, ensure_ascii=False, indent=indent)
    except (TypeError, ValueError):
        return False
    return atomic_write_text(path, text)


def read_json(path: Path, default: Any = None) -> Any:
    """读取 JSON；文件不存在或损坏时返回 default。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default

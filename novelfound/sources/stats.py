# -*- coding: utf-8 -*-
"""书源健康度统计。

给每个书源记录成功率、平均耗时、连续失败次数和最近一次错误，
用于：
* 在书源列表里展示"这个源还活着吗"；
* 连续失败超过阈值时**自动禁用**，避免每次搜索都白等一遍超时。

数据单独存在 ``source_stats.json``（与 config.json 分开，避免频繁改写主配置）。
工作线程会调用 :meth:`record`，因此内部加锁并做写入节流。
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..config import data_dir
from ..storage import atomic_write_json


class SourceStats:
    """书源健康度记录（线程安全）。"""

    def __init__(self, path: Optional[Path] = None):
        self.path = path or (data_dir() / "source_stats.json")
        self._lock = threading.RLock()
        self._data: Dict[str, Dict[str, Any]] = {}
        self._dirty = False
        self._last_save = 0.0
        self.load()

    # ------------------------------------------------------------------ 读写
    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                self._data = {k: v for k, v in data.items() if isinstance(v, dict)}
        except (OSError, ValueError):
            pass

    def save(self) -> None:
        with self._lock:
            if not self._dirty:
                return
            if atomic_write_json(self.path, self._data):
                self._dirty = False
                self._last_save = time.time()

    def _maybe_save(self) -> None:
        """写入节流：工作线程里最多每 5 秒落盘一次。"""
        if self._dirty and (time.time() - self._last_save) > 5:
            self.save()

    # ------------------------------------------------------------------ 记录
    def _entry(self, key: str) -> Dict[str, Any]:
        entry = self._data.get(key)
        if entry is None:
            entry = {"success": 0, "failure": 0, "consecutive_failures": 0,
                     "last_ok_at": 0.0, "last_error": "", "last_error_at": 0.0,
                     "total_elapsed": 0.0, "samples": 0, "auto_disabled_at": 0.0}
            self._data[key] = entry
        return entry

    def record(self, key: str, ok: bool, elapsed: float = 0.0,
               error: str = "") -> None:
        """记录一次调用结果。"""
        if not key:
            return
        with self._lock:
            entry = self._entry(key)
            if ok:
                entry["success"] = int(entry.get("success", 0)) + 1
                entry["consecutive_failures"] = 0
                entry["last_ok_at"] = time.time()
                if elapsed > 0:
                    entry["total_elapsed"] = float(entry.get("total_elapsed", 0)) + elapsed
                    entry["samples"] = int(entry.get("samples", 0)) + 1
            else:
                entry["failure"] = int(entry.get("failure", 0)) + 1
                entry["consecutive_failures"] = int(entry.get("consecutive_failures", 0)) + 1
                entry["last_error"] = (error or "未知错误")[:200]
                entry["last_error_at"] = time.time()
            self._dirty = True
            self._maybe_save()

    def reset(self, key: Optional[str] = None) -> None:
        """重置某个书源（或全部）的统计。"""
        with self._lock:
            if key:
                self._data.pop(key, None)
            else:
                self._data.clear()
            self._dirty = True
        self.save()

    # ------------------------------------------------------------------ 查询
    def snapshot(self, key: str) -> Dict[str, Any]:
        with self._lock:
            entry = dict(self._data.get(key) or {})
        success = int(entry.get("success", 0))
        failure = int(entry.get("failure", 0))
        total = success + failure
        samples = int(entry.get("samples", 0))
        return {
            "success": success,
            "failure": failure,
            "rate": (success / total) if total else None,
            "avg_elapsed": (float(entry.get("total_elapsed", 0)) / samples) if samples else 0.0,
            "consecutive_failures": int(entry.get("consecutive_failures", 0)),
            "last_ok_at": float(entry.get("last_ok_at", 0)),
            "last_error": entry.get("last_error", ""),
            "last_error_at": float(entry.get("last_error_at", 0)),
            "auto_disabled_at": float(entry.get("auto_disabled_at", 0)),
        }

    def should_disable(self, key: str, threshold: int) -> bool:
        """连续失败是否已达到自动禁用阈值。"""
        if threshold <= 0:
            return False
        with self._lock:
            entry = self._data.get(key) or {}
        return int(entry.get("consecutive_failures", 0)) >= threshold

    def mark_auto_disabled(self, key: str) -> None:
        with self._lock:
            entry = self._entry(key)
            entry["auto_disabled_at"] = time.time()
            self._dirty = True
        self.save()

    def describe(self, key: str) -> str:
        """给界面用的一行摘要。"""
        snap = self.snapshot(key)
        if not snap["success"] and not snap["failure"]:
            return "未使用"
        parts: List[str] = []
        if snap["rate"] is not None:
            parts.append(f"成功率 {snap['rate'] * 100:.0f}%")
        if snap["avg_elapsed"]:
            parts.append(f"平均 {snap['avg_elapsed']:.1f}s")
        if snap["last_ok_at"]:
            parts.append("最近可用 " + time.strftime("%m-%d %H:%M",
                                                time.localtime(snap["last_ok_at"])))
        if snap["consecutive_failures"]:
            parts.append(f"连续失败 {snap['consecutive_failures']} 次")
        return "　·　".join(parts) or "未使用"

    def failing_sources(self, keys: List[str], threshold: int) -> List[str]:
        """返回达到自动禁用阈值的书源 key 列表。"""
        return [k for k in keys if self.should_disable(k, threshold)]

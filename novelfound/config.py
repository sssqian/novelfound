# -*- coding: utf-8 -*-
"""应用配置与数据目录。

配置以 JSON 保存在用户数据目录，便于手动修改：
    Windows: %APPDATA%/NovelFound/config.json
    macOS:   ~/Library/Application Support/NovelFound/config.json
    Linux:   ~/.local/share/NovelFound/config.json
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List

from .storage import atomic_write_json

APP_NAME = "NovelFound"
APP_TITLE = "小说搜索阅读器"
APP_VERSION = "1.0.0"


def data_dir() -> Path:
    """返回用户数据目录（自动创建）。

    可用环境变量 ``NOVELFOUND_HOME`` 覆盖，便于做绿色版/便携模式。
    若目标目录不可写（例如只读盘、权限受限），退回系统临时目录，保证程序仍能启动。
    """
    override = os.environ.get("NOVELFOUND_HOME")
    if override:
        path = Path(override).expanduser()
    elif sys.platform.startswith("win"):
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        path = Path(base) / APP_NAME
    elif sys.platform == "darwin":
        path = Path.home() / "Library" / "Application Support" / APP_NAME
    else:
        base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
        path = Path(base) / APP_NAME
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return path
    except OSError:
        fallback = Path(tempfile.gettempdir()) / APP_NAME
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback


def cache_dir() -> Path:
    path = data_dir() / "cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


DEFAULT_CONFIG: Dict[str, Any] = {
    # ---- 阅读器 ----
    "font_size": 19,
    "font_family": "",              # 空字符串 = 跟随系统默认
    "line_height": 1.9,             # 行距倍数
    "paragraph_spacing": 12,        # 段间距（像素）
    "first_line_indent": 2,         # 首行缩进（字符数，0 = 不缩进）
    "reader_theme": "warm",         # warm / eye / sepia / night / dark
    "reader_mode": "scroll",        # scroll（滚动）/ page（翻页）
    "page_columns": 1,              # 翻页模式下的排版：1 = 单页，2 = 左右双页
    "content_width": 820,           # 正文最大宽度（像素），一行太长会读不下去
    "auto_hide_bars": True,         # 阅读时上下控制条自动隐藏（鼠标靠近才出现）
    "strict_ad_filter": True,       # 更激进的广告过滤
    "auto_load_cover": True,        # 自动加载封面
    # ---- 网络 ----
    "timeout": 12.0,
    "retries": 2,
    "search_limit": 30,             # 每个书源最多返回多少条
    "request_interval": 0.8,        # 同一个站点两次请求之间的最小间隔（秒）
    "cover_max_concurrent": 2,      # 封面同时下载数量上限
    "cache_enabled": True,          # 章节正文缓存
    "cache_days": 30,               # 缓存保留天数
    # ---- 书源 ----
    "enabled_sources": {},          # {source_key: bool}
    "custom_sources": [],           # 用户自定义 / 导入的规则（结构同 builtin.py）
    "source_subscriptions": [],     # 订阅地址列表 [{url, name, enabled, last_sync}]
    "subscription_update_days": 7,  # 订阅自动更新间隔（天）
    "auto_update_sources": True,    # 启动时自动更新订阅
    "auto_disable_after": 5,        # 连续失败多少次后自动禁用该书源
    "discover_max_sites": 5,        # 网络找书源时最多探测几个候选站点
    "discover_engine": "",          # 搜索引擎（空 = 自动）
    # ---- 界面 ----
    "last_search": "",
    "toast_seconds": 6,             # 底部轻提示显示时长
    "search_debounce_ms": 300,      # 搜索浮层输入防抖（毫秒）
    "window_geometry": "",          # base64 编码的窗口尺寸
    "window_state": "",
}

# P1 起废弃的键（左侧栏 → 目录抽屉）：读到就忽略并清理
DEPRECATED_KEYS = ("sidebar_visible", "auto_hide_sidebar", "splitter_sizes")


class AppConfig:
    """配置对象，支持属性式访问与自动保存。"""

    def __init__(self, path: Path | None = None):
        self.path = path or (data_dir() / "config.json")
        self._data: Dict[str, Any] = dict(DEFAULT_CONFIG)
        self.load()

    # ------------------------------------------------------------------ 读写
    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                for key, value in loaded.items():
                    self._data[key] = value
            # P1：左侧栏已被目录抽屉取代，清掉旧键避免残留产生怪异行为
            for key in DEPRECATED_KEYS:
                self._data.pop(key, None)
        except (OSError, ValueError):
            # 配置损坏时使用默认值，不影响启动
            pass

    def save(self) -> None:
        atomic_write_json(self.path, self._data)

    # -------------------------------------------------------------- 字典式访问
    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, DEFAULT_CONFIG.get(key, default))

    def set(self, key: str, value: Any, autosave: bool = True) -> None:
        self._data[key] = value
        if autosave:
            self.save()

    def update(self, values: Dict[str, Any], autosave: bool = True) -> None:
        self._data.update(values)
        if autosave:
            self.save()

    def __getitem__(self, key: str) -> Any:
        return self.get(key)

    def __setitem__(self, key: str, value: Any) -> None:
        self.set(key, value)

    # ------------------------------------------------------------ 书源相关便捷
    def is_source_enabled(self, key: str, default: bool = True) -> bool:
        enabled = self.get("enabled_sources") or {}
        return bool(enabled.get(key, default))

    def set_source_enabled(self, key: str, enabled: bool) -> None:
        values = dict(self.get("enabled_sources") or {})
        values[key] = enabled
        self.set("enabled_sources", values)

    def custom_sources(self) -> List[Dict[str, Any]]:
        items = self.get("custom_sources") or []
        return [i for i in items if isinstance(i, dict) and i.get("key") and i.get("base_url")]

    def add_custom_source(self, rule: Dict[str, Any]) -> None:
        items = [i for i in self.custom_sources() if i.get("key") != rule.get("key")]
        items.append(rule)
        self.set("custom_sources", items)

    def add_custom_sources(self, rules: List[Dict[str, Any]]) -> int:
        """批量写入规则，返回实际新增/更新的条数。"""
        items = {i.get("key"): i for i in self.custom_sources()}
        added = 0
        for rule in rules:
            key = rule.get("key")
            if not key:
                continue
            items[key] = rule
            added += 1
        self.set("custom_sources", list(items.values()))
        return added

    def remove_custom_source(self, key: str) -> None:
        items = [i for i in self.custom_sources() if i.get("key") != key]
        self.set("custom_sources", items)

    # ------------------------------------------------------------ 订阅相关便捷
    def subscriptions(self) -> List[Dict[str, Any]]:
        items = self.get("source_subscriptions") or []
        return [i for i in items if isinstance(i, dict) and i.get("url")]

    def set_subscriptions(self, items: List[Dict[str, Any]]) -> None:
        self.set("source_subscriptions", items)

    def add_subscription(self, url: str, name: str = "") -> None:
        items = self.subscriptions()
        for item in items:
            if item.get("url") == url:
                if name:
                    item["name"] = name
                self.set_subscriptions(items)
                return
        items.append({"url": url, "name": name or url, "enabled": True,
                      "last_sync": 0})
        self.set_subscriptions(items)

    def remove_subscription(self, url: str) -> None:
        self.set_subscriptions([i for i in self.subscriptions() if i.get("url") != url])

    def update_subscription(self, url: str, **fields: Any) -> None:
        items = self.subscriptions()
        for item in items:
            if item.get("url") == url:
                item.update(fields)
                break
        self.set_subscriptions(items)

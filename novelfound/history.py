# -*- coding: utf-8 -*-
"""浏览历史：**一本书只留一条**最新记录。

与 `library.py` 的区别：

* ``library`` 存的是**书架与阅读进度**（一本书只留最新进度，用于"继续阅读"）；
* ``history`` 存的是**最近浏览/阅读过的书**（按时间倒序），
  右上角「🕘 历史」按钮里展示，点一条即可回到那本书、那一章。

设计要点：

1. **按书去重（upsert）**：同一本书无论打开详情多少次、读了多少章，
   历史里始终只有一条，位置与时间刷新到最新——列表里不会出现重复的书。
2. 章节信息在**离开阅读器**或**关闭应用**时写入（不是每切一章写一次），
   减少写入次数，也避免历史被同一本书刷屏。
3. 条数上限 ``MAX_ITEMS``（默认 30），超出丢最旧的。
4. 写入沿用 ``storage.atomic_write_json``（先写临时文件再 ``os.replace``）。
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import localbooks
from .config import data_dir
from .models import Book
from .storage import atomic_write_json, read_json

MAX_ITEMS = 30               # 一本书一条 → 30 条足够覆盖"最近看过的书"
KIND_DETAIL = "detail"       # 最近一次是打开书籍详情
KIND_CHAPTER = "chapter"     # 最近一次是阅读某一章


class BrowseHistory:
    """浏览历史（一本书一条，按最近时间倒序，JSON 持久化）。"""

    def __init__(self, path: Optional[Path] = None):
        self.path = path or (data_dir() / "history.json")
        self._lock = threading.RLock()
        self._items: List[Dict[str, Any]] = []
        self.load()

    # ------------------------------------------------------------------ 读写
    def load(self) -> None:
        data = read_json(self.path, None)
        items: List[Any] = []
        if isinstance(data, dict):
            items = data.get("items") or []
        elif isinstance(data, list):        # 兼容裸数组
            items = data
        self._items = [i for i in items if isinstance(i, dict) and i.get("key")]

    def save(self) -> None:
        with self._lock:
            payload = {"version": 1, "items": self._items[:MAX_ITEMS]}
        atomic_write_json(self.path, payload)

    # ------------------------------------------------------------------ 记录
    def record(self, book: Book, kind: str = KIND_DETAIL,
               chapter_index: int = -1, chapter_title: str = "",
               chapter_url: str = "", now: Optional[float] = None) -> Dict[str, Any]:
        """写入/刷新这本书的记录（**同一本书只留一条**）。

        已存在就整条替换并移到最前面（时间刷新），不存在才新增。
        """
        now = time.time() if now is None else float(now)
        entry = {
            "kind": kind,
            "key": book.key,
            "title": book.title,
            "author": book.author,
            "url": book.url,
            "cover_url": book.cover_url,
            "intro": book.intro,
            "source": book.source,
            "source_name": book.source_name,
            "category": book.category,
            "status": book.status,
            "latest_chapter": book.latest_chapter,
            "chapter_index": int(chapter_index),
            "chapter_title": chapter_title or "",
            "chapter_url": chapter_url or "",
            "at": now,
        }
        with self._lock:
            previous = None
            for index, item in enumerate(self._items):
                if item.get("key") == entry["key"]:
                    previous = self._items.pop(index)   # 去重：旧的同书记录先摘掉
                    break
            if previous is not None and entry["chapter_index"] < 0:
                # 只是打开了详情（没带章节信息）：保留上次读到的章节，
                # 否则"点历史回到那一章"会失效。
                for field in ("chapter_index", "chapter_title", "chapter_url"):
                    entry[field] = previous.get(field, entry[field])
            self._items.insert(0, entry)
            del self._items[MAX_ITEMS:]
        self.save()
        return entry

    # ------------------------------------------------------------------ 查询
    def items(self, limit: int = 0, kind: str = "") -> List[Dict[str, Any]]:
        """按时间倒序返回历史（可只取某类动作）。"""
        with self._lock:
            data = list(self._items)
        if kind:
            data = [i for i in data if i.get("kind") == kind]
        return data[:limit] if limit > 0 else data

    def count(self) -> int:
        with self._lock:
            return len(self._items)

    def last(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            return dict(self._items[0]) if self._items else None

    def book_of(self, entry: Dict[str, Any]) -> Book:
        """把一条历史还原成 Book（可直接拿去打开详情）。"""
        return Book.from_record(entry or {})

    # ------------------------------------------------------------------ 清理
    def clear(self) -> None:
        with self._lock:
            self._items = []
        self.save()

    def remove(self, index: int) -> None:
        """删除第 index 条（列表里的序号）。"""
        with self._lock:
            if 0 <= index < len(self._items):
                del self._items[index]
        self.save()

    def trim(self, keep: int = MAX_ITEMS) -> None:
        with self._lock:
            del self._items[max(0, keep):]
        self.save()

    # ------------------------------------------------- 删除某本书 / 清理失效
    def remove_book(self, book_key: str) -> int:
        """删掉某本书的全部历史（一台书只剩一条，所以通常是 0 或 1 条）。"""
        with self._lock:
            before = len(self._items)
            self._items = [i for i in self._items if i.get("key") != book_key]
            removed = before - len(self._items)
        if removed:
            self.save()
        return removed

    def stale_local_items(self, valid_ids) -> List[Dict[str, Any]]:
        """指向"已不存在的本地书"的历史条目。"""
        valid = {f"local|{localbooks.local_url(i)}" for i in valid_ids}
        return [i for i in self._items
                if localbooks.is_local_key(i.get("key", "")) and i.get("key") not in valid]

    def purge_stale_local(self, valid_ids) -> int:
        """清掉指向已不存在本地书的历史条目，返回清掉几条。"""
        with self._lock:
            before = len(self._items)
            self._items = [i for i in self._items
                           if not (localbooks.is_local_key(i.get("key", ""))
                                   and i.get("key") not in
                                   {f"local|{localbooks.local_url(v)}" for v in valid_ids})]
            removed = before - len(self._items)
        if removed:
            self.save()
        return removed

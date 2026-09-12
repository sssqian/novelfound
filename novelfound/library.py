# -*- coding: utf-8 -*-
"""书架与阅读进度（JSON 持久化）。

保存的内容：
* 书架里的书（含完整书籍信息与目录条数）
* 每本书的阅读进度（章节 URL、章节序号、滚动位置）
* 最近阅读列表

读写都在主线程完成，抓取线程只通过界面层间接调用，因此实现简单、
用一把锁保证一致即可。
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import data_dir
from . import localbooks
from .models import Book, BookDetail
from .storage import atomic_write_json


class Library:
    """书架 / 阅读进度存储。

    ``books``   书架里的书（用户显式加入）
    ``history`` 阅读历史（包括未加入书架的书），用于"继续阅读"与进度记忆
    ``recent``  最近阅读顺序
    """

    def __init__(self, path: Optional[Path] = None):
        self.path = path or (data_dir() / "library.json")
        self._lock = threading.RLock()
        self._data: Dict[str, Any] = {"books": {}, "history": {}, "recent": []}
        self.load()

    # ------------------------------------------------------------------ 读写
    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                self._data["books"] = data.get("books") or {}
                self._data["history"] = data.get("history") or {}
                self._data["recent"] = data.get("recent") or []
        except (OSError, ValueError):
            pass

    def save(self) -> None:
        with self._lock:
            atomic_write_json(self.path, self._data)

    # ------------------------------------------------------------------ 书架
    def contains(self, key: str) -> bool:
        return key in self._data["books"]

    def books(self) -> List[Dict[str, Any]]:
        """按最近阅读时间倒序返回书架条目。"""
        items = list(self._data["books"].values())
        items.sort(key=lambda i: i.get("last_read_at") or i.get("added_at") or 0,
                   reverse=True)
        return items

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        return self._data["books"].get(key)

    def add(self, book: Book, detail: Optional[BookDetail] = None) -> Dict[str, Any]:
        """加入书架（已存在则更新信息）。"""
        with self._lock:
            record = self._data["books"].get(book.key) or {}
            record.update(_book_fields(book))
            record["key"] = book.key
            record["added_at"] = record.get("added_at") or time.time()
            if detail is not None:
                record["chapter_count"] = len(detail.chapters)
            record.setdefault("last_chapter_url", "")
            record.setdefault("last_chapter_title", "")
            record.setdefault("last_index", 0)
            record.setdefault("scroll_pos", 0)
            record.setdefault("last_read_at", 0)
            # 如果之前在历史里读过，把进度带进书架
            history = self._data["history"].get(book.key) or {}
            for field in ("last_chapter_url", "last_chapter_title", "last_index",
                          "scroll_pos", "last_read_at"):
                if not record.get(field) and history.get(field):
                    record[field] = history[field]
            self._data["books"][book.key] = record
        self.save()
        return record

    def remove(self, key: str) -> None:
        with self._lock:
            self._data["books"].pop(key, None)
        self.save()

    def toggle(self, book: Book, detail: Optional[BookDetail] = None) -> bool:
        """加入 / 移出书架，返回加入后的状态。"""
        if self.contains(book.key):
            self.remove(book.key)
            return False
        self.add(book, detail)
        return True

    # ------------------------------------------------------------- 阅读进度
    def _touch_history(self, book: Book) -> Dict[str, Any]:
        """写入/更新阅读历史记录（不加入书架也能记住进度）。"""
        record = self._data["history"].get(book.key) or {}
        record.update(_book_fields(book))
        record["key"] = book.key
        record.setdefault("last_chapter_url", "")
        record.setdefault("last_chapter_title", "")
        record.setdefault("last_index", 0)
        record.setdefault("scroll_pos", 0)
        record.setdefault("last_read_at", 0)
        self._data["history"][book.key] = record
        return record

    def update_progress(self, book: Book, chapter_url: str, chapter_title: str,
                        index: int, scroll_pos: int = 0, block_index: int = -1,
                        page_index: int = -1, char_offset: int = -1) -> None:
        """记录阅读进度；书架内与历史记录同步更新。

        ``char_offset`` 是整章显示文本的字符偏移，跨排版/尺寸都稳定，是主锚点；
        ``scroll_pos`` 只在滚动模式下有意义，``block_index`` / ``page_index`` 兼容旧数据。
        """
        with self._lock:
            targets = [self._touch_history(book)]
            shelf = self._data["books"].get(book.key)
            if shelf is not None:
                targets.append(shelf)
            for record in targets:
                record["last_chapter_url"] = chapter_url
                record["last_chapter_title"] = chapter_title
                record["last_index"] = int(index)
                record["scroll_pos"] = int(scroll_pos)
                record["block_index"] = int(block_index)
                record["page_index"] = int(page_index)
                record["char_offset"] = int(char_offset)
                record["last_read_at"] = time.time()
            recent = [r for r in self._data["recent"] if r != book.key]
            recent.insert(0, book.key)
            self._data["recent"] = recent[:30]
        self.save()

    def mark_read(self, book: Book) -> None:
        """记录"最近阅读"（同时补一份历史记录）。"""
        with self._lock:
            self._touch_history(book)
            shelf = self._data["books"].get(book.key)
            if shelf is not None:
                shelf["last_read_at"] = time.time()
            recent = [r for r in self._data["recent"] if r != book.key]
            recent.insert(0, book.key)
            self._data["recent"] = recent[:30]
        self.save()

    def progress(self, key: str) -> Dict[str, Any]:
        """返回某本书的阅读进度（优先书架记录）。"""
        record = self._data["books"].get(key) or self._data["history"].get(key) or {}
        return {
            "chapter_url": record.get("last_chapter_url", ""),
            "chapter_title": record.get("last_chapter_title", ""),
            "index": record.get("last_index", 0),
            "scroll_pos": record.get("scroll_pos", 0),
            "block_index": record.get("block_index", -1),
            "page_index": record.get("page_index", -1),
            "char_offset": record.get("char_offset", -1),
        }

    def history(self) -> List[Dict[str, Any]]:
        """阅读历史（按最近阅读时间倒序）。"""
        items = list(self._data["history"].values())
        items.sort(key=lambda i: i.get("last_read_at") or 0, reverse=True)
        return [i for i in items if i.get("last_chapter_url")]

    # ------------------------------------------------------- 删除 / 清理
    def forget(self, key: str) -> bool:
        """彻底忘掉一本书：书架条目 + 阅读进度 + 最近列表。

        「移出书架」（:meth:`remove`）只是从书架隐藏；要连进度一起清就用这个。
        """
        with self._lock:
            removed = False
            if self._data["books"].pop(key, None) is not None:
                removed = True
            if self._data["history"].pop(key, None) is not None:
                removed = True
            self._data["recent"] = [k for k in self._data["recent"] if k != key]
        self.save()
        return removed

    def stale_local_keys(self, valid_ids) -> List[str]:
        """指向"已不存在的本地书"的记录键（清理失效记录用）。"""
        valid = {f"local|{localbooks.local_url(i)}" for i in valid_ids}
        keys = set(self._data["books"]) | set(self._data["history"]) | set(self._data["recent"])
        return sorted(k for k in keys if localbooks.is_local_key(k) and k not in valid)

    def purge_stale_local(self, valid_ids) -> int:
        """清掉指向已不存在本地书的书架/进度/最近记录，返回清掉几条。"""
        stale = self.stale_local_keys(valid_ids)
        with self._lock:
            for key in stale:
                self._data["books"].pop(key, None)
                self._data["history"].pop(key, None)
            self._data["recent"] = [k for k in self._data["recent"]
                                    if k not in set(stale)]
        if stale:
            self.save()
        return len(stale)


def _book_fields(book: Book) -> Dict[str, Any]:
    return {
        "source": book.source, "source_name": book.source_name, "url": book.url,
        "title": book.title, "author": book.author, "cover_url": book.cover_url,
        "intro": book.intro, "status": book.status, "category": book.category,
        "latest_chapter": book.latest_chapter,
    }

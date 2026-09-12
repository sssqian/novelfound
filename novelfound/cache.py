# -*- coding: utf-8 -*-
"""本地缓存（SQLite）。

缓存三类数据，全部以"书源 + 地址"为主键，互不干扰：
* ``chapter``  章节正文（支持离线重读，也能减少对站点的请求）
* ``detail``   书籍详情与目录（避免每次打开详情都重新抓目录）
* ``cover``    封面图片二进制（离线也能显示封面）

数据库放在用户数据目录，线程安全（``check_same_thread=False`` + 互斥锁），
因为抓取任务跑在 QThreadPool 的工作线程里。
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import cache_dir

SCHEMA = """
CREATE TABLE IF NOT EXISTS chapter (
    source      TEXT NOT NULL,
    book_url    TEXT NOT NULL,
    chapter_url TEXT NOT NULL,
    title       TEXT,
    content     TEXT,
    updated_at  REAL,
    PRIMARY KEY (source, book_url, chapter_url)
);
CREATE TABLE IF NOT EXISTS detail (
    source     TEXT NOT NULL,
    book_url   TEXT NOT NULL,
    payload    TEXT,
    updated_at REAL,
    PRIMARY KEY (source, book_url)
);
CREATE TABLE IF NOT EXISTS cover (
    url        TEXT PRIMARY KEY,
    data       BLOB,
    updated_at REAL
);
"""


class Cache:
    """线程安全的本地缓存。"""

    def __init__(self, path: Optional[Path] = None, enabled: bool = True,
                 ttl_days: int = 30):
        self.enabled = enabled
        self.ttl = max(ttl_days, 1) * 86400
        self._lock = threading.RLock()
        self._path = path or (cache_dir() / "cache.db")
        self._conn: Optional[sqlite3.Connection] = None
        if self.enabled:
            self._open()

    # ------------------------------------------------------------------ 基础
    def _open(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
            self._conn.executescript(SCHEMA)
            self._conn.commit()
        except sqlite3.Error:
            self._conn = None
            self.enabled = False

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                except sqlite3.Error:
                    pass
                self._conn = None

    @property
    def available(self) -> bool:
        return self.enabled and self._conn is not None

    def _fresh(self, updated_at: Optional[float]) -> bool:
        if not updated_at:
            return False
        return (time.time() - updated_at) < self.ttl

    # ---------------------------------------------------------------- 章节正文
    def get_chapter(self, source: str, book_url: str, chapter_url: str,
                    ignore_ttl: bool = False) -> Optional[Dict[str, Any]]:
        if not self.available:
            return None
        with self._lock:
            try:
                row = self._conn.execute(
                    "SELECT title, content, updated_at FROM chapter "
                    "WHERE source=? AND book_url=? AND chapter_url=?",
                    (source, book_url, chapter_url)).fetchone()
            except sqlite3.Error:
                return None
        if not row:
            return None
        title, content, updated_at = row
        if not ignore_ttl and not self._fresh(updated_at):
            return None
        try:
            paragraphs = json.loads(content or "[]")
        except ValueError:
            paragraphs = []
        if not paragraphs:
            return None
        return {"title": title or "", "paragraphs": paragraphs, "updated_at": updated_at}

    def put_chapter(self, source: str, book_url: str, chapter_url: str,
                    title: str, paragraphs: List[str]) -> None:
        if not self.available:
            return
        with self._lock:
            try:
                self._conn.execute(
                    "INSERT OR REPLACE INTO chapter"
                    "(source, book_url, chapter_url, title, content, updated_at) "
                    "VALUES (?,?,?,?,?,?)",
                    (source, book_url, chapter_url, title,
                     json.dumps(paragraphs, ensure_ascii=False), time.time()))
                self._conn.commit()
            except sqlite3.Error:
                pass

    def has_chapter(self, source: str, book_url: str) -> int:
        """该书已缓存的章节数（用于界面提示"已缓存 N 章"）。"""
        if not self.available:
            return 0
        with self._lock:
            try:
                row = self._conn.execute(
                    "SELECT COUNT(*) FROM chapter WHERE source=? AND book_url=?",
                    (source, book_url)).fetchone()
                return int(row[0]) if row else 0
            except sqlite3.Error:
                return 0

    # ---------------------------------------------------------------- 详情目录
    def get_detail(self, source: str, book_url: str) -> Optional[Dict[str, Any]]:
        if not self.available:
            return None
        with self._lock:
            try:
                row = self._conn.execute(
                    "SELECT payload, updated_at FROM detail WHERE source=? AND book_url=?",
                    (source, book_url)).fetchone()
            except sqlite3.Error:
                return None
        if not row:
            return None
        payload, updated_at = row
        if not self._fresh(updated_at):
            return None
        try:
            return json.loads(payload or "{}")
        except ValueError:
            return None

    def put_detail(self, source: str, book_url: str, payload: Dict[str, Any]) -> None:
        if not self.available:
            return
        with self._lock:
            try:
                self._conn.execute(
                    "INSERT OR REPLACE INTO detail(source, book_url, payload, updated_at) "
                    "VALUES (?,?,?,?)",
                    (source, book_url, json.dumps(payload, ensure_ascii=False), time.time()))
                self._conn.commit()
            except sqlite3.Error:
                pass

    # ------------------------------------------------------------------- 封面
    def get_cover(self, url: str) -> Optional[bytes]:
        if not self.available or not url:
            return None
        with self._lock:
            try:
                row = self._conn.execute(
                    "SELECT data FROM cover WHERE url=?", (url,)).fetchone()
            except sqlite3.Error:
                return None
        return bytes(row[0]) if row and row[0] else None

    def put_cover(self, url: str, data: bytes) -> None:
        if not self.available or not url or not data:
            return
        with self._lock:
            try:
                self._conn.execute(
                    "INSERT OR REPLACE INTO cover(url, data, updated_at) VALUES (?,?,?)",
                    (url, sqlite3.Binary(data), time.time()))
                self._conn.commit()
            except sqlite3.Error:
                pass

    # ------------------------------------------------------------------- 维护
    def count_for_book(self, source: str, book_url: str) -> int:
        """这本书在缓存里有多少行（章节 + 详情），删除前给用户看明细用。"""
        if not self.available or not book_url:
            return 0
        with self._lock:
            total = 0
            for table in ("chapter", "detail"):
                try:
                    total += self._conn.execute(
                        f"SELECT COUNT(*) FROM {table} WHERE source=? AND book_url=?",
                        (source, book_url)).fetchone()[0]
                except sqlite3.Error:
                    pass
        return total

    def delete_book(self, source: str, book_url: str) -> int:
        """删掉某本书的章节与详情缓存，返回删掉的行数（封面按 URL 单独管）。"""
        if not self.available or not book_url:
            return 0
        removed = 0
        with self._lock:
            for table in ("chapter", "detail"):
                try:
                    cur = self._conn.execute(
                        f"DELETE FROM {table} WHERE source=? AND book_url=?",
                        (source, book_url))
                    removed += cur.rowcount or 0
                except sqlite3.Error:
                    pass
            try:
                self._conn.commit()
            except sqlite3.Error:
                pass
        return removed

    def purge_stale_local(self, valid_urls) -> int:
        """清掉指向"已不存在的本地书"的缓存行，返回清掉的行数。"""
        if not self.available:
            return 0
        valid = set(valid_urls)
        removed = 0
        with self._lock:
            for table in ("chapter", "detail"):
                try:
                    rows = self._conn.execute(
                        f"SELECT book_url FROM {table} WHERE source='local'").fetchall()
                    for (book_url,) in rows:
                        if book_url in valid:
                            continue
                        cur = self._conn.execute(
                            f"DELETE FROM {table} WHERE source='local' AND book_url=?",
                            (book_url,))
                        removed += cur.rowcount or 0
                except sqlite3.Error:
                    pass
            try:
                self._conn.commit()
            except sqlite3.Error:
                pass
        return removed

    def stats(self) -> Dict[str, Any]:
        if not self.available:
            return {"available": False, "chapters": 0, "books": 0, "covers": 0, "size": 0}
        with self._lock:
            try:
                chapters = self._conn.execute("SELECT COUNT(*) FROM chapter").fetchone()[0]
                books = self._conn.execute("SELECT COUNT(*) FROM detail").fetchone()[0]
                covers = self._conn.execute("SELECT COUNT(*) FROM cover").fetchone()[0]
            except sqlite3.Error:
                chapters = books = covers = 0
        size = self._path.stat().st_size if self._path.exists() else 0
        return {"available": True, "chapters": chapters, "books": books,
                "covers": covers, "size": size, "path": str(self._path)}

    def clear(self, what: str = "all") -> None:
        if not self.available:
            return
        tables = {"chapter": ["chapter"], "detail": ["detail"], "cover": ["cover"]}
        targets = tables.get(what, ["chapter", "detail", "cover"])
        with self._lock:
            try:
                for table in targets:
                    self._conn.execute(f"DELETE FROM {table}")
                self._conn.commit()
                self._conn.execute("VACUUM")
            except sqlite3.Error:
                pass

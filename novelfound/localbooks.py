# -*- coding: utf-8 -*-
"""本地书籍库：导入的 TXT / EPUB 存在这里，供 :class:`~novelfound.sources.local.LocalSource` 读取。

设计：

* 导入时把原文件**复制**到数据目录 ``local_books/``，之后原文件移走/删除也不影响阅读；
* 元数据（书名、作者、章节表）写在 ``local_books.json``，避免每次打开都重新解析；
* ``Book.url`` 用伪地址 ``local://<id>``，与网文书源完全同构——
  所以目录抽屉、阅读器、书架、「继续阅读」这些现成能力全都直接可用。
"""
from __future__ import annotations

import hashlib
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import data_dir
from .models import Book
from .storage import atomic_write_json, read_json

SOURCE_KEY = "local"
SOURCE_NAME = "本地导入"
URL_PREFIX = "local://"
COVER_PREFIX = "local-cover://"      # 本地 EPUB 封面的伪地址（走本地读取，不联网）
META_FILE = "local_books.json"
FILES_DIR = "local_books"


def local_url(book_id: str) -> str:
    return f"{URL_PREFIX}{book_id}"


def book_id_from_url(url: str) -> str:
    return url[len(URL_PREFIX):] if url.startswith(URL_PREFIX) else ""


def is_local_url(url: str) -> bool:
    return bool(url) and url.startswith(URL_PREFIX)


class LocalBooks:
    """本地书籍库（JSON 索引 + 文件目录）。"""

    def __init__(self, path: Optional[Path] = None, files_dir: Optional[Path] = None):
        base = data_dir()
        self.path = path or (base / META_FILE)
        self.files_dir = files_dir or (base / FILES_DIR)
        self._lock = threading.RLock()
        self._items: List[Dict[str, Any]] = []
        self.load()

    # ------------------------------------------------------------------ 读写
    def load(self) -> None:
        data = read_json(self.path, None)
        items: List[Any] = []
        if isinstance(data, dict):
            items = data.get("items") or []
        elif isinstance(data, list):
            items = data
        self._items = [i for i in items if isinstance(i, dict) and i.get("id")]

    def save(self) -> None:
        with self._lock:
            payload = {"version": 1, "items": self._items}
        self.files_dir.mkdir(parents=True, exist_ok=True)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(self.path, payload)

    # ------------------------------------------------------------------ 查询
    def all(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [dict(item) for item in self._items]

    def count(self) -> int:
        with self._lock:
            return len(self._items)

    def get(self, book_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            for item in self._items:
                if item.get("id") == book_id:
                    return dict(item)
        return None

    def find(self, keyword: str) -> List[Dict[str, Any]]:
        """按书名 / 作者模糊匹配（本地搜索用）。"""
        keyword = (keyword or "").strip().lower()
        if not keyword:
            return self.all()
        result = []
        for item in self.all():
            haystack = f"{item.get('title', '')} {item.get('author', '')}".lower()
            if keyword in haystack:
                result.append(item)
        return result

    def file_path(self, item: Dict[str, Any]) -> Path:
        return self.files_dir / str(item.get("file", ""))

    # ------------------------------------------------------------------ 导入
    def import_file(self, source: Path, title: str = "", author: str = "",
                    on_note=None) -> Dict[str, Any]:
        """导入一个 TXT / EPUB 文件，返回入库记录。

        解析在调用方线程执行（GUI 里走 :class:`~novelfound.tasks.LocalImportTask`）。
        """
        from .local_parse import parse_book      # 延迟导入，避免循环依赖

        source = Path(source)
        if not source.is_file():
            raise FileNotFoundError(f"文件不存在：{source}")

        def note(text: str) -> None:
            if on_note is not None:
                on_note(text)

        note(f"正在解析：{source.name}")
        raw = source.read_bytes()
        digest = hashlib.sha1(raw).hexdigest()
        data = parse_book(source)
        book_id = uuid.uuid4().hex[:12]
        target = self.files_dir / f"{book_id}{source.suffix.lower()}"
        self.files_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)

        record = {
            "id": book_id,
            "title": (title or data.get("title") or source.stem).strip(),
            "author": (author or data.get("author") or "").strip(),
            "format": data.get("format", "txt"),
            "file": target.name,
            "digest": digest,
            "bytes": target.stat().st_size,
            "encoding": data.get("encoding", ""),
            "char_count": int(data.get("char_count") or 0),
            "chapters": data.get("chapters") or [],
            "added_at": time.time(),
        }
        cover = data.get("cover")
        if cover:
            cover_path = self.files_dir / f"{book_id}_cover.img"
            cover_path.write_bytes(cover)
            record["cover_file"] = cover_path.name

        note(f"解析完成：{len(record['chapters'])} 章")
        with self._lock:
            # 同一个文件（内容相同，改名也算）重复导入时替换旧记录，避免书架里出现两条
            old = [i for i in self._items if i.get("digest") == digest]
            self._items = [i for i in self._items if i.get("digest") != digest]
            self._items.insert(0, record)
        for item in old:                       # 清掉被替换那条留下的文件
            for key in ("file", "cover_file"):
                name = item.get(key)
                if name and name != record.get(key):
                    try:
                        (self.files_dir / name).unlink()
                    except OSError:
                        pass
        self.save()
        return record

    def cover_bytes(self, item: Dict[str, Any]) -> bytes:
        name = item.get("cover_file")
        if not name:
            return b""
        path = self.files_dir / name
        try:
            return path.read_bytes()
        except OSError:
            return b""

    # ------------------------------------------------------------------ 删除
    def remove(self, book_id: str) -> bool:
        with self._lock:
            before = len(self._items)
            removed = [i for i in self._items if i.get("id") == book_id]
            self._items = [i for i in self._items if i.get("id") != book_id]
            if len(self._items) == before:
                return False
        for item in removed:
            for key in ("file", "cover_file"):
                name = item.get(key)
                if name:
                    try:
                        (self.files_dir / name).unlink()
                    except OSError:
                        pass
        self.save()
        return True

    # ------------------------------------------------------------------ 模型
    def to_book(self, item: Dict[str, Any]) -> Book:
        """把记录转成界面通用的 Book（url 是 ``local://<id>``）。"""
        cover = f"{COVER_PREFIX}{item.get('id', '')}" if item.get("cover_file") else ""
        return Book(
            title=item.get("title", ""),
            author=item.get("author", ""),
            url=local_url(item.get("id", "")),
            cover_url=cover,
            intro=self.describe(item),
            source=SOURCE_KEY,
            source_name=SOURCE_NAME,
            category=item.get("format", "").upper(),
            status="本地",
        )

    @staticmethod
    def describe(item: Dict[str, Any]) -> str:
        """简介位置显示的一句话说明。"""
        fmt = str(item.get("format", "")).upper()
        chapters = len(item.get("chapters") or [])
        size = int(item.get("bytes") or 0) / 1024
        parts = [f"本地 {fmt} 文件", f"{chapters} 章", f"{size:.0f} KB"]
        if item.get("encoding"):
            parts.append(f"编码 {item['encoding']}")
        return "　·　".join(parts)

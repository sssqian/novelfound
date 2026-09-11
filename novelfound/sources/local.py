# -*- coding: utf-8 -*-
"""本地书源：把导入的 TXT / EPUB 接进现有的书源体系。

实现 :class:`~novelfound.sources.base.BaseSource` 的三个方法后，
本地书籍自动获得网文的全套能力：搜索结果、详情页、目录抽屉、
阅读器（分页/主题/位置记忆）、书架与「继续阅读」——**不需要任何特殊分支**。

约定：``Book.url == "local://<id>"``，``Chapter.url`` 用
``"local://<id>/<章节序号>"``（同时作为缓存键）。
"""
from __future__ import annotations

from pathlib import Path
from typing import List

from .. import localbooks
from ..localbooks import LocalBooks
from ..local_parse import read_chapter
from ..models import Book, BookDetail, Chapter, ChapterContent
from ..net import NovelError
from .base import BaseSource


class LocalSource(BaseSource):
    """已导入的本地电子书。"""

    key = localbooks.SOURCE_KEY
    name = localbooks.SOURCE_NAME
    base_url = ""
    enabled_by_default = True
    note = "读取导入的 TXT / EPUB 文件（不联网）"

    def __init__(self, http=None, books: LocalBooks = None, strict: bool = True):
        super().__init__(http)
        self.books = books or LocalBooks()
        self.strict = strict

    # ------------------------------------------------------------------ 搜索
    def search(self, keyword: str, limit: int = 30) -> List[Book]:
        """在已导入的书里按书名 / 作者模糊匹配。"""
        self.books.load()          # 可能刚导入过新书，重新读一次索引
        return [self.books.to_book(item)
                for item in self.books.find(keyword)[:limit]]

    # ------------------------------------------------------------------ 详情
    def fetch_detail(self, book: Book) -> BookDetail:
        item = self._item(book)
        chapters = [
            Chapter(title=entry.get("title") or f"第 {index + 1} 章",
                    url=f"{book.url}/{index}", index=index)
            for index, entry in enumerate(item.get("chapters") or [])
        ]
        detail = BookDetail(book=book, chapters=chapters)
        if not chapters:
            raise NovelError("这本书里没有解析出任何章节",
                             "文件可能是空的，或格式不被支持")
        return detail

    # ------------------------------------------------------------------ 正文
    def fetch_chapter(self, book: Book, chapter: Chapter) -> ChapterContent:
        item = self._item(book)
        entries = item.get("chapters") or []
        index = chapter.index if chapter.index is not None else -1
        if not 0 <= index < len(entries):
            # 兼容旧进度：按章节序号落在 URL 末尾的情况找一次
            index = self._index_from_url(chapter.url, len(entries))
        if not 0 <= index < len(entries):
            raise NovelError("找不到这一章", f"章节序号 {chapter.index} 超出范围")

        path = self.books.file_path(item)
        if not path.is_file():
            raise NovelError("本地文件已丢失",
                             f"找不到 {path.name}，建议重新导入这本书")
        paragraphs = read_chapter(path, item, entries[index], strict=self.strict)
        if not paragraphs:
            paragraphs = ["（这一章没有解析出正文）"]
        return ChapterContent(title=chapter.title or entries[index].get("title", ""),
                              paragraphs=paragraphs,
                              url=chapter.url or f"{book.url}/{index}")

    # ------------------------------------------------------------------ 封面
    def cover_bytes(self, book: Book) -> bytes:
        try:
            return self.books.cover_bytes(self._item(book))
        except NovelError:
            return b""

    # ------------------------------------------------------------------ 健康
    def health_check(self):
        count = self.books.count()
        if not count:
            return True, "还没有导入本地书籍"
        return True, f"已导入 {count} 本（本地文件，无需联网）"

    # ------------------------------------------------------------------ 内部
    def _item(self, book: Book) -> dict:
        book_id = localbooks.book_id_from_url(book.url)
        item = self.books.get(book_id)
        if item is None:
            self.books.load()      # 可能是刚导入的书（索引变了），重读一次
            item = self.books.get(book_id)
        if item is None:
            raise NovelError("这本书不在本地库里",
                             f"找不到 local://{book_id}，可能已被删除，请重新导入")
        return item

    @staticmethod
    def _index_from_url(url: str, total: int) -> int:
        tail = (url or "").rsplit("/", 1)[-1]
        if tail.isdigit():
            index = int(tail)
            if 0 <= index < total:
                return index
        return -1

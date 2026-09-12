# -*- coding: utf-8 -*-
"""书架 / 本地书管理：删除级联与"清理失效记录"的单元测试。

背景（用户实测反馈）："移出书架"只删了书架那一行，导入的文件、缓存、
浏览历史、阅读进度全都留着；而且界面上没有入口能彻底删掉一本导入的书。
这里覆盖新增的：
* ``LocalBooks.remove``：删文件 + 记录
* ``Library.forget`` / ``purge_stale_local``
* ``BrowseHistory.remove_book`` / ``purge_stale_local``
* ``Cache.delete_book`` / ``count_for_book`` / ``purge_stale_local``
"""
from __future__ import annotations

import shutil
import sys
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novelfound import localbooks as lb  # noqa: E402
from novelfound.cache import Cache  # noqa: E402
from novelfound.history import BrowseHistory  # noqa: E402
from novelfound.library import Library  # noqa: E402
from novelfound.localbooks import LocalBooks  # noqa: E402
from novelfound.models import Book  # noqa: E402

TXT = "第一章 起点\n正文内容，足够长以便通过过滤。\n第二章 风起\n又一段正文内容。\n"


class ManagementTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = ROOT / "tests" / ".tmp" / f"manage_{id(self)}"
        shutil.rmtree(self.dir, ignore_errors=True)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.library = Library(path=self.dir / "library.json")
        self.history = BrowseHistory(path=self.dir / "history.json")
        self.cache = Cache(path=self.dir / "cache.db", enabled=True, ttl_days=30)
        self.books = LocalBooks(path=self.dir / "local_books.json",
                                files_dir=self.dir / "local_books")

    def tearDown(self) -> None:
        try:
            self.cache.close()
        except Exception:
            pass
        shutil.rmtree(self.dir, ignore_errors=True)

    def import_book(self, name: str = "测试书.txt", body: str = TXT) -> dict:
        src = self.dir / name
        src.write_text(body, encoding="utf-8")
        return self.books.import_file(src)

    def book_of(self, record: dict) -> Book:
        return self.books.to_book(record)


class TestLocalBooksRemove(ManagementTestCase):
    def test_remove_deletes_file_and_record(self) -> None:
        record = self.import_book()
        path = self.books.file_path(record)
        self.assertTrue(path.is_file())
        self.assertTrue(self.books.remove(record["id"]))
        self.assertFalse(path.exists())
        self.assertIsNone(self.books.get(record["id"]))
        self.assertEqual(self.books.count(), 0)

    def test_remove_also_deletes_cover(self) -> None:
        record = self.import_book()
        cover = self.books.files_dir / "fake_cover.img"
        cover.write_bytes(b"\x89PNG")
        record["cover_file"] = "fake_cover.img"
        self.books.save()
        self.books.remove(record["id"])
        self.assertFalse(cover.exists())


class TestLibraryForget(ManagementTestCase):
    def test_forget_clears_shelf_and_progress(self) -> None:
        record = self.import_book()
        book = self.book_of(record)
        self.library.add(book)
        self.library.update_progress(book, f"{book.url}/1", "第二章 风起", 1)
        self.assertTrue(self.library.contains(book.key))
        self.assertTrue(self.library.progress(book.key)["chapter_url"])

        self.assertTrue(self.library.forget(book.key))
        self.assertFalse(self.library.contains(book.key))
        self.assertFalse(self.library.progress(book.key)["chapter_url"])

    def test_remove_keeps_progress_but_forget_clears_it(self) -> None:
        """「移出书架」保留进度（再读还能接着），「彻底删除」才清进度。"""
        record = self.import_book()
        book = self.book_of(record)
        self.library.add(book)
        self.library.update_progress(book, f"{book.url}/1", "第二章 风起", 1)
        self.library.remove(book.key)
        self.assertFalse(self.library.contains(book.key))
        self.assertTrue(self.library.progress(book.key)["chapter_url"])

    def test_purge_stale_local(self) -> None:
        good = self.import_book("好的一本.txt")
        stale = self.import_book("要被删的.txt", TXT.replace("起点", "开端"))
        good_book, stale_book = self.book_of(good), self.book_of(stale)
        self.library.add(good_book)
        self.library.add(stale_book)
        self.library.update_progress(stale_book, f"{stale_book.url}/1", "第二章", 1)

        self.assertEqual(self.library.stale_local_keys([good["id"]]), [stale_book.key])
        removed = self.library.purge_stale_local([good["id"]])
        self.assertEqual(removed, 1)
        self.assertTrue(self.library.contains(good_book.key))
        self.assertFalse(self.library.contains(stale_book.key))
        # 清理后不再有失效键
        self.assertEqual(self.library.stale_local_keys([good["id"]]), [])


class TestHistoryPurge(ManagementTestCase):
    def test_remove_book_removes_only_that_book(self) -> None:
        first = self.import_book("第一本.txt")
        second = self.import_book("第二本.txt", TXT.replace("起点", "开端"))
        a, b = self.book_of(first), self.book_of(second)
        self.history.record(a, kind="detail")
        self.history.record(b, kind="detail")
        self.assertEqual(self.history.count(), 2)
        self.assertEqual(self.history.remove_book(a.key), 1)
        self.assertEqual(self.history.count(), 1)
        self.assertEqual(self.history.items()[0]["key"], b.key)

    def test_purge_stale_local(self) -> None:
        keep = self.import_book("保留.txt")
        drop = self.import_book("丢弃.txt", TXT.replace("起点", "开端"))
        self.history.record(self.book_of(keep), kind="detail")
        self.history.record(self.book_of(drop), kind="detail")
        self.assertEqual(len(self.history.stale_local_items([keep["id"]])), 1)
        self.assertEqual(self.history.purge_stale_local([keep["id"]]), 1)
        self.assertEqual(self.history.count(), 1)


class TestCacheDelete(ManagementTestCase):
    def test_delete_book_and_count(self) -> None:
        record = self.import_book()
        book = self.book_of(record)
        self.cache.put_chapter(lb.SOURCE_KEY, book.url, f"{book.url}/0",
                               "第一章", ["正文一"])
        self.cache.put_detail(lb.SOURCE_KEY, book.url, {"book": {}, "chapters": []})
        self.assertEqual(self.cache.count_for_book(lb.SOURCE_KEY, book.url), 2)
        self.assertEqual(self.cache.delete_book(lb.SOURCE_KEY, book.url), 2)
        self.assertEqual(self.cache.count_for_book(lb.SOURCE_KEY, book.url), 0)

    def test_purge_stale_local_only_touches_local(self) -> None:
        keep = self.import_book("保留.txt")
        drop = self.import_book("丢弃.txt", TXT.replace("起点", "开端"))
        keep_book, drop_book = self.book_of(keep), self.book_of(drop)
        self.cache.put_chapter(lb.SOURCE_KEY, keep_book.url, f"{keep_book.url}/0",
                               "第一章", ["正文"])
        self.cache.put_chapter(lb.SOURCE_KEY, drop_book.url, f"{drop_book.url}/0",
                               "第一章", ["正文"])
        # 网络书源的缓存不能被误删
        self.cache.put_chapter("hetushu", "http://example.test/book/1",
                               "http://example.test/c1", "第一章", ["正文"])
        removed = self.cache.purge_stale_local([lb.local_url(keep["id"])])
        self.assertEqual(removed, 1)
        self.assertEqual(self.cache.count_for_book(lb.SOURCE_KEY, keep_book.url), 1)
        self.assertEqual(self.cache.count_for_book(lb.SOURCE_KEY, drop_book.url), 0)
        self.assertEqual(self.cache.count_for_book("hetushu",
                                                   "http://example.test/book/1"), 1)


class TestFullDeleteFlow(ManagementTestCase):
    """走一遍"彻底删除一本书"的完整级联（界面上那个动作）。"""

    def test_delete_cascade(self) -> None:
        record = self.import_book()
        book = self.book_of(record)
        self.library.add(book)
        self.library.update_progress(book, f"{book.url}/1", "第二章 风起", 1)
        self.history.record(book, kind="detail")
        self.cache.put_chapter(lb.SOURCE_KEY, book.url, f"{book.url}/1", "第二章", ["正文"])
        path = self.books.file_path(record)
        self.assertTrue(path.is_file())

        # 与 main_window.delete_local_book 相同的级联顺序
        self.cache.delete_book(lb.SOURCE_KEY, book.url)
        self.history.remove_book(book.key)
        self.library.forget(book.key)
        self.books.remove(record["id"])

        self.assertFalse(path.exists())
        self.assertIsNone(self.books.get(record["id"]))
        self.assertFalse(self.library.contains(book.key))
        self.assertFalse(self.library.progress(book.key)["chapter_url"])
        self.assertEqual(self.history.count(), 0)
        self.assertEqual(self.cache.count_for_book(lb.SOURCE_KEY, book.url), 0)
        # 删完之后不该再有任何失效引用
        self.assertEqual(self.library.stale_local_keys([]), [])
        self.assertEqual(self.history.stale_local_items([]), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)

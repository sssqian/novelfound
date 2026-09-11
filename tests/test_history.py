# -*- coding: utf-8 -*-
"""浏览历史单元测试（纯离线，不访问网络）。

运行：
    python -m unittest discover -s tests -v
"""
from __future__ import annotations

import shutil
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novelfound.history import (KIND_CHAPTER, KIND_DETAIL, MAX_ITEMS,  # noqa: E402
                                BrowseHistory)
from novelfound.models import Book  # noqa: E402


def make_book(title: str = "斗罗大陆", url: str = "http://a/1") -> Book:
    return Book(title=title, author="唐家三少", url=url, source="s1",
                source_name="和图书", cover_url="http://a/c.jpg")


class HistoryTestCase(unittest.TestCase):
    """给每个用例一个独立目录。

    注意：**不用** `tempfile.mkdtemp()`——本项目的受限环境里系统临时目录不可写，
    统一放在工作区的 tests/.tmp 下（与其它测试一致）。
    """

    def setUp(self) -> None:
        self.dir = ROOT / "tests" / ".tmp" / f"history_{id(self)}"
        shutil.rmtree(self.dir, ignore_errors=True)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / "history.json"
        self.history = BrowseHistory(self.path)

    def tearDown(self) -> None:
        shutil.rmtree(self.dir, ignore_errors=True)


class TestRecord(HistoryTestCase):
    """记录与去重。"""

    def test_record_detail(self) -> None:
        entry = self.history.record(make_book(), KIND_DETAIL, now=1000.0)
        self.assertEqual(self.history.count(), 1)
        self.assertEqual(entry["kind"], KIND_DETAIL)
        self.assertEqual(entry["title"], "斗罗大陆")
        self.assertEqual(entry["at"], 1000.0)

    def test_newest_first(self) -> None:
        self.history.record(make_book("第一本", "http://a/1"), now=1000.0)
        self.history.record(make_book("第二本", "http://a/2"), now=2000.0)
        titles = [i["title"] for i in self.history.items()]
        self.assertEqual(titles, ["第二本", "第一本"])

    def test_same_book_upserted(self) -> None:
        """同一本书反复打开详情：只留一条，时间刷新。"""
        self.history.record(make_book(), KIND_DETAIL, now=1000.0)
        self.history.record(make_book(), KIND_DETAIL, now=1030.0)
        self.assertEqual(self.history.count(), 1)
        self.assertEqual(self.history.last()["at"], 1030.0)

    def test_same_book_stays_single_after_other_books(self) -> None:
        """中间隔了别的书，同一本书仍然只有一条（按书去重，不是按"连续"去重）。"""
        book = make_book()
        self.history.record(book, KIND_DETAIL, now=1000.0)
        self.history.record(make_book("别的书", "http://a/9"), KIND_DETAIL, now=1001.0)
        self.history.record(book, KIND_DETAIL, now=1002.0)
        self.assertEqual(self.history.count(), 2)
        self.assertEqual(self.history.items()[0]["title"], "斗罗大陆")   # 刷新后回到最前

    def test_same_book_no_window_limit(self) -> None:
        """相隔很久再打开同一本书，也只刷新同一条。"""
        self.history.record(make_book(), KIND_DETAIL, now=1000.0)
        self.history.record(make_book(), KIND_DETAIL, now=1000.0 + 3600 * 24)
        self.assertEqual(self.history.count(), 1)

    def test_chapter_record_keeps_chapter(self) -> None:
        entry = self.history.record(make_book(), KIND_CHAPTER, chapter_index=2,
                                    chapter_title="第三章 双生武魂",
                                    chapter_url="http://a/1c2", now=1000.0)
        self.assertEqual(entry["chapter_index"], 2)
        self.assertEqual(entry["chapter_title"], "第三章 双生武魂")
        self.assertEqual(self.history.items(kind=KIND_CHAPTER)[0]["chapter_url"],
                         "http://a/1c2")

    def test_detail_then_chapter_is_one_entry(self) -> None:
        """先看详情、再读一章：同一本书仍然只有一条，内容是最后那次动作。"""
        book = make_book()
        self.history.record(book, KIND_DETAIL, now=1000.0)
        self.history.record(book, KIND_CHAPTER, chapter_index=0,
                            chapter_title="第一章", chapter_url="http://a/1c0",
                            now=1001.0)
        self.assertEqual(self.history.count(), 1)
        entry = self.history.last()
        self.assertEqual(entry["kind"], KIND_CHAPTER)
        self.assertEqual(entry["chapter_index"], 0)

    def test_detail_after_reading_keeps_chapter(self) -> None:
        """读完后只打开详情：记录会变成"浏览"，但不能丢掉上次读到的章节。"""
        book = make_book()
        self.history.record(book, KIND_CHAPTER, chapter_index=5,
                            chapter_title="第六章", chapter_url="http://a/1c5",
                            now=1000.0)
        self.history.record(book, KIND_DETAIL, now=1001.0)
        self.assertEqual(self.history.count(), 1)
        entry = self.history.last()
        self.assertEqual(entry["kind"], KIND_DETAIL)
        self.assertEqual(entry["chapter_index"], 5)
        self.assertEqual(entry["chapter_url"], "http://a/1c5")

    def test_many_chapters_still_one_entry(self) -> None:
        """连读 10 章也只有一条（回归：以前每切一章就多一条）。"""
        book = make_book()
        for index in range(10):
            self.history.record(book, KIND_CHAPTER, chapter_index=index,
                                chapter_title=f"第{index + 1}章",
                                chapter_url=f"http://a/1c{index}", now=1000.0 + index)
        self.assertEqual(self.history.count(), 1)
        self.assertEqual(self.history.last()["chapter_index"], 9)


class TestQuery(HistoryTestCase):
    """查询与筛选。"""

    def test_filter_by_kind(self) -> None:
        """按类型筛的是"每本书最近一次的动作"。"""
        book = make_book()
        self.history.record(book, KIND_DETAIL, now=1000.0)
        self.history.record(book, KIND_CHAPTER, chapter_index=1,
                            chapter_url="http://a/1c1", now=1001.0)
        self.history.record(make_book("只看过详情的书", "http://a/2"), KIND_DETAIL,
                            now=1002.0)
        self.assertEqual(len(self.history.items(kind=KIND_CHAPTER)), 1)
        self.assertEqual(len(self.history.items(kind=KIND_DETAIL)), 1)
        self.assertEqual(len(self.history.items()), 2)

    def test_limit(self) -> None:
        for i in range(5):
            self.history.record(make_book(f"第{i}本", f"http://a/{i}"), now=1000.0 + i)
        self.assertEqual(len(self.history.items(limit=2)), 2)

    def test_book_of_roundtrip(self) -> None:
        entry = self.history.record(make_book(), KIND_DETAIL, now=1000.0)
        book = self.history.book_of(entry)
        self.assertEqual(book.key, "s1|http://a/1")
        self.assertEqual(book.title, "斗罗大陆")
        self.assertEqual(book.source_name, "和图书")


class TestLimits(HistoryTestCase):
    """上限与清理。"""

    def test_max_items_is_30(self) -> None:
        self.assertEqual(MAX_ITEMS, 30)

    def test_trim_keeps_newest(self) -> None:
        for i in range(MAX_ITEMS + 20):
            self.history.record(make_book(f"书{i}", f"http://a/{i}"), now=1000.0 + i)
        self.assertEqual(self.history.count(), MAX_ITEMS)
        self.assertEqual(self.history.last()["title"], f"书{MAX_ITEMS + 19}")
        self.assertEqual(self.history.items()[0]["title"], f"书{MAX_ITEMS + 19}")

    def test_clear(self) -> None:
        self.history.record(make_book(), now=1000.0)
        self.history.clear()
        self.assertEqual(self.history.count(), 0)
        self.assertIsNone(self.history.last())

    def test_remove_one(self) -> None:
        self.history.record(make_book("甲", "http://a/1"), now=1000.0)
        self.history.record(make_book("乙", "http://a/2"), now=1001.0)
        self.history.remove(0)
        self.assertEqual([i["title"] for i in self.history.items()], ["甲"])


class TestPersistence(HistoryTestCase):
    """落盘与重载。"""

    def test_saved_and_reloaded(self) -> None:
        self.history.record(make_book("第一本", "http://a/1"), KIND_DETAIL, now=1000.0)
        self.history.record(make_book("第二本", "http://a/2"), KIND_CHAPTER,
                            chapter_index=3, chapter_title="第四章",
                            chapter_url="http://a/2c3", now=1001.0)
        again = BrowseHistory(self.path)
        self.assertEqual(again.count(), 2)
        self.assertEqual(again.items()[0]["chapter_index"], 3)
        self.assertEqual(again.items()[0]["title"], "第二本")

    def test_corrupt_file_is_ignored(self) -> None:
        self.path.write_text("{ 这不是 JSON", encoding="utf-8")
        again = BrowseHistory(self.path)
        self.assertEqual(again.count(), 0)

    def test_missing_file_is_empty(self) -> None:
        again = BrowseHistory(self.dir / "nope.json")
        self.assertEqual(again.count(), 0)

    def test_bare_array_compatible(self) -> None:
        """兼容早期可能写成裸数组的格式。"""
        self.path.write_text(
            '[{"key": "s1|http://a/1", "title": "老格式", "kind": "detail", "at": 1}]',
            encoding="utf-8")
        again = BrowseHistory(self.path)
        self.assertEqual(again.count(), 1)
        self.assertEqual(again.last()["title"], "老格式")


if __name__ == "__main__":
    unittest.main(verbosity=2)

# -*- coding: utf-8 -*-
"""书源解析单元测试：用离线 HTML 快照验证搜索 / 目录 / 正文解析。

这些快照取自真实站点（tests/fixtures），因此当解析规则被改动时可以
立刻发现回归，而不必每次都去访问网络。

运行：
    python -m unittest discover -s tests -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novelfound.models import Book  # noqa: E402
from novelfound.sources.builtin import BUILTIN_RULES  # noqa: E402
from novelfound.sources.rule_source import RuleSource  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"


class FakeHttp:
    """把网络请求替换成本地快照，按 URL 片段返回对应 HTML。"""

    def __init__(self, mapping):
        self.mapping = mapping

    def get_text(self, url, referer="", encoding="", timeout=None):
        for fragment, filename in self.mapping.items():
            if fragment in url:
                return (FIXTURES / filename).read_text(encoding="utf-8")
        raise AssertionError(f"测试未提供该地址的快照：{url}")

    def get_bytes(self, url, referer="", timeout=None):
        return b""

    def request(self, *args, **kwargs):
        raise AssertionError("测试不应触发 request()")

    def close(self):
        pass


def rule(key: str) -> dict:
    for item in BUILTIN_RULES:
        if item["key"] == key:
            return item
    raise KeyError(key)


class TestHetushuParsing(unittest.TestCase):
    """和图书（结构清晰的新模板）。"""

    def setUp(self) -> None:
        http = FakeHttp({
            "/search/?keyword=": "hetushu_search.html",
            "/book/27/index.html": "hetushu_book.html",
            "/book/27/17756.html": "hetushu_chapter.html",
        })
        self.source = RuleSource(http, rule("hetushu"))

    def test_search(self) -> None:
        books = self.source.search("斗罗大陆")
        self.assertGreater(len(books), 3)
        first = books[0]
        self.assertEqual(first.title, "斗罗大陆")
        self.assertEqual(first.author, "唐家三少")
        self.assertTrue(first.url.startswith("https://www.hetushu.com/book/"))
        self.assertTrue(first.cover_url.startswith("https://"))
        self.assertGreater(len(first.intro), 20)

    def test_detail_and_catalog(self) -> None:
        book = Book(title="斗罗大陆", url="https://www.hetushu.com/book/27/index.html",
                    source="hetushu", source_name="和图书")
        detail = self.source.fetch_detail(book)
        self.assertEqual(detail.book.title, "斗罗大陆")
        self.assertEqual(detail.book.author, "唐家三少")
        self.assertEqual(detail.book.category, "玄幻小说")
        self.assertIn("唐门", detail.book.intro)
        self.assertGreater(len(detail.chapters), 300)
        self.assertEqual(detail.chapters[0].title, "引子 穿越的唐家三少")
        self.assertTrue(detail.chapters[0].url.endswith("/book/27/17756.html"))
        # 章节序号连续
        self.assertEqual([c.index for c in detail.chapters[:5]], [0, 1, 2, 3, 4])

    def test_chapter_content(self) -> None:
        book = Book(title="斗罗大陆", url="https://www.hetushu.com/book/27/index.html",
                    source="hetushu")
        from novelfound.models import Chapter
        chapter = Chapter(title="引子 穿越的唐家三少",
                          url="https://www.hetushu.com/book/27/17756.html")
        content = self.source.fetch_chapter(book, chapter)
        self.assertGreater(len(content.paragraphs), 20)
        self.assertTrue(any("唐三" in p for p in content.paragraphs))
        # 防盗版标记 acronym 必须被清掉
        self.assertFalse(any("和-图-书" in p for p in content.paragraphs))
        # 广告行必须被清掉
        self.assertFalse(any("和图书" in p and len(p) < 8 for p in content.paragraphs))
        self.assertTrue(content.next_url.endswith("17757.html"))
        self.assertEqual(content.prev_url, "")


class TestClassicTemplateParsing(unittest.TestCase):
    """经典笔趣阁模板（table.grid 搜索 + #content 正文）。"""

    def setUp(self) -> None:
        http = FakeHttp({
            "searchkey=": "mayiwsk_search.html",
            "/9_9331/5966561.html": "mayiwsk_chapter.html",
        })
        self.source = RuleSource(http, rule("mayiwsk"))

    def test_search_table(self) -> None:
        books = self.source.search("斗罗大陆")
        self.assertGreater(len(books), 5)
        first = books[0]
        self.assertEqual(first.title, "斗罗大陆")
        self.assertEqual(first.author, "唐家三少")
        self.assertEqual(first.url, "https://www.mayiwsk.com/9_9331/")
        self.assertIn(first.status, ("连载", "完本"))
        # 搜索结果里的表头行不能被当成书籍
        self.assertTrue(all(b.title and b.url for b in books))

    def test_chapter_content(self) -> None:
        book = Book(title="斗罗大陆", url="https://www.mayiwsk.com/9_9331/",
                    source="mayiwsk")
        from novelfound.models import Chapter
        chapter = Chapter(title="第六百八十三章 完美融合之复活神光",
                          url="https://www.mayiwsk.com/9_9331/5966561.html")
        content = self.source.fetch_chapter(book, chapter)
        self.assertGreater(len(content.paragraphs), 10)
        self.assertTrue(any("唐三" in p for p in content.paragraphs))
        # 站点插在正文里的 "最新网址" 提示必须被过滤
        self.assertFalse(any("最新网址" in p for p in content.paragraphs))
        # 导航文字不应混进正文
        self.assertFalse(any(p in ("我的书架", "玄幻小说") for p in content.paragraphs))
        self.assertGreater(content.char_count, 500)


class TestErrorHandling(unittest.TestCase):
    """结构变化 / 异常页面时的友好报错。"""

    def test_unknown_structure_raises_parse_error(self) -> None:
        class BrokenHttp:
            def get_text(self, url, referer="", encoding="", timeout=None):
                return "<html><body><div>这是一个没有任何正文结构的页面</div></body></html>"

        source = RuleSource(BrokenHttp(), rule("hetushu"))
        book = Book(title="测试", url="https://www.hetushu.com/book/1/index.html",
                    source="hetushu")
        from novelfound.models import Chapter
        from novelfound.net import ParseError
        with self.assertRaises(ParseError):
            source.fetch_detail(book)
        with self.assertRaises(ParseError):
            source.fetch_chapter(book, Chapter(title="第一章",
                                               url="https://www.hetushu.com/book/1/1.html"))

    def test_search_returns_empty_when_no_match(self) -> None:
        class EmptyHttp:
            def get_text(self, url, referer="", encoding="", timeout=None):
                # 真实站点无结果时会把关键词回显在页面上
                return ("<html><body><dl class='list' id='body'>"
                        "<dt>共搜索到0本作品<span>(关键词：不存在的书名)</span></dt>"
                        "</dl></body></html>")

        source = RuleSource(EmptyHttp(), rule("hetushu"))
        self.assertEqual(source.search("不存在的书名"), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)

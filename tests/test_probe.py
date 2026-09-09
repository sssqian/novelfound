# -*- coding: utf-8 -*-
"""书源自动探测（方案 B）的离线单元测试。

用真实的页面快照模拟一个"只支持 /search/?keyword= 且结构同和图书"的站点，
验证四关探测、模板匹配、失败分类与半成品规则。

运行：
    python -m unittest discover -s tests -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Dict
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novelfound.net import NetworkError, NovelError  # noqa: E402
from novelfound.sources.probe import ProbeOutcome, probe_domain  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"
HOME_HTML = "<html><body><h1>示例小说站</h1>" + "首页内容" * 80 + "</body></html>"
EMPTY_SEARCH = "<html><body><div id='content'>没有找到相关作品</div>" * 1 + \
               "占位" * 200 + "</body></html>"
JUNK_DETAIL = "<html><body><div class='box'>这个页面没有任何目录结构</div>" + \
              "占位" * 200 + "</body></html>"


class FakeHttp:
    """按「路径 + 查询串」匹配预设页面；路径为 / 时返回首页。"""

    def __init__(self, mapping: Dict[str, str]):
        self.mapping = mapping
        self.requests = []

    def get_text(self, url, referer="", encoding="", timeout=None):
        self.requests.append(url)
        parsed = urlparse(url)
        target = parsed.path + (("?" + parsed.query) if parsed.query else "")
        if target in self.mapping:
            return self.mapping[target]
        hits = [(key, html) for key, html in self.mapping.items() if key in target]
        if hits:
            hits.sort(key=lambda item: -len(item[0]))
            return hits[0][1]
        if parsed.path in ("", "/"):
            return HOME_HTML
        return EMPTY_SEARCH

    def get_bytes(self, url, referer="", timeout=None):
        return b""

    def request(self, url, **kwargs):
        raise NovelError("测试环境不支持 POST")

    def close(self):
        pass


class UnreachableHttp(FakeHttp):
    def get_text(self, url, referer="", encoding="", timeout=None):
        raise NetworkError("网络请求失败，请检查网络连接或稍后重试", url)


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class TestProbeSuccess(unittest.TestCase):
    """三关全过的成功路径。"""

    def setUp(self) -> None:
        base = "https://www.hetushu.com"
        self.http = FakeHttp({
            "/search/?keyword=": fixture("hetushu_search.html"),
            "/book/27/index.html": fixture("hetushu_book.html"),
            "/book/27/17756.html": fixture("hetushu_chapter.html"),
        })
        self.outcome = probe_domain(self.http, base, "斗罗大陆")

    def test_probe_succeeds(self) -> None:
        self.assertTrue(self.outcome.ok, self.outcome.error)
        self.assertEqual(self.outcome.stage, "完成")
        self.assertEqual(self.outcome.template, "和图书式模板")
        self.assertGreaterEqual(self.outcome.books_found, 1)
        self.assertGreater(self.outcome.chapter_count, 300)
        self.assertGreater(self.outcome.char_count, 300)

    def test_search_pattern_discovered(self) -> None:
        """只支持 /search/?keyword= 的站点要能试出来。"""
        self.assertEqual(self.outcome.search_url, "/search/?keyword={q}")
        self.assertEqual(self.outcome.search_method, "GET")

    def test_rule_is_ready_to_save(self) -> None:
        rule = self.outcome.rule
        self.assertEqual(rule["key"], "probe_www_hetushu_com")
        self.assertEqual(rule["base_url"], "https://www.hetushu.com")
        self.assertEqual(rule["search_url"], "/search/?keyword={q}")
        self.assertEqual(rule["content"], ["#content"])
        self.assertEqual(rule["import_format"], "probe")
        self.assertTrue(rule["enabled_by_default"])

    def test_preview_content(self) -> None:
        preview = self.outcome.preview
        self.assertEqual(preview["title"], "斗罗大陆")
        self.assertEqual(preview["author"], "唐家三少")
        self.assertGreaterEqual(len(preview["chapters"]), 3)
        self.assertIn("唐三", preview["snippet"])

    def test_progress_notes(self) -> None:
        joined = "\n".join(self.outcome.notes)
        for stage in ("① 连通性", "② 搜索入口", "③ 目录解析", "④ 正文提取"):
            self.assertIn(stage, joined)


class TestProbeFailures(unittest.TestCase):
    """各种失败路径的提示与半成品规则。"""

    def test_unreachable_site(self) -> None:
        outcome = probe_domain(UnreachableHttp({}), "https://down.example.com", "斗罗大陆")
        self.assertFalse(outcome.ok)
        self.assertIn("无法访问", outcome.error)

    def test_invalid_url(self) -> None:
        outcome = probe_domain(FakeHttp({}), "这不是网址", "斗罗大陆")
        self.assertFalse(outcome.ok)
        self.assertIn("不合法", outcome.error)

    def test_search_page_not_recognized(self) -> None:
        http = FakeHttp({})
        outcome = probe_domain(http, "https://www.unknown.com", "斗罗大陆")
        self.assertFalse(outcome.ok)
        self.assertIn("模板未匹配", outcome.error)

    def test_catalog_failure_returns_partial_rule(self) -> None:
        http = FakeHttp({
            "/search/?keyword=": fixture("hetushu_search.html"),
            "/book/27/index.html": JUNK_DETAIL,
        })
        outcome = probe_domain(http, "https://www.half.com", "斗罗大陆")
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.stage, "目录解析")
        self.assertIsNotNone(outcome.partial_rule)
        # 半成品里保留了已经探通的搜索入口
        self.assertEqual(outcome.partial_rule["search_url"], "/search/?keyword={q}")


class TestProbeOutcome(unittest.TestCase):
    """结果对象的默认值。"""

    def test_defaults(self) -> None:
        outcome = ProbeOutcome()
        self.assertFalse(outcome.ok)
        outcome.note("第一关")
        self.assertEqual(outcome.notes, ["第一关"])
        self.assertIsNone(outcome.partial_rule)


if __name__ == "__main__":
    unittest.main(verbosity=2)

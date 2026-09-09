# -*- coding: utf-8 -*-
"""网络找书源（方案 C）的离线单元测试。

用真实 Bing 结果页快照 + 真实站点页面快照，验证：
候选站点提取、官方/百科站过滤、已配置站点跳过、探测结果筛选、书名匹配。

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

from novelfound.net import NetworkError  # noqa: E402
from novelfound.sources.discover import (Candidate, discover_sources,  # noqa: E402
                                         search_candidates)
from novelfound.sources.probe import ProbeOutcome  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"
HOME_HTML = "<html><body><h1>示例站</h1>" + "首页内容" * 80 + "</body></html>"
EMPTY_SEARCH = "<html><body><div>没有找到相关作品</div>" + "占位" * 200 + "</body></html>"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class FakeHttp:
    """按「路径 + 查询串」返回页面；命中不了就返回空搜索页。"""

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
        raise NetworkError("测试环境不支持 POST")

    def close(self):
        pass


class TestCandidateExtraction(unittest.TestCase):
    """搜索引擎结果页解析。"""

    def setUp(self) -> None:
        self.http = FakeHttp({"/search": fixture("bing_search.html")})
        self.candidates = search_candidates(self.http, "斗罗大陆 小说", "bing_cn")

    def test_extracts_hosts(self) -> None:
        hosts = {c.host for c in self.candidates}
        self.assertIn("www.wyshu.com", hosts)
        self.assertIn("m.zhaobiquge.com", hosts)

    def test_official_sites_skipped(self) -> None:
        by_host = {c.host: c for c in self.candidates}
        self.assertTrue(by_host["www.qidian.com"].skipped)
        self.assertTrue(by_host["baike.baidu.com"].skipped)
        self.assertFalse(by_host["www.wyshu.com"].skipped)

    def test_dedupe_and_ranking(self) -> None:
        hosts = [c.host for c in self.candidates]
        self.assertEqual(len(hosts), len(set(hosts)), "同一域名只保留一次")
        # 未跳过的排在前面
        first_skipped = next((i for i, c in enumerate(self.candidates) if c.skipped),
                             len(self.candidates))
        self.assertTrue(all(not c.skipped for c in self.candidates[:first_skipped]))

    def test_title_and_snippet(self) -> None:
        candidate = next(c for c in self.candidates if c.host == "www.wyshu.com")
        self.assertIn("斗罗大陆", candidate.title)
        self.assertTrue(candidate.url.startswith("https://www.wyshu.com/"))


class TestDiscoverFlow(unittest.TestCase):
    """完整发现流程：搜索 → 探测 → 书名匹配。"""

    def _http(self) -> FakeHttp:
        return FakeHttp({
            "/search": fixture("bing_search.html"),
            # 让 www.wyshu.com 这个候选"看起来像"和图书的结构
            "/search/?keyword=": fixture("hetushu_search.html"),
            "/book/27/index.html": fixture("hetushu_book.html"),
            "/book/27/17756.html": fixture("hetushu_chapter.html"),
        })

    def test_finds_usable_source(self) -> None:
        outcome = discover_sources(self._http(), "斗罗大陆", limit=2)
        usable = outcome.usable
        self.assertTrue(usable, f"应至少找到一个可用源；notes={outcome.notes}")
        first = usable[0]
        self.assertTrue(first.outcome.ok)
        self.assertTrue(first.matched)
        self.assertEqual(first.outcome.rule["search_url"], "/search/?keyword={q}")
        self.assertTrue(first.outcome.rule["key"].startswith("probe_"))

    def test_skips_existing_hosts(self) -> None:
        http = self._http()
        outcome = discover_sources(http, "斗罗大陆", limit=3,
                                   existing_hosts=["www.wyshu.com"])
        probed_hosts = [r.candidate.host for r in outcome.results]
        self.assertNotIn("www.wyshu.com", probed_hosts)

    def test_title_mismatch_rejected(self) -> None:
        """站点能解析，但搜不到目标书时不能算可用。"""
        http = FakeHttp({
            "/search": fixture("bing_search.html"),
            "/search/?keyword=": fixture("hetushu_search.html"),
            "/book/27/index.html": fixture("hetushu_book.html"),
            "/book/27/17756.html": fixture("hetushu_chapter.html"),
        })
        outcome = discover_sources(http, "完全不存在的书名XYZ", limit=1)
        self.assertEqual(outcome.usable, [])
        if outcome.results:
            # 探测阶段就会因为"搜索结果与关键词无关"而拒绝
            self.assertIn("无关", outcome.results[0].reason)

    def test_no_candidates(self) -> None:
        http = FakeHttp({"/search": "<html><body>无结果</body></html>"})
        outcome = discover_sources(http, "斗罗大陆", limit=2)
        self.assertEqual(outcome.results, [])
        self.assertTrue(outcome.error)

    def test_notes_record_progress(self) -> None:
        notes = []
        discover_sources(self._http(), "斗罗大陆", limit=1, on_note=notes.append)
        self.assertTrue(any("搜索" in n for n in notes))
        self.assertTrue(any("探测" in n for n in notes))


class TestHelpers(unittest.TestCase):
    """辅助逻辑。"""

    def test_candidate_base_url(self) -> None:
        candidate = Candidate(host="m.example.com", url="https://m.example.com/book/1")
        self.assertEqual(candidate.base_url, "https://m.example.com")

    def test_discovered_source_reason(self) -> None:
        from novelfound.sources.discover import DiscoveredSource
        result = DiscoveredSource(candidate=Candidate(host="a.com"),
                                  outcome=ProbeOutcome(ok=False, stage="目录解析",
                                                       error="目录解析失败"),
                                  matched=False)
        self.assertFalse(result.ok)
        self.assertIn("目录解析", result.reason)

        result2 = DiscoveredSource(candidate=Candidate(host="a.com"),
                                   outcome=ProbeOutcome(ok=True, search_titles=["别的书"]),
                                   matched=False)
        self.assertIn("没搜到这本书", result2.reason)


if __name__ == "__main__":
    unittest.main(verbosity=2)

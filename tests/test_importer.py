# -*- coding: utf-8 -*-
"""书源导入与健康度统计的单元测试（离线）。

运行：
    python -m unittest discover -s tests -v
"""
from __future__ import annotations

import json
import shutil
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novelfound.sources.importer import parse_payload  # noqa: E402
from novelfound.sources.stats import SourceStats  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"


class TestNativeImport(unittest.TestCase):
    """本项目原生规则格式。"""

    def test_single_object(self) -> None:
        text = json.dumps({"key": "demo", "name": "示例", "base_url": "https://a.com",
                           "search_url": "/s?q={q}"})
        parsed = parse_payload(text, "test")
        self.assertEqual(len(parsed), 1)
        self.assertTrue(parsed[0].ok)
        self.assertEqual(parsed[0].key, "demo")
        self.assertIn("导入", parsed[0].rule["note"])

    def test_list_and_wrapper(self) -> None:
        items = [{"key": "a", "name": "A", "base_url": "https://a.com"},
                 {"key": "b", "name": "B", "base_url": "https://b.com"}]
        self.assertEqual(len(parse_payload(json.dumps(items))), 2)
        self.assertEqual(len(parse_payload(json.dumps({"sources": items}))), 2)

    def test_missing_fields_invalid(self) -> None:
        parsed = parse_payload(json.dumps({"name": "没有 key"}))
        self.assertEqual(parsed[0].status, "invalid")
        self.assertFalse(parsed[0].ok)

    def test_broken_json(self) -> None:
        parsed = parse_payload("{not json at all")
        self.assertEqual(parsed[0].status, "invalid")


class TestLegadoImport(unittest.TestCase):
    """开源「阅读」(Legado) 书源格式。"""

    def setUp(self) -> None:
        self.text = (FIXTURES / "legado_sample.json").read_text(encoding="utf-8")
        self.parsed = parse_payload(self.text, "fixture")

    def test_usable_and_unsupported_split(self) -> None:
        usable = [p for p in self.parsed if p.ok]
        skipped = [p for p in self.parsed if not p.ok]
        self.assertEqual(len(usable), 2)
        self.assertEqual(len(skipped), 1)
        self.assertIn("JS", skipped[0].reason)

    def test_rule_mapping(self) -> None:
        rule = next(p.rule for p in self.parsed if p.ok)
        self.assertEqual(rule["base_url"], "https://www.hetushu.com")
        # 相对搜索地址会被补全成绝对地址，两种写法解析器都支持
        self.assertEqual(rule["search_url"],
                         "https://www.hetushu.com/search/?keyword={q}")
        self.assertEqual(rule["search_items"], ["dl#body dd"])
        # @text / @href 之类的尾部指令要被剥掉（后面会追加兜底选择器）
        self.assertEqual(rule["book_title"][0], "h4 a")
        self.assertEqual(rule["book_author"][0], "h4 span")
        self.assertEqual(rule["book_cover"][0], "img")
        self.assertEqual(rule["catalog_groups"], ["dl#dir dd a"])
        self.assertEqual(rule["content"], ["#content"])
        self.assertEqual(rule["import_format"], "legado")

    def test_key_from_host(self) -> None:
        keys = {p.key for p in self.parsed if p.ok}
        self.assertIn("ld_www_hetushu_com", keys)

    def test_post_search_url_conversion(self) -> None:
        text = json.dumps([{
            "bookSourceName": "POST 站",
            "bookSourceUrl": "https://p.com",
            "searchUrl": '/s.php,{"method":"POST","body":"type=articlename&s={{key}}"}',
            "ruleSearch": {"bookList": "@css:dl dd", "name": "@css:a@text",
                           "bookUrl": "@css:a@href"},
            "ruleToc": {"chapterList": "@css:#list a"},
            "ruleContent": {"content": "@css:#content"},
        }])
        rule = parse_payload(text)[0].rule
        self.assertEqual(rule["search_method"], "POST")
        self.assertEqual(rule["search_data"], {"type": "articlename", "s": "{q}"})
        self.assertEqual(rule["search_url"], "https://p.com/s.php")

    def test_xpath_source_rejected(self) -> None:
        text = json.dumps([{
            "bookSourceName": "XPath 站",
            "bookSourceUrl": "https://x.com",
            "searchUrl": "/s?q={{key}}",
            "ruleSearch": {"bookList": "@XPath://div[@class='item']"},
            "ruleToc": {"chapterList": "@css:#list a"},
            "ruleContent": {"content": "@css:#content"},
        }])
        parsed = parse_payload(text)[0]
        self.assertEqual(parsed.status, "unsupported")
        self.assertIn("不支持", parsed.reason)

    def test_missing_rules_rejected(self) -> None:
        text = json.dumps([{
            "bookSourceName": "缺目录规则",
            "bookSourceUrl": "https://m.com",
            "searchUrl": "/s?q={{key}}",
            "ruleSearch": {"bookList": "@css:.item"},
            "ruleContent": {"content": "@css:#content"},
        }])
        parsed = parse_payload(text)[0]
        self.assertEqual(parsed.status, "unsupported")
        self.assertIn("chapterList", parsed.reason)


class TestSourceStats(unittest.TestCase):
    """书源健康度统计与自动禁用判定。"""

    def setUp(self) -> None:
        # 用工作区内的临时目录：系统临时目录在受限环境下可能不可写/不可删
        self.tmpdir = ROOT / "tests" / ".tmp" / f"stats_{id(self)}"
        self.tmpdir.mkdir(parents=True, exist_ok=True)
        self.stats = SourceStats(self.tmpdir / "stats.json")

    def tearDown(self) -> None:
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_record_and_rate(self) -> None:
        self.stats.record("a", ok=True, elapsed=1.0)
        self.stats.record("a", ok=True, elapsed=3.0)
        self.stats.record("a", ok=False, error="超时")
        snap = self.stats.snapshot("a")
        self.assertEqual(snap["success"], 2)
        self.assertEqual(snap["failure"], 1)
        self.assertAlmostEqual(snap["rate"], 2 / 3, places=3)
        self.assertAlmostEqual(snap["avg_elapsed"], 2.0, places=3)
        self.assertIn("超时", snap["last_error"])

    def test_auto_disable_threshold(self) -> None:
        for _ in range(3):
            self.stats.record("b", ok=False, error="连接失败")
        self.assertFalse(self.stats.should_disable("b", 5))
        self.assertTrue(self.stats.should_disable("b", 3))
        self.assertEqual(self.stats.failing_sources(["b", "c"], 3), ["b"])
        # 成功一次后连续失败清零
        self.stats.record("b", ok=True)
        self.assertFalse(self.stats.should_disable("b", 3))

    def test_persistence(self) -> None:
        self.stats.record("d", ok=True, elapsed=0.5)
        self.stats.save()
        again = SourceStats(self.stats.path)
        self.assertEqual(again.snapshot("d")["success"], 1)

    def test_describe(self) -> None:
        self.assertEqual(self.stats.describe("nobody"), "未使用")
        self.stats.record("e", ok=True, elapsed=2.0)
        self.assertIn("成功率", self.stats.describe("e"))


if __name__ == "__main__":
    unittest.main(verbosity=2)

# -*- coding: utf-8 -*-
"""正文清洗与广告过滤单元测试（纯离线，不访问网络）。

运行：
    python -m unittest discover -s tests -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novelfound.cleaner import (extract_paragraphs, is_ad_line, looks_like_nav,
                                make_soup, pick_content_node, strip_noise,
                                strip_title_echo)  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"


class TestAdFilter(unittest.TestCase):
    """广告行识别。"""

    def test_ad_lines_detected(self) -> None:
        ads = [
            "请记住本站域名 www.example.com",
            "最新网址：www.biquge.com",
            "笔趣阁无弹窗全文字首发",
            "天才一秒记住本站地址",
            "求收藏求推荐求月票",
            "点击下一页继续阅读",
            "https://spam.example.com/promo",
            "广告",
        ]
        for line in ads:
            with self.subTest(line=line):
                self.assertTrue(is_ad_line(line), f"应判定为广告：{line}")

    def test_normal_lines_kept(self) -> None:
        normal = [
            "唐三缓缓抬起头，看着远处的山峦。",
            "“你是谁？”少年警惕地问道。",
            "他心中暗道：这世上竟有如此奇妙的武魂。",
        ]
        for line in normal:
            with self.subTest(line=line):
                self.assertFalse(is_ad_line(line), f"不应判定为广告：{line}")

    def test_strict_mode_extra_filter(self) -> None:
        html = """
        <div id="content">
          <p>第一段正文内容，足够长以便通过长度检查。</p>
          <p>Third line written in plain ascii only</p>
          <p>第二段正文内容。</p>
        </div>
        """
        node = strip_noise(make_soup(html)).select_one("#content")
        plain = extract_paragraphs(node, strict=False)
        strict = extract_paragraphs(node, strict=True)
        self.assertTrue(any("Third line" in p for p in plain))
        self.assertFalse(any("Third line" in p for p in strict))
        self.assertTrue(any("第一段正文" in p for p in strict))

    def test_strict_mode_removes_links(self) -> None:
        html = ('<div id="content"><p>正文内容足够长的一段话。</p>'
                '<p>这里有个链接<a href="http://ad.com">点我领奖</a>混在段落里。</p></div>')
        node = strip_noise(make_soup(html)).select_one("#content")
        strict = extract_paragraphs(node, strict=True)
        self.assertFalse(any("点我领奖" in p for p in strict))


class TestContentNode(unittest.TestCase):
    """正文容器优先级。"""

    def test_priority_over_text_length(self) -> None:
        """外层容器文本更多时，仍应选中优先级更高的 #content。"""
        html = """
        <div class="content_read">
          <div class="nav">首页 目录 上一章 下一章 投推荐票 加入书签 热门推荐 玄幻小说</div>
          <div id="content"><p>真正的正文内容。</p></div>
          <div class="tail">更多推荐内容推荐内容推荐内容推荐内容推荐内容</div>
        </div>
        """
        soup = strip_noise(make_soup(html))
        node = pick_content_node(soup, ("#content", "div.content_read"))
        self.assertIsNotNone(node)
        self.assertEqual(node.get("id"), "content")

    def test_short_chapter_still_found(self) -> None:
        html = '<div id="content"><p>很短的一章。</p></div><div class="wrapper">其它</div>'
        soup = strip_noise(make_soup(html))
        node = pick_content_node(soup, ("#content",))
        self.assertEqual(node.get("id"), "content")


class TestNoiseRemoval(unittest.TestCase):
    """DOM 层噪声清除。"""

    def test_removes_scripts_and_ad_blocks(self) -> None:
        html = """
        <div id="wrapper">
          <script>var ad=1;</script>
          <div id="adt1">广告位</div>
          <div id="cbad">底部广告</div>
          <div id="center_tip">最新网址：www.example.com</div>
          <div id="content"><p>正文。</p></div>
        </div>
        """
        soup = strip_noise(make_soup(html))
        self.assertEqual(soup.select("script"), [])
        self.assertEqual(soup.select("#adt1"), [])
        self.assertEqual(soup.select("#cbad"), [])
        self.assertEqual(soup.select("#center_tip"), [])
        self.assertEqual(len(soup.select("#content")), 1)

    def test_keeps_content_read_container(self) -> None:
        """content_read 名字里含 "ad"，但绝不能当广告删掉。"""
        html = '<div class="content_read"><div id="content"><p>正文</p></div></div>'
        soup = strip_noise(make_soup(html))
        self.assertEqual(len(soup.select(".content_read")), 1)

    def test_removes_anti_piracy_tags(self) -> None:
        html = '<div id="content"><p>唐三可以<acronym>和-图-书</acronym>对天发誓。</p></div>'
        soup = strip_noise(make_soup(html))
        node = soup.select_one("#content")
        paragraphs = extract_paragraphs(node)
        self.assertTrue(any("唐三可以对天发誓" in p for p in paragraphs))


class TestTitleAndNav(unittest.TestCase):
    """标题清洗与导航页识别。"""

    def test_strip_title_echo(self) -> None:
        paragraphs = ["第一集 斗罗世界", "引子 穿越的唐家三少", "正文第一段。"]
        result = strip_title_echo(paragraphs, "第一集 斗罗世界 引子 穿越的唐家三少")
        self.assertEqual(result, ["正文第一段。"])

    def test_looks_like_nav(self) -> None:
        nav = ["首页", "我的书架", "玄幻小说", "修真小说", "排行榜", "完本小说"]
        self.assertTrue(looks_like_nav(nav))
        real = ["唐三点了点头，看着眼前的山谷，心中升起一股豪气。",
                "他知道，接下来的路不会平坦。"]
        self.assertFalse(looks_like_nav(real))


if __name__ == "__main__":
    unittest.main(verbosity=2)

# -*- coding: utf-8 -*-
"""网络层单元测试：请求头、同站限速排队（离线，不发真实请求）。

运行：
    python -m unittest discover -s tests -v
"""
from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novelfound.net import DEFAULT_HEADERS, HttpSession  # noqa: E402


class TestHeaders(unittest.TestCase):
    """请求头要尽量像真实浏览器。"""

    def setUp(self) -> None:
        self.session = HttpSession(host_interval=0)

    def tearDown(self) -> None:
        self.session.close()

    def test_default_headers_are_browser_like(self) -> None:
        for key in ("User-Agent", "Accept", "Accept-Language", "Sec-Fetch-Dest",
                    "Sec-Fetch-Mode", "Sec-Fetch-Site", "sec-ch-ua",
                    "Upgrade-Insecure-Requests"):
            self.assertIn(key, DEFAULT_HEADERS, f"缺少请求头 {key}")
        self.assertIn("Chrome", DEFAULT_HEADERS["User-Agent"])
        # 不能手动指定 Accept-Encoding，否则可能解不开 br
        self.assertNotIn("Accept-Encoding", DEFAULT_HEADERS)

    def test_referer_switches_sec_fetch_site(self) -> None:
        plain = self.session._build_headers()
        self.assertEqual(plain, {})

        with_referer = self.session._build_headers("https://a.com/book/1.html")
        self.assertEqual(with_referer["Referer"], "https://a.com/book/1.html")
        self.assertEqual(with_referer["Sec-Fetch-Site"], "same-origin")

    def test_image_headers(self) -> None:
        headers = self.session._build_headers(image=True)
        self.assertEqual(headers["Sec-Fetch-Dest"], "image")
        self.assertIn("image/", headers["Accept"])


class TestThrottle(unittest.TestCase):
    """同站限速：同站排队、异站互不阻塞。"""

    def test_same_host_waits(self) -> None:
        session = HttpSession(min_interval=0, host_interval=0.5)
        first = session._reserve_slot("https://a.com/1")
        second = session._reserve_slot("https://a.com/2")
        self.assertLess(first, 0.05)
        self.assertGreaterEqual(second, 0.3)   # 0.5 × 抖动(0.75~1.35) 的下限
        session.close()

    def test_different_hosts_do_not_block(self) -> None:
        session = HttpSession(min_interval=0, host_interval=1.0)
        session._reserve_slot("https://a.com/1")
        other = session._reserve_slot("https://b.com/1")
        self.assertLess(other, 0.2, "不同站点不应该互相等待")
        session.close()

    def test_interval_zero_disables_throttle(self) -> None:
        session = HttpSession(min_interval=0, host_interval=0)
        session._reserve_slot("https://a.com/1")
        self.assertLess(session._reserve_slot("https://a.com/2"), 0.05)
        session.close()

    def test_throttle_actually_sleeps(self) -> None:
        session = HttpSession(min_interval=0, host_interval=0.4)
        session._throttle("https://a.com/1")
        started = time.time()
        session._throttle("https://a.com/2")
        self.assertGreaterEqual(time.time() - started, 0.25)
        session.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)

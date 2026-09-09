# -*- coding: utf-8 -*-
"""崩溃兜底与槽函数保护的单元测试。

运行：
    python -m unittest discover -s tests -v
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novelfound import errors  # noqa: E402


class TestCrashLog(unittest.TestCase):
    """崩溃日志写入。"""

    def setUp(self) -> None:
        self.tmpdir = ROOT / "tests" / ".tmp" / f"errors_{id(self)}"
        self.tmpdir.mkdir(parents=True, exist_ok=True)
        self._saved = errors._log_path
        errors._log_path = self.tmpdir / "crash.log"

    def tearDown(self) -> None:
        errors._log_path = self._saved
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_write_crash_log(self) -> None:
        try:
            raise ValueError("模拟错误")
        except ValueError as exc:
            path = errors.write_crash_log("测试", type(exc), exc, exc.__traceback__)
        self.assertIsNotNone(path)
        text = path.read_text(encoding="utf-8")
        self.assertIn("测试", text)
        self.assertIn("ValueError: 模拟错误", text)
        self.assertIn("Traceback", text)

    def test_excepthook_writes_file(self) -> None:
        try:
            raise RuntimeError("主线程异常")
        except RuntimeError as exc:
            errors._excepthook(type(exc), exc, exc.__traceback__)
        text = (self.tmpdir / "crash.log").read_text(encoding="utf-8")
        self.assertIn("主线程异常", text)
        self.assertIn("未捕获异常（主线程）", text)


class TestGuardSlot(unittest.TestCase):
    """槽函数保护装饰器。"""

    def setUp(self) -> None:
        self.tmpdir = ROOT / "tests" / ".tmp" / f"guard_{id(self)}"
        self.tmpdir.mkdir(parents=True, exist_ok=True)
        self._saved = errors._log_path
        errors._log_path = self.tmpdir / "crash.log"

    def tearDown(self) -> None:
        errors._log_path = self._saved
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_normal_return_passes_through(self) -> None:
        @errors.guard_slot
        def ok() -> int:
            return 42

        self.assertEqual(ok(), 42)

    def test_exception_is_swallowed_and_logged(self) -> None:
        @errors.guard_slot
        def boom() -> None:
            raise AttributeError("'str' object has no attribute 'base_url'")

        self.assertIsNone(boom())      # 不再把异常抛回 Qt
        text = (self.tmpdir / "crash.log").read_text(encoding="utf-8")
        self.assertIn("boom", text)
        self.assertIn("AttributeError", text)

    def test_extra_signal_arguments_are_dropped(self) -> None:
        """PyQt 的 clicked(bool) 会给槽多传一个参数，不能因此报错。"""
        class Owner:
            @errors.guard_slot
            def on_click(self, checked: bool = False) -> str:
                return f"ok-{checked}"

        self.assertEqual(Owner().on_click(False), "ok-False")

        class Bare:
            @errors.guard_slot
            def on_click(self) -> str:
                return "ok"

        # 只接受 self 的槽，被传了信号参数也要能正常工作
        self.assertEqual(Bare().on_click(True), "ok")

    def test_banner_is_updated_when_available(self) -> None:
        class FakeBanner:
            def __init__(self) -> None:
                self.message = ""

            def show_message(self, text, level="info", **kwargs) -> None:
                self.message = text

        class FakeOwner:
            def __init__(self) -> None:
                self.banner = FakeBanner()

            @errors.guard_slot
            def handler(self) -> None:
                raise ValueError("内部错误")

        owner = FakeOwner()
        owner.handler()
        self.assertIn("内部错误", owner.banner.message)
        self.assertIn("crash.log", owner.banner.message)


if __name__ == "__main__":
    unittest.main(verbosity=2)

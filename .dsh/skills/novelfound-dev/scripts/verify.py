#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""NovelFound 验证闭环（一条命令跑完）。

    单测 → 界面自检（联网） → 打包 exe → exe --selftest → GUI 冒烟 → 清理

用法（在仓库根目录）：

    .\\.venv\\Scripts\\python.exe .dsh\\skills\\novelfound-dev\\scripts\\verify.py --quick
    ... verify.py --ui        # 单测 + 界面自检（5–8 分钟）
    ... verify.py --pack      # 单测 + 打包 + selftest + GUI 冒烟（约 3 分钟）
    ... verify.py             # 全跑（约 10 分钟）

设计要点（都是踩过的坑）：

* 子进程输出**重定向到文件**，不用管道——受限环境里管道会 EPERM；
  终端只打「每步结论 + 失败行」，完整日志落 build/verify-logs/。
* 关 exe 只用精确进程名 `NovelFound.exe`（绝不按 python/pythonw 广杀）。
* 打包前把 TEMP/TMP 覆盖到工作区内目录，否则 PyInstaller 在受限环境会失败。
* 任何一步失败都继续跑完（拿到完整信息），最后用退出码汇总。
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT_HINT = "找不到仓库根（需要包含 main.py 与 novelfound/ 的目录）"
LOG_DIR_NAME = "verify-logs"


# --------------------------------------------------------------------------- 基础
def find_root() -> Path:
    """从脚本位置向上找仓库根。"""
    for candidate in [Path(__file__).resolve(), *Path(__file__).resolve().parents]:
        if (candidate / "main.py").is_file() and (candidate / "novelfound").is_dir():
            return candidate
    raise SystemExit(ROOT_HINT)


ROOT = find_root()
LOG_DIR = ROOT / "build" / LOG_DIR_NAME
VENV_PY = ROOT / ".venv" / "Scripts" / "python.exe"
PY = str(VENV_PY) if VENV_PY.is_file() else sys.executable

RESULTS: list[tuple[str, bool, str]] = []


def say(text: str = "") -> None:
    print(text, flush=True)


def record(stage: str, ok: bool, note: str = "") -> None:
    RESULTS.append((stage, ok, note))
    say(f"{'PASS' if ok else 'FAIL'}  {stage}  {note}")


def run(cmd: list[str], log_name: str, env_extra: dict | None = None,
        timeout: int = 900, cwd: Path | None = None) -> tuple[int, Path]:
    """跑子进程，stdout/stderr 落文件（不用管道），返回 (退出码, 日志路径)。"""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / log_name
    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    if env_extra:
        env.update(env_extra)
    with open(log_path, "w", encoding="utf-8", errors="replace") as handle:
        try:
            proc = subprocess.run(cmd, cwd=str(cwd or ROOT), env=env,
                                  stdout=handle, stderr=subprocess.STDOUT,
                                  timeout=timeout)
            code = proc.returncode
        except subprocess.TimeoutExpired:
            handle.write(f"\n[verify.py] 超时（>{timeout}s）\n")
            code = -9
    return code, log_path


def tail_lines(path: Path, patterns: tuple[str, ...], limit: int = 12) -> list[str]:
    """取出日志里匹配的行（用于只打失败/结论）。"""
    if not path.is_file():
        return []
    hits = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if any(p in line for p in patterns):
            hits.append(line.rstrip())
    return hits[-limit:]


def kill_app() -> None:
    """只关本项目的 exe（按精确进程名，绝不广杀 python/pythonw）。"""
    if os.name != "nt":
        return
    subprocess.run(["taskkill", "/IM", "NovelFound.exe", "/F"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)


# --------------------------------------------------------------------------- 各步
def stage_unit() -> bool:
    code, log = run([PY, "-m", "unittest", "discover", "-s", "tests"],
                    "unit-tests.log", timeout=600)
    summary = tail_lines(log, ("Ran ", "OK", "FAILED", "Error", "Traceback"), limit=8)
    ok = code == 0
    record("单元测试", ok, summary[-1] if summary else f"exit={code}")
    if not ok:
        for line in summary:
            say(f"      {line}")
    return ok


def stage_ui() -> bool:
    code, log = run([PY, "-u", "tests/verify_ui.py"], "verify-ui.log",
                    env_extra={"QT_QPA_PLATFORM": "offscreen",
                               "NOVELFOUND_HOME": str(ROOT / "tests" / ".uicheck")},
                    timeout=1800)
    fails = [ln for ln in tail_lines(log, ("FAIL",), limit=40) if ln.startswith("FAIL")]
    totals = tail_lines(log, ("通过",), limit=1)
    ok = code == 0 and not fails
    record("界面自检", ok, totals[0] if totals else f"exit={code}")
    for line in fails[:10]:
        say(f"      {line}")
    return ok


def stage_pack() -> bool:
    kill_app()
    shutil.rmtree(ROOT / "dist", ignore_errors=True)
    tmp = ROOT / ".pyi-tmp" / "tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    code, log = run([PY, "-m", "PyInstaller", "--noconfirm", "--clean",
                     "--distpath", "dist", "--workpath", "build/pyi",
                     "build/novelfound.spec"],
                    "pyinstaller.log",
                    env_extra={"TEMP": str(tmp), "TMP": str(tmp)}, timeout=1200)
    exe = ROOT / "dist" / "NovelFound" / "NovelFound.exe"
    ok = code == 0 and exe.is_file()
    record("打包 exe", ok, f"{exe.stat().st_size // 1024} KB" if ok else f"exit={code}")
    if not ok:
        for line in tail_lines(log, ("ERROR", "Error", "error:"), limit=8):
            say(f"      {line}")
    return ok


def stage_selftest() -> bool:
    exe = ROOT / "dist" / "NovelFound" / "NovelFound.exe"
    if not exe.is_file():
        record("exe 自检", False, "exe 不存在（打包失败？）")
        return False
    home = ROOT / "tests" / ".uicheck"
    home.mkdir(parents=True, exist_ok=True)
    report = home / "selftest.txt"
    report.unlink(missing_ok=True)
    code, log = run([str(exe), "--selftest"], "selftest.log",
                    env_extra={"NOVELFOUND_HOME": str(home),
                               "QT_QPA_PLATFORM": "offscreen"}, timeout=600)
    text = report.read_text(encoding="utf-8", errors="replace") if report.is_file() else ""
    ok = code == 0 and "自检结果：通过" in text
    last = [ln for ln in text.splitlines() if ln.strip()][-2:]
    record("exe 自检", ok, "；".join(last) if last else f"exit={code}")
    for line in tail_lines(log, ("失败", "异常"), limit=4):
        say(f"      {line}")
    return ok


def stage_gui() -> bool:
    exe = ROOT / "dist" / "NovelFound" / "NovelFound.exe"
    if not exe.is_file():
        record("GUI 冒烟", False, "exe 不存在")
        return False
    home = ROOT / "tests" / ".uicheck"
    home.mkdir(parents=True, exist_ok=True)
    crash = home / "crash.log"
    crash.unlink(missing_ok=True)
    env = dict(os.environ)
    env.update({"NOVELFOUND_HOME": str(home)})
    proc = subprocess.Popen([str(exe)], cwd=str(ROOT), env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(10)
    alive = proc.poll() is None
    if alive:
        proc.terminate()               # 只关自己启动的这个 PID
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
    trace = ""
    if crash.is_file():
        lines = crash.read_text(encoding="utf-8", errors="replace").splitlines()
        trace = "\n".join(lines[3:]).strip()      # 前 3 行是固定表头
    ok = alive and not trace
    record("GUI 冒烟", ok,
           "运行 10 秒无退出、crash.log 无堆栈" if ok
           else ("进程已退出" if not alive else "crash.log 有堆栈"))
    if trace:
        say(f"      {trace[:400]}")
    return ok


def stage_clean() -> None:
    """清临时产物（不动 tests/.uicheck：下一次自检会自己重建）。"""
    for path in (ROOT / "tests" / ".tmp", ROOT / "build" / "pyi", ROOT / ".pyi-tmp"):
        shutil.rmtree(path, ignore_errors=True)
    say("已清理：tests/.tmp、build/pyi、.pyi-tmp")


# --------------------------------------------------------------------------- 入口
def main() -> int:
    parser = argparse.ArgumentParser(description="NovelFound 验证闭环")
    parser.add_argument("--quick", action="store_true", help="只跑单元测试")
    parser.add_argument("--ui", action="store_true", help="单测 + 界面自检")
    parser.add_argument("--no-pack", action="store_true",
                        help="单测 + 界面自检（不打包）")
    parser.add_argument("--pack", action="store_true",
                        help="单测 + 打包 + exe 自检 + GUI 冒烟")
    args = parser.parse_args()

    if args.quick:
        steps = [stage_unit]
    elif args.ui or args.no_pack:
        steps = [stage_unit, stage_ui]
    elif args.pack:
        steps = [stage_unit, stage_pack, stage_selftest, stage_gui]
    else:
        steps = [stage_unit, stage_ui, stage_pack, stage_selftest, stage_gui]

    say(f"仓库：{ROOT}")
    say(f"Python：{PY}")
    say(f"步骤：{' → '.join(s.__name__.replace('stage_', '') for s in steps)}")
    say("-" * 60)
    started = time.time()
    for step in steps:
        step()
    if not args.quick:
        stage_clean()

    say("-" * 60)
    failed = [name for name, ok, _ in RESULTS if not ok]
    say(f"结果：{'全部通过' if not failed else '失败 ' + '、'.join(failed)}"
        f"（{len(RESULTS) - len(failed)}/{len(RESULTS)}，耗时 {time.time() - started:.0f}s）")
    say(f"完整日志：{LOG_DIR}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

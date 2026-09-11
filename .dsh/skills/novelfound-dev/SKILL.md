---
name: novelfound-dev
description: NovelFound（PyQt5 小说搜索阅读器）的开发与验证规约：一条命令跑完验证闭环、Qt 文本排版测量模型、沙箱与打包陷阱、界面铁律与回归重点。
whenToUse: 在 D:\AI\option\novelfound 里改代码、跑测试、打包 exe，或排查该项目的界面/排版/抓取问题时。
---

# NovelFound 开发规约

PC 端小说搜索 + 阅读器（Python 3.13 + PyQt5 5.15，Windows）。仓库：`D:\AI\option\novelfound`。

## 1. 验证闭环：一条命令

```powershell
# 全跑（单测 → 界面自检 → 打包 → exe 自检 → GUI 冒烟），约 10 分钟
.\.venv\Scripts\python.exe .dsh\skills\novelfound-dev\scripts\verify.py

# 常用档位
... verify.py --quick     # 只跑单元测试（约 5 秒，改纯逻辑时用）
... verify.py --ui        # 单测 + 界面自检（联网，5–8 分钟）
... verify.py --pack      # 单测 + 打包 + exe 自检 + GUI 冒烟（约 3 分钟）
... verify.py --no-pack   # 单测 + 界面自检（最常见的开发循环）
```

脚本只往终端打「每步结论 + 失败行」，完整日志落 `build/verify-logs/`。
**不要**自己拼那串命令（杀 exe → 清 dist → 设 TEMP → PyInstaller → selftest → 清理），
沙箱下 `TEMP` 必须覆盖成工作区目录，漏了会失败。

改完代码的规矩：**先 `--quick`，再 `--ui`（涉及界面时），最后 `--pack`**。
不要在没有跑过验证的情况下说"完成"。

## 2. 铁律（违反过，代价很大）

1. **关进程只按 PID 或精确 exe 名**：`Stop-Process -Name pythonw` 这类操作会误杀用户
   其它程序（本项目开发中真的误杀过一个正在使用的应用）。只允许针对 `NovelFound.exe`。
2. **`.git` 是用户的**：不要 `git commit` / `git checkout` / 改 `.gitignore`，除非用户明确要求。
3. **界面文案、注释、文档一律中文**；代码里的 UI 字符串也用中文。
4. **受限环境下系统临时目录不可写**：`tempfile.mkstemp/mkdtemp` 会卡死或 `PermissionError`，
   `pip install` 也会挂在 `*.whl.metadata`。测试用 `tests/.tmp/`，配置用
   `novelfound/storage.py` 的 `atomic_write_*`（同目录 + PID 后缀 + `os.replace`）。
5. **抓取层（`net.py` / `cleaner.py` / `sources/*` / `cache.py` / `library.py` / `tasks.py`）
   在界面任务里不要动**——只改界面层时，这些文件应当零改动。

## 3. 三条必过回归（每次改动都要确认）

| 回归点 | 断言位置 |
| --- | --- |
| 「继续阅读」落在上次那一章 | `tests/verify_ui.py`：`reading_index()` + 点主按钮拿到的 index |
| 滚动位置恢复 | 同上：「滚动位置已记录」「返回章节恢复滚动位置」 |
| 封面节流（并发上限 + 只下可见项） | 同上：「封面并发不超过上限」「滚动后封面队列被消费」 |

## 4. 界面铁律（P0/P1/P2 重构后定下来的）

- **配色只用 `ui/theme.py` 的 tokens**：暖白 `#F7F3E9`、正文 `#333333`，**不要纯白/纯黑**，
  `ACCENT = None`（强调色待定，改动只动这一个变量）。
- **浮层不参与布局**：搜索面板 / 浏览历史 / 目录抽屉 / 遮罩 / 阅读设置浮层都是
  `centralWidget()` 或 `canvas` 的子控件，由 `_layout_overlays()` 用 `setGeometry()` 定位。
  放进布局会让动画和定位互相打架。
- **动画 200–300ms**：`widgets.FADE_MS = 200`、`widgets.DRAWER_MS = 250`。
  动画只负责视觉，**显示状态必须由显式的 `_shown_state` 立刻更新**，
  否则"刚 close、动画还在跑"的窗口期状态是错的。
- **阅读设置只有一个入口**：阅读器右上角 `Aa`（`ui/reader_settings.py`）。
  设置对话框里不要再加一份字号/主题/排版，避免"两处真相"。
- **书架格子进度展示**：封面右下角角标 + 书名下百分比文字（两处并存是用户明确要求的，
  未读显示「未读」，**不要加进度条**）。

## 5. 参考文档（按需加载，不要一次性全读）

| 文件 | 什么时候读 |
| --- | --- |
| `references/qt-layout.md` | 改分页 / 行距 / 正文宽度 / 翻页，或界面出现"被裁切""一页只装一段"时 |
| `references/traps.md` | 沙箱报错、打包失败、自检偶发失败、进程/编码诡异问题时 |
| `scripts/harness.py` | 要写一次性验证脚本时（省掉 40 行样板） |

仓库内已有：`docs/TROUBLESHOOTING.md`（15 节踩坑复盘，含根因和实测数字）、
`docs/UI重构实施方案.md`（P0/P0.5/P1/P2 的方案与验收表）、`Readme.md`（功能/结构/用法）。

## 6. 写一次性验证脚本时

不要重敲样板，直接：

```python
import sys
sys.path.insert(0, r"D:\AI\option\novelfound\.dsh\skills\novelfound-dev\scripts")
from harness import make_app, pump, make_window, synthetic_chapter

app = make_app()                      # 离屏，设好 QT_QPA_PLATFORM / NOVELFOUND_HOME
window = make_window()                # 真主窗口，1360x880，已 show
pump(app, 0.5)
window.reader.set_content(synthetic_chapter(), index=0)   # 内容量确定的正文
pump(app, 0.4)
```

**注意**：必须先 `import harness` 再 `import novelfound.*`（环境变量要在 Qt/配置模块
导入之前设好）。

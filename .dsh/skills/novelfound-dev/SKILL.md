---
name: novelfound-dev
description: NovelFound（PyQt5 小说搜索阅读器）的开发与验证规约：一条命令跑完验证闭环、验证方法论（断言铁律/反向对照/护栏）、Qt 文本排版测量模型、沙箱与打包陷阱、界面铁律与回归重点。
whenToUse: 在 D:\AI\option\novelfound 里改代码、跑测试、打包 exe，或排查该项目的界面/排版/抓取问题时。
---

# NovelFound 开发规约

PC 端小说搜索 + 阅读器（Python 3.13 + PyQt5 5.15，Windows）。仓库：`D:\AI\option\novelfound`。

## 1. 验证闭环：一条命令

```powershell
# 全跑（单测 → 界面自检 → 打包 → exe 自检 → GUI 冒烟），约 2.5 分钟
.\.venv\Scripts\python.exe .dsh\skills\novelfound-dev\scripts\verify.py

# 常用档位
... verify.py --quick     # 只跑单元测试（约 5 秒，改纯逻辑时用）
... verify.py --ui        # 单测 + 界面自检（联网，约 1 分钟）
... verify.py --pack      # 单测 + 打包 + exe 自检 + GUI 冒烟（约 2 分钟）
... verify.py --no-pack   # 单测 + 界面自检（最常见的开发循环）
```

脚本只往终端打「每步结论 + 失败行」，完整日志落 `build/verify-logs/`。
**不要**自己拼那串命令（杀 exe → 清 dist → 设 TEMP → PyInstaller → selftest → 清理），
沙箱下 `TEMP` 必须覆盖成工作区目录，漏了会失败。

改完代码的规矩：**先 `--quick`，再 `--ui`（涉及界面时），最后 `--pack`**。
不要在没有跑过验证的情况下说"完成"。

## 2. 验证方法论（血泪换来的，改代码前先读这节）

追"翻页丢内容"那个 bug 花掉的时间，**一半浪费在验证本身不可靠上**。每条都有代价：

1. **断言只许盯"用户能看到的结果"**，不许盯实现内部的量。
   - 反面教材：判断"页面没溢出"曾经用 `document().size().height() <= viewport + 2` ——
     那正是**分页自己用的高度模型**，属于**循环验证**：259 项全绿，界面在裁字。
   - 正确做法：① 裁切 → 抓 `viewport()` 的图**数像素**；② 内容完整性 → 把"逐屏收集的文字"
     （去空白）与整章原文**逐字比较**。
2. **反向对照**：写完断言，把**旧的错误实现临时装回去**，确认断言**真的会红**。
   不会红的断言等于没写（同一天栽过两次：坏掉的测试 PNG、只查 `<img>` 标签的插图断言）。
3. **断言绿了但症状还在 → 先怀疑断言**，不是怀疑修复。症状是唯一的事实来源。
4. **改了没反应 → 质疑前提**：`_line_advance` 连改两次，分页页数一次没变（17→17→17），
   因为真正错的是"用整篇文档的行坐标推算页面"这个**做法**本身。
5. **抓图要抓稳定帧**：连续两次 `grab()` 内容一致再用，否则会抓到上一帧、误报"贴底"。
6. **一次性探针必须带护栏**：循环都要有上限（页数/章节数/迭代数），超限就报
   "疑似死循环"而不是跑到超时。真实事故：分页坏成 2898 页时扫描脚本照样逐屏走，
   撞了 4 次超时（420s×3 + 300s），纯等待二十多分钟。
7. **前置条件不满足要主动报错，不许"顺利通过"**：
   - **几何护栏**：视口尺寸不合理时分页会走兜底 → 扫描变成"每章 1 页、全部通过"的**假绿**。
     真实踩到：没切到阅读器页面，视口只有 30px。凡是量页面的脚本，先打印并校验视口尺寸。
   - **样本要能覆盖被测行为**：给 EPUB 断言"插图会显示"时，样本 EPUB 里**得真的有图**。
8. **真实样本放 `tests/.testdata/`**（`.gitignore` 已忽略、`verify.py` 不会清），
   不要放 `tests/.tmp/`——每轮验证都会清掉，实测重复复制过 4 次 16MB 的书。
9. **拿到用户报障先问三件事**：哪一章、什么模式（单页/双页/滚动）、什么输入方式
   （滚轮/键盘/按钮）。"偶尔能顺畅阅读"这类描述往往就是**根因指纹**（驱动把一次滚动拆成
   多个事件），一开始就问能省掉几轮。
10. **全量扫描比抽样强**：`scripts/sweep.py` 逐章"逐屏收集文字 vs 原文"实测
    12 章 1 秒 → 整本 1408 章约 2 分钟，能给出"**每一章**都过了"的结论，
    比"我抽查了 6 章"有说服力得多。改分页/翻页后**应当跑一次全量**。

## 3. 铁律（违反过，代价很大）

1. **关进程只按 PID 或精确 exe 名**：`Stop-Process -Name pythonw` 这类操作会误杀用户
   其它程序（本项目开发中真的误杀过一个正在使用的应用）。只允许针对 `NovelFound.exe`。
   **用户自己开着的 `NovelFound.exe` 不要杀**（会锁住 `dist/` 让打包失败）——告诉用户关掉再打包。
2. **`.git` 是用户的**：不要 `git commit` / `git checkout` / 改 `.gitignore`，除非用户明确要求。
3. **界面文案、注释、文档一律中文**；代码里的 UI 字符串也用中文。
4. **受限环境下系统临时目录不可写**：`tempfile.mkstemp/mkdtemp` 会卡死或 `PermissionError`，
   `pip install` 也会挂在 `*.whl.metadata`。测试用 `tests/.tmp/`，配置用
   `novelfound/storage.py` 的 `atomic_write_*`（同目录 + PID 后缀 + `os.replace`）。
   同理**沙箱不能写 `%APPDATA%`**：`config.data_dir()` 的写测试会失败并回落到临时目录，
   想拿用户真实数据做验证，先复制到 `tests/.testdata/` 再把 `NOVELFOUND_HOME` 指过去。
5. **抓取层（`net.py` / `cleaner.py` / `sources/*` / `cache.py` / `library.py` / `tasks.py`）
   在界面任务里不要动**——只改界面层时，这些文件应当零改动。

## 4. 三条必过回归（每次改动都要确认）

| 回归点 | 断言位置 |
| --- | --- |
| 「继续阅读」落在上次那一章 | `tests/verify_ui.py`：`reading_index()` + 点主按钮拿到的 index |
| 滚动位置恢复 | 同上：「滚动位置已记录」「返回章节恢复滚动位置」 |
| 封面节流（并发上限 + 只下可见项） | 同上：「封面并发不超过上限」「滚动后封面队列被消费」 |

翻页/分页相关改动另加四条（见 `verify_ui.py` 阅读器段落）：
「一格被拆成多个事件也只翻一屏」「逐屏翻完全程不丢内容」
「每页底部都留有余量（像素判据）」「翻页模式视口宽度恒定 + 不显示滚动条」。

## 5. 界面铁律（P0/P1/P2 重构后定下来的）

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
- **翻页模式宽度必须恒定**：视图一律 `ScrollBarAlwaysOff`。滚动条一旦出现就会挤窄视口
  → 文字重排更多行 → 更溢出（**粘住**），而分页是按原宽度算的 → 底部被裁。

## 6. 参考文档（按需加载，不要一次性全读）

| 文件 | 什么时候读 |
| --- | --- |
| `references/qt-layout.md` | 改分页 / 行距 / 正文宽度 / 翻页，或界面出现"被裁切""一页只装一段"时 |
| `references/traps.md` | 沙箱报错、打包失败、自检偶发失败、进程/编码诡异问题时 |
| `scripts/harness.py` | 要写一次性验证脚本时（省掉 40 行样板） |
| `scripts/sweep.py` | 改完分页/翻页后要"每章都过一遍"时（全量扫描，带几何护栏） |

仓库内已有：`docs/TROUBLESHOOTING.md`（20 节踩坑复盘，含根因和实测数字）、
`docs/UI重构实施方案.md`（P0/P0.5/P1/P2 的方案与验收表）、`docs/项目总结.md`、
`Readme.md`（功能/结构/用法）。

## 7. 写一次性验证脚本时

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

**注意**：
- 必须先 `import harness` 再 `import novelfound.*`（环境变量要在 Qt/配置模块导入之前设好）。
- 要量阅读器/页面的脚本，**必须真的切到那一页**（`window.on_read_requested(...)`），
  否则控件没尺寸、分页走兜底、结果假绿（见 §2.7）。
- 脚本结尾打印**视口尺寸、样本量、失败数**，让"这次验证到底覆盖了什么"一目了然。

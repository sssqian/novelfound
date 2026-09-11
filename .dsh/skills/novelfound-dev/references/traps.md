# 环境与流程陷阱（都真实踩过）

## 一、沙箱 / 环境

| 现象 | 根因 | 做法 |
| --- | --- | --- |
| `pip install` 报 `[Errno 13] Permission denied: ...whl.metadata` | 系统临时目录对进程不可写 | 手动从 PyPI 取 wheel 解包；或直接用已装好的 `.venv` |
| `tempfile.mkstemp()/mkdtemp()` 卡住或 `PermissionError` | 同上 | 测试用 `tests/.tmp/`；写文件用 `novelfound/storage.py` 的 `atomic_write_*` |
| 后台命令 `[exit code: 1]` 但输出显示成功 | Windows 上强杀进程不带信号标记；或 PowerShell 管道的 `$LASTEXITCODE` | 看输出内容 + 单独确认退出码，别只看 code |
| 中文输出在终端乱码 | 控制台代码页 | 命令前设 `$env:PYTHONIOENCODING='utf-8'`，或读脚本落盘的日志文件 |

## 二、进程（最严重的一条）

- **不要 `Stop-Process -Name pythonw` / `-Name python`**：会把用户其它 Python 程序一起杀掉
  （本项目开发中真的误杀过一个正在使用的应用，事后道歉才补回来）。
- 只允许两种关法：
  1. `Get-Process NovelFound | Stop-Process -Force`（精确到本项目的 exe）；
  2. `Popen` 拿到的 PID → `terminate()`。
- `verify.py` 已经内置了安全关法，改脚本时别退化成按名字广杀。

## 三、打包

- **onedir 是默认，不要切 onefile**：onefile 在受限环境下报
  `Could not create temporary directory` / `fopen: Permission denied`（连 hello world 都失败）。
  需要单文件时用 `NOVELFOUND_ONEFILE=1` 环境变量，并接受它可能失败。
- **`TEMP` / `TMP` 必须覆盖成工作区内目录**（如 `.pyi-tmp\tmp`），否则 PyInstaller 失败。
- 打包前必须**先关掉正在运行的 `NovelFound.exe`**，否则 dist 被占用、清理失败。
- 打包命令固定为：
  `python -m PyInstaller --noconfirm --clean --distpath dist --workpath build\pyi build\novelfound.spec`
- 窗口化 exe 没有控制台：`--selftest` 的结果写在 `NOVELFOUND_HOME/selftest.txt`，
  要看那个文件，不要指望 stdout。

## 四、自检（verify_ui.py）

- 它**联网**：搜索 → 详情 → 阅读全流程，跑一次 5–8 分钟；别和重活并行，也别反复空跑。
- 每次运行会 `rmtree` 独立数据目录 `tests/.uicheck`（不影响用户真实书架）。
- 会写 18 张截图到 `tests/shots/`；截图是给人看的，**当前模型读不了图**，
  所以视觉问题要靠数值断言兜住（尺寸、颜色统计、几何关系）。
- **偶发失败的典型来源是网络内容**，不是代码：
  同一本书不同书源命中的章节长度差别极大（实测遇到过整章 272 字、只有一屏），
  "翻页/滚轮前进"这类相对断言会失真。
  → 分页类断言改用 `synthetic_chapter()`；需要滚动恢复时先找一章
  `scrollBar().maximum() >= 700` 的长章节再断言。
- 断言绑定控件名：重构后要么改断言对象，要么保留可测试的显式状态方法
  （`show_bars/hide_bars/is_bar_visible`、`open/close/is_open`），不要用 `sleep` 等动画。

## 五、界面代码里的坑

- 浮层放进布局 → 动画与 `setSizes`/布局互相打架。浮层一律 `setGeometry()` 定位。
- `QPropertyAnimation` 不保存引用会被 GC，表现是"偶尔不动画"。`widgets._keep()` 已经处理。
- `QGraphicsOpacityEffect` 会让**子控件一起半透明**，只给容器加，不给正文视图加。
- 先 `open_drawer()` 再布局 → 滑入目标位置是旧尺寸，动画结束会"跳"一下。
  正确顺序：先 `_layout_overlays()` 再 `open_drawer()`。
- 配置里出现过两份"阅读设置"（设置对话框 + 阅读器工具栏），用户改了 A 以为 B 也变。
  现在统一到 `Aa` 浮层，**不要再加第二处**。
- 废弃配置键（`sidebar_visible` / `auto_hide_sidebar` / `splitter_sizes`）由
  `config.DEPRECATED_KEYS` 在读盘时清掉，别恢复使用。

## 六、仓库纪律

- `.git` 属于用户：只读不改。也不要顺手把 `.dsh/` 写进 `.gitignore`——
  skill 是否入库由用户决定。
- `docs/TROUBLESHOOTING.md` 是给人和 agent 看的踩坑记录，**修完复杂 bug 要补一节**：
  现象 / 根因 / 修复 / 实测验证数字。
- 改动后同步 `Readme.md` 的功能表、快捷键、目录树（用户会照着 README 用）。

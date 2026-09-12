# -*- coding: utf-8 -*-
"""界面配色与样式表。

设计目标见 `docs/UI设计方案：现代沉浸式小说阅读器.md`：
安静、沉浸、内容优先。所有颜色集中在这里（tokens），
其它模块不要再写死颜色值。

配色要点：
* 背景用暖白 ``#F7F3E9``，**不用纯白**；
* 正文 ``#333333``，**不用纯黑**；
* **不用蓝色强调色**，主按钮用墨色（深灰）+ 留白区分层级；
* 尽量少用边框，靠底色差与间距划分区域。
"""
from __future__ import annotations

from typing import Dict

# ---------------------------------------------------------------------------
# Design tokens（主界面）
# ---------------------------------------------------------------------------
BG_APP = "#F7F3E9"          # 应用背景（方案指定暖白）
BG_SURFACE = "#FBF9F3"      # 卡片 / 浮层（比底色略亮，避免纯白）
BG_HOVER = "#F1ECE0"        # 悬停态
BG_SELECTED = "#F3EEE2"     # 选中态
TEXT_MAIN = "#333333"       # 正文 / 标题（方案指定）
TEXT_SUB = "#8A8578"        # 辅助文字（暖灰）
TEXT_FAINT = "#A9A296"      # 更弱的文字 / 次要边框
DIVIDER = "#E8E2D6"         # 极浅分隔线
SCROLL_HANDLE = "#DCD5C7"
SCROLL_HANDLE_HOVER = "#C9C0AE"

# 文本选中高亮：不给就会用 Qt 默认的**系统蓝**，与"暖白 / 无蓝"的配色规范冲突。
SELECTION_BG = "#E5DBC3"    # 暖沙色，比 BG_SELECTED 明显一点才看得出选中
SELECTION_FG = TEXT_MAIN
# 封面无图时的占位底色：原来是硬编码冷灰 #f2f4f7，在暖白背景上发蓝。
PLACEHOLDER_BG = "#F1ECE0"
PLACEHOLDER_FG = TEXT_FAINT
# 封面右下角的进度角标（半透明深色药丸 + 暖白字）
BADGE_BG = "rgba(45, 41, 35, 205)"
BADGE_FG = BG_SURFACE

# 强调色待定（方案：先不着急）。代码统一引用这个变量，定了只改这里。
ACCENT = None
WARN = "#8A6A2F"
ERROR = "#A3402F"
SUCCESS = "#3F6B4A"

RADIUS = 8
RADIUS_SM = 4

# 兼容旧代码里的名字（widgets.py 等仍 import 这些）
BG = BG_APP
CARD = BG_SURFACE
BORDER = DIVIDER
TEXT = TEXT_MAIN
MUTED = TEXT_SUB
ACCENT_DARK = TEXT_MAIN      # 旧代码里按钮 hover 用的深色，这里改用墨色

# ---------------------------------------------------------------------------
# 阅读器主题：暖白（默认）/ 护眼 / 羊皮纸 / 夜间 / 深灰
# ---------------------------------------------------------------------------
READER_THEMES: Dict[str, Dict[str, str]] = {
    "warm": {"name": "暖白", "bg": "#F7F3E9", "fg": "#333333",
             "muted": "#8A8578", "link": "#8A6A2F"},
    "eye": {"name": "护眼", "bg": "#c7edcc", "fg": "#1d2b1d",
            "muted": "#4f6b4f", "link": "#1f6f4a"},
    "sepia": {"name": "羊皮纸", "bg": "#f6ecd8", "fg": "#3a2f1e",
              "muted": "#7a6a4f", "link": "#8a5a1f"},
    "night": {"name": "夜间", "bg": "#1b1f23", "fg": "#c2c9d1",
              "muted": "#7c8794", "link": "#6ea8fe"},
    "dark": {"name": "深灰", "bg": "#2b2b2b", "fg": "#d6d6d6",
             "muted": "#9a9a9a", "link": "#7fb0ff"},
}

# 旧配置里的 "day" 指向暖白（纯白已按方案弃用）
THEME_ALIASES = {"day": "warm"}


def reader_theme(key: str) -> Dict[str, str]:
    """按 key 取阅读主题（兼容旧的 day → warm）。"""
    key = THEME_ALIASES.get(key or "", key or "")
    return READER_THEMES.get(key) or READER_THEMES["warm"]


def theme_is_dark(theme: Dict[str, str]) -> bool:
    """判断主题底色是不是深色（决定浮条分隔线用黑还是白）。"""
    bg = (theme or {}).get("bg", "#ffffff").lstrip("#")
    try:
        r, g, b = (int(bg[i:i + 2], 16) for i in (0, 2, 4))
    except (ValueError, IndexError):
        return False
    return (0.299 * r + 0.587 * g + 0.114 * b) < 128


def reader_selection(theme: Dict[str, str]) -> tuple:
    """阅读器正文的选中色 (背景, 前景)。

    浅色主题用暖沙色；深色主题用半亮灰蓝，保证"选中"在深底上也看得出来，
    且都不使用 Qt 默认的系统蓝。
    """
    if theme_is_dark(theme):
        return "#3C4650", theme.get("fg", "#c2c9d1")
    return SELECTION_BG, theme.get("fg", TEXT_MAIN)



def app_stylesheet() -> str:
    """主界面 QSS（暖白 + 少边框 + 无蓝色）。"""
    return f"""
    QWidget {{
        font-family: "Microsoft YaHei UI", "PingFang SC", "Noto Sans CJK SC", sans-serif;
        font-size: 13px;
        color: {TEXT_MAIN};
    }}
    QMainWindow, #centralArea {{ background: {BG_APP}; }}
    /* 对话框也用暖白：原来只有窗口页签面板（QTabWidget::pane）有底色，
       且用的是卡片色 BG_SURFACE，于是设置对话框看着比主窗口白一档。 */
    QDialog, QTabWidget::pane {{ background: {BG_APP}; }}

    /* 文本选中高亮统一用暖沙色。不写这几条时，Qt 会用**系统蓝**渲染选中态，
       在搜索浮层打开（输入框自动全选）时尤其显眼，违反"暖白 / 不用蓝"的规范。 */
    QLineEdit, QTextEdit, QPlainTextEdit, QTextBrowser {{
        selection-background-color: {SELECTION_BG};
        selection-color: {SELECTION_FG};
    }}
    QLabel {{ selection-background-color: {SELECTION_BG}; selection-color: {SELECTION_FG}; }}

    #topBar {{ background: {BG_APP}; border-bottom: 1px solid {DIVIDER}; }}
    QLineEdit#searchBox {{
        background: {BG_SURFACE}; border: 1px solid {DIVIDER}; border-radius: 17px;
        padding: 6px 14px; font-size: 14px; min-height: 22px;
    }}
    QLineEdit#searchBox:focus {{ border: 1px solid {TEXT_FAINT}; background: {BG_SURFACE}; }}

    QPushButton {{
        background: {BG_SURFACE}; border: 1px solid {DIVIDER}; border-radius: 6px;
        padding: 6px 14px; color: {TEXT_MAIN};
    }}
    QPushButton:hover {{ background: {BG_HOVER}; border-color: {TEXT_FAINT}; }}
    QPushButton:disabled {{ color: {TEXT_FAINT}; border-color: {DIVIDER}; }}
    QPushButton#primary {{
        background: {TEXT_MAIN}; border: 1px solid {TEXT_MAIN}; color: {BG_SURFACE};
        font-weight: 600;
    }}
    QPushButton#primary:hover {{ background: #4A4A4A; border-color: #4A4A4A; }}
    QPushButton#primary:disabled {{ background: {TEXT_FAINT}; border-color: {TEXT_FAINT}; }}
    QPushButton#ghost {{ background: transparent; border: none; color: {TEXT_SUB}; }}
    QPushButton#ghost:hover {{ color: {TEXT_MAIN}; }}

    #sidePanel {{ background: {BG_APP}; border-right: 1px solid {DIVIDER}; }}
    #sideHeader {{ color: {TEXT_SUB}; padding: 10px 12px 4px 12px; font-size: 12px; }}

    #card {{
        background: {BG_SURFACE}; border: none; border-radius: {RADIUS}px;
    }}
    #card:hover {{ background: {BG_HOVER}; }}
    #card[selected="true"] {{
        background: {BG_SELECTED}; border: 1px solid {TEXT_FAINT};
    }}
    #bookTitle {{ font-size: 14px; font-weight: 600; color: {TEXT_MAIN}; }}
    #bookMeta {{ color: {TEXT_SUB}; font-size: 12px; }}
    #sourceTag {{
        color: {TEXT_SUB}; background: {BG_HOVER}; border-radius: 3px;
        padding: 1px 6px; font-size: 11px;
    }}

    #sectionTitle {{ font-size: 15px; font-weight: 600; color: {TEXT_MAIN}; }}
    #muted {{ color: {TEXT_SUB}; }}
    #introText {{ color: #4A4A4A; }}
    QListWidget {{
        background: {BG_SURFACE}; border: none; border-radius: {RADIUS}px;
        outline: none;
    }}
    QListWidget::item {{ padding: 7px 10px; border-radius: {RADIUS_SM}px; }}
    QListWidget::item:hover {{ background: {BG_HOVER}; }}
    QListWidget::item:selected {{ background: {BG_SELECTED}; color: {TEXT_MAIN}; }}
    QScrollArea {{ border: none; background: transparent; }}
    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
    QScrollBar::handle:vertical {{
        background: {SCROLL_HANDLE}; border-radius: 5px; min-height: 30px;
    }}
    QScrollBar::handle:vertical:hover {{ background: {SCROLL_HANDLE_HOVER}; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QStatusBar {{
        background: {BG_APP}; border-top: 1px solid {DIVIDER}; color: {TEXT_SUB};
    }}
    QComboBox {{
        background: {BG_SURFACE}; border: 1px solid {DIVIDER}; border-radius: 6px;
        padding: 4px 10px; min-height: 20px;
    }}
    QComboBox:hover {{ border-color: {TEXT_FAINT}; }}
    QComboBox::drop-down {{ border: none; width: 18px; }}
    QSpinBox, QDoubleSpinBox, QLineEdit#plain {{
        background: {BG_SURFACE}; border: 1px solid {DIVIDER}; border-radius: 6px;
        padding: 4px 8px;
    }}
    QGroupBox {{
        border: 1px solid {DIVIDER}; border-radius: {RADIUS}px; margin-top: 12px;
        padding: 10px 10px 6px 10px; background: {BG_SURFACE};
    }}
    QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 4px; color: {TEXT_SUB}; }}
    QTabWidget::pane {{ border: 1px solid {DIVIDER}; border-radius: 6px; background: {BG_APP}; }}
    QTabBar::tab {{
        background: transparent; padding: 7px 16px; color: {TEXT_SUB};
        border-bottom: 2px solid transparent;
    }}
    QTabBar::tab:hover {{ color: {TEXT_MAIN}; }}
    QTabBar::tab:selected {{ color: {TEXT_MAIN}; border-bottom: 2px solid {TEXT_MAIN}; }}
    QToolButton {{ background: transparent; border: none; padding: 4px 8px; border-radius: 5px; }}
    QToolButton:hover {{ background: {BG_HOVER}; }}
    QToolButton:checked {{ background: {BG_SELECTED}; color: {TEXT_MAIN}; }}
    QCheckBox {{ color: {TEXT_MAIN}; }}

    #readerToolBar {{ background: {BG_SURFACE}; border-bottom: 1px solid {DIVIDER}; }}
    #readerTitle {{ font-size: 14px; font-weight: 600; color: {TEXT_MAIN}; }}
    #chapterHeader {{ color: {TEXT_SUB}; font-size: 12px; padding: 10px 16px 2px 16px; }}
    #autoHideBar {{ background: {BG_SURFACE}; }}
    #autoHideBar[edge="top"] {{ border-bottom: 1px solid {DIVIDER}; }}
    #autoHideBar[edge="bottom"] {{ border-top: 1px solid {DIVIDER}; }}

    /* ---------------- P1：书架首页 / 搜索浮层 / 详情页 / 目录抽屉 ---------------- */
    #libraryView, #bookView {{ background: {BG_APP}; }}
    QScrollArea#pageScroll, QWidget#pageBody {{ background: {BG_APP}; }}
    #pageTitle {{ font-size: 22px; font-weight: 700; color: {TEXT_MAIN}; }}
    #continueTitle {{ font-size: 16px; font-weight: 600; color: {TEXT_MAIN}; }}
    #continuePercent {{ font-size: 15px; font-weight: 600; color: {TEXT_SUB}; }}

    #continueCard {{
        background: {BG_SURFACE}; border: none; border-radius: 12px;
    }}
    #continueCard:hover {{ background: {BG_HOVER}; }}

    #coverTile {{ background: transparent; border-radius: 10px; }}
    #coverTile:hover {{ background: {BG_HOVER}; }}
    #tileTitle {{ font-size: 13px; color: {TEXT_MAIN}; }}
    #tileMeta {{ font-size: 12px; color: {TEXT_SUB}; }}
    /* 封面右下角的进度角标（半透明小圆角标签）。
       实际样式由 widgets.CoverTile 给角标单独设置——父控件 CoverLabel 的样式表
       会连子控件一起生效并盖掉这条，详见 CoverTile 里的注释。 */
    #coverBadge {{
        background: {BADGE_BG}; color: {BADGE_FG};
        border-radius: 9px; padding: 1px 7px; font-size: 11px;
    }}

    /* 阅读设置浮层（Aa） */
    #settingsPopover {{
        background: {BG_SURFACE}; border: 1px solid {DIVIDER}; border-radius: 10px;
    }}
    #settingsPopover QLabel {{ color: {TEXT_SUB}; }}
    #settingsPopover QToolButton {{
        background: {BG_SURFACE}; border: 1px solid {DIVIDER}; border-radius: 6px;
        padding: 4px 10px; color: {TEXT_MAIN};
    }}
    #settingsPopover QToolButton:hover {{ background: {BG_HOVER}; }}
    #settingsPopover QToolButton:checked {{
        background: {BG_SELECTED}; border-color: {TEXT_FAINT};
    }}

    QPushButton#link {{
        background: transparent; border: none; padding: 2px 4px;
        color: {TEXT_SUB}; text-align: left;
    }}
    QPushButton#link:hover {{ color: {TEXT_MAIN}; }}

    QPushButton#iconButton {{
        background: transparent; border: none; border-radius: 6px;
        padding: 6px 10px; font-size: 15px; color: {TEXT_MAIN};
    }}
    QPushButton#iconButton:hover {{ background: {BG_HOVER}; }}

    #scrim {{ background: rgba(40, 36, 30, 70); }}

    #paletteCard {{
        background: {BG_SURFACE}; border: 1px solid {DIVIDER}; border-radius: 12px;
    }}
    QLineEdit#paletteInput {{
        background: transparent; border: none; font-size: 16px; padding: 2px 0;
        color: {TEXT_MAIN};
    }}
    #paletteIcon {{ font-size: 16px; color: {TEXT_SUB}; }}
    #paletteHint {{ color: {TEXT_SUB}; font-size: 12px; }}
    QListWidget#paletteResults {{ background: transparent; border: none; }}
    QListWidget#paletteResults::item {{ border-radius: 8px; padding: 0; }}
    QListWidget#paletteResults::item:hover {{ background: {BG_HOVER}; }}
    QListWidget#paletteResults::item:selected,
    QListWidget#paletteResults::item:selected:active,
    QListWidget#paletteResults::item:selected:!active {{ background: {BG_SELECTED}; }}
    #paletteRow {{ background: transparent; }}

    #catalogDrawer {{
        background: {BG_SURFACE}; border-right: 1px solid {DIVIDER};
    }}
    #drawerHeader {{ background: {BG_SURFACE}; }}
    #drawerTitle {{ font-size: 14px; font-weight: 600; color: {TEXT_MAIN}; }}
    QListWidget#drawerList {{ background: {BG_SURFACE}; border: none; }}
    QTreeWidget#drawerList {{ background: {BG_SURFACE}; border: none; outline: none; }}
    QTreeWidget#drawerList::item {{ padding: 5px 4px; border-radius: {RADIUS_SM}px; }}
    QTreeWidget#drawerList::item:hover {{ background: {BG_HOVER}; }}
    /* 注意要写 :active / :!active 两个变体：焦点在筛选框上时（视图非活动），
       只写 ::item:selected 会被 Qt 默认的**蓝色**高亮盖掉（视觉验收时发现的）。 */
    QTreeWidget#drawerList::item:selected,
    QTreeWidget#drawerList::item:selected:active,
    QTreeWidget#drawerList::item:selected:!active {{
        background: {BG_SELECTED}; color: {TEXT_MAIN};
    }}

    /* 本书插图浏览窗口 */
    QListWidget#galleryList {{ background: {BG_SURFACE}; border: 1px solid {DIVIDER};
        border-radius: {RADIUS}px; }}
    QListWidget#galleryList::item {{ border-radius: {RADIUS_SM}px; color: {TEXT_SUB};
        font-size: 11px; padding: 4px; }}
    QListWidget#galleryList::item:selected {{ background: {BG_SELECTED}; color: {TEXT_MAIN}; }}
    #galleryPreview {{ background: {BG_SURFACE}; border: 1px solid {DIVIDER};
        border-radius: {RADIUS}px; color: {TEXT_SUB}; }}

    /* 危险操作按钮（删除本地书） */
    QPushButton#danger {{ color: #B23A2E; border: 1px solid #E3B7B0;
        background: {BG_SURFACE}; padding: 6px 14px; border-radius: {RADIUS_SM}px; }}
    QPushButton#danger:hover {{ background: #FBEDEA; }}

    #toast {{
        background: rgba(51, 51, 51, 235); color: {BG_SURFACE};
        border-radius: {RADIUS}px; padding: 10px 14px;
    }}
    #toastError {{ background: rgba(163, 64, 47, 240); color: #FFFFFF; }}
    #banner {{ background: {BG_HOVER}; color: {WARN}; border-radius: 6px; padding: 8px 12px; }}
    #bannerError {{ background: #F3E4DF; color: {ERROR}; border-radius: 6px; padding: 8px 12px; }}
    #emptyTitle {{ font-size: 16px; font-weight: 600; color: #4A4A4A; }}
    #emptyHint {{ color: {TEXT_SUB}; }}
    """

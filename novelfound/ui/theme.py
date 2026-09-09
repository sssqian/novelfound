# -*- coding: utf-8 -*-
"""界面配色与样式表。

集中管理颜色，便于二次修改主题；阅读器配色单独定义，支持护眼 / 夜间等模式。
"""
from __future__ import annotations

from typing import Dict

# 主界面配色
BG = "#f4f5f7"
CARD = "#ffffff"
BORDER = "#e2e5ea"
TEXT = "#1f2328"
MUTED = "#6b7280"
ACCENT = "#2f6fed"
ACCENT_DARK = "#1f52c4"
WARN = "#b45309"
ERROR = "#c0392b"
SUCCESS = "#1f8a4c"

# 阅读器主题：名称 -> (背景, 文字, 次要文字, 选中/链接)
READER_THEMES: Dict[str, Dict[str, str]] = {
    "day": {"name": "日间", "bg": "#ffffff", "fg": "#1a1a1a",
            "muted": "#8a8a8a", "link": "#2f6fed"},
    "eye": {"name": "护眼绿", "bg": "#c7edcc", "fg": "#1d2b1d",
            "muted": "#4f6b4f", "link": "#1f6f4a"},
    "sepia": {"name": "羊皮纸", "bg": "#f6ecd8", "fg": "#3a2f1e",
              "muted": "#7a6a4f", "link": "#8a5a1f"},
    "night": {"name": "夜间", "bg": "#1b1f23", "fg": "#c2c9d1",
              "muted": "#7c8794", "link": "#6ea8fe"},
    "dark": {"name": "深灰", "bg": "#2b2b2b", "fg": "#d6d6d6",
             "muted": "#9a9a9a", "link": "#7fb0ff"},
}


def reader_theme(key: str) -> Dict[str, str]:
    return READER_THEMES.get(key) or READER_THEMES["eye"]


def app_stylesheet() -> str:
    """主界面 QSS。"""
    return f"""
    QWidget {{
        font-family: "Microsoft YaHei UI", "PingFang SC", "Noto Sans CJK SC", sans-serif;
        font-size: 13px;
        color: {TEXT};
    }}
    QMainWindow, #centralArea {{ background: {BG}; }}

    #topBar {{ background: {CARD}; border-bottom: 1px solid {BORDER}; }}
    QLineEdit#searchBox {{
        background: {BG}; border: 1px solid {BORDER}; border-radius: 17px;
        padding: 6px 14px; font-size: 14px; min-height: 22px;
    }}
    QLineEdit#searchBox:focus {{ border: 1px solid {ACCENT}; background: #ffffff; }}

    QPushButton {{
        background: {CARD}; border: 1px solid {BORDER}; border-radius: 6px;
        padding: 6px 14px;
    }}
    QPushButton:hover {{ border-color: {ACCENT}; color: {ACCENT}; }}
    QPushButton:disabled {{ color: #b0b6bd; border-color: {BORDER}; }}
    QPushButton#primary {{
        background: {ACCENT}; border: 1px solid {ACCENT}; color: #ffffff;
        font-weight: 600;
    }}
    QPushButton#primary:hover {{ background: {ACCENT_DARK}; }}
    QPushButton#primary:disabled {{ background: #a9bdf0; border-color: #a9bdf0; }}
    QPushButton#ghost {{ background: transparent; border: none; color: {MUTED}; }}
    QPushButton#ghost:hover {{ color: {ACCENT}; }}

    #sidePanel {{ background: {CARD}; border-right: 1px solid {BORDER}; }}
    #sideHeader {{ color: {MUTED}; padding: 10px 12px 4px 12px; font-size: 12px; }}

    #card {{ background: {CARD}; border: 1px solid {BORDER}; border-radius: 8px; }}
    #card:hover {{ border-color: {ACCENT}; }}
    #card[selected="true"] {{ border: 1px solid {ACCENT}; background: #f2f6ff; }}
    #bookTitle {{ font-size: 14px; font-weight: 600; }}
    #bookMeta {{ color: {MUTED}; font-size: 12px; }}
    #sourceTag {{
        color: {ACCENT}; background: #eaf0ff; border-radius: 3px;
        padding: 1px 6px; font-size: 11px;
    }}

    #sectionTitle {{ font-size: 15px; font-weight: 600; }}
    #muted {{ color: {MUTED}; }}
    #introText {{ color: #374151; }}
    QListWidget {{ background: {CARD}; border: 1px solid {BORDER}; border-radius: 6px; }}
    QListWidget::item {{ padding: 6px 8px; }}
    QListWidget::item:selected {{ background: #eaf0ff; color: {ACCENT}; }}
    QScrollArea {{ border: none; }}
    QScrollBar:vertical {{
        background: transparent; width: 10px; margin: 2px;
    }}
    QScrollBar::handle:vertical {{ background: #c8ccd2; border-radius: 5px; min-height: 30px; }}
    QScrollBar::handle:vertical:hover {{ background: #a9aeb5; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QStatusBar {{ background: {CARD}; border-top: 1px solid {BORDER}; color: {MUTED}; }}
    QComboBox {{
        background: {CARD}; border: 1px solid {BORDER}; border-radius: 6px;
        padding: 4px 10px; min-height: 20px;
    }}
    QComboBox::drop-down {{ border: none; width: 18px; }}
    QSpinBox, QDoubleSpinBox, QLineEdit#plain {{
        background: {CARD}; border: 1px solid {BORDER}; border-radius: 6px; padding: 4px 8px;
    }}
    QGroupBox {{
        border: 1px solid {BORDER}; border-radius: 8px; margin-top: 12px;
        padding: 10px 10px 6px 10px; background: {CARD};
    }}
    QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 4px; color: {MUTED}; }}
    QTabWidget::pane {{ border: 1px solid {BORDER}; border-radius: 6px; background: {CARD}; }}
    QTabBar::tab {{
        background: transparent; padding: 7px 16px; color: {MUTED};
        border-bottom: 2px solid transparent;
    }}
    QTabBar::tab:selected {{ color: {ACCENT}; border-bottom: 2px solid {ACCENT}; }}
    QToolButton {{ background: transparent; border: none; padding: 4px 8px; border-radius: 5px; }}
    QToolButton:hover {{ background: #eef1f5; }}
    QToolButton:checked {{ background: #eaf0ff; color: {ACCENT}; }}
    #readerToolBar {{ background: {CARD}; border-bottom: 1px solid {BORDER}; }}
    #readerTitle {{ font-size: 14px; font-weight: 600; }}
    #banner {{ background: #fff7e6; border: 1px solid #f5d9a8; color: {WARN};
               border-radius: 6px; padding: 8px 12px; }}
    #bannerError {{ background: #fdecea; border: 1px solid #f5c6c0; color: {ERROR};
                    border-radius: 6px; padding: 8px 12px; }}
    #emptyTitle {{ font-size: 16px; font-weight: 600; color: #4b5563; }}
    #emptyHint {{ color: {MUTED}; }}
    """

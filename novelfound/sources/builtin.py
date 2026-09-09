# -*- coding: utf-8 -*-
"""内置书源规则表。

每条规则对应一个站点模板。绝大多数"笔趣阁"系站点共用同一套模板
（``#list dl dd a`` 目录、``#content`` 正文、``table.grid`` 搜索结果），
因此把模板抽成规则后，新增同类站点只需复制一条配置并改 ``base_url``。

字段说明（除 key / name / base_url 外均可省略）：
    key                书源唯一标识
    name               界面显示名
    base_url           站点根地址
    search_url         搜索地址模板，``{q}`` 会被替换成关键词
    search_method      GET / POST（默认 GET）
    search_data        POST 时的表单字段
    encoding           站点强制编码（如 "gb18030"），留空表示自动探测
    search_encoding    关键词 URL 编码（老站用 gbk）
    search_items       搜索结果条目选择器（按顺序尝试）
    book_title/link/author/cover/latest/intro/status/category
                       条目内各字段选择器
    detail_title/author/intro/cover/status/category/latest
                       详情页字段选择器
    catalog_groups     目录容器选择器
    catalog_items      目录项选择器（相对容器）
    content            正文容器选择器
    chapter_title      章节标题选择器
    prev_selectors / next_selectors
                       上一章 / 下一章链接选择器（图标按钮无文字时必需）
    nav                上一章/下一章所在容器选择器（按文字兜底）
    note               书源管理里的备注
"""
from __future__ import annotations

from typing import Dict, List

# ---------------------------------------------------------------------------
# 模板一：经典笔趣阁（table.grid 搜索 + #list dl dd a 目录 + #content 正文）
# ---------------------------------------------------------------------------
CLASSIC_BIQUGE: Dict = {
    "search_url": "/modules/article/search.php?searchkey={q}",
    "search_items": ("table.grid tr", "div#main table tr", "div#nr table tr"),
    "book_title": ("td:nth-of-type(1) a",),
    "book_link": ("td:nth-of-type(1) a",),
    "book_latest": ("td:nth-of-type(2) a",),
    "book_author": ("td:nth-of-type(3)",),
    "book_status": ("td:nth-of-type(6)",),
    "detail_title": ("#info h1",),
    "detail_author": ("#info p:nth-of-type(1)",),
    "detail_intro": ("#intro",),
    "detail_cover": ("#fmimg img",),
    "detail_latest": ("#info p:nth-of-type(3)",),
    "label_fields": {
        "author": ("作者",),
        "latest": ("最新章节",),
    },
    "catalog_groups": ("#list dl", "#list"),
    "catalog_items": ("dd a",),
    "content": ("#content",),
    "chapter_title": ("div.bookname h1",),
    "nav": (".bottem1", ".bottem2"),
}

# ---------------------------------------------------------------------------
# 模板二：和图书（结构清晰、无广告）
# ---------------------------------------------------------------------------
HETUSHU: Dict = {
    "search_url": "/search/?keyword={q}",
    "search_items": ("dl#body dd", "dl.list dd"),
    "book_title": ("h4 a",),
    "book_link": ("h4 a",),
    "book_author": ("h4 span",),
    "book_cover": ("img",),
    "book_intro": ("div.intro",),
    "detail_title": (".book_info h2",),
    "detail_author": (".book_info div",),
    "detail_intro": (".book_info .intro", "div.intro"),
    "detail_cover": (".book_info img",),
    # "类型：玄幻小说 / 字数：288.72万字" 这类字段用标签精确取值
    "label_fields": {
        "category": ("类型", "类别"),
        "word_count": ("字数",),
        "status": ("状态", "连载状态"),
    },
    "catalog_groups": ("dl#dir",),
    "catalog_items": ("dd a",),
    "content": ("#content",),
    "chapter_title": ("#ctitle .title", "#ctitle"),
    "prev_selectors": ("a#pre", "a.pre", "a.prev", "a#prev"),
    "next_selectors": ("a#next", "a.next", "a#nextPage"),
}

BUILTIN_RULES: List[Dict] = [
    {
        **HETUSHU,
        "key": "hetushu",
        "name": "和图书",
        "base_url": "https://www.hetushu.com",
        "enabled_by_default": True,
        "note": "结构稳定、正文干净，推荐首选书源。",
    },
    {
        **CLASSIC_BIQUGE,
        "key": "mayiwsk",
        "name": "蚂蚁文学",
        "base_url": "https://www.mayiwsk.com",
        "enabled_by_default": True,
        "note": "经典笔趣阁模板，可访问性较好。",
    },
    {
        **CLASSIC_BIQUGE,
        "key": "biquge5200",
        "name": "笔趣阁5200",
        "base_url": "https://www.biquge5200.cc",
        "encoding": "gb18030",
        "enabled_by_default": True,
        "note": "老牌站点，页面为 GBK 编码（搜索关键词仍用 UTF-8 编码）。",
    },
    {
        "key": "biquge365",
        "name": "笔趣阁365",
        "base_url": "https://www.biquge365.net",
        "search_url": "/s.php",
        "search_method": "POST",
        "search_data": {"type": "articlename", "s": "{q}"},
        "search_items": ("div.find dl", "div.jie dd", "dl dd", "div.bookbox", "li.item"),
        "book_title": ("h4 a", "h3 a", "a"),
        "book_link": ("h4 a", "h3 a", "a"),
        "book_author": ("span", ".author"),
        "catalog_groups": ("div#list dl", "#list dl", "div.listmain"),
        "catalog_items": ("dd a",),
        "content": ("#content", "#chaptercontent"),
        "chapter_title": ("div.bookname h1", "h1"),
        "enabled_by_default": False,
        "note": "搜索有 15 秒频率限制，默认关闭；需要时再启用。",
    },
]

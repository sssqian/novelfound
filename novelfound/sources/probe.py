# -*- coding: utf-8 -*-
"""书源自动探测（方案 B）。

给一个站点域名，依次闯四关：

1. **连通性**：首页能否打开、是否被反爬拦截；
2. **搜索入口**：把常见的搜索 URL 写法逐个试一遍，拿到搜索结果页；
3. **目录解析**：用几套已知模板的选择器解析详情页目录；
4. **正文提取**：打开第一章，确认能取到足够长且不是导航页的正文。

三关全过就把「模板选择器 + 命中的搜索地址」拼成一条标准规则返回，界面预览后即可保存。
实现上直接复用 :class:`RuleSource` 与现有清洗逻辑，所以**探测出来的规则与内置书源
行为完全一致**，不存在"探得通、存下来却用不了"的情况。

请求量控制：每个搜索写法只抓一次页面，然后拿这份 HTML 去试所有模板（本地比对，
不重复请求）；单个域名最多约 1 + 8 + 1 + 1 次请求。
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.parse import urlparse

from ..net import HttpSession, NovelError, ParseError
from .rule_source import RuleSource

# 探测通过的最低门槛
MIN_SEARCH_RESULTS = 1        # 搜索结果至少 1 条
MIN_CHAPTERS = 3              # 目录至少 3 章
MIN_CONTENT_CHARS = 300       # 正文至少 300 字
GOOD_SEARCH_RESULTS = 3       # 找到 3 条以上就认为搜索入口很合适，不再试更多写法

# ---------------------------------------------------------------------------
# 搜索地址写法（{q} 会被替换为关键词）
# ---------------------------------------------------------------------------
SEARCH_PATTERNS: List[Dict[str, Any]] = [
    {"url": "/modules/article/search.php?searchkey={q}", "method": "GET"},
    {"url": "/search.php?keyword={q}", "method": "GET"},
    {"url": "/s?q={q}", "method": "GET"},
    {"url": "/search/?keyword={q}", "method": "GET"},
    {"url": "/search.php?searchkey={q}", "method": "GET"},
    {"url": "/so/{q}.html", "method": "GET"},
    {"url": "/search?q={q}", "method": "GET"},
    {"url": "/s.php", "method": "POST",
     "data": {"type": "articlename", "s": "{q}"}},
    {"url": "/modules/article/search.php", "method": "POST",
     "data": {"searchkey": "{q}"}},
]

# 站点首页里 <form> 的解析（优先用站点自己的搜索表单，命中率高得多）
FORM_RE = re.compile(r"<form([^>]*)>(.*?)</form>", re.S | re.I)
INPUT_RE = re.compile(r"<input([^>]*)>", re.I)
ATTR_RE = re.compile(r'([\w-]+)\s*=\s*["\']?([^"\'\s>]+)', re.I)
TEXT_INPUT_TYPES = ("text", "search", "searchbox", "q", "name", "key")

# ---------------------------------------------------------------------------
# 解析模板：只放选择器，不含搜索地址
# ---------------------------------------------------------------------------
TEMPLATES: List[Dict[str, Any]] = [
    {
        "key": "classic",
        "name": "经典笔趣阁模板",
        "search_items": ["table.grid tr", "div#main table tr", "div#nr table tr"],
        "book_title": ["td:nth-of-type(1) a"],
        "book_link": ["td:nth-of-type(1) a"],
        "book_latest": ["td:nth-of-type(2) a"],
        "book_author": ["td:nth-of-type(3)"],
        "book_status": ["td:nth-of-type(6)"],
        "detail_title": ["#info h1"],
        "detail_author": ["#info p:nth-of-type(1)"],
        "detail_intro": ["#intro"],
        "detail_cover": ["#fmimg img"],
        "detail_latest": ["#info p:nth-of-type(3)"],
        "label_fields": {"author": ["作者"], "latest": ["最新章节"]},
        "catalog_groups": ["#list dl", "#list"],
        "catalog_items": ["dd a"],
        "content": ["#content"],
        "chapter_title": ["div.bookname h1"],
        "nav": [".bottem1", ".bottem2"],
    },
    {
        "key": "hetushu",
        "name": "和图书式模板",
        "search_items": ["dl#body dd", "dl.list dd", "div.search-list dd"],
        "book_title": ["h4 a", "h3 a"],
        "book_link": ["h4 a", "h3 a"],
        "book_author": ["h4 span"],
        "book_cover": ["img"],
        "book_intro": ["div.intro"],
        "detail_title": [".book_info h2", "h1"],
        "detail_author": [".book_info div"],
        "detail_intro": [".book_info .intro", "div.intro"],
        "detail_cover": [".book_info img"],
        "label_fields": {"category": ["类型"], "word_count": ["字数"]},
        "catalog_groups": ["dl#dir", "dl.dir", "div#dir"],
        "catalog_items": ["dd a"],
        "content": ["#content"],
        "chapter_title": ["#ctitle .title", "#ctitle"],
        "prev_selectors": ["a#pre", "a.pre"],
        "next_selectors": ["a#next", "a.next"],
    },
    {
        "key": "new_biquge",
        "name": "新笔趣阁（列表型）模板",
        "search_items": ["div.result-item", "div.bookbox", "div.book-item",
                         "ul.list li", "div.item"],
        "book_title": [".bookname a", "h4 a", "h3 a", "a"],
        "book_link": [".bookname a", "h4 a", "h3 a", "a"],
        "book_author": [".author", "span.author", "p.author"],
        "book_cover": ["img"],
        "book_latest": [".uptime", ".update"],
        "book_intro": [".intro", ".desc", "dd"],
        "detail_title": ["#info h1", "div.book_info h1", "h1"],
        "detail_author": ["#info p", "div.book_info p", ".author"],
        "detail_intro": ["#intro", "div.intro", ".intro", "div.desc"],
        "detail_cover": ["#fmimg img", "div.book_info img", ".book-cover img"],
        "catalog_groups": ["div.listmain", "ul.list-chapter", "div#chapterlist",
                           "div#list", "#list"],
        "catalog_items": ["dd a", "li a"],
        "content": ["#chaptercontent", "#content", "div.showtxt", "div.read-content",
                    "div.content"],
        "chapter_title": ["h1", ".bookname h1", "div.chapter-title"],
        "prev_selectors": ["a#prev", "a.prev", "a#pb_prev"],
        "next_selectors": ["a#next", "a.next", "a#pb_next"],
    },
    {
        "key": "generic",
        "name": "通用兜底模板",
        # 不给条目选择器，交给 RuleSource 的启发式（按链接形态推断）
        "search_items": [],
        "content": ["#content", "#chaptercontent", "#booktext", "#nr1", "div.content"],
        "chapter_title": ["h1", ".bookname h1"],
    },
]


# ---------------------------------------------------------------------------
# 结果
# ---------------------------------------------------------------------------
@dataclass
class ProbeOutcome:
    """一次探测的完整结果。"""

    base_url: str = ""
    keyword: str = ""
    ok: bool = False
    stage: str = "未开始"
    error: str = ""
    template: str = ""
    template_key: str = ""
    search_url: str = ""
    search_method: str = "GET"
    search_data: Dict[str, str] = field(default_factory=dict)
    books_found: int = 0
    chapter_count: int = 0
    char_count: int = 0
    elapsed: float = 0.0
    preview: Dict[str, Any] = field(default_factory=dict)
    rule: Dict[str, Any] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)
    # 搜索结果页里出现过的书名（方案 C 用来验证"这个站真的有这本书"）
    search_titles: List[str] = field(default_factory=list)
    # 只探到一半时的规则（供用户手动补齐选择器）
    partial_rule: Optional[Dict[str, Any]] = None

    def note(self, text: str) -> None:
        self.notes.append(text)


# ---------------------------------------------------------------------------
# 预取缓存：把已抓到的页面喂给 RuleSource，避免重复请求
# ---------------------------------------------------------------------------
class _CachedHttp:
    """优先返回预取页面，其余请求转发给真实会话。"""

    def __init__(self, real: HttpSession, pages: Dict[str, str]):
        self.real = real
        self.pages = pages

    def get_text(self, url: str, referer: str = "", encoding: str = "",
                 timeout: Optional[float] = None) -> str:
        for key, html in self.pages.items():
            if url.startswith(key):
                return html
        return self.real.get_text(url, referer=referer, encoding=encoding, timeout=timeout)

    def get_bytes(self, url: str, referer: str = "",
                  timeout: Optional[float] = None) -> bytes:
        return self.real.get_bytes(url, referer=referer, timeout=timeout)

    def request(self, *args, **kwargs):
        return self.real.request(*args, **kwargs)

    def close(self) -> None:  # pragma: no cover - 会话由外部管理
        pass


def probe_domain(http: HttpSession, base_url: str, keyword: str,
                 deep: bool = False,
                 on_note=None) -> ProbeOutcome:
    """探测一个站点，返回 :class:`ProbeOutcome`。

    ``on_note`` 是可选的进度回调（界面用来实时显示每一关的结果）。
    """
    started = time.time()
    outcome = ProbeOutcome(base_url=base_url.strip(), keyword=keyword.strip())

    def note(text: str) -> None:
        outcome.note(text)
        if on_note is not None:
            on_note(text)

    base = _normalize_base(outcome.base_url)
    if not base:
        outcome.error = "站点地址不合法，请填写形如 https://www.example.com 的地址"
        return outcome
    outcome.base_url = base
    host = urlparse(base).netloc
    slug = _slug(host)

    # ------------------------------------------------------------ 第 1 关：连通性
    outcome.stage = "连通性"
    try:
        home = http.get_text(base, timeout=15)
    except NovelError as exc:
        outcome.error = f"无法访问该站点：{exc.message}"
        note(f"① 连通性  ✗ {exc.message}")
        return outcome
    if len(home) < 200:
        outcome.error = "站点返回内容过短，可能被拦截或域名已失效"
        note("① 连通性  ✗ 首页内容过短")
        return outcome
    note(f"① 连通性  ✓ 首页可访问（{len(home)} 字节）")

    # ------------------------------------------------------------ 第 2 关：搜索入口
    outcome.stage = "搜索入口"
    form_patterns = _form_search_patterns(home)
    if form_patterns:
        note(f"② 搜索入口  从首页表单识别到 {len(form_patterns)} 种写法，优先尝试")
    patterns = _merge_patterns(form_patterns,
                              SEARCH_PATTERNS if deep else SEARCH_PATTERNS[:6],
                              limit=14 if deep else 8)
    templates = TEMPLATES if deep else TEMPLATES[:3]
    best: Optional[Tuple[int, int, Dict[str, Any], Dict[str, Any], List[Any], str]] = None
    tried = 0
    for pattern in patterns:
        url = _build_search_url(base, pattern, keyword)
        try:
            html = _fetch_search(http, url, pattern)
        except NovelError as exc:
            note(f"② 搜索入口  试 {pattern['url']} → {exc.message}")
            continue
        tried += 1
        for template in templates:
            rule = _build_rule(slug, template, base, pattern, outcome)
            source = RuleSource(_CachedHttp(http, {url: html}), rule)
            try:
                books = source.search(keyword, limit=10)
            except (ParseError, NovelError):
                books = []
            except Exception:  # noqa: BLE001 - 探测阶段任何异常都视为该模板不匹配
                books = []
            if len(books) < MIN_SEARCH_RESULTS:
                continue
            # 关键：搜索结果必须与关键词相关，否则这个"搜索接口"其实是摆设
            hits = matched_titles(keyword, [b.title for b in books])
            if not hits:
                note(f"② 搜索入口  {pattern['url']} × {template['name']} → "
                     f"返回了 {len(books)} 条但与「{keyword}」无关，跳过")
                continue
            note(f"② 搜索入口  ✓ {pattern['url']} × {template['name']} "
                 f"→ 命中 {len(books)} 条（匹配「{hits[0]}」）")
            score = (1, len(hits), len(books))
            if best is None or score > (best[0], best[1], best[2]):
                best = (1, len(hits), template, pattern, books, url, hits[0])
            break
        if best is not None and best[1] >= GOOD_SEARCH_RESULTS:
            break
    if best is None:
        if tried == 0:
            outcome.error = "所有常见搜索入口都打不开，可能是 JS 渲染站点或需要人机验证"
        else:
            outcome.error = ("能打开搜索页，但搜索结果与关键词无关（搜索接口可能已失效、"
                             "需要登录，或模板未匹配）")
        note("② 搜索入口  ✗ 未找到可用入口")
        return outcome

    _strong, _hits, template, pattern, books, url, matched_title = best
    outcome.books_found = len(books)
    outcome.search_titles = [b.title for b in books[:10]]
    outcome.template = template["name"]
    outcome.template_key = template["key"]
    outcome.search_url = pattern["url"]
    outcome.search_method = pattern.get("method", "GET")
    outcome.search_data = dict(pattern.get("data") or {})
    rule = _build_rule(slug, template, base, pattern, outcome)

    # ------------------------------------------------------------ 第 3 关：目录
    outcome.stage = "目录解析"
    source = RuleSource(http, rule)
    # 优先挑与关键词匹配的那本，避免抓到列表里的无关书籍
    book = next((b for b in books
                 if title_matches(keyword, [b.title])), books[0])
    try:
        detail = source.fetch_detail(book)
    except NovelError as exc:
        outcome.error = f"目录解析失败：{exc.message}"
        outcome.partial_rule = rule
        note(f"③ 目录解析  ✗ {exc.message}")
        return outcome
    if len(detail.chapters) < MIN_CHAPTERS:
        outcome.error = f"目录只有 {len(detail.chapters)} 章，判定为不可用"
        outcome.partial_rule = rule
        note(f"③ 目录解析  ✗ 章节数过少（{len(detail.chapters)}）")
        return outcome
    outcome.chapter_count = len(detail.chapters)
    note(f"③ 目录解析  ✓ 解析出 {len(detail.chapters)} 章")

    # ------------------------------------------------------------ 第 4 关：正文
    outcome.stage = "正文提取"
    try:
        content = source.fetch_chapter(detail.book, detail.chapters[0])
    except NovelError as exc:
        outcome.error = f"正文提取失败：{exc.message}"
        outcome.partial_rule = rule
        note(f"④ 正文提取  ✗ {exc.message}")
        return outcome
    if content.char_count < MIN_CONTENT_CHARS:
        outcome.error = f"正文只有 {content.char_count} 字，可能抓错容器"
        outcome.partial_rule = rule
        note(f"④ 正文提取  ✗ 正文字数过少（{content.char_count}）")
        return outcome
    outcome.char_count = content.char_count
    note(f"④ 正文提取  ✓ {content.char_count} 字，未命中导航页")

    # ------------------------------------------------------------ 组装结果
    outcome.ok = True
    outcome.stage = "完成"
    outcome.elapsed = time.time() - started
    outcome.preview = {
        "title": detail.book.title,
        "author": detail.book.author,
        "cover_url": detail.book.cover_url,
        "intro": detail.book.intro,
        "category": detail.book.category,
        "chapters": [c.title for c in detail.chapters[:3]],
        "snippet": "\n".join(content.paragraphs[:3])[:220],
    }
    rule["name"] = f"{host}（自动探测）"
    rule["note"] = (f"由「探测新书源」于 {time.strftime('%Y-%m-%d %H:%M')} 生成，"
                    f"模板：{template['name']}")
    rule["import_format"] = "probe"
    rule["imported_from"] = base
    rule["enabled_by_default"] = True
    outcome.rule = rule
    return outcome


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------
def _normalize_base(raw: str) -> str:
    """规范化站点地址；不合法时返回空串。"""
    raw = (raw or "").strip().strip("'\" ")
    if not raw:
        return ""
    if not raw.startswith(("http://", "https://")):
        raw = "https://" + raw
    parsed = urlparse(raw)
    netloc = parsed.netloc
    # 主机名必须是"字母数字点横线"，且至少包含一个点（排除"这不是网址"这类输入）
    if not netloc or "." not in netloc.split(":")[0]:
        return ""
    if not re.match(r"^[A-Za-z0-9.-]+(:\d+)?$", netloc):
        return ""
    return f"{parsed.scheme}://{netloc}"


def _slug(host: str) -> str:
    host = (host or "").split(":")[0].lower()
    host = re.sub(r"[^a-z0-9]+", "_", host).strip("_") or "site"
    return f"probe_{host}"


def _form_search_patterns(home_html: str) -> List[Dict[str, Any]]:
    """从站点首页的 <form> 里推断搜索地址。

    很多站点的搜索表单没有 action（提交到当前页）或参数名很特别（key/wd/word），
    硬编码的写法覆盖不到；直接读它自己的表单最靠谱，而且不额外增加请求。
    """
    patterns: List[Dict[str, Any]] = []
    for attrs_text, body in FORM_RE.findall(home_html or ""):
        attrs = {k.lower(): v for k, v in ATTR_RE.findall(attrs_text)}
        method = (attrs.get("method") or "get").lower()
        action = (attrs.get("action") or "").strip()
        names: List[str] = []
        for input_attrs_text in INPUT_RE.findall(body):
            input_attrs = {k.lower(): v for k, v in ATTR_RE.findall(input_attrs_text)}
            input_type = (input_attrs.get("type") or "text").lower()
            if input_type not in TEXT_INPUT_TYPES and input_type not in ("text", "search"):
                continue
            name = input_attrs.get("name") or input_attrs.get("id")
            if name and name.lower() not in ("submit", "searchsubmit", "button"):
                names.append(name)
        if not names:
            continue
        if action.startswith("http"):
            target = action
        elif action:
            target = action if action.startswith("/") else "/" + action
        else:
            target = "/"
        for name in names[:2]:
            if method == "post":
                patterns.append({"url": target, "method": "POST",
                                 "data": {name: "{q}"}})
            else:
                sep = "&" if "?" in target else "?"
                patterns.append({"url": f"{target}{sep}{name}={{q}}",
                                 "method": "GET"})
    return patterns


def _merge_patterns(primary: Sequence[Dict[str, Any]],
                    secondary: Sequence[Dict[str, Any]],
                    limit: int) -> List[Dict[str, Any]]:
    """合并两组搜索写法并去重（按 URL + 方法）。"""
    merged: List[Dict[str, Any]] = []
    seen = set()
    for pattern in list(primary) + list(secondary):
        key = (str(pattern.get("url")), str(pattern.get("method", "GET")).upper())
        if key in seen:
            continue
        seen.add(key)
        merged.append(pattern)
        if len(merged) >= limit:
            break
    return merged


def normalize_title(text: str) -> str:
    """去掉书名号、标点与空白，便于比对书名。"""
    text = re.sub(r"[《》〈〉【】\[\]（）()\s·,，.。、\-—_!！?？:：;；'\"“”‘’]",
                  "", text or "")
    return text.strip().lower()


def title_matches(query: str, titles: Sequence[str]) -> bool:
    """判断一组书名里是否包含目标书（宽松的双向包含匹配）。"""
    target = normalize_title(query)
    if not target:
        return False
    for title in titles:
        found = normalize_title(title)
        if found and (target in found or found in target):
            return True
    return False


def matched_titles(query: str, titles: Sequence[str]) -> List[str]:
    """返回与目标书匹配的书名。"""
    target = normalize_title(query)
    if not target:
        return []
    return [t for t in titles if (normalize_title(t) in target or target in normalize_title(t))]


def _build_search_url(base: str, pattern: Dict[str, Any], keyword: str) -> str:
    """把搜索地址模板 + 关键词拼成完整 URL（关键词做 URL 编码）。"""
    from urllib.parse import quote
    url = str(pattern["url"])
    if url.startswith("http"):
        return url.replace("{q}", quote(keyword))
    return base + ("" if url.startswith("/") else "/") + url.replace("{q}", quote(keyword))


def _fetch_search(http: HttpSession, url: str, pattern: Dict[str, Any]) -> str:
    """按 GET / POST 抓取搜索页。"""
    if pattern.get("method", "GET") == "POST":
        data = dict(pattern.get("data") or {})
        return http.request(url, method="POST", data=data, timeout=20).text
    return http.get_text(url, timeout=20)


def _build_rule(slug: str, template: Dict[str, Any], base: str,
                pattern: Dict[str, Any], outcome: ProbeOutcome) -> Dict[str, Any]:
    rule: Dict[str, Any] = {
        "key": slug,
        "name": template["name"],
        "base_url": base,
        "search_url": pattern["url"],
        "search_method": pattern.get("method", "GET"),
        "search_data": dict(pattern.get("data") or {}),
        "enabled_by_default": True,
        "note": "自动探测生成",
    }
    for field_name, value in template.items():
        if field_name in ("key", "name"):
            continue
        rule[field_name] = value
    return rule

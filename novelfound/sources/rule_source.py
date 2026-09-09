# -*- coding: utf-8 -*-
"""规则驱动的通用书源实现。

绝大多数"笔趣阁"系站点共用几套模板，因此这里把"怎么抓"抽象成一份规则
（选择器列表 + URL 模板），把"抓什么站点"写进 ``builtin.py`` 的规则表。
这样新增书源通常只需要增加一条配置，无需改动代码。

同时，每条解析路径都带启发式兜底（fallback），站点小改版时仍能工作；
彻底失效时抛出带中文提示的 :class:`ParseError`，由界面友好呈现。
"""
from __future__ import annotations

import re
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from bs4 import Tag

from ..cleaner import (clean_inline, clean_title, extract_paragraphs, looks_like_nav,
                       make_soup, normalize_url_key, pick_content_node, pick_intro,
                       strip_noise, strip_title_echo)
from ..models import (Book, BookDetail, Chapter, ChapterContent, absolutize,
                      normalize_cover)
from ..net import ParseError
from .base import BaseSource

# 详情页链接的通用特征（用于启发式兜底）
DETAIL_URL_RE = re.compile(r"^/(book/\d+/?|\d+_\d+/?|\d+/\d+/?|novel/\d+/?|read/\d+/?)$")

# 目录分组中"最新章节"预览块的标题特征（需要跳过，避免目录顺序错乱）
LATEST_GROUP_RE = re.compile(r"最新章节|最新更新|最近更新|最新章")
# 真正的正文分组标题特征
BODY_GROUP_RE = re.compile(r"正文|目录|全部章节|章节列表|所有章节|正文卷|章节目录")
# 卷标题（如 "第一卷 xxx"），不是章节，需要过滤
VOLUME_RE = re.compile(r"^\s*(第[一二三四五六七八九十百千万零〇\d]+\s*[卷部篇]|作品相关|楔子|前言|公告)\s*$")
# 导航链接文字
PREV_RE = re.compile(r"上一[章页节]|上章|上一篇|前一[章页]")
NEXT_RE = re.compile(r"下一[章页节]|下章|下一篇|后一[章页]|下一页")
# 正文最少字数：低于这个数基本可以断定没抓到正文（正常章节不会这么短）
MIN_CHAPTER_CHARS = 20

DEFAULT_CONTENT_SELECTORS = (
    "#content", "#chaptercontent", "#chapter_content", "#booktext", "#htmlContent",
    "#nr1", "#nr", "#txt", "div.read-content", "div.showtxt", "div.content_read",
    "div.content", "div.text", "div#htmlContent", "article.content", ".articlecontent",
    "div.read-content.j_readContent", "div.entry-content", "div#booktxt",
)
DEFAULT_CHAPTER_TITLE_SELECTORS = (
    "div.bookname h1", ".bookname h1", "h1.bookname", "#chaptercontent h1",
    "h1.wap_none", "div.chapter-title", "h1", ".j_chapterName", "div.title h1",
)
DEFAULT_CATALOG_GROUPS = (
    "#list dl", "#list", "div.listmain", "#chapterlist", "ul.list-chapter",
    "div#chapterlist", "div.book_list", "div.chapter-list", "ul#chapter-list",
    "div.mulu", "div#mulu", "div.list",
)
DEFAULT_CATALOG_ITEMS = ("dd a", "li a", "a")
DEFAULT_SEARCH_ITEMS = (
    "div.result-item", "div.bookbox", "div.result-list div.item", "div.book-item",
    "div.item", "div.newbox", "li.item", "table.grid tr", "ul.list li",
    "div#booklist div", "div.so_list div.hots div.bookbox",
)


class RuleSource(BaseSource):
    """由规则表驱动的书源。"""

    def __init__(self, http, rule: Dict, options: Optional[Dict] = None):
        super().__init__(http)
        self.rule = rule
        self.options = options or {}
        self.key = rule["key"]
        self.name = rule["name"]
        self.base_url = rule["base_url"].rstrip("/")
        self.enabled_by_default = rule.get("enabled_by_default", True)
        self.note = rule.get("note", "")
        self.encoding = rule.get("encoding", "")
        # 搜索关键词默认用 UTF-8 编码；部分老站需要 GBK 时才显式配置 search_encoding
        self.search_encoding = rule.get("search_encoding", "")

    @property
    def strict_filter(self) -> bool:
        """是否启用严格广告过滤（来自设置界面）。"""
        return bool(self.options.get("strict_ad_filter", False))

    # ------------------------------------------------------------------ 工具方法
    def _select(self, root, selectors: Sequence[str]) -> List[Tag]:
        """按顺序尝试选择器，返回第一个有结果的列表。"""
        for selector in selectors:
            try:
                nodes = root.select(selector)
            except Exception:
                continue
            nodes = [n for n in nodes if isinstance(n, Tag)]
            if nodes:
                return nodes
        return []

    def _first_text(self, root, selectors: Sequence[str]) -> str:
        for selector in selectors:
            try:
                node = root.select_one(selector)
            except Exception:
                continue
            if node is not None:
                text = clean_inline(node.get_text(" ", strip=True))
                if text:
                    return text
        return ""

    def _first_attr(self, root, selectors: Sequence[str], attrs: Sequence[str]) -> str:
        for selector in selectors:
            try:
                node = root.select_one(selector)
            except Exception:
                continue
            if node is None:
                continue
            for attr in attrs:
                value = node.get(attr)
                if value:
                    if attr in ("src", "data-src") and isinstance(value, list):
                        value = value[0]
                    return str(value).strip()
        return ""

    def _soup(self, url: str, referer: str = "") -> Tuple[object, str]:
        html = self.http.get_text(url, referer=referer or self.base_url + "/",
                                  encoding=self.encoding)
        soup = strip_noise(make_soup(html))
        return soup, html

    # --------------------------------------------------------------------- 搜索
    def search(self, keyword: str, limit: int = 30) -> List[Book]:
        rule = self.rule
        keyword = (keyword or "").strip()
        if not keyword:
            return []
        url_tpl = rule.get("search_url", "")
        if not url_tpl:
            raise ParseError("该书源未配置搜索地址", self.key)
        quoted = _quote(keyword, self.search_encoding)
        url = url_tpl.replace("{q}", quoted)
        if not url.startswith("http"):
            url = self.base_url + ("" if url.startswith("/") else "/") + url

        method = (rule.get("search_method") or "GET").upper()
        if method == "POST":
            data = {k: v.replace("{q}", keyword) for k, v in (rule.get("search_data") or {}).items()}
            html = self.http.request(url, referer=self.base_url + "/", method="POST",
                                     data=data).text
            if self.encoding:
                html = self.http.get_text(url, referer=self.base_url + "/",
                                          encoding=self.encoding)
        else:
            html = self.http.get_text(url, referer=self.base_url + "/",
                                      encoding=self.encoding)
        soup = strip_noise(make_soup(html))

        selectors = tuple(rule.get("search_items") or ()) + DEFAULT_SEARCH_ITEMS
        items = self._select(soup, selectors)
        books: List[Book] = []
        for item in items:
            book = self._parse_search_item(item, url)
            if book and book.url:
                books.append(book)
            if len(books) >= limit:
                break
        if not books:
            books = self._heuristic_search(soup, url, limit)
        if not books:
            if keyword not in html and len(html) < 3000:
                raise ParseError("站点返回内容异常，可能被反爬拦截或需要人机验证",
                                 f"{self.name} {url}")
            return []
        return _dedupe_books(books)[:limit]

    def _parse_search_item(self, item: Tag, page_url: str) -> Optional[Book]:
        rule = self.rule
        link_node = None
        for selector in tuple(rule.get("book_link") or ()) + ("h4 a", "h3 a", "a"):
            try:
                node = item.select_one(selector)
            except Exception:
                continue
            if node is not None and node.get("href"):
                link_node = node
                break
        if link_node is None:
            return None
        href = str(link_node.get("href") or "").strip()
        if not href or href.startswith("javascript:"):
            return None

        title = ""
        for selector in tuple(rule.get("book_title") or ()) + ("h4 a", "h3 a", "h2 a", "a"):
            try:
                node = item.select_one(selector)
            except Exception:
                continue
            if node is not None:
                text = clean_inline(node.get_text(" ", strip=True))
                if text:
                    title = text
                    break
        if not title:
            title = clean_inline(link_node.get_text(" ", strip=True))

        author = self._first_text(item, tuple(rule.get("book_author") or ()) +
                                  (".author", "p.author", "span.author", "td:nth-of-type(3)"))
        author = _clean_author(author)
        cover = self._first_attr(item, tuple(rule.get("book_cover") or ()) +
                                 ("img",), ("src", "data-src", "data-original", "data-lazy-src"))
        latest = self._first_text(item, tuple(rule.get("book_latest") or ()) +
                                  (".uptime", ".update", "p.update", "td:nth-of-type(2)"))
        intro = self._first_text(item, tuple(rule.get("book_intro") or ()) +
                                 (".intro", "p.intro", ".desc", "dd"))
        status = self._first_text(item, tuple(rule.get("book_status") or ()) + (".status",))
        category = self._first_text(item, tuple(rule.get("book_category") or ()) +
                                    (".cat", ".category", "td:nth-of-type(3)",))
        return Book(
            title=title or "未知书名",
            author=author,
            url=absolutize(href, page_url),
            cover_url=normalize_cover(cover, self.base_url),
            intro=intro,
            source=self.key,
            source_name=self.name,
            latest_chapter=latest,
            status=status,
            category=category,
        )

    def _heuristic_search(self, soup, page_url: str, limit: int) -> List[Book]:
        """选择器全部失效时的兜底：按链接形态推断搜索结果。"""
        books: List[Book] = []
        seen = set()
        for node in soup.find_all("a", href=True):
            href = str(node["href"]).strip()
            if not DETAIL_URL_RE.match(href.split("?")[0]):
                continue
            url = absolutize(href, page_url)
            key = normalize_url_key(url)
            if key in seen:
                continue
            title = clean_inline(node.get_text(" ", strip=True))
            if not title or len(title) < 2:
                continue
            seen.add(key)
            books.append(Book(title=title, url=url, source=self.key,
                              source_name=self.name))
            if len(books) >= limit:
                break
        return books

    # --------------------------------------------------------------------- 详情
    def fetch_detail(self, book: Book) -> BookDetail:
        rule = self.rule
        soup, _html = self._soup(book.url, referer=self.base_url + "/")

        title = self._first_text(soup, tuple(rule.get("detail_title") or ()) +
                                 ("#info h1", "h1", "div.book_info h1", ".book-info h1",
                                  "#bookinfo h1"))
        author = self._first_text(soup, tuple(rule.get("detail_author") or ()) +
                                  ("#info p", "div.book_info p", ".book-info p", ".author"))
        author = _clean_author(author) or _extract_author(author)
        intro = pick_intro(soup, tuple(rule.get("detail_intro") or ()) +
                           ("#intro", "div.intro", "#bookIntro", "div.book-intro",
                            "p.desc", "div.desc", "div#book_summary", ".book-info-desc",
                            ".intro", "#bookintro"))
        if not intro:
            meta = soup.select_one("meta[name=description]")
            if meta and meta.get("content"):
                intro = clean_inline(meta["content"])
        cover = self._first_attr(soup, tuple(rule.get("detail_cover") or ()) +
                                 ("#fmimg img", "#sidebar img", "div.book_info img",
                                  ".book-cover img", "img.cover"), ("src", "data-src"))
        status = self._first_text(soup, tuple(rule.get("detail_status") or ()) +
                                  (".status",))
        category = self._first_text(soup, tuple(rule.get("detail_category") or ()) +
                                    (".category", ".cat",))
        latest = self._first_text(soup, tuple(rule.get("detail_latest") or ()) +
                                  ("#info p", ".last", ".latest",))
        word_count = ""

        # "类型：玄幻小说" 这类带标签的字段，用标签定位，避免抓到兄弟节点的文本
        for field, labels in (rule.get("label_fields") or {}).items():
            value = self._labeled_value(soup, tuple(labels))
            if not value:
                continue
            if field == "category":
                category = value
            elif field == "status":
                status = value
            elif field == "word_count":
                word_count = value
            elif field == "author" and not author:
                author = _clean_author(value)
            elif field == "latest" and not latest:
                latest = value

        detail = Book(
            title=title or book.title,
            author=author or book.author,
            url=book.url,
            cover_url=normalize_cover(cover, self.base_url) or book.cover_url,
            intro=intro or book.intro,
            source=self.key,
            source_name=self.name,
            status=status or book.status,
            category=category or book.category,
            latest_chapter=latest or book.latest_chapter,
            word_count=word_count or book.word_count,
        )
        merged = book.merge(detail)
        chapters = self._parse_catalog(soup, book)
        if not chapters:
            raise ParseError("目录解析失败，站点结构可能已变化", f"{self.name} {book.url}")
        return BookDetail(book=merged, chapters=chapters)

    def _labeled_value(self, soup, labels: Sequence[str]) -> str:
        """从 "类型：玄幻小说" 这类文本里取出冒号后的值。

        标签内部可能被站点插入空格（如"作　　者："），因此按字符间允许空白来匹配。
        """
        if not labels:
            return ""
        patterns = [r"\s*".join(re.escape(ch) for ch in label) for label in labels]
        combined = re.compile(rf"^({'|'.join(patterns)})\s*[:：]\s*(.+)$")
        for node in soup.find_all(["div", "p", "span", "li", "td", "dd"]):
            text = clean_inline(node.get_text(" ", strip=True))
            if not text or len(text) > 40:
                continue
            match = combined.match(text)
            if match:
                return match.group(2).strip()
        return ""

    def _parse_catalog(self, soup, book: Book) -> List[Chapter]:
        """解析目录。

        经典模板的目录是一堆 ``<dl>``，每个 ``<dl>`` 用 ``<dt>`` 分段：
        "《书名》最新章节"（倒序预览，需要丢弃）与"《书名》正文"（真正的目录）。
        有些站点把两段塞进同一个 ``<dl>``，因此必须按 ``<dt>`` 分段处理，
        而不是整块跳过。
        """
        rule = self.rule
        groups = tuple(rule.get("catalog_groups") or ()) + DEFAULT_CATALOG_GROUPS
        item_selectors = tuple(rule.get("catalog_items") or ()) + DEFAULT_CATALOG_ITEMS
        skip_hint = re.compile(rule.get("catalog_skip_hint") or LATEST_GROUP_RE.pattern)

        containers: List[Tag] = []
        for selector in groups:
            try:
                nodes = [n for n in soup.select(selector) if isinstance(n, Tag)]
            except Exception:
                continue
            if nodes:
                containers = nodes
                break

        sections: List[Tuple[str, List[Tag]]] = []
        for container in containers:
            sections.extend(_split_sections(container))

        # 优先使用"正文"分组；否则排除"最新章节"分组
        body_sections = [s for s in sections
                         if s[0] and BODY_GROUP_RE.search(s[0]) and not skip_hint.search(s[0])]
        if body_sections:
            use_sections = body_sections
        else:
            use_sections = [s for s in sections if not (s[0] and skip_hint.search(s[0]))]
            if not use_sections:
                use_sections = sections

        pairs: List[Tuple[str, str]] = []
        for _head, items in use_sections:
            for item in items:
                link = None
                for selector in item_selectors:
                    try:
                        node = item.select_one(selector)
                    except Exception:
                        continue
                    if node is not None and node.get("href"):
                        link = node
                        break
                if link is None:
                    link = item if item.name == "a" and item.get("href") else item.find("a")
                if link is None or not link.get("href"):
                    continue
                href = str(link["href"]).strip()
                if not href or href.startswith(("javascript:", "#")):
                    continue
                text = clean_title(link.get_text(" ", strip=True) or link.get("title") or "")
                if not text or VOLUME_RE.match(text):
                    continue
                pairs.append((text, absolutize(href, book.url)))

        if not pairs:
            pairs = self._fallback_catalog(soup, book)
        if not pairs:
            return []

        # 去重：保留每个 URL 最后一次出现的位置（自动剔除开头的"最新章节"预览）
        kept: Dict[str, str] = {}
        for text, url in pairs:
            kept[normalize_url_key(url)] = text
        ordered = [(text, url) for url, text in kept.items()]
        ordered = _order_chapters(ordered)
        return [Chapter(title=text, url=url, index=i)
                for i, (text, url) in enumerate(ordered)]

    def _fallback_catalog(self, soup, book: Book) -> List[Tuple[str, str]]:
        """目录选择器全部失效时的兜底：扫描形如章节的链接。"""
        pairs: List[Tuple[str, str]] = []
        for node in soup.find_all("a", href=True):
            href = str(node["href"]).strip()
            if not re.search(r"/\d+\.html?$|/\d+_\d+/\d+|chapter|_\d+\.html?$", href):
                continue
            text = clean_title(node.get_text(" ", strip=True))
            if not text or len(text) > 60:
                continue
            pairs.append((text, absolutize(href, book.url)))
        return pairs

    # --------------------------------------------------------------------- 正文
    def fetch_chapter(self, book: Book, chapter: Chapter) -> ChapterContent:
        rule = self.rule
        soup, _html = self._soup(chapter.url, referer=book.url or self.base_url + "/")
        selectors = tuple(rule.get("content") or ()) + DEFAULT_CONTENT_SELECTORS
        node = pick_content_node(soup, selectors)
        if node is None:
            raise ParseError("未能定位正文内容，站点结构可能已变化",
                             f"{self.name} {chapter.url}")
        paragraphs = extract_paragraphs(node, strict=self.strict_filter)
        if not paragraphs or sum(len(p) for p in paragraphs) < MIN_CHAPTER_CHARS:
            raise ParseError(
                "未能提取到正文内容（可能被登录墙、验证页或广告页替换）",
                f"{self.name} {chapter.url}")
        if looks_like_nav(paragraphs):
            raise ParseError("抓到的内容疑似导航/推荐页而非正文，请切换书源或重试",
                             f"{self.name} {chapter.url}")

        title = self._first_text(soup, tuple(rule.get("chapter_title") or ()) +
                                 DEFAULT_CHAPTER_TITLE_SELECTORS)
        if not title:
            title = chapter.title
        title = clean_title(title)
        paragraphs = strip_title_echo(paragraphs, title)
        prev_url, next_url = self._parse_prev_next(soup, chapter.url, book.url)
        return ChapterContent(
            title=title,
            paragraphs=paragraphs,
            url=chapter.url,
            prev_url=prev_url,
            next_url=next_url,
        )

    def _parse_prev_next(self, soup, page_url: str, book_url: str) -> Tuple[str, str]:
        """解析上一章 / 下一章链接。

        优先级：规则里的专用选择器 -> 导航容器内按文字匹配 -> 全页文字匹配。
        很多站点的翻页按钮是纯图标（无文字），因此必须先按选择器找。
        """
        rule = self.rule
        prev_url = self._pick_link(soup, tuple(rule.get("prev_selectors") or ()) + (
            "a#pre", "a.pre", "a.prev", "a#prev", "a#pb_prev", "#pb_prev a",
            "a[rel=prev]", ".prev a", ".page_prev a", ".section-opt a.prev",
        ), page_url)
        next_url = self._pick_link(soup, tuple(rule.get("next_selectors") or ()) + (
            "a#next", "a.next", "a#nextPage", "a#pb_next", "#pb_next a",
            "a[rel=next]", ".next a", ".page_next a", ".section-opt a.next",
        ), page_url)
        if prev_url and next_url:
            return prev_url, next_url

        scopes = tuple(rule.get("nav") or ()) + (
            ".bottem1", ".bottem2", ".page_chapter", ".readpage", ".section-opt",
            ".chapter-nav", ".nav", "div.bookname", "div#wrapper",
        )
        for selector in scopes:
            try:
                nodes = soup.select(selector)
            except Exception:
                continue
            for node in nodes:
                for link in node.find_all("a", href=True):
                    text = clean_inline(link.get_text(" ", strip=True))
                    href = str(link["href"]).strip()
                    if not href or href.startswith("javascript:"):
                        continue
                    if not prev_url and PREV_RE.search(text):
                        prev_url = absolutize(href, page_url)
                    elif not next_url and NEXT_RE.search(text):
                        next_url = absolutize(href, page_url)
            if prev_url and next_url:
                break
        # 兜底：全页扫描
        if not (prev_url and next_url):
            for link in soup.find_all("a", href=True):
                text = clean_inline(link.get_text(" ", strip=True))
                href = str(link["href"]).strip()
                if not href or href.startswith("javascript:"):
                    continue
                if not prev_url and PREV_RE.search(text):
                    prev_url = absolutize(href, page_url)
                elif not next_url and NEXT_RE.search(text):
                    next_url = absolutize(href, page_url)
        return prev_url, next_url

    def _pick_link(self, soup, selectors: Sequence[str], page_url: str) -> str:
        """按选择器取第一个有效链接（用于无文字的翻页按钮）。"""
        for selector in selectors:
            try:
                node = soup.select_one(selector)
            except Exception:
                continue
            if node is None:
                continue
            href = str(node.get("href") or "").strip()
            if not href or href.startswith("javascript:"):
                continue
            return absolutize(href, page_url)
        return ""


def _quote(keyword: str, encoding: str) -> str:
    """按站点编码对关键词做 URL 编码（老站常用 gbk）。"""
    from urllib.parse import quote
    enc = encoding or "utf-8"
    try:
        return quote(keyword, encoding=enc)
    except LookupError:
        return quote(keyword, encoding="utf-8")


# ---------------------------------------------------------------------------
# 目录结构工具
# ---------------------------------------------------------------------------
def _split_sections(container: Tag) -> List[Tuple[str, List[Tag]]]:
    """把目录容器按 ``<dt>`` 切分成若干段，返回 [(段标题, [条目节点...])]。

    兼容两种结构：
    1. ``<dl><dt>《书》最新章节</dt><dd/>…<dt>《书》正文</dt><dd/>…</dl>``
    2. ``<div class="listmain"><dl>…</dl><dl>…</dl></div>``
    3. 无 ``dl`` 的列表：``<ul><li><a/></li>…</ul>``
    """
    dls = [container] if container.name == "dl" else container.find_all("dl")
    if not dls:
        items = container.find_all("li") or container.find_all("dd") or [container]
        return [("", [i for i in items if isinstance(i, Tag)])]

    sections: List[Tuple[str, List[Tag]]] = []
    for dl in dls:
        head, bucket = "", []
        for child in dl.children:
            if not isinstance(child, Tag):
                continue
            if child.name == "dt":
                if bucket or head:
                    sections.append((head, bucket))
                head, bucket = clean_inline(child.get_text(" ", strip=True)), []
            elif child.name in ("dd", "li"):
                bucket.append(child)
        if bucket or head:
            sections.append((head, bucket))
    return sections


CN_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
             "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
CN_UNITS = {"十": 10, "百": 100, "千": 1000, "万": 10000}
CHAPTER_NUM_RE = re.compile(
    r"第\s*([0-9〇零一二三四五六七八九十百千万两]{1,9})\s*[章节回卷篇集话]")
LEADING_NUM_RE = re.compile(r"^\s*(\d{1,5})\s*[\.、,，:：]")


def _cn_to_int(text: str) -> Optional[int]:
    """中文数字转阿拉伯数字（支持 十/百/千/万 的常见写法）。"""
    if not text:
        return None
    if text.isdigit():
        return int(text)
    total = section = number = 0
    for ch in text:
        if ch in CN_DIGITS:
            number = CN_DIGITS[ch]
        elif ch in CN_UNITS:
            unit = CN_UNITS[ch]
            if number == 0:
                number = 1
            if unit >= 10000:
                section = (section + number) * unit
                total += section
                section = 0
            else:
                section += number * unit
            number = 0
        else:
            return None
    return total + section + number


def _chapter_number(title: str) -> Optional[int]:
    """从章节标题中提取章节号，用于判断目录顺序。"""
    match = CHAPTER_NUM_RE.search(title or "")
    if match:
        return _cn_to_int(match.group(1))
    match = LEADING_NUM_RE.match(title or "")
    if match:
        return int(match.group(1))
    return None


def _order_chapters(pairs: List[Tuple[str, str]]) -> List[Tuple[str, str]]:
    """目录整体倒序时自动翻转。

    不少站点把目录按"最新在前"排列，直接展示会让读者从最后一章开始，
    这里用章节号统计相邻对的升降比例来判断。
    """
    numbered = [(i, _chapter_number(t)) for i, (t, _u) in enumerate(pairs)]
    known = [(i, n) for i, n in numbered if n is not None]
    if len(known) < 8:
        return pairs
    inc = dec = 0
    for (_, n1), (_, n2) in zip(known, known[1:]):
        if n2 > n1:
            inc += 1
        elif n2 < n1:
            dec += 1
    if dec > inc * 1.3:
        return list(reversed(pairs))
    return pairs


def _clean_author(text: str) -> str:
    """清洗作者字段：去掉 "/"、"|"、"作者：" 等修饰。"""
    text = clean_inline(text)
    if not text:
        return ""
    text = re.sub(r"^\s*(作者|作\s*者|authou?r)\s*[:：]?\s*", "", text, flags=re.I)
    text = text.strip(" /|,，、-—")
    text = re.sub(r"^(作者|作\s*者)\s*[:：]\s*", "", text)
    return text.strip()[:40]


def _extract_author(text: str) -> str:
    """从 "作者：xxx" 之类文本里提取作者名。"""
    text = clean_inline(text)
    match = re.search(r"(?:作者|作\s*者)\s*[:：]\s*([^|/\s,，。]{1,30})", text)
    if match:
        return match.group(1).strip()
    return ""


def _dedupe_books(books: Iterable[Book]) -> List[Book]:
    seen = set()
    result = []
    for book in books:
        key = normalize_url_key(book.url)
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(book)
    return result

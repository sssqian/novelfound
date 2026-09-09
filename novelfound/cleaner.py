# -*- coding: utf-8 -*-
"""正文清洗与广告过滤。

小说站点普遍在正文里插入广告（外链、站点域名、公众号、"请记住本站" 等），
本模块负责把 HTML 变成"只剩小说正文"的纯文本段落列表：

1. :func:`strip_noise` 先在 DOM 层删除脚本、样式、iframe、广告容器、隐藏节点；
2. :func:`pick_content_node` 在候选容器中挑选文本量最大的节点（自适应改版）；
3. :func:`extract_paragraphs` 逐行取文本，并用正则过滤广告行、空行、重复行。
"""
from __future__ import annotations

import re
from typing import Iterable, List, Optional, Sequence

from bs4 import BeautifulSoup, NavigableString, Tag

# ---------------------------------------------------------------------------
# 需要在 DOM 层直接删除的标签
# ---------------------------------------------------------------------------
# acronym / samp 等是部分站点（如和图书）插入的防盗版标记，正文里必须去掉
DROP_TAGS = ("script", "style", "noscript", "iframe", "ins", "object", "embed",
             "form", "button", "input", "select", "svg", "canvas", "video", "audio",
             "acronym", "samp", "tt", "kbd", "var", "cite", "sup", "sub")

# 广告容器的 class / id 特征（按词边界匹配，避免误伤 header/read 之类的名字）
AD_TOKEN_RE = re.compile(
    r"(^|[-_ ])(ads?|advert|adsbygoogle|banner|gg|guanggao|sponsor|promo|popup|"
    r"pop|tips?|recommend|tuiguang)([-_ ]|$)", re.I)
# 广告 id 的常见写法：ad / ads / adt1 / cbad / ctad / cstad / gg1 / banner2
AD_ID_RE = re.compile(r"^(c?[a-z]{0,2}ad[a-z]?\d*|ads?\d*|gg\d*|banner\d*|guanggao\d*)$", re.I)
# 这些名字一定是正文/目录容器，绝不能当成广告删除
SAFE_CONTAINER_RE = re.compile(
    r"^(content|chaptercontent|chapter_content|booktext|booktxt|txt|read|readcontent|"
    r"content_read|article|main|list|dir|wrapper|box_con|text|nr|nr1|book|"
    r"chapter|section|reader|showtxt|htmlcontent|bookinfo|intro)$", re.I)
# 行内元素不整体删除，交给文本行过滤处理
INLINE_TAGS = ("a", "span", "p", "br", "em", "strong", "b", "i", "font", "u")

# ---------------------------------------------------------------------------
# 广告 / 导航行文本特征
# ---------------------------------------------------------------------------
AD_LINE_PATTERNS: Sequence[re.Pattern] = tuple(re.compile(p, re.I) for p in (
    # 站点推广类
    r"请记住本站|记住本站|收藏本站|加入书签|添加到书签|本站网址|本站域名|永久域名|发布页|防走丢",
    r"最新网址|最新域名|手机版阅读网址|手机阅读网址|电脑访问|浏览器地址栏|换个域名",
    r"笔趣阁|笔趣书阁|笔趣岛|无弹窗|全文字|txt下载|电子书下载|免费阅读网",
    r"天才一秒记住|一秒记住|一秒记住本站|记住网址|书友们?请|书友群|QQ群|微信公众号|扫码关注",
    r"求收藏|求推荐|求月票|求订阅|求打赏|推荐票|月票|打赏|投推荐|催更|收藏本书",
    r"章节错误|内容有误|点此举报|举报本章|举报本书|反馈|意见建议",
    # 跳转 / 分页提示类
    r"点击下一页|下一页继续|翻页继续|本章未完|未完待续.*点击|继续阅读|下一页$",
    # 纯广告词
    r"^广告$|^推广$|^赞助$|广告位|广告合作|商务合作|投放广告|联系客服",
    # 明显的外链
    r"https?://|www\.[\w-]+\.(com|net|cn|cc|la|info|org|top|xyz|vip)",
))
# 只保留正文时，需要整体丢弃的短行（避免把 "上一章 目录 下一章" 之类导航当成正文）
NAV_LINE_RE = re.compile(
    r"^\s*(上一[章页节]|下一[章页节]|返回目录|目\s*录|章节目录|回目录|书页|首页|尾页)\s*$")

# 章节标题里的站点尾巴，如 "第1章 xxx_笔趣阁"
TITLE_TAIL_RE = re.compile(r"[_\-\|—]+\s*(最新章节|笔趣阁|笔趣.*|无弹窗.*|.*小说网)\s*$")

SYMBOL_ONLY_RE = re.compile(r"^[\s\W_]+$")


def make_soup(html: str) -> BeautifulSoup:
    """解析 HTML；优先 lxml（更快更容错），失败则退回内置解析器。"""
    try:
        return BeautifulSoup(html, "lxml")
    except Exception:  # pragma: no cover - 环境缺少 lxml 时
        return BeautifulSoup(html, "html.parser")


def _is_ad_container(tag: Tag) -> bool:
    """判断节点是否是广告容器（class / id 特征 + 隐藏样式）。"""
    if tag.name in INLINE_TAGS:
        return False
    values: List[str] = []
    for attr in ("class", "id"):
        raw = tag.get(attr)
        if not raw:
            continue
        values.extend(raw if isinstance(raw, list) else [raw])
    if not values:
        return False
    # 正文/目录容器名优先保护，避免"content_read"这类名字被误删
    for value in values:
        if SAFE_CONTAINER_RE.match(value.strip()):
            return False
    for value in values:
        value = value.strip()
        if not value:
            continue
        if AD_TOKEN_RE.search(value) or AD_ID_RE.match(value):
            return True
    style = (tag.get("style") or "").replace(" ", "").lower()
    if "display:none" in style or "visibility:hidden" in style:
        return True
    return False


def strip_noise(soup: BeautifulSoup) -> BeautifulSoup:
    """删除脚本、样式、广告容器、隐藏节点，返回同一个 soup 以便链式调用。"""
    for tag in soup(list(DROP_TAGS)):
        tag.decompose()
    for comment in soup.find_all(string=lambda s: isinstance(s, NavigableString)
                                 and s.__class__.__name__ == "Comment"):
        comment.extract()
    # 广告容器可能嵌套，反复清理直到稳定
    for _ in range(3):
        removed = False
        for tag in soup.find_all(True):
            if _is_ad_container(tag):
                tag.decompose()
                removed = True
                break
        if not removed:
            break
    # 合并相邻文本节点：删除内联标签（acronym 等）后会留下碎片文本，
    # 不合并的话取文本时会被误当成多个段落
    try:
        soup.smooth()
    except AttributeError:  # pragma: no cover - 极老版本 bs4
        pass
    return soup


def _text_len(node: Tag) -> int:
    return len(re.sub(r"\s+", "", node.get_text("", strip=True)))


def pick_content_node(soup: BeautifulSoup, selectors: Sequence[str],
                      min_chars: int = 200) -> Optional[Tag]:
    """按优先级挑选正文容器。

    规则里配置的选择器排在前面，因此**必须按顺序**判断，而不是简单取"文本最多"
    的节点：像 ``div.content_read`` 这类外层容器会把导航、推荐位一起算进去，
    文本量反而比真正的 ``#content`` 更大。
    策略：按顺序找第一个"文本量达标"的选择器；都不达标时退而取第一个命中的选择器
    （短章节可能只有几十个字）；全都命中不了时再用全页最大块兜底。
    """
    first_match: Optional[Tag] = None
    for selector in selectors:
        try:
            nodes = [n for n in soup.select(selector) if isinstance(n, Tag)]
        except Exception:
            continue
        if not nodes:
            continue
        best = max(nodes, key=_text_len)
        if first_match is None:
            first_match = best
        if _text_len(best) >= min_chars:
            return best
    if first_match is not None:
        return first_match
    # 兜底：全页文本量最大的 div / article
    best_node: Optional[Tag] = None
    best_len = 0
    for node in soup.find_all(["div", "article", "section"]):
        length = _text_len(node)
        if length > best_len:
            best_node, best_len = node, length
    return best_node if best_len > 0 else None


def pick_intro(soup: BeautifulSoup, selectors: Sequence[str]) -> str:
    """从候选选择器提取简介文本。"""
    for selector in selectors:
        try:
            nodes = soup.select(selector)
        except Exception:
            continue
        for node in nodes:
            text = clean_inline(node.get_text(" ", strip=True))
            if len(text) >= 20:
                return text
    return ""


def clean_inline(text: str) -> str:
    """压缩空白，去掉零宽字符与首尾噪声。"""
    if not text:
        return ""
    text = text.replace("\u3000", " ").replace("\xa0", " ")
    text = re.sub(r"[\u200b\u200c\u200d\ufeff]", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def is_ad_line(line: str) -> bool:
    """判断某一行是否属于广告 / 导航 / 推广内容。"""
    stripped = line.strip()
    if not stripped:
        return True
    if NAV_LINE_RE.match(stripped):
        return True
    for pattern in AD_LINE_PATTERNS:
        if pattern.search(stripped):
            return True
    # 纯符号行
    if SYMBOL_ONLY_RE.match(stripped) and len(stripped) < 12:
        return True
    return False


# 严格模式额外过滤：整行纯 ASCII（多为站点推广）、含 emoji 的推广行
STRICT_LINE_PATTERNS: Sequence[re.Pattern] = tuple(re.compile(p, re.I) for p in (
    r"^\s*[\x00-\x7f]+\s*$",
    r"^\s*[（(\[【]?\s*(本章完|未完待续|请收藏|求票|广告|推荐|公告|说明|ps|PS)\s*[:：]?[）)\]】]?\s*$",
    r"[\U0001F300-\U0001FAFF\u2600-\u27bf]",
))


def clean_lines(lines: Iterable[str], keep_short: bool = False,
                strict: bool = False) -> List[str]:
    """过滤广告行、空行与连续重复行，返回干净的段落列表。

    ``strict=True`` 时额外丢弃纯 ASCII 行、推广式括号行与含 emoji 的行。
    """
    result: List[str] = []
    for raw in lines:
        line = clean_inline(raw)
        if not line or is_ad_line(line):
            continue
        if strict and any(p.search(line) for p in STRICT_LINE_PATTERNS):
            continue
        if not keep_short and len(line) < 2:
            continue
        # 去掉站点加在正文行尾的推广片段
        line = re.sub(r"[（(【\[]?\s*(本章完|全文完|未完待续)\s*[）)】\]]?$", "", line).strip()
        if not line:
            continue
        if result and result[-1] == line:
            continue
        result.append(line)
    return result


def extract_paragraphs(node: Tag, keep_short: bool = False,
                       strict: bool = False) -> List[str]:
    """把一个内容节点转成段落列表。

    优先按块级元素（p / div）分段；没有块级元素时按 <br> 与换行切分。
    严格模式下会先把正文里的超链接整段去掉（很多站点把广告链接塞进段落中）。
    """
    if strict:
        for link in node.find_all("a"):
            link.decompose()
    blocks = node.find_all(["p", "div", "dd", "li", "h1", "h2", "h3", "blockquote"])
    lines: List[str] = []
    if blocks:
        for block in blocks:
            if block.find(["p", "div", "dd", "li", "blockquote"]):
                continue  # 嵌套容器，交给最内层处理
            text = block.get_text("\n", strip=True)
            lines.extend(text.split("\n"))
    if not lines or len("".join(lines)) < 40:
        lines = node.get_text("\n", strip=True).split("\n")
    return clean_lines(lines, keep_short=keep_short, strict=strict)


def clean_title(title: str) -> str:
    """清洗章节标题（去掉站点后缀、多余空白）。"""
    text = clean_inline(title or "")
    text = TITLE_TAIL_RE.sub("", text)
    text = re.sub(r"\s*[-–—]\s*$", "", text)
    return text.strip() or "正文"


NAV_WORD_RE = re.compile(
    r"^(首页|目录|书架|我的书架|上一[章页节]|下一[章页节]|上章|下章|加入书签|投推荐票|"
    r"热门推荐|推荐阅读|小说分类|玄幻小说|修真小说|都市小说|穿越小说|网游小说|科幻小说|"
    r"排行榜|完本小说|返回|顶部|底部|手机阅读|阅读记录|最新章节|章节目录|上一章←章节目录→下一章)$")


def looks_like_nav(paragraphs: List[str], ratio: float = 0.6,
                   max_chars: int = 400) -> bool:
    """判断提取到的"正文"是否其实是导航/推荐页。

    短章节 + 大量导航词时几乎可以确定抓错了容器，此时应提示用户而不是展示垃圾内容。
    """
    if not paragraphs:
        return True
    total = sum(len(p) for p in paragraphs)
    if total > max_chars:
        return False
    nav = 0
    for para in paragraphs:
        text = para.strip()
        if NAV_WORD_RE.match(text) or len(text) <= 4:
            nav += 1
    return nav / len(paragraphs) > ratio


def strip_title_echo(paragraphs: List[str], title: str) -> List[str]:
    """去掉正文开头与章节标题重复的行。

    不少站点会把"集名 / 章名"作为 <h2> 放在正文容器里，直接展示会显得重复。
    """
    if not paragraphs or not title:
        return paragraphs
    title_parts = [p.strip() for p in re.split(r"[\s　]+", title) if p.strip()]
    result = list(paragraphs)
    while result:
        head = result[0].strip()
        if len(head) > 40:
            break
        if head == title or head in title or any(head == part for part in title_parts):
            result.pop(0)
            continue
        break
    return result or paragraphs


def normalize_url_key(url: str) -> str:
    """用于去重的 URL 归一化（去掉锚点与常见追踪参数）。"""
    url = (url or "").split("#")[0]
    url = re.sub(r"[?&](from|_from|utm_[^=&]*)=[^&]*", "", url)
    return url.rstrip("/")

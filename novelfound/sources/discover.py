# -*- coding: utf-8 -*-
"""网络发现（方案 C）。

当所有已启用书源都搜不到目标书时，退一步去**搜索引擎**里找候选站点，
再把候选域名交给方案 B 的探测引擎验证，最后只把真正能用、且确实收录了
这本书的站点作为新书源推荐给用户。

设计上的几个取舍：

* **只用可用的引擎**：实测本机环境下只有 Bing（cn.bing.com / www.bing.com）
  能稳定返回可解析的结果页；百度/360/搜狗/DDG 等要么反爬、要么需要 JS。
  引擎清单是可扩展的，加新引擎只要补一条配置。
* **一定要人工确认**：发现流程只推荐、不自动启用，避免把垃圾站写进配置。
* **必须验证"这本书真的在"**：探测通过只能说明站点结构能解析，
  还要比对搜索到的书名与用户要找的书是否一致。
* **控制请求量**：默认最多探测 5 个候选域名，逐站点限速（复用 HttpSession），
  已配置过的站点直接跳过。
"""
from __future__ import annotations

import html as html_module
import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import quote, urlparse

from ..net import HttpSession, NovelError
from .probe import ProbeOutcome, probe_domain, title_matches

# ---------------------------------------------------------------------------
# 搜索引擎（模板里的 {q} 会被替换为 URL 编码后的查询词）
# ---------------------------------------------------------------------------
ENGINES: Dict[str, Dict[str, str]] = {
    "bing_cn": {
        "name": "Bing（中国）",
        "url": "https://cn.bing.com/search?q={q}&setlang=zh-CN",
        "item": r'<li class="b_algo".*?</li>',
    },
    "bing": {
        "name": "Bing（国际）",
        "url": "https://www.bing.com/search?q={q}",
        "item": r'<li class="b_algo".*?</li>',
    },
}

# 明显不适合当书源的站点：搜索引擎自己、社交、百科、官方付费站、购物等
SKIP_HOST_RE = re.compile(
    r"(^|\.)("
    r"bing\.com|baidu\.com|bdstatic\.com|google\.[a-z.]+|duckduckgo\.com|"
    r"so\.com|360tres\.com|360\.cn|sogou\.com|mojeek\.com|brave\.com|"
    r"startpage\.com|yandex\.[a-z.]+|yastatic\.net|"
    r"zhihu\.com|weibo\.(com|cn)|wikipedia\.org|baike\.[a-z.]+|douban\.com|"
    r"qidian\.com|readnovel\.com|xxsy\.net|jjwxc\.net|zongheng\.com|17k\.com|"
    r"ciweimao\.com|fanqienovel\.com|hongxiu\.com|yunqi\.qq\.com|"
    r"taobao\.com|tmall\.com|jd\.com|pinduoduo\.com|1688\.com|"
    r"bilibili\.com|douyin\.com|kuaishou\.com|youtube\.com|facebook\.com|"
    r"twitter\.com|x\.com|instagram\.com|reddit\.com|tiktok\.com|"
    r"qq\.com|163\.com|sina\.com|sohu\.com|toutiao\.com|ifeng\.com|"
    r"csdn\.net|cnblogs\.com|jianshu\.com|github\.com|gitee\.com|gitlab\.com|"
    r"tieba\.baidu\.com|bbs\.[a-z0-9.-]+|"
    r"gov\.cn|edu\.cn|org\.cn|microsoft\.com|apple\.com|"
    r"mastodon\.social|buttondown\.email"
    r")$", re.I)

# 结果标题/摘要里出现这些词，说明更像"能在线看小说"的站点
GOOD_HINT_RE = re.compile(r"小说|阅读|最新章节|全文|免费|笔趣阁|书城|txt|书库", re.I)
BAD_HINT_RE = re.compile(r"正版|付费|会员|购买|百科|介绍|简介|评价|下载app|手游|游戏|"
                         r"官网|官方|预约|商城", re.I)

# 搜索词模板：实测 "{书名} 笔趣阁" 最能直接命中可用的聚合站，
# 其次"{书名} 免费小说"；"{书名} 小说 在线阅读" 作为兜底（多为官方/付费站）。
QUERY_TEMPLATES = (
    "{title} 笔趣阁",
    "{title} 免费小说",
    "{title} 小说 在线阅读",
)
# 打分低于该值的候选直接跳过（多为游戏官网、百科、商城等）
MIN_CANDIDATE_SCORE = 0


@dataclass
class Candidate:
    """一个候选站点。"""

    host: str = ""
    url: str = ""
    title: str = ""
    snippet: str = ""
    score: int = 0
    skipped: str = ""       # 被跳过的原因（空 = 参与探测）

    @property
    def base_url(self) -> str:
        scheme = urlparse(self.url).scheme or "https"
        return f"{scheme}://{self.host}"


@dataclass
class DiscoveredSource:
    """一个候选站点的探测结果。"""

    candidate: Candidate
    outcome: Optional[ProbeOutcome] = None
    matched: bool = False          # 站点里是否真的搜到了这本书

    @property
    def ok(self) -> bool:
        return bool(self.outcome and self.outcome.ok and self.matched)

    @property
    def reason(self) -> str:
        if self.outcome is None:
            return "未探测"
        if not self.outcome.ok:
            return f"探测未通过（卡在{self.outcome.stage}）：{self.outcome.error}"
        if not self.matched:
            titles = "、".join(self.outcome.search_titles[:3]) or "无"
            return f"探测通过，但没搜到这本书（搜到的是：{titles}）"
        return ""


@dataclass
class DiscoveryOutcome:
    """一次网络发现的完整结果。"""

    query: str = ""
    book_title: str = ""
    engine: str = ""
    error: str = ""
    candidates: List[Candidate] = field(default_factory=list)
    results: List[DiscoveredSource] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    @property
    def usable(self) -> List[DiscoveredSource]:
        return [r for r in self.results if r.ok]

    def note(self, text: str) -> None:
        self.notes.append(text)


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------
def discover_sources(http: HttpSession, book_title: str, *, author: str = "",
                     limit: int = 5, engine: str = "",
                     existing_hosts: Iterable[str] = (),
                     on_note=None) -> DiscoveryOutcome:
    """搜索网络 → 提取候选站点 → 逐个探测 → 返回可用书源。"""
    outcome = DiscoveryOutcome(query=book_title, book_title=book_title)

    def note(text: str) -> None:
        outcome.note(text)
        if on_note is not None:
            on_note(text)

    query = f"{book_title} {author} 小说 在线阅读".strip() if author \
        else f"{book_title} 小说 在线阅读"
    engine_keys = [engine] if engine in ENGINES else list(ENGINES.keys())

    candidates: List[Candidate] = []
    for key in engine_keys:
        try:
            note(f"正在用 {ENGINES[key]['name']} 搜索…")
            candidates = search_candidates_multi(http, book_title, key,
                                                 on_note=note)
            outcome.engine = key
        except NovelError as exc:
            note(f"{ENGINES[key]['name']} 搜索失败：{exc.message}")
            continue
        if candidates:
            note(f"{ENGINES[key]['name']} 返回 {len(candidates)} 个候选站点")
            break
    if not candidates:
        outcome.error = ("没有从搜索结果里提取到候选站点（可能被搜索引擎限流，"
                         "或该书名过于生僻）")
        note("✗ " + outcome.error)
        return outcome

    outcome.candidates = candidates
    known = {h.lower() for h in existing_hosts}
    todo = [c for c in candidates if not c.skipped and c.host.lower() not in known]
    if not todo:
        outcome.error = "候选站点都已配置过，或全部被过滤"
        note("✗ " + outcome.error)
        return outcome

    for index, candidate in enumerate(todo[:max(1, limit)]):
        note(f"探测 {index + 1}/{min(len(todo), limit)}：{candidate.host} …")
        try:
            probe = probe_domain(http, candidate.base_url, book_title)
        except Exception as exc:  # noqa: BLE001 - 单个站点异常不影响其它候选
            probe = ProbeOutcome(base_url=candidate.base_url, keyword=book_title,
                                 error=f"探测异常：{type(exc).__name__}")
        matched = _title_matches(book_title, probe.search_titles)
        result = DiscoveredSource(candidate=candidate, outcome=probe, matched=matched)
        outcome.results.append(result)
        if result.ok:
            note(f"  ✓ {candidate.host}：{probe.template}，目录 {probe.chapter_count} 章，"
                 f"正文 {probe.char_count} 字")
        else:
            note(f"  ✗ {candidate.host}：{result.reason}")
    return outcome


# ---------------------------------------------------------------------------
# 搜索引擎解析
# ---------------------------------------------------------------------------
def search_candidates_multi(http: HttpSession, book_title: str, engine: str,
                            max_queries: int = 3, on_note=None) -> List[Candidate]:
    """用多个搜索词模板查一遍，合并去重后按相关度排序。

    单个搜索词的结果往往被官方/付费站占满，多词合并能显著提高命中聚合站的概率。
    """
    merged: Dict[str, Candidate] = {}
    for template in QUERY_TEMPLATES[:max(1, max_queries)]:
        query = template.format(title=book_title)
        if on_note is not None:
            on_note(f"  搜索词：{query}")
        try:
            found = search_candidates(http, query, engine)
        except NovelError as exc:
            if on_note is not None:
                on_note(f"  该搜索词失败：{exc.message}")
            continue
        for candidate in found:
            current = merged.get(candidate.host)
            if current is None:
                merged[candidate.host] = candidate
            else:
                # 同一域名在多个搜索词里都出现，说明更相关
                current.score += 1
    candidates = list(merged.values())
    for candidate in candidates:
        if not candidate.skipped and candidate.score < MIN_CANDIDATE_SCORE:
            candidate.skipped = "搜索结果不像在线阅读站（官网/游戏/商城等）"
    candidates.sort(key=lambda c: (bool(c.skipped), -c.score))
    return candidates


def search_candidates(http: HttpSession, query: str, engine: str,
                      max_items: int = 20) -> List[Candidate]:
    """抓取搜索结果页并提取候选站点（按相关度排序）。"""
    config = ENGINES.get(engine)
    if not config:
        return []
    url = config["url"].replace("{q}", quote(query))
    html = http.get_text(url, timeout=20)
    items = _extract_items(html, config.get("item", ""))
    seen: Dict[str, Candidate] = {}
    for title, href, snippet in items[:max_items]:
        parsed = urlparse(href)
        host = parsed.netloc.lower()
        if not host:
            continue
        host = host.split(":")[0]
        if not host or host in seen:
            continue
        candidate = Candidate(host=host, url=href,
                              title=_clean_text(title), snippet=_clean_text(snippet))
        candidate.score = _score(candidate)
        if SKIP_HOST_RE.search(host):
            candidate.skipped = "官方/付费/百科/社交等站点，不适合作为书源"
        seen[host] = candidate
    candidates = list(seen.values())
    # 未跳过的按分数排序，跳过的排在最后（界面仍可见，便于用户理解为什么没探它）
    candidates.sort(key=lambda c: (bool(c.skipped), -c.score))
    return candidates


def _extract_items(html: str, item_pattern: str) -> List[Tuple[str, str, str]]:
    """从结果页里抽出 (标题, 链接, 摘要)。"""
    items: List[Tuple[str, str, str]] = []
    blocks = re.findall(item_pattern, html, re.S) if item_pattern else []
    if not blocks:
        blocks = re.findall(r"<h2[^>]*>.*?</h2>", html, re.S)
    for block in blocks:
        match = re.search(r'<h2[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', block, re.S)
        if not match:
            continue
        href = html_module.unescape(match.group(1))
        title = match.group(2)
        if not href.startswith("http"):
            continue
        snippet = ""
        para = re.search(r"<p[^>]*>(.*?)</p>", block, re.S)
        if para:
            snippet = para.group(1)
        else:
            cite = re.search(r"<cite[^>]*>(.*?)</cite>", block, re.S)
            if cite:
                snippet = cite.group(1)
        items.append((title, href, snippet))
    return items


def _clean_text(raw: str) -> str:
    text = re.sub(r"<[^>]+>", "", raw or "")
    text = html_module.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _score(candidate: Candidate) -> int:
    """给候选站点打分：标题/摘要更像"在线小说站"的排前面。"""
    score = 0
    text = f"{candidate.title} {candidate.snippet}"
    if GOOD_HINT_RE.search(text):
        score += 3
    if "笔趣阁" in text:
        score += 3
    if BAD_HINT_RE.search(text):
        score -= 4
    if candidate.host.startswith("m."):
        score -= 1        # 移动站结构通常更简单，但优先桌面站
    return score


def _title_matches(query: str, titles: Sequence[str]) -> bool:
    """判断站点搜到的书名里是否包含目标书（复用探测模块的匹配逻辑）。"""
    return title_matches(query, titles)

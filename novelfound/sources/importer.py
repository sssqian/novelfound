# -*- coding: utf-8 -*-
"""书源导入。

支持两种格式，都只接受**纯数据**（URL 模板 + CSS 选择器），不执行任何远程脚本：

1. **本项目原生格式**：一个规则对象或规则数组，字段与 ``builtin.py`` 一致；
2. **开源「阅读」(Legado) 书源 JSON**：社区里最大的公开书源生态，
   把它的字段映射成本项目的规则。带 JS / XPath / JSONPath 规则的源会被标记为
   "不支持"并跳过——执行远程 JS 等于让陌生代码在你机器上运行，这里一律拒绝。

解析结果是 :class:`ParsedRule` 列表，界面可以先预览、再选择性导入。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

# Legado 规则里出现的这些特征说明需要执行脚本或使用非 CSS 语法，一律不支持
UNSUPPORTED_MARKERS = ("<js>", "</js>", "@js:", "$.", "@xpath:", "@json:", "##")
CSS_PREFIXES = ("@css:", "@css")

# 规则尾部的提取指令，如 @css:h4 a@text
TAIL_DIRECTIVE_RE = re.compile(r"@(text|textNodes|href|src|html|ownText|all)\s*$", re.I)
# 形如 selector@text@href 的多段指令
SPLIT_DIRECTIVE_RE = re.compile(r"@(?=(?:text|href|src|html|ownText|all)\b)", re.I)

DEFAULT_TITLE = (".bookname h1", "h1")
DEFAULT_AUTHOR = (".author", "span.author")


@dataclass
class ParsedRule:
    """一条解析出来的书源规则（含导入状态）。"""

    rule: Dict[str, Any] = field(default_factory=dict)
    name: str = ""
    key: str = ""
    base_url: str = ""
    status: str = "ok"          # ok / unsupported / invalid
    reason: str = ""
    fmt: str = "native"         # native / legado

    @property
    def ok(self) -> bool:
        return self.status == "ok" and bool(self.rule)


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
def parse_payload(text: str, origin: str = "") -> List[ParsedRule]:
    """解析一段 JSON 文本，自动识别原生格式或 Legado 格式。"""
    text = (text or "").strip()
    if not text:
        return [ParsedRule(status="invalid", reason="内容为空", name=origin)]
    try:
        data = json.loads(text)
    except ValueError as exc:
        # 有些订阅地址返回的是 JSONP 或带说明文字，尝试提取第一段 JSON
        match = re.search(r"[\[{].*[\]}]", text, re.S)
        if match:
            try:
                data = json.loads(match.group(0))
            except ValueError:
                return [ParsedRule(status="invalid", name=origin,
                                   reason=f"JSON 解析失败：{exc}")]
        else:
            return [ParsedRule(status="invalid", name=origin,
                               reason=f"JSON 解析失败：{exc}")]

    if isinstance(data, dict):
        # 原生单条 / 原生包装 / Legado 单条
        if "key" in data and "base_url" in data:
            return [_parse_native_item(data, origin)]
        if isinstance(data.get("sources"), list):
            return _parse_native_list(data["sources"], origin)
        if "bookSourceUrl" in data or "bookSourceName" in data:
            return [_parse_legado_item(data, origin)]
        return [ParsedRule(status="invalid", name=origin,
                           reason="无法识别的 JSON 结构（既不是原生规则也不是书源列表）")]
    if isinstance(data, list):
        if not data:
            return [ParsedRule(status="invalid", name=origin, reason="列表为空")]
        if any(isinstance(i, dict) and ("bookSourceUrl" in i or "bookSourceName" in i)
               for i in data):
            return [_parse_legado_item(i, origin) for i in data if isinstance(i, dict)]
        return _parse_native_list(data, origin)
    return [ParsedRule(status="invalid", name=origin, reason="JSON 顶层类型不支持")]


# ---------------------------------------------------------------------------
# 原生格式
# ---------------------------------------------------------------------------
def _parse_native_list(items: List[Any], origin: str) -> List[ParsedRule]:
    return [_parse_native_item(i, origin) for i in items if isinstance(i, dict)]


def _parse_native_item(item: Dict[str, Any], origin: str) -> ParsedRule:
    key = str(item.get("key") or "").strip()
    name = str(item.get("name") or key or "未命名书源").strip()
    base = str(item.get("base_url") or "").strip()
    if not key or not base:
        return ParsedRule(status="invalid", name=name, key=key, base_url=base,
                          reason="缺少 key 或 base_url", fmt="native")
    rule = dict(item)
    rule.setdefault("enabled_by_default", True)
    rule.setdefault("note", f"导入自 {origin}" if origin else "导入的书源")
    return ParsedRule(rule=rule, name=name, key=key, base_url=base, fmt="native")


# ---------------------------------------------------------------------------
# Legado（阅读）格式
# ---------------------------------------------------------------------------
def _parse_legado_item(src: Dict[str, Any], origin: str) -> ParsedRule:
    name = str(src.get("bookSourceName") or "").strip() or "未命名书源"
    raw_url = str(src.get("bookSourceUrl") or "").strip()
    if not raw_url:
        return ParsedRule(status="invalid", name=name, reason="缺少 bookSourceUrl",
                          fmt="legado")

    parsed = urlparse(raw_url if "://" in raw_url else "https://" + raw_url)
    base = f"{parsed.scheme}://{parsed.netloc}" if parsed.netloc else raw_url.rstrip("/")
    key = _make_key(parsed.netloc or name)

    search = src.get("ruleSearch") or {}
    toc = src.get("ruleToc") or {}
    content = src.get("ruleContent") or {}
    info = src.get("ruleBookInfo") or {}

    # 先把所有要用的规则串收集起来，统一判断是否含脚本语法
    raw_rules = {
        "search_url": str(src.get("searchUrl") or ""),
        "book_list": _rule_text(search.get("bookList")),
        "book_name": _rule_text(search.get("name")),
        "book_author": _rule_text(search.get("author")),
        "book_url": _rule_text(search.get("bookUrl")),
        "cover": _rule_text(search.get("coverUrl")),
        "intro": _rule_text(search.get("intro")),
        "latest": _rule_text(search.get("lastChapter")),
        "chapter_list": _rule_text(toc.get("chapterList")),
        "chapter_name": _rule_text(toc.get("chapterName")),
        "content": _rule_text(content.get("content")),
        "info_name": _rule_text(info.get("name")),
        "info_author": _rule_text(info.get("author")),
        "info_intro": _rule_text(info.get("intro")),
        "info_cover": _rule_text(info.get("coverUrl")),
        "info_kind": _rule_text(info.get("kind")),
    }
    for field_name, value in raw_rules.items():
        if value and _needs_script(value):
            return ParsedRule(status="unsupported", name=name, key=key, base_url=base,
                              reason=f"字段 {field_name} 使用 JS/XPath/JSON 规则，本程序不支持",
                              fmt="legado")

    search_url, method, data = _convert_search_url(raw_rules["search_url"], base)
    if not search_url:
        return ParsedRule(status="unsupported", name=name, key=key, base_url=base,
                          reason="缺少 searchUrl 或无法解析", fmt="legado")
    if not raw_rules["book_list"]:
        return ParsedRule(status="unsupported", name=name, key=key, base_url=base,
                          reason="缺少 ruleSearch.bookList", fmt="legado")
    if not raw_rules["chapter_list"]:
        return ParsedRule(status="unsupported", name=name, key=key, base_url=base,
                          reason="缺少 ruleToc.chapterList", fmt="legado")
    if not raw_rules["content"]:
        return ParsedRule(status="unsupported", name=name, key=key, base_url=base,
                          reason="缺少 ruleContent.content", fmt="legado")

    rule: Dict[str, Any] = {
        "key": key,
        "name": name,
        "base_url": base,
        "search_url": search_url,
        "search_items": [_clean_selector(raw_rules["book_list"])],
        "book_title": _selector_list(raw_rules["book_name"], DEFAULT_TITLE),
        "book_link": _selector_list(raw_rules["book_url"] or raw_rules["book_name"],
                                    DEFAULT_TITLE),
        "book_author": _selector_list(raw_rules["book_author"], DEFAULT_AUTHOR),
        "book_cover": _selector_list(raw_rules["cover"]),
        "book_latest": _selector_list(raw_rules["latest"]),
        "book_intro": _selector_list(raw_rules["intro"]),
        "detail_title": _selector_list(raw_rules["info_name"], DEFAULT_TITLE),
        "detail_author": _selector_list(raw_rules["info_author"], DEFAULT_AUTHOR),
        "detail_intro": _selector_list(raw_rules["info_intro"], ("#intro", "div.intro")),
        "detail_cover": _selector_list(raw_rules["info_cover"]),
        # Legado 的 chapterList 通常直接给出章节链接选择器，本项目也支持
        # "选择器直接命中 <a>" 的写法（见 rule_source._split_sections 的兜底分支）
        "catalog_groups": [_clean_selector(raw_rules["chapter_list"])],
        "catalog_items": ["a"],
        "content": [_clean_selector(raw_rules["content"])],
        "chapter_title": _selector_list(raw_rules["chapter_name"], DEFAULT_TITLE),
        "enabled_by_default": True,
        "note": f"由 Legado 书源导入（{origin}）" if origin else "由 Legado 书源导入",
        "imported_from": origin,
        "import_format": "legado",
    }
    if method == "POST":
        rule["search_method"] = "POST"
        rule["search_data"] = data
    # 去掉空字段，保持配置干净
    rule = {k: v for k, v in rule.items() if v not in ([], "", None)}
    return ParsedRule(rule=rule, name=name, key=key, base_url=base, fmt="legado")


def _rule_text(value: Any) -> str:
    """Legado 的规则字段可能是 str / list / dict，统一取字符串。"""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return " ".join(_rule_text(v) for v in value).strip()
    if isinstance(value, dict):
        # 少数源写成 {"selector": "@css:.x"} 之类的结构
        for key in ("selector", "rule", "value", "text"):
            if key in value:
                return _rule_text(value[key])
    return ""


def _needs_script(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in UNSUPPORTED_MARKERS)


def _clean_selector(text: str) -> str:
    """把 Legado 的选择器清洗成本项目可用的 CSS 选择器。"""
    text = (text or "").strip()
    if not text:
        return ""
    # 去掉 @css: 前缀
    if text.lower().startswith("@css:"):
        text = text[5:]
    elif text.lower() == "@css":
        text = ""
    # 去掉 @text / @href 之类的尾部指令
    text = TAIL_DIRECTIVE_RE.sub("", text)
    # 多段指令只保留第一段（其余是提取方式，本项目用节点类型自动判断）
    text = SPLIT_DIRECTIVE_RE.split(text)[0]
    # Legado 里 || 表示"或"，取第一个候选
    text = text.split("||")[0].strip()
    # 去掉前后引号
    text = text.strip("'\" ")
    return text


def _selector_list(text: str, fallback: Tuple[str, ...] = ()) -> List[str]:
    selector = _clean_selector(text)
    result: List[str] = []
    if selector:
        result.append(selector)
    for item in fallback:
        if item not in result:
            result.append(item)
    return result


def _convert_search_url(raw: str, base: str) -> Tuple[str, str, Dict[str, str]]:
    """把 Legado 的 searchUrl 转成本项目的 URL 模板。

    形如 ``https://site/s?q={{key}},{"method":"POST","body":"kw={{key}}"}``。
    返回 (url 模板, 方法, POST 表单)。
    """
    raw = (raw or "").strip()
    if not raw:
        return "", "GET", {}
    method = "GET"
    data: Dict[str, str] = {}
    url_part = raw
    if ",{" in raw:
        url_part, _, options = raw.partition(",{")
        # Legado 的选项段形如 {"method":"POST","body":"..."}，
        # partition 已经吃掉了 "{", 这里按需补回再解析
        options = options.strip()
        opts_text = options if options.startswith("{") else "{" + options
        try:
            opts = json.loads(opts_text)
        except ValueError:
            opts = {}
        method = str(opts.get("method") or "GET").upper()
        body = str(opts.get("body") or "")
        for pair in re.split(r"[&\n]", body):
            if "=" in pair:
                k, v = pair.split("=", 1)
                data[k.strip()] = v.strip()

    url_part = url_part.strip().strip("'\"")
    url_part = (url_part.replace("{{key}}", "{q}")
                        .replace("{{Key}}", "{q}")
                        .replace("{{}}", "{q}")
                        .replace("{{page}}", "1"))
    # POST 表单里的占位符同样替换
    data = {k: (v.replace("{{key}}", "{q}").replace("{{}}", "{q}"))
            for k, v in data.items()}
    # 关键词必须出现在 URL 或 POST 表单里，否则这个搜索地址没法用
    if "{q}" not in url_part and "{q}" not in "".join(data.values()):
        return "", method, data
    if not url_part.startswith("http"):
        url_part = base + ("" if url_part.startswith("/") else "/") + url_part
    return url_part, method, data


def _make_key(netloc: str) -> str:
    host = (netloc or "").split(":")[0].lower()
    host = re.sub(r"[^a-z0-9]+", "_", host).strip("_") or "source"
    return f"ld_{host}"

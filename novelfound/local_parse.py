# -*- coding: utf-8 -*-
"""本地电子书解析：TXT 与 EPUB。

导出的两类数据：

* :func:`parse_txt`   → ``(title, author, chapters)``，章节带**字符偏移** ``start/end``；
* :func:`parse_epub`  → ``(title, author, chapters, cover)``，章节带 ``href``（zip 内路径）。

阅读时分别用 :func:`read_txt_chapter` / :func:`read_epub_chapter` 按需取正文，
不需要把整本书都解析成段落（几 MB 的 TXT 也只在导入时扫一遍）。

TXT 的章节识别按中文网文的常见写法：``第12章``、``第 12 节``、``第十二回``、
``楔子``、``序章``、``番外`` 等；识别不到足够的章节时退化为**按长度切块**，
保证"导进来就能读"。
"""
from __future__ import annotations

import re
import zipfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from xml.etree import ElementTree

from .cleaner import clean_lines, clean_title, make_soup, strip_noise, strip_title_echo

# --------------------------------------------------------------------------- 编码
BOM_ENCODINGS = ((b"\xef\xbb\xbf", "utf-8-sig"), (b"\xff\xfe", "utf-16"),
                 (b"\xfe\xff", "utf-16"))
# 中文 TXT / EPUB 里常见的编码别名，统一到 Python 认识的规范名
ENCODING_ALIASES = {
    "utf8": "utf-8", "utf-8": "utf-8", "utf-8-sig": "utf-8-sig",
    "gbk": "gb18030", "gb2312": "gb18030", "gb-2312": "gb18030",
    "gb18030": "gb18030", "gb18030-2000": "gb18030", "cp936": "gb18030",
    "big5": "big5", "big-5": "big5", "cp950": "big5",
    "utf-16": "utf-16", "utf16": "utf-16", "unicode": "utf-16",
    "latin-1": "latin-1", "iso-8859-1": "latin-1", "latin1": "latin-1",
}
# 文件里声明的编码（XML 声明 / <meta charset>）——这是最权威的来源
DECL_RE = re.compile(rb"""encoding\s*=\s*["']([\w.\-]+)["']""", re.IGNORECASE)
CHARSET_RE = re.compile(rb"""charset\s*=\s*["']?([\w.\-]+)""", re.IGNORECASE)
# 猜编码时的候选顺序（GB18030 放前面：中文 TXT 里最常见，且容错强）
CANDIDATE_ENCODINGS = ("utf-8", "gb18030", "big5")


def normalize_encoding(name: str) -> str:
    """把文件里写的编码名归一化成 Python 认识的名字（不认识的返回空串）。"""
    name = (name or "").strip().lower().replace("_", "-")
    if not name:
        return ""
    if name in ENCODING_ALIASES:
        return ENCODING_ALIASES[name]
    try:
        import codecs
        codecs.lookup(name)
        return name
    except LookupError:
        return ""


def declared_encoding(raw: bytes) -> str:
    """从文件开头读它自己声明的编码（XML 声明或 meta charset）。"""
    head = raw[:2048]
    match = DECL_RE.search(head) or CHARSET_RE.search(head)
    if not match:
        return ""
    try:
        return normalize_encoding(match.group(1).decode("ascii", "ignore"))
    except Exception:      # pragma: no cover - 防御
        return ""


def _prefix_decodes(raw: bytes, encoding: str, limit: int = 65536) -> bool:
    """用**增量解码器**判断前缀是否是这种编码。

    关键：不能拿截断的前缀直接 ``decode()``——中文 UTF-8 是 3 字节/字，
    在 4KB 处截断很容易把一个字符切成两半，于是合法的 UTF-8 会被误判成
    GBK，整本书 60% 的章节就乱码了（真实踩过）。
    增量解码器把"尾部不完整"当作待续字节，只有真正的非法序列才会报错。
    """
    import codecs

    decoder = codecs.getincrementaldecoder(encoding)()
    try:
        decoder.decode(raw[:limit], False)      # final=False → 允许尾部不完整
        return True
    except (UnicodeDecodeError, LookupError, ValueError):
        return False


def detect_encoding(raw: bytes, declared: str = "") -> str:
    """猜文本编码：BOM → 文件声明 → utf-8 / gb18030 / big5（增量判定）。"""
    for bom, encoding in BOM_ENCODINGS:
        if raw.startswith(bom):
            return encoding
    name = normalize_encoding(declared)
    if name and _prefix_decodes(raw, name):
        return name
    for encoding in CANDIDATE_ENCODINGS:
        if _prefix_decodes(raw, encoding):
            return encoding
    return "gb18030"          # 兜底：GB18030 几乎能解出所有字节序列


def decode_text(raw: bytes, declared: str = "") -> Tuple[str, str]:
    """返回 ``(文本, 使用的编码)``；``declared`` 是文件自己声明的编码。"""
    encoding = detect_encoding(raw, declared)
    try:
        return raw.decode(encoding, errors="replace"), encoding
    except LookupError:       # pragma: no cover - 系统缺该编码时兜底
        return raw.decode("utf-8", errors="replace"), "utf-8"


# --------------------------------------------------------------------------- TXT
# 章节标题行：``第12章 风起`` / ``第十二回`` / ``楔子`` / ``番外`` / ``Chapter 3``。
# 关键是把"正文里出现第X章"的句子排除掉，否则一本 3 章的书会被切出几十章。
CHAPTER_LINE_RE = re.compile(
    r"^\s*("
    r"第\s*[0-9０-９一二三四五六七八九十百千万零〇两]{1,10}\s*[章节節回话話]"
    r"|(?:序章|序言|楔子|引子|前言|后记|後記|尾声|尾聲|结局|結局|番外|终章|終章)"
    r"|(?:chapter|part)\s*[0-9]{1,4}"
    r")\s*(?P<rest>[^\n]*)$",
    re.IGNORECASE,
)
# 分卷/分部标题行：``第一部 小丑`` / ``第二卷 风起`` / ``上册``。
# 它们**不生成章节**，而是给后面的章节打上分组（目录里可折叠）。
GROUP_LINE_RE = re.compile(
    r"^\s*("
    r"第\s*[0-9０-９一二三四五六七八九十百千万零〇两]{1,10}\s*[部卷集册篇]"
    r"|(?:上|中|下)(?:部|卷|册|篇)"
    r"|(?:卷|部|篇)\s*[0-9０-９一二三四五六七八九十百千万零〇两]{1,6}"
    r")\s*(?P<rest>[^\n]*)$")
MAX_CHAPTER_TITLE = 30              # 章节标题最长多少字（含"第X章"部分）
MAX_GROUP_TITLE = 36                # 分组标题（"第X部 小丑"这类）上限
NO_SEPARATOR_MAX = 12               # 标题与"第X章"之间没有分隔符时，整行必须更短
# 句子收尾/内部标点：真正的章节标题基本不会出现
SENTENCE_PUNCT = ("。", "！", "？", "…", "；", ";", "，", ",")
SEPARATORS = (" ", "　", "\t", ":", "：", "、", ".", "·", "-", "—", "_")
FALLBACK_CHUNK = 4000               # 认不出章节时每块多少字
# 这些分组名没有信息量（EPUB 的 NCX 常把前置页挂在"正文"下），组内章节少时就拆掉
GENERIC_GROUPS = {"正文", "正文卷", "目录", "text", "book", "body"}
GENERIC_GROUP_MAX = 20
IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp")


def _title_like(line: str, pattern, max_len: int, no_sep_max: int) -> bool:
    """通用的"这行像不像标题"判断（含分隔符宽松、无分隔符收紧）。"""
    line = line.strip()
    if not line or len(line) > max_len:
        return False
    if any(ch in line for ch in SENTENCE_PUNCT):
        return False                       # 带句读 → 是正文句子
    match = pattern.match(line)
    if not match:
        return False
    token = match.group(1)
    rest = (match.group("rest") or "").strip()
    if not rest:
        return True                        # 纯"第二章"/"第一部"
    separator = line[len(token):len(token) + 1] in SEPARATORS
    return len(line) <= max_len if separator else len(line) <= no_sep_max


def is_chapter_title(line: str) -> bool:
    """判断一行是不是章节标题（宁可漏判，也不要把正文行当标题）。"""
    return _title_like(line, CHAPTER_LINE_RE, MAX_CHAPTER_TITLE, NO_SEPARATOR_MAX)


def is_group_title(line: str) -> bool:
    """判断一行是不是"部/卷"这种分组标题。"""
    return _title_like(line, GROUP_LINE_RE, MAX_GROUP_TITLE, 14)


# TXT 开头常见的书籍信息行（很多下载站会写一段）
META_LINE_RE = re.compile(
    r"^\s*(书\s*名|小说名|标\s*题|作\s*者|著\s*者|类\s*别|状\s*态|字\s*数|"
    r"简\s*介|内容简介|来\s*源|整\s*理|更新时间|下载|声明)[：:]\s*(.*)$")
PREAMBLE_KEEP_MIN = 40        # 首个章节标记之前的内容超过这么多字，才当成"前言"保留


def parse_txt_header(preamble: str) -> Tuple[str, str]:
    """从 TXT 开头那段书籍信息里取 ``(书名, 作者)``。"""
    title = author = ""
    for line in preamble.splitlines():
        match = META_LINE_RE.match(line)
        if not match:
            continue
        key = re.sub(r"\s+", "", match.group(1))
        value = match.group(2).strip()
        if not value:
            continue
        if key in ("书名", "小说名", "标题") and not title:
            title = value
        elif key in ("作者", "著者") and not author:
            author = value
    return title, author


def _preamble_is_metadata(preamble: str) -> bool:
    """开头那段是不是"只有书籍信息"——是的话就别单独当一章。"""
    other = 0
    for line in preamble.splitlines():
        line = line.strip()
        if not line or META_LINE_RE.match(line):
            continue
        other += len(line)
    return other < PREAMBLE_KEEP_MIN


def split_txt_chapters(text: str, keep_preamble: Optional[bool] = None) -> List[Dict]:
    """把 TXT 正文切成章节列表 ``[{title, start, end, group}]``（字符偏移，含 start 不含 end）。

    ``第X部 / 第X卷`` 这类行**不生成章节**，而是给后面的章节打上 ``group``，
    目录抽屉据此折叠；章节索引不受影响，所以阅读进度照旧。
    """
    marks: List[Tuple[int, int, str, bool]] = []   # (行首, 行尾, 标题, 是否分组)
    offset = 0
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if is_chapter_title(stripped):
            marks.append((offset, offset + len(line), stripped, False))
        elif is_group_title(stripped):
            marks.append((offset, offset + len(line), stripped, True))
        offset += len(line)

    chapter_marks = [m for m in marks if not m[3]]
    if len(chapter_marks) < 2:
        # 认不出来：按长度切块，保证能读（标题给"第 N 部分"）
        chapters: List[Dict] = []
        for index, start in enumerate(range(0, max(1, len(text)), FALLBACK_CHUNK)):
            chapters.append({"title": f"第 {index + 1} 部分", "group": "",
                             "start": start,
                             "end": min(len(text), start + FALLBACK_CHUNK)})
        return chapters or [{"title": "全文", "group": "", "start": 0, "end": len(text)}]

    chapters = []
    # 第一个标记之前的内容：只有"书籍信息"就直接丢掉，否则保留成「前言」
    if marks[0][0] > 0 and text[:marks[0][0]].strip():
        preamble = text[:marks[0][0]]
        keep = (not _preamble_is_metadata(preamble)) if keep_preamble is None \
            else bool(keep_preamble)
        if keep:
            chapters.append({"title": "前言", "group": "", "start": 0,
                             "end": marks[0][0]})

    group = ""
    pending_start: Optional[int] = None      # 分组行之后、下一章之前的内容起点
    for index, (start, line_end, title, is_group) in enumerate(marks):
        # 下一处标记（章节或分组）都是本段的结束边界；
        # 分组行自身不属于任何章节（它在目录里当标题用）
        end = marks[index + 1][0] if index + 1 < len(marks) else len(text)
        if is_group:
            group = clean_title(title)
            pending_start = line_end           # 卷首语（如果有）留给下一章
            continue
        chapter_start = start if pending_start is None else min(start, pending_start)
        pending_start = None
        chapters.append({"title": clean_title(title), "group": group,
                         "start": chapter_start, "end": end})
    return chapters


def read_txt_chapter(text: str, chapter: Dict, strict: bool = False,
                     keep_short: bool = False) -> List[str]:
    """取出一章的段落（沿用与网文相同的广告过滤）。"""
    chunk = text[int(chapter.get("start", 0)):int(chapter.get("end", 0))]
    title = chapter.get("title", "")
    lines = [ln for ln in chunk.splitlines()]
    # 去掉开头的章节标题行（正文里不再重复显示一遍）
    if lines and lines[0].strip() == title.strip():
        lines = lines[1:]
    paragraphs = clean_lines(lines, keep_short=keep_short, strict=strict)
    return strip_title_echo(paragraphs, title)


def guess_txt_title(path: Path) -> str:
    """没解析出书名时用文件名兜底（去掉常见的下载站后缀）。"""
    name = path.stem
    name = re.sub(r"[（(\[].*?[)）\]]", "", name)
    name = re.sub(r"(全本|完结|校对版|精校版|txt|下载|最新章节|无弹窗)", "", name,
                  flags=re.IGNORECASE)
    name = re.sub(r"[_\-\s]+$", "", name.strip())
    return name or path.stem


def _dissolve_generic_groups(chapters: List[Dict]) -> List[Dict]:
    """把"正文/目录"这类没有信息量的通用分组拆掉（这些书里的前置页不该被折叠），
    但组里章节很多时保留（那种情况分组是有意义的）。
    """
    counts: Dict[str, int] = {}
    for chapter in chapters:
        group = chapter.get("group") or ""
        if group:
            counts[group] = counts.get(group, 0) + 1
    for chapter in chapters:
        group = chapter.get("group") or ""
        if group in GENERIC_GROUPS and counts.get(group, 0) < GENERIC_GROUP_MAX:
            chapter["group"] = ""
    return chapters


def parse_txt(path: Path) -> Dict:
    """解析 TXT：返回 ``{title, author, encoding, chapters, char_count}``。

    书名/作者优先取文件开头的"书名：/作者："信息，取不到再用文件名。
    """
    raw = path.read_bytes()
    text, encoding = decode_text(raw, declared_encoding(raw))
    chapters = _dissolve_generic_groups(split_txt_chapters(text))
    head_title, head_author = parse_txt_header(text[:2000])
    return {
        "title": head_title or guess_txt_title(path),
        "author": head_author,
        "encoding": encoding,
        "chapters": chapters,
        "char_count": len(text),
    }


# --------------------------------------------------------------------------- EPUB
def _local_name(tag: str) -> str:
    """去掉 XML 命名空间前缀。"""
    return tag.rsplit("}", 1)[-1].lower()


def _find_all(root, name: str) -> List:
    return [el for el in root.iter() if _local_name(el.tag) == name]


def _find_first(root, name: str):
    found = _find_all(root, name)
    return found[0] if found else None


def _read_zip_text(archive: zipfile.ZipFile, name: str) -> str:
    """读 zip 内的文本文件：**优先按文件自己声明的编码解**（EPUB 里的 HTML 大多声明 utf-8）。"""
    raw = archive.read(name)
    text, _ = decode_text(raw, declared_encoding(raw))
    return text


def parse_epub(path: Path) -> Dict:
    """解析 EPUB：返回 ``{title, author, chapters, cover, encoding}``。

    只读 OPF 的元数据与 spine 顺序，正文按需再读（见 :func:`read_epub_chapter`）。
    """
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        container = _find_text(archive, names, "META-INF/container.xml")
        opf_path = ""
        if container:
            root = ElementTree.fromstring(container)
            rootfile = _find_first(root, "rootfile")
            if rootfile is not None:
                opf_path = (rootfile.get("full-path") or "").replace("\\", "/")
        if not opf_path or opf_path not in names:
            opf_path = next((n for n in names if n.lower().endswith(".opf")), "")
        if not opf_path:
            raise ValueError("EPUB 里找不到 OPF 文件，可能不是标准电子书")

        opf = ElementTree.fromstring(_read_zip_text(archive, opf_path))
        base_dir = opf_path.rsplit("/", 1)[0] + "/" if "/" in opf_path else ""

        title = author = ""
        metadata = _find_first(opf, "metadata")
        if metadata is not None:
            title_el = _find_first(metadata, "title")
            creator_el = _find_first(metadata, "creator")
            title = (title_el.text or "").strip() if title_el is not None else ""
            author = (creator_el.text or "").strip() if creator_el is not None else ""

        manifest: Dict[str, Dict[str, str]] = {}
        manifest_el = _find_first(opf, "manifest")
        if manifest_el is not None:
            for item in _find_all(manifest_el, "item"):
                item_id = item.get("id") or ""
                manifest[item_id] = {"href": (item.get("href") or "").replace("\\", "/"),
                                     "type": (item.get("media-type") or "").lower(),
                                     "properties": (item.get("properties") or "").lower()}

        spine_ids: List[str] = []
        spine_el = _find_first(opf, "spine")
        if spine_el is not None:
            for ref in _find_all(spine_el, "itemref"):
                if (ref.get("linear") or "yes").lower() == "no":
                    continue
                spine_ids.append(ref.get("idref") or "")

        chapters: List[Dict] = []
        for item_id in spine_ids:
            item = manifest.get(item_id)
            if not item or "html" not in item["type"]:
                continue
            href = _join_zip_path(base_dir, item["href"])
            if href not in names:
                continue
            chapters.append({"title": "", "group": "", "href": href})
        if not chapters:
            raise ValueError("EPUB 里没有可读的正文（spine 为空）")

        # 标题与分组：优先用目录（NCX / nav）——它是权威的，还带"部/卷"层级；
        # 目录里没有的章节才退回从 HTML 里猜 h1~h3 / <title> / "第 N 章"。
        toc = parse_epub_toc(archive, names, manifest, base_dir)
        for index, chapter in enumerate(chapters):
            entry = toc.get(chapter["href"])
            if entry and entry.get("title"):
                chapter["title"] = clean_title(entry["title"]) or f"第 {index + 1} 章"
                chapter["group"] = clean_title(entry.get("group", ""))
                continue
            heading = _epub_heading(archive, chapter["href"])
            chapter["title"] = clean_title(heading) or f"第 {index + 1} 章"

        cover = _epub_cover_bytes(archive, names, manifest, base_dir)
        return {
            "title": title or path.stem,
            "author": author,
            "chapters": _dissolve_generic_groups(chapters),
            "cover": cover,
            "images": _epub_image_list(archive, names, manifest, base_dir),
            "encoding": "utf-8",
            "toc_source": "ncx/nav" if toc else "",
        }


def _find_text(archive: zipfile.ZipFile, names: List[str], want: str) -> str:
    for name in names:
        if name.lower() == want.lower():
            return _read_zip_text(archive, name)
    return ""


def _join_zip_path(base_dir: str, href: str) -> str:
    """把相对 href 解析成 zip 内的完整路径（处理 ``../``）。"""
    if href.startswith("/"):
        return href.lstrip("/")
    parts = (base_dir + href).split("/")
    stack: List[str] = []
    for part in parts:
        if part in ("", "."):
            continue
        if part == "..":
            if stack:
                stack.pop()
            continue
        stack.append(part)
    return "/".join(stack)


# --------------------------------------------------------------------------- 目录（NCX / nav）
def _toc_href(src: str, base_dir: str) -> str:
    """把目录里的 src（可能带 #锚点）转成 zip 内路径。"""
    src = (src or "").split("#", 1)[0]
    return _join_zip_path(base_dir, src) if src else ""


def _parse_ncx_toc(archive: zipfile.ZipFile, ncx_path: str, base_dir: str) -> Dict[str, Dict]:
    """解析 EPUB2 的 NCX：返回 ``{href: {title, group, is_group}}``。

    NCX 的 ``navPoint`` 是嵌套的：有子节点的就是"部/卷"，叶子是章节。
    这比从每个 HTML 里猜标题准得多（真实书里 HTML 的 title 常是 "chapter 10 - 0"）。
    """
    result: Dict[str, Dict] = {}
    try:
        root = ElementTree.fromstring(_read_zip_text(archive, ncx_path))
    except (ElementTree.ParseError, KeyError, OSError):
        return result

    def label_of(node) -> str:
        for child in node:
            if _local_name(child.tag) == "navlabel":
                for text in child:
                    if _local_name(text.tag) == "text":
                        return (text.text or "").strip()
        return ""

    def src_of(node) -> str:
        for child in node:
            if _local_name(child.tag) == "content":
                return (child.get("src") or "").strip()
        return ""

    def walk(node, group: str) -> None:
        for child in node:
            if _local_name(child.tag) != "navpoint":
                continue
            title = label_of(child)
            href = _toc_href(src_of(child), base_dir)
            kids = [c for c in child if _local_name(c.tag) == "navpoint"]
            if kids:
                # 有子节点 → 它是"部/卷"这一层：自己不算章节，给子节点当分组名
                if href:
                    result[href] = {"title": title, "group": title, "is_group": True}
                walk(child, title)
            elif href:
                result[href] = {"title": title, "group": group, "is_group": False}

    # 关键：navPoint 都挂在 <navMap> 下面（<ncx><navMap><navPoint>…），
    # 直接从根节点找 navPoint 会一个都找不到（真实踩过）。
    nav_map = next((el for el in root.iter() if _local_name(el.tag) == "navmap"), root)
    walk(nav_map, "")
    return result


def _parse_nav_toc(archive: zipfile.ZipFile, nav_path: str, base_dir: str) -> Dict[str, Dict]:
    """解析 EPUB3 的 nav.xhtml（``<nav epub:type="toc"><ol>`` 嵌套）。"""
    result: Dict[str, Dict] = {}
    try:
        soup = make_soup(_read_zip_text(archive, nav_path))
    except (KeyError, OSError):
        return result
    nav = None
    for candidate in soup.find_all("nav"):
        types = " ".join(str(candidate.get(attr, "")) for attr in
                         ("epub:type", "type", "role"))
        if "toc" in types.lower() or nav is None:
            nav = candidate
            if "toc" in types.lower():
                break
    if nav is None:
        return result
    root_list = nav.find("ol")
    if root_list is None:
        return result

    def walk(list_node, group: str) -> None:
        for item in list_node.find_all("li", recursive=False):
            link = item.find("a")
            title = link.get_text(" ", strip=True) if link is not None else ""
            href = _toc_href(link.get("href", "") if link is not None else "", base_dir)
            sub = item.find("ol")
            if sub is not None:
                if href:
                    result[href] = {"title": title, "group": title, "is_group": True}
                walk(sub, title)
            elif href:
                result[href] = {"title": title, "group": group, "is_group": False}

    walk(root_list, "")
    return result


def parse_epub_toc(archive: zipfile.ZipFile, names: List[str],
                   manifest: Dict[str, Dict[str, str]], base_dir: str) -> Dict[str, Dict]:
    """按优先级取目录：NCX（EPUB2）→ nav.xhtml（EPUB3）。"""
    for item in manifest.values():
        if "dtbncx" in item["type"]:
            path = _join_zip_path(base_dir, item["href"])
            if path in names:
                toc = _parse_ncx_toc(archive, path, base_dir)
                if toc:
                    return toc
    for name in names:
        if name.lower().endswith(".ncx"):
            toc = _parse_ncx_toc(archive, name, name.rsplit("/", 1)[0] + "/")
            if toc:
                return toc
    for item in manifest.values():
        if "nav" in item["properties"] and "html" in item["type"]:
            path = _join_zip_path(base_dir, item["href"])
            if path in names:
                toc = _parse_nav_toc(archive, path, base_dir)
                if toc:
                    return toc
    return {}


def _epub_heading(archive: zipfile.ZipFile, href: str) -> str:
    try:
        html = _read_zip_text(archive, href)
    except (KeyError, OSError):
        return ""
    soup = make_soup(html)
    for tag in ("h1", "h2", "h3", "h4"):
        node = soup.find(tag)
        if node is not None:
            text = node.get_text(" ", strip=True)
            if text and len(text) <= MAX_CHAPTER_TITLE:
                return text
    if soup.title is not None and soup.title.string:
        return soup.title.string.strip()[:MAX_CHAPTER_TITLE]
    return ""


def _epub_cover_bytes(archive: zipfile.ZipFile, names: List[str],
                      manifest: Dict[str, Dict[str, str]], base_dir: str) -> bytes:
    """找封面图：``properties="cover-image"`` → ``<meta name="cover">`` → 名字带 cover 的图。"""
    candidate = ""
    for item in manifest.values():
        if "cover-image" in item["properties"] and "image" in item["type"]:
            candidate = _join_zip_path(base_dir, item["href"])
            break
    if not candidate:
        for name in names:
            lowered = name.lower()
            if "cover" in lowered and lowered.endswith((".jpg", ".jpeg", ".png", ".gif", ".webp")):
                candidate = name
                break
    if candidate and candidate in names:
        try:
            return archive.read(candidate)
        except (KeyError, OSError):     # pragma: no cover
            return b""
    return b""


def _epub_image_list(archive: zipfile.ZipFile, names: List[str],
                     manifest: Dict[str, Dict[str, str]],
                     base_dir: str) -> List[Dict]:
    """本书所有插图清单（带**真实字节数**），供「本书插图」入口展示。

    注意：这里列的是**包里存在的图片**，不等于正文引用了它们——
    很多 EPUB 把插图放进包里却没在 HTML 里引用，正文里看不到，只能在这里看。
    体积要读 zip 条目（OPF 的 manifest 里没有 size，之前取它一律显示 0 B）。
    """
    result = []
    for name in names:
        if Path(name).suffix.lower() not in IMAGE_SUFFIXES:
            continue
        try:
            size = archive.getinfo(name).file_size
        except KeyError:                       # pragma: no cover - 防御
            size = 0
        result.append({"path": name, "name": Path(name).name, "size": int(size)})
    return result


# 正文里内嵌图片的占位：清洗管线里用"对象替换字符 + 序号"，
# 交给阅读器前统一变成单个 U+FFFC（Qt 插入图片就用它，只占 1 个字符，
# 所以分页/进度那套字符偏移计算完全不受图片影响）。
# 为什么不用 `@@IMG0@@` 这种纯 ASCII 标记：**严格广告过滤会丢掉整行纯 ASCII**，
# 图片占位会被一起清掉（真实踩过）。带序号还能避免"连续两行相同被去重"。
IMAGE_CHAR = "\ufffc"
IMAGE_PLACEHOLDER_RE = re.compile(r"^\ufffc(\d+)\ufffc$")


def _image_placeholder(slot: int) -> str:
    return f"{IMAGE_CHAR}{slot}{IMAGE_CHAR}"


def _extract_images(soup, base_dir: str) -> List[str]:
    """把正文里的 ``<img>`` 换成占位段落，返回**按文档顺序**排列的 zip 内路径。

    两点注意：
    * 必须在清洗**之前**替换，这样图片在段落流里的位置才准；
    * ``base_dir`` 必须是**这个 XHTML 文件所在目录**（不是 OPF 所在目录）——
      ``src="../Images/c1.png"`` 相对文档解析（真实书里踩过：用 OPF 目录会算错一层）。
    """
    paths: List[str] = []
    for slot, node in enumerate(soup.find_all(["img", "image"])):
        src = (node.get("src") or node.get("xlink:href")
               or node.get("{http://www.w3.org/1999/xlink}href") or "").strip()
        paths.append(_join_zip_path(base_dir, src) if src else "")
        holder = soup.new_tag("p")
        holder.string = _image_placeholder(slot)
        node.insert_after(holder)
        node.decompose()
    return paths


def read_epub_chapter(path: Path, chapter: Dict, strict: bool = False,
                      keep_short: bool = False) -> List[str]:
    """读一章 EPUB 的**文字**（图片见 :func:`read_epub_chapter_rich`）。"""
    return read_epub_chapter_rich(path, chapter, strict=strict,
                                  keep_short=keep_short)[0]


def read_epub_chapter_rich(path: Path, chapter: Dict, strict: bool = False,
                           keep_short: bool = False) -> Tuple[List[str], Dict[int, bytes]]:
    """读一章 EPUB，返回 ``(段落, {段落序号: 图片字节})``。

    图片按在正文里的位置插入（段落值就是 ``U+FFFC``），阅读器据此渲染成图。
    """
    href = chapter.get("href", "")
    if not href:
        return [], {}
    with zipfile.ZipFile(path) as archive:
        try:
            raw = archive.read(href)
        except (KeyError, OSError):
            return [], {}
        soup = strip_noise(make_soup(decode_text(raw, declared_encoding(raw))[0]))
        base_dir = href.rsplit("/", 1)[0] + "/" if "/" in href else ""
        image_paths = _extract_images(soup, base_dir)
        body = soup.body or soup
        paragraphs = extract_epub_paragraphs(body, keep_short=keep_short, strict=strict)

        images: Dict[int, bytes] = {}
        result: List[str] = []
        for text in paragraphs:
            match = IMAGE_PLACEHOLDER_RE.match(text.strip())
            if not match:
                result.append(text)
                continue
            slot = int(match.group(1))
            target = image_paths[slot] if 0 <= slot < len(image_paths) else ""
            data = b""
            if target:
                try:
                    data = archive.read(target)
                except (KeyError, OSError):
                    data = b""
            if not data:
                continue                      # 图丢了就不占位置，正文照常
            images[len(result)] = data
            result.append(IMAGE_CHAR)
    return strip_title_echo(result, chapter.get("title", "")), images


def extract_epub_paragraphs(node, keep_short: bool = False,
                            strict: bool = False) -> List[str]:
    """EPUB 正文取段落：整篇就是一个大 <div> 时也要能正确断句。"""
    from .cleaner import extract_paragraphs

    paragraphs = extract_paragraphs(node, keep_short=keep_short, strict=strict)
    # 有些 EPUB 把所有正文塞进一个 <p>，只切出一段；按换行再切一次
    if len(paragraphs) == 1 and len(paragraphs[0]) > 200:
        lines = re.split(r"[\r\n]+", paragraphs[0])
        paragraphs = clean_lines(lines, keep_short=keep_short, strict=strict)
    return paragraphs


# --------------------------------------------------------------------------- 统一入口
def parse_book(path: Path) -> Dict:
    """按扩展名解析本地电子书，返回统一的元数据字典。"""
    suffix = path.suffix.lower()
    if suffix == ".epub":
        data = parse_epub(path)
        data["format"] = "epub"
    elif suffix == ".txt":
        data = parse_txt(path)
        data["format"] = "txt"
    else:
        raise ValueError(f"暂不支持的文件类型：{path.suffix or '（无扩展名）'}")
    if not data.get("title"):
        data["title"] = path.stem
    return data


def read_chapter(path: Path, data: Dict, chapter: Dict, strict: bool = False,
                 keep_short: bool = False) -> List[str]:
    """按记录里的格式读一章正文（只要文字）。"""
    return read_chapter_rich(path, data, chapter, strict=strict,
                             keep_short=keep_short)[0]


def read_chapter_rich(path: Path, data: Dict, chapter: Dict, strict: bool = False,
                      keep_short: bool = False) -> Tuple[List[str], Dict[int, bytes]]:
    """按记录里的格式读一章，返回 ``(段落, {段落序号: 图片字节})``。"""
    if data.get("format") == "epub":
        return read_epub_chapter_rich(path, chapter, strict=strict,
                                      keep_short=keep_short)
    raw = path.read_bytes()
    text, _ = decode_text(raw, declared_encoding(raw))
    return read_txt_chapter(text, chapter, strict=strict,
                            keep_short=keep_short), {}

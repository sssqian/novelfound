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
# 中文 TXT 的常见编码，按命中概率排序
FALLBACK_ENCODINGS = ("utf-8", "gb18030", "big5", "utf-16", "latin-1")


def detect_encoding(raw: bytes) -> str:
    """猜文本编码：先看 BOM，再依次试 utf-8 / gb18030 / big5。"""
    for bom, encoding in BOM_ENCODINGS:
        if raw.startswith(bom):
            return encoding
    head = raw[:4096]
    for encoding in ("utf-8", "gb18030", "big5"):
        try:
            head.decode(encoding)
            return encoding
        except UnicodeDecodeError:
            continue
    return "gb18030"          # 兜底：GB18030 几乎能解出所有字节序列


def decode_text(raw: bytes) -> Tuple[str, str]:
    """返回 ``(文本, 使用的编码)``。"""
    encoding = detect_encoding(raw)
    try:
        return raw.decode(encoding, errors="replace"), encoding
    except LookupError:       # pragma: no cover - 系统缺该编码时兜底
        return raw.decode("utf-8", errors="replace"), "utf-8"


# --------------------------------------------------------------------------- TXT
# 章节标题行：``第12章 风起`` / ``第十二回`` / ``楔子`` / ``番外`` / ``Chapter 3``。
# 关键是把"正文里出现第X章"的句子排除掉，否则一本 3 章的书会被切出几十章。
CHAPTER_LINE_RE = re.compile(
    r"^\s*("
    r"第\s*[0-9０-９一二三四五六七八九十百千万零〇两]{1,10}\s*[章节節回卷集话話篇部]"
    r"|(?:序章|序言|楔子|引子|前言|后记|後記|尾声|尾聲|结局|結局|番外|终章|終章)"
    r"|(?:chapter|part)\s*[0-9]{1,4}"
    r")\s*(?P<rest>[^\n]*)$",
    re.IGNORECASE,
)
VOLUME_LINE_RE = re.compile(
    r"^\s*(第\s*[0-9０-９一二三四五六七八九十百千万零〇两]{1,10}\s*[卷部])\s*(?P<rest>[^\n]*)$")
MAX_CHAPTER_TITLE = 30              # 章节标题最长多少字（含"第X章"部分）
NO_SEPARATOR_MAX = 12               # 标题与"第X章"之间没有分隔符时，整行必须更短
# 句子收尾/内部标点：真正的章节标题基本不会出现
SENTENCE_PUNCT = ("。", "！", "？", "…", "；", ";", "，", ",")
SEPARATORS = (" ", "　", "\t", ":", "：", "、", ".", "·", "-", "—", "_")
FALLBACK_CHUNK = 4000               # 认不出章节时每块多少字


def is_chapter_title(line: str) -> bool:
    """判断一行是不是章节标题（宁可漏判，也不要把正文行当标题）。"""
    line = line.strip()
    if not line or len(line) > MAX_CHAPTER_TITLE:
        return False
    if any(ch in line for ch in SENTENCE_PUNCT):
        return False                       # 带句读 → 是正文句子
    match = CHAPTER_LINE_RE.match(line) or VOLUME_LINE_RE.match(line)
    if not match:
        return False
    token = match.group(1)
    rest = (match.group("rest") or "").strip()
    if not rest:
        return True                        # 纯"第二章"
    # "第二章 风起" 有分隔符；"第二章风起" 没分隔符 → 要求整行更短
    separator = line[len(token):len(token) + 1] in SEPARATORS
    return len(line) <= MAX_CHAPTER_TITLE if separator else len(line) <= NO_SEPARATOR_MAX


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
    """把 TXT 正文切成章节列表 ``[{title, start, end}]``（字符偏移，含 start 不含 end）。"""
    marks: List[Tuple[int, str]] = []
    offset = 0
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if is_chapter_title(stripped):
            marks.append((offset, stripped))
        offset += len(line)

    if len(marks) < 2:
        # 认不出来：按长度切块，保证能读（标题给"第 N 部分"）
        chapters: List[Dict] = []
        for index, start in enumerate(range(0, max(1, len(text)), FALLBACK_CHUNK)):
            chapters.append({"title": f"第 {index + 1} 部分",
                             "start": start,
                             "end": min(len(text), start + FALLBACK_CHUNK)})
        return chapters or [{"title": "全文", "start": 0, "end": len(text)}]

    chapters = []
    # 第一个标记之前的内容：只有"书籍信息"就直接丢掉，否则保留成「前言」
    if marks[0][0] > 0 and text[:marks[0][0]].strip():
        preamble = text[:marks[0][0]]
        keep = (not _preamble_is_metadata(preamble)) if keep_preamble is None \
            else bool(keep_preamble)
        if keep:
            chapters.append({"title": "前言", "start": 0, "end": marks[0][0]})
    for index, (start, title) in enumerate(marks):
        end = marks[index + 1][0] if index + 1 < len(marks) else len(text)
        chapters.append({"title": clean_title(title), "start": start, "end": end})
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


def parse_txt(path: Path) -> Dict:
    """解析 TXT：返回 ``{title, author, encoding, chapters, char_count}``。

    书名/作者优先取文件开头的"书名：/作者："信息，取不到再用文件名。
    """
    raw = path.read_bytes()
    text, encoding = decode_text(raw)
    chapters = split_txt_chapters(text)
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
    raw = archive.read(name)
    text, _ = decode_text(raw)
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
            chapters.append({"title": "", "href": href})
        if not chapters:
            raise ValueError("EPUB 里没有可读的正文（spine 为空）")

        # 每章标题：优先取正文里的第一个 h1~h3，其次 <title>，最后"第 N 章"
        for index, chapter in enumerate(chapters):
            heading = _epub_heading(archive, chapter["href"])
            chapter["title"] = clean_title(heading) or f"第 {index + 1} 章"

        cover = _epub_cover_bytes(archive, names, manifest, base_dir)
        return {
            "title": title or path.stem,
            "author": author,
            "chapters": chapters,
            "cover": cover,
            "encoding": "utf-8",
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


def read_epub_chapter(path: Path, chapter: Dict, strict: bool = False,
                      keep_short: bool = False) -> List[str]:
    """读一章 EPUB 正文（走与网文相同的清洗管线）。"""
    href = chapter.get("href", "")
    if not href:
        return []
    with zipfile.ZipFile(path) as archive:
        try:
            html = _read_zip_text(archive, href)
        except (KeyError, OSError):
            return []
    soup = strip_noise(make_soup(html))
    body = soup.body or soup
    paragraphs = extract_epub_paragraphs(body, keep_short=keep_short, strict=strict)
    return strip_title_echo(paragraphs, chapter.get("title", ""))


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
    """按记录里的格式读一章正文。"""
    if data.get("format") == "epub":
        return read_epub_chapter(path, chapter, strict=strict, keep_short=keep_short)
    raw = path.read_bytes()
    text, _ = decode_text(raw)
    return read_txt_chapter(text, chapter, strict=strict, keep_short=keep_short)

# -*- coding: utf-8 -*-
"""数据模型。

所有跨模块传递的数据都使用这里定义的轻量数据类，避免在各层之间传递裸 dict，
也让书源适配层与界面层保持解耦。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class Book:
    """搜索结果 / 详情页共用的书籍信息。"""

    title: str = ""
    author: str = ""
    url: str = ""                 # 详情页地址
    cover_url: str = ""           # 封面图地址（可能为空）
    intro: str = ""               # 简介
    source: str = ""              # 书源 key
    source_name: str = ""         # 书源显示名
    category: str = ""            # 分类
    status: str = ""              # 连载 / 完结
    latest_chapter: str = ""      # 最新章节标题
    updated_at: str = ""          # 更新时间
    word_count: str = ""          # 字数
    extra: Dict[str, str] = field(default_factory=dict)

    @property
    def key(self) -> str:
        """全局唯一标识（书源 + 详情页地址）。"""
        return f"{self.source}|{self.url}"

    @classmethod
    def from_record(cls, record: Dict) -> "Book":
        """从书架/历史记录（dict）还原成 Book。"""
        record = record or {}
        return cls(
            title=record.get("title", ""), author=record.get("author", ""),
            url=record.get("url", ""), cover_url=record.get("cover_url", ""),
            intro=record.get("intro", ""), source=record.get("source", ""),
            source_name=record.get("source_name", ""),
            category=record.get("category", ""), status=record.get("status", ""),
            latest_chapter=record.get("latest_chapter", ""))

    def merge(self, other: "Book") -> "Book":
        """用更完整的信息补齐当前对象（例如用详情页信息补全搜索结果）。"""
        for name in ("title", "author", "url", "cover_url", "intro", "category",
                     "status", "latest_chapter", "updated_at", "word_count"):
            new = getattr(other, name, "")
            if new and not getattr(self, name, ""):
                setattr(self, name, new)
        if other.extra:
            merged = dict(self.extra)
            merged.update(other.extra)
            self.extra = merged
        return self


@dataclass
class Chapter:
    """章节目录项。"""

    title: str = ""
    url: str = ""
    index: int = 0
    group: str = ""               # 所属"部/卷"分组（本地书从目录解析，网文一般为空）

    @property
    def display_title(self) -> str:
        return self.title or f"第 {self.index + 1} 章"


@dataclass
class BookDetail:
    """书籍详情：书籍信息 + 完整目录。"""

    book: Book
    chapters: List[Chapter] = field(default_factory=list)

    def find_index(self, chapter_url: str) -> int:
        for i, c in enumerate(self.chapters):
            if c.url == chapter_url:
                return i
        return -1


@dataclass
class ChapterContent:
    """章节正文（已清洗，纯文本段落）。

    ``images`` 是"内嵌图片"：键是段落序号，值是图片原始字节。
    对应的段落内容固定是 ``U+FFFC``（对象替换字符，只占 1 个字符），
    这样分页、进度、位置记忆用的字符偏移完全不受图片影响。
    """

    title: str = ""
    paragraphs: List[str] = field(default_factory=list)
    url: str = ""
    prev_url: str = ""
    next_url: str = ""
    from_cache: bool = False
    images: Dict[int, bytes] = field(default_factory=dict)

    @property
    def text(self) -> str:
        return "\n".join(self.paragraphs)

    @property
    def char_count(self) -> int:
        return sum(len(p) for p in self.paragraphs)


@dataclass
class SearchOutcome:
    """单个书源的搜索返回结果（成功或失败都返回，便于界面聚合展示）。"""

    source: str = ""
    source_name: str = ""
    books: List[Book] = field(default_factory=list)
    error: str = ""
    elapsed: float = 0.0

    @property
    def ok(self) -> bool:
        return not self.error


def normalize_cover(url: str, base: str = "") -> str:
    """把相对封面地址补全为绝对地址。"""
    if not url:
        return ""
    url = url.strip()
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("http"):
        return url
    if url.startswith("/") and base:
        return base.rstrip("/") + url
    if base:
        return base.rstrip("/") + "/" + url.lstrip("/")
    return url


def absolutize(url: str, page_url: str) -> str:
    """把目录/章节相对链接补全为绝对地址。"""
    if not url:
        return ""
    url = url.strip()
    if url.startswith("http"):
        return url
    if url.startswith("//"):
        return "https:" + url
    if not page_url:
        return url
    return urllib_join(page_url, url)


def urllib_join(base: str, rel: str) -> str:
    from urllib.parse import urljoin
    return urljoin(base, rel)


def clean_text(value: Optional[str]) -> str:
    """压缩空白、去掉常见占位符。"""
    if not value:
        return ""
    text = value.replace("\u3000", " ").replace("\xa0", " ")
    text = text.replace("\r", "\n")
    lines = [ln.strip() for ln in text.split("\n")]
    lines = [ln for ln in lines if ln]
    return "\n".join(lines).strip()

# -*- coding: utf-8 -*-
"""书源抽象接口。

新增书源只需要继承 :class:`BaseSource` 并实现 search / fetch_detail / fetch_chapter，
或者更简单地往 ``builtin.py`` 的规则表里加一条配置（推荐）。
"""
from __future__ import annotations

from typing import List, Tuple

from ..models import Book, BookDetail, Chapter, ChapterContent
from ..net import HttpSession


class BaseSource:
    """书源基类。"""

    key: str = "base"          # 唯一标识（配置里用它来启用/禁用）
    name: str = "未命名书源"    # 界面显示名
    base_url: str = ""         # 站点根地址
    enabled_by_default: bool = True
    note: str = ""             # 备注（显示在书源管理里）

    def __init__(self, http: HttpSession):
        self.http = http

    # ------------------------------------------------------------ 需要实现的方法
    def search(self, keyword: str, limit: int = 30) -> List[Book]:
        raise NotImplementedError

    def fetch_detail(self, book: Book) -> BookDetail:
        raise NotImplementedError

    def fetch_chapter(self, book: Book, chapter: Chapter) -> ChapterContent:
        raise NotImplementedError

    # ---------------------------------------------------------------- 可选能力
    def health_check(self) -> Tuple[bool, str]:
        """探测站点是否可访问，返回 (是否可用, 说明)。"""
        try:
            html = self.http.get_text(self.base_url, timeout=10)
            if len(html) < 200:
                return False, "站点返回内容过短，可能被拦截"
            return True, f"可访问（{len(html)} 字节）"
        except Exception as exc:  # noqa: BLE001 - 健康检查不抛出异常
            return False, str(exc)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<{self.__class__.__name__} {self.key}>"

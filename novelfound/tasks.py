# -*- coding: utf-8 -*-
"""异步任务层。

GUI 里所有网络操作都通过这里的任务类执行，保证界面永不卡死：

* :class:`SearchTask`   多书源并发搜索（按书源逐个回报结果）
* :class:`DetailTask`   抓取书籍详情与目录
* :class:`ChapterTask`  抓取章节正文（优先命中本地缓存）
* :class:`CoverTask`    下载封面图片
* :class:`HealthTask`   书源可用性检测

所有任务都继承 :class:`BaseTask`，把底层异常统一翻译成
``failed(message, detail)`` 信号，界面只需展示中文提示。
"""
from __future__ import annotations

import time
import traceback
from pathlib import Path
from typing import Any, List, Optional, Sequence

from PyQt5.QtCore import QObject, QRunnable, QThreadPool, pyqtSignal

from .cache import Cache
from .config import AppConfig
from .localbooks import LocalBooks
from .models import Book, BookDetail, Chapter, ChapterContent, SearchOutcome
from .net import HttpSession, NovelError
from .sources import BaseSource
from .sources.discover import DiscoveryOutcome, discover_sources
from .sources.importer import ParsedRule, parse_payload
from .sources.probe import ProbeOutcome, probe_domain


class TaskSignals(QObject):
    """任务信号集合（在工作线程 emit，Qt 自动排队到主线程）。"""

    finished = pyqtSignal(object)          # 最终结果
    failed = pyqtSignal(str, str)          # (用户可读消息, 详情)
    partial = pyqtSignal(object)           # 增量结果（多源搜索）
    progress = pyqtSignal(str)             # 进度文本
    elapsed = pyqtSignal(float)            # 任务耗时（秒）


class BaseTask(QRunnable):
    """任务基类：统一异常处理与信号发射。"""

    def __init__(self) -> None:
        super().__init__()
        self.signals = TaskSignals()
        self.setAutoDelete(False)   # 由 TaskManager 负责回收，避免信号对象提前销毁
        self.cancelled = False

    # ------------------------------------------------------------------ 子类实现
    def work(self) -> Any:
        raise NotImplementedError

    def cancel(self) -> None:
        self.cancelled = True

    # ---------------------------------------------------------------- 执行入口
    def run(self) -> None:  # noqa: D401 - QRunnable 接口
        started = time.time()
        try:
            result = self.work()
        except NovelError as exc:
            if not self.cancelled:
                self.signals.failed.emit(exc.message, exc.detail)
        except Exception as exc:  # noqa: BLE001 - 兜底，避免线程静默崩溃
            if not self.cancelled:
                self.signals.failed.emit(
                    "出现未预期的错误，已记录详情", f"{type(exc).__name__}: {exc}")
                traceback.print_exc()
        else:
            if not self.cancelled:
                self.signals.finished.emit(result)
        finally:
            self.signals.elapsed.emit(time.time() - started)


class TaskManager:
    """任务管理器：持有运行中任务的引用并统一调度。"""

    def __init__(self, max_threads: int = 8):
        self.pool = QThreadPool()
        self.pool.setMaxThreadCount(max(2, max_threads))
        self._running: List[BaseTask] = []

    def start(self, task: BaseTask) -> BaseTask:
        self._running.append(task)
        task.signals.finished.connect(lambda *_: self._forget(task))
        task.signals.failed.connect(lambda *_: self._forget(task))
        self.pool.start(task)
        return task

    def _forget(self, task: BaseTask) -> None:
        if task in self._running:
            self._running.remove(task)

    def cancel_all(self) -> None:
        for task in list(self._running):
            task.cancel()
        self._running.clear()

    def wait(self, timeout_ms: int = 3000) -> bool:
        return self.pool.waitForDone(timeout_ms)


# ---------------------------------------------------------------------------
# 具体任务
# ---------------------------------------------------------------------------
class SearchTask(BaseTask):
    """在多个书源上搜索关键词，逐源回报结果。"""

    def __init__(self, http: HttpSession, sources: Sequence[BaseSource],
                 keyword: str, limit: int = 30, stats=None):
        super().__init__()
        self.http = http
        self.sources = list(sources)
        self.keyword = keyword
        self.limit = limit
        self.stats = stats

    def work(self) -> List[SearchOutcome]:
        outcomes: List[SearchOutcome] = []
        for index, source in enumerate(self.sources):
            if self.cancelled:
                break
            self.signals.progress.emit(f"正在搜索：{source.name} "
                                       f"（{index + 1}/{len(self.sources)}）")
            started = time.time()
            try:
                books = source.search(self.keyword, limit=self.limit)
                outcome = SearchOutcome(source=source.key, source_name=source.name,
                                        books=books, elapsed=time.time() - started)
            except NovelError as exc:
                outcome = SearchOutcome(source=source.key, source_name=source.name,
                                        error=exc.message, elapsed=time.time() - started)
            except Exception as exc:  # noqa: BLE001
                outcome = SearchOutcome(source=source.key, source_name=source.name,
                                        error=f"未预期错误：{type(exc).__name__}",
                                        elapsed=time.time() - started)
            if self.stats is not None:
                self.stats.record(source.key, ok=not outcome.error,
                                  elapsed=outcome.elapsed, error=outcome.error)
            outcomes.append(outcome)
            self.signals.partial.emit(outcome)
        return outcomes


class DetailTask(BaseTask):
    """抓取书籍详情与目录（命中缓存时直接返回）。"""

    def __init__(self, source: BaseSource, book: Book, cache: Optional[Cache] = None,
                 force: bool = False, stats=None):
        super().__init__()
        self.source = source
        self.book = book
        self.cache = cache
        self.force = force
        self.stats = stats

    def work(self) -> BookDetail:
        if self.cache is not None and not self.force:
            cached = self.cache.get_detail(self.source.key, self.book.url)
            if cached:
                self.signals.progress.emit("使用本地缓存的目录")
                return _detail_from_cache(cached, self.book, self.source)
        started = time.time()
        try:
            detail = self.source.fetch_detail(self.book)
        except Exception as exc:
            if self.stats is not None:
                self.stats.record(self.source.key, ok=False,
                                  elapsed=time.time() - started,
                                  error=str(getattr(exc, "message", exc)))
            raise
        if self.stats is not None:
            self.stats.record(self.source.key, ok=True, elapsed=time.time() - started)
        if self.cache is not None:
            self.cache.put_detail(self.source.key, self.book.url,
                                  _detail_to_cache(detail))
        return detail


class ChapterTask(BaseTask):
    """抓取章节正文；优先读缓存，成功后写回缓存。"""

    def __init__(self, source: BaseSource, book: Book, chapter: Chapter,
                 cache: Optional[Cache] = None, force: bool = False, stats=None):
        super().__init__()
        self.source = source
        self.book = book
        self.chapter = chapter
        self.cache = cache
        self.force = force
        self.stats = stats

    def work(self) -> ChapterContent:
        # 本地书不缓存：① 读本地本来就快；② 内嵌插图的字节不该塞进缓存库
        # （同一张图可能被上千章引用，会白白撑大 cache.db）。
        cacheable = getattr(self.source, "cacheable", True)
        if self.cache is not None and cacheable and not self.force:
            cached = self.cache.get_chapter(self.source.key, self.book.url,
                                            self.chapter.url)
            if cached:
                self.signals.progress.emit("已从本地缓存读取")
                return ChapterContent(title=cached["title"] or self.chapter.title,
                                      paragraphs=cached["paragraphs"],
                                      url=self.chapter.url, from_cache=True)
        started = time.time()
        try:
            content = self.source.fetch_chapter(self.book, self.chapter)
        except Exception as exc:
            if self.stats is not None:
                self.stats.record(self.source.key, ok=False,
                                  elapsed=time.time() - started,
                                  error=str(getattr(exc, "message", exc)))
            raise
        if self.stats is not None:
            self.stats.record(self.source.key, ok=True, elapsed=time.time() - started)
        if not content.title:
            content.title = self.chapter.title
        if self.cache is not None and cacheable and content.paragraphs:
            self.cache.put_chapter(self.source.key, self.book.url, self.chapter.url,
                                   content.title, content.paragraphs)
        return content


class CoverTask(BaseTask):
    """下载封面（可选缓存）。"""

    def __init__(self, http: HttpSession, url: str, cache: Optional[Cache] = None):
        super().__init__()
        self.http = http
        self.url = url
        self.cache = cache

    def work(self) -> bytes:
        if not self.url:
            return b""
        if self.cache is not None:
            cached = self.cache.get_cover(self.url)
            if cached:
                return cached
        data = self.http.get_bytes(self.url, referer="")
        if self.cache is not None and data:
            self.cache.put_cover(self.url, data)
        return data


class HealthTask(BaseTask):
    """书源可用性检测。"""

    def __init__(self, sources: Sequence[BaseSource]):
        super().__init__()
        self.sources = list(sources)

    def work(self) -> List[dict]:
        results = []
        for index, source in enumerate(self.sources):
            if self.cancelled:
                break
            self.signals.progress.emit(f"检测书源：{source.name} "
                                       f"（{index + 1}/{len(self.sources)}）")
            started = time.time()
            try:
                ok, message = source.health_check()
            except Exception as exc:  # noqa: BLE001
                ok, message = False, f"{type(exc).__name__}: {exc}"
            results.append({"key": source.key, "name": source.name, "ok": ok,
                            "message": message, "elapsed": time.time() - started})
            self.signals.partial.emit(results[-1])
        return results


class ImportTask(BaseTask):
    """从 URL 或本地文本导入书源规则。"""
    def __init__(self, http: HttpSession, url: str = "", text: str = ""):
        super().__init__()
        self.http = http
        self.url = url.strip()
        self.text = text

    def work(self) -> List[ParsedRule]:
        if self.url:
            self.signals.progress.emit(f"正在下载书源：{self.url}")
            payload = self.http.get_text(self.url, timeout=30)
            origin = self.url
        else:
            payload = self.text
            origin = "本地内容"
        parsed = parse_payload(payload, origin)
        self.signals.progress.emit(f"解析完成：{len(parsed)} 条")
        return parsed


class LocalImportTask(BaseTask):
    """导入本地电子书（TXT / EPUB）：解析 + 复制进数据目录。

    解析一本几 MB 的 TXT 需要一两秒，所以放到线程池里跑，界面不卡。
    """

    def __init__(self, paths: Sequence[str], books=None, title: str = "",
                 author: str = ""):
        super().__init__()
        self.paths = [Path(p) for p in paths]
        self.books = books if books is not None else LocalBooks()
        self.title = title
        self.author = author

    def work(self) -> dict:
        imported, failures = [], []
        for index, path in enumerate(self.paths):
            if self.cancelled:
                break
            self.signals.progress.emit(
                f"正在导入（{index + 1}/{len(self.paths)}）：{path.name}")
            try:
                record = self.books.import_file(
                    path, title=self.title, author=self.author,
                    on_note=self.signals.progress.emit)
                imported.append(record)
            except Exception as exc:  # noqa: BLE001 - 单个文件失败不影响其它文件
                failures.append(f"{path.name}：{exc}")
        return {"imported": imported, "failures": failures}


class ProbeTask(BaseTask):
    """自动探测一个新站点（方案 B）：试模板 → 验证目录与正文 → 生成规则。"""

    def __init__(self, http: HttpSession, base_url: str, keyword: str,
                 deep: bool = False):
        super().__init__()
        self.http = http
        self.base_url = base_url
        self.keyword = keyword
        self.deep = deep

    def work(self) -> "ProbeOutcome":
        return probe_domain(self.http, self.base_url, self.keyword,
                            deep=self.deep, on_note=self.signals.progress.emit)


class DiscoverTask(BaseTask):
    """网络发现（方案 C）：搜索引擎找候选站点 → 自动探测 → 推荐可用书源。"""

    def __init__(self, http: HttpSession, book_title: str, author: str = "",
                 limit: int = 5, engine: str = "",
                 existing_hosts: Sequence[str] = ()):
        super().__init__()
        self.http = http
        self.book_title = book_title
        self.author = author
        self.limit = limit
        self.engine = engine
        self.existing_hosts = list(existing_hosts)

    def work(self) -> "DiscoveryOutcome":
        return discover_sources(self.http, self.book_title, author=self.author,
                                limit=self.limit, engine=self.engine,
                                existing_hosts=self.existing_hosts,
                                on_note=self.signals.progress.emit)


class SubscriptionTask(BaseTask):
    """批量拉取订阅地址里的书源规则。

    返回 ``(results, failures)``：results 是所有解析出来的规则（已按 key 去重），
    failures 是拉取失败或解析异常的订阅地址说明。
    """

    def __init__(self, http: HttpSession, subscriptions: Sequence[dict]):
        super().__init__()
        self.http = http
        self.subscriptions = [s for s in subscriptions if s.get("enabled", True)]

    def work(self) -> tuple:
        merged: dict = {}
        failures: List[str] = []
        for index, item in enumerate(self.subscriptions):
            if self.cancelled:
                break
            url = item.get("url", "")
            name = item.get("name") or url
            self.signals.progress.emit(f"更新订阅：{name} "
                                       f"（{index + 1}/{len(self.subscriptions)}）")
            try:
                payload = self.http.get_text(url, timeout=30)
                parsed = parse_payload(payload, url)
            except Exception as exc:  # noqa: BLE001
                failures.append(f"{name}：{getattr(exc, 'message', exc)}")
                continue
            usable = [p for p in parsed if p.ok]
            if not usable:
                reason = parsed[0].reason if parsed else "没有可用规则"
                failures.append(f"{name}：{reason}")
                continue
            for item_rule in usable:
                merged[item_rule.key] = item_rule
            self.signals.partial.emit({"url": url, "name": name,
                                       "count": len(usable)})
        return list(merged.values()), failures


# ---------------------------------------------------------------------------
# 缓存序列化
# ---------------------------------------------------------------------------
def _detail_to_cache(detail: BookDetail) -> dict:
    book = detail.book
    return {
        "book": {
            "title": book.title, "author": book.author, "url": book.url,
            "cover_url": book.cover_url, "intro": book.intro,
            "source": book.source, "source_name": book.source_name,
            "category": book.category, "status": book.status,
            "latest_chapter": book.latest_chapter, "updated_at": book.updated_at,
            "word_count": book.word_count, "extra": book.extra,
        },
        "chapters": [{"title": c.title, "url": c.url, "index": c.index,
                      "group": c.group}
                     for c in detail.chapters],
    }


def _detail_from_cache(payload: dict, fallback: Book,
                       source: BaseSource) -> BookDetail:
    data = payload.get("book") or {}
    book = Book(
        title=data.get("title") or fallback.title,
        author=data.get("author") or fallback.author,
        url=data.get("url") or fallback.url,
        cover_url=data.get("cover_url") or fallback.cover_url,
        intro=data.get("intro") or fallback.intro,
        source=data.get("source") or source.key,
        source_name=data.get("source_name") or source.name,
        category=data.get("category") or fallback.category,
        status=data.get("status") or fallback.status,
        latest_chapter=data.get("latest_chapter") or fallback.latest_chapter,
        updated_at=data.get("updated_at") or fallback.updated_at,
        word_count=data.get("word_count") or fallback.word_count,
        extra=data.get("extra") or {},
    )
    chapters = [Chapter(title=c.get("title", ""), url=c.get("url", ""),
                        index=int(c.get("index", i)),
                        group=c.get("group", "") or "")
                for i, c in enumerate(payload.get("chapters") or [])]
    return BookDetail(book=book, chapters=chapters)

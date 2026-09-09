# -*- coding: utf-8 -*-
"""网络访问层。

职责：
1. 统一 UA / 请求头 / 超时 / 重试，降低被站点拒绝的概率；
2. 自动处理编码（响应头 charset -> HTML meta -> 启发式探测 -> 常见中文编码兜底）；
3. 把底层异常翻译成用户能看懂的中文提示（网络超时、站点拒绝、结构变化等）。

所有书源都复用同一个 :class:`HttpSession` 实例，从而共享连接池与 Cookie。
"""
from __future__ import annotations

import json
import random
import re
import threading
import time
from typing import Any, Dict, Optional
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.exceptions import InsecureRequestWarning
from urllib3.util.retry import Retry

# 常见桌面浏览器 UA，避免被站点当成脚本直接拦截
DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# 尽量贴近真实浏览器：很多站点只看请求头就决定拦不拦。
# 注意不要手动设置 Accept-Encoding——交给 requests 处理（装了 brotli 才会带 br）。
DEFAULT_HEADERS = {
    "User-Agent": DEFAULT_UA,
    "Accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,"
               "image/avif,image/webp,image/apng,*/*;q=0.8"),
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.6",
    "Cache-Control": "max-age=0",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "Connection": "keep-alive",
}

# 图片类请求用另一套头，更像浏览器加载封面
IMAGE_HEADERS = {
    "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
    "Sec-Fetch-Dest": "image",
    "Sec-Fetch-Mode": "no-cors",
}

CHINESE_ENCODINGS = ("utf-8", "gb18030", "gbk", "big5")


class NovelError(Exception):
    """业务异常基类：message 会直接显示在界面上。"""

    def __init__(self, message: str, detail: str = ""):
        super().__init__(message)
        self.message = message
        self.detail = detail

    def __str__(self) -> str:  # pragma: no cover - 仅用于日志
        return f"{self.message}（{self.detail}）" if self.detail else self.message


class NetworkError(NovelError):
    """网络层错误：超时、连接失败、DNS 等。"""


class BlockedError(NovelError):
    """站点反爬：人机验证、403、访问频率限制等。"""


class ParseError(NovelError):
    """页面结构解析失败，通常意味着站点改版。"""


_META_CHARSET_RE = re.compile(
    br'<meta[^>]+charset\s*=\s*["\']?\s*([\w-]+)', re.I)
_VERIFY_HINTS = ("人机验证", "安全验证", "verify", "cf-browser-verification",
                 "cloudflare", "访问过于频繁", "请稍候")


class HttpSession:
    """带重试与编码处理的 HTTP 会话。"""

    def __init__(self, timeout: float = 12.0, retries: int = 2,
                 min_interval: float = 0.05, host_interval: float = 0.8,
                 verify: bool = True) -> None:
        self.timeout = timeout
        self.retries = retries
        self.min_interval = min_interval        # 全局最小间隔（很轻）
        self.host_interval = host_interval      # 同一站点的请求间隔
        self.verify = verify          # 遇到过期证书时会自动降级为 False
        self.ssl_fallback_used = False
        self._last_request_at = 0.0
        self._host_slots: Dict[str, float] = {}   # host -> 下一次可请求的时间
        self._throttle_lock = threading.Lock()
        self.session = requests.Session()
        self.session.headers.update(DEFAULT_HEADERS)
        retry = Retry(total=0, redirect=True, raise_on_redirect=False)
        adapter = HTTPAdapter(max_retries=retry, pool_connections=16, pool_maxsize=16)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)

    # ---------------------------------------------------------------- 基础请求
    def _reserve_slot(self, url: str) -> float:
        """为某站点"预约"一个请求时刻，返回需要等待的秒数。

        做法：在锁内算出该站点下一个可用的时间点并写回去（相当于排队），
        锁外再 sleep——这样同一站点是串行的，不同站点互不阻塞。
        间隔带随机抖动，避免固定节奏被识别成脚本。
        """
        host = urlparse(url).netloc.lower() or "default"
        with self._throttle_lock:
            now = time.time()
            interval = self.host_interval
            if interval > 0:
                interval *= random.uniform(0.75, 1.35)
            start = max(now,
                        self._host_slots.get(host, 0.0) + interval,
                        self._last_request_at + self.min_interval)
            self._host_slots[host] = start
            self._last_request_at = start
        return max(0.0, start - time.time())

    def _throttle(self, url: str) -> None:
        """按站点限速（等待 + 轻微抖动）。"""
        delay = self._reserve_slot(url)
        if delay > 0:
            time.sleep(min(delay, 5.0))

    def _build_headers(self, referer: str = "", image: bool = False) -> Dict[str, str]:
        """按请求类型组装请求头（Referer / Sec-Fetch-* 要自洽）。"""
        headers: Dict[str, str] = dict(IMAGE_HEADERS) if image else {}
        if referer:
            headers["Referer"] = referer
            headers.setdefault("Sec-Fetch-Site", "same-origin")
        return headers

    def request(self, url: str, *, referer: str = "", encoding: str = "",
                timeout: Optional[float] = None, method: str = "GET",
                data: Optional[Dict[str, Any]] = None,
                image: bool = False) -> requests.Response:
        """执行一次请求，内置重试与友好异常。"""
        headers = self._build_headers(referer, image=image)
        last_error: Optional[Exception] = None
        for attempt in range(self.retries + 1):
            try:
                self._throttle(url)
                resp = self.session.request(
                    method, url, headers=headers, data=data,
                    timeout=timeout or self.timeout, verify=self.verify)
                if resp.status_code in (403, 429):
                    raise BlockedError(
                        "站点拒绝了本次访问（可能需要人机验证或访问过于频繁）",
                        f"HTTP {resp.status_code} {url}")
                if resp.status_code >= 500:
                    raise NetworkError(
                        "站点服务器暂时不可用", f"HTTP {resp.status_code} {url}")
                resp.raise_for_status()
                if encoding:
                    resp.encoding = encoding
                return resp
            except BlockedError:
                raise
            except requests.exceptions.SSLError as exc:
                # 这类站点经常证书过期/自签名，降级重试一次（会在日志中标记）
                if self.verify:
                    self.verify = False
                    self.ssl_fallback_used = True
                    warnings_filter()
                    last_error = exc
                    continue
                last_error = exc
            except (requests.Timeout, requests.ConnectionError) as exc:
                last_error = exc
            except requests.HTTPError as exc:
                last_error = exc
                if exc.response is not None and exc.response.status_code == 404:
                    raise ParseError("页面不存在（404），该链接可能已失效", url) from exc
            except requests.RequestException as exc:
                last_error = exc
            if attempt < self.retries:
                time.sleep(0.6 * (2 ** attempt) + random.uniform(0, 0.3))
        detail = f"{type(last_error).__name__}: {last_error}" if last_error else url
        raise NetworkError("网络请求失败，请检查网络连接或稍后重试", detail)

    # ---------------------------------------------------------------- 便捷方法
    def get_text(self, url: str, *, referer: str = "", encoding: str = "",
                 timeout: Optional[float] = None) -> str:
        resp = self.request(url, referer=referer, encoding=encoding, timeout=timeout)
        return decode_response(resp, forced=encoding)

    def get_bytes(self, url: str, *, referer: str = "",
                  timeout: Optional[float] = None) -> bytes:
        """下载二进制内容（封面等），使用图片类请求头。"""
        return self.request(url, referer=referer, timeout=timeout,
                            image=True).content

    def get_json(self, url: str, *, referer: str = "",
                 timeout: Optional[float] = None) -> Any:
        resp = self.request(url, referer=referer, timeout=timeout)
        try:
            return resp.json()
        except ValueError as exc:
            text = resp.text.strip()
            # 部分站点返回 JSONP 或带 BOM 的内容，做一次宽松提取
            match = re.search(r"[\[{].*[\]}]", text, re.S)
            if match:
                try:
                    return json.loads(match.group(0))
                except ValueError:
                    pass
            raise ParseError("站点返回的数据格式已变化（不是合法 JSON）",
                             text[:120]) from exc

    def close(self) -> None:
        self.session.close()


def decode_response(resp: requests.Response, forced: str = "") -> str:
    """按 响应头 -> meta -> 常见中文编码 的顺序解码，尽量不出现乱码。"""
    raw = resp.content
    if forced:
        try:
            return raw.decode(forced, "replace")
        except LookupError:
            pass
    candidates = []
    if resp.encoding:
        candidates.append(resp.encoding)
    meta = _META_CHARSET_RE.search(raw[:4096])
    if meta:
        try:
            candidates.append(meta.group(1).decode("ascii", "ignore").lower())
        except Exception:
            pass
    if resp.apparent_encoding:
        candidates.append(resp.apparent_encoding)
    candidates.extend(CHINESE_ENCODINGS)

    best_text, best_score = "", -1.0
    seen = set()
    for enc in candidates:
        enc = (enc or "").lower()
        if not enc or enc in seen:
            continue
        seen.add(enc)
        try:
            text = raw.decode(enc)
        except (LookupError, UnicodeDecodeError):
            text = raw.decode(enc, "replace") if _known(enc) else ""
        if not text:
            continue
        score = _quality(text)
        if score > best_score:
            best_text, best_score = text, score
    if not best_text:
        best_text = raw.decode("utf-8", "replace")
    if _looks_like_verify(best_text):
        raise BlockedError("站点要求人机验证，请稍后重试或切换其它书源", "")
    return best_text


def _known(enc: str) -> bool:
    try:
        "".encode(enc)
        return True
    except LookupError:
        return False


def _quality(text: str) -> float:
    """用替换字符/乱码字符比例粗略评价解码质量。"""
    if not text:
        return -1.0
    bad = text.count("\ufffd") + text.count("锟斤拷")
    sample = text[:20000]
    cjk = sum(1 for ch in sample if "\u4e00" <= ch <= "\u9fff")
    return cjk / max(len(sample), 1) - bad * 0.1


def _looks_like_verify(text: str) -> bool:
    head = text[:2000].lower()
    return any(hint.lower() in head for hint in _VERIFY_HINTS) and len(text) < 3000


_warnings_filtered = False


def warnings_filter() -> None:
    """降级为不校验证书时，屏蔽 urllib3 的重复告警。"""
    global _warnings_filtered
    if _warnings_filtered:
        return
    import warnings
    warnings.simplefilter("ignore", InsecureRequestWarning)
    _warnings_filtered = True

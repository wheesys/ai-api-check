"""异步 HTTP 客户端：统一超时、退避重试与脱敏（设计 §11.3）。

封装 aiohttp 会话，对外提供协议无关的 request_json / stream_lines。
重试策略（tenacity）：仅对可重试类别（rate_limit/timeout/upstream_5xx）做指数退避
+ 抖动，最大重试次数可配（默认 2）；不可重试类别立即失败，不浪费额度。
所有错误经 ProbeError 脱敏，绝不泄露 Key。
"""
import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any, TypeVar

import aiohttp
from tenacity import (
    AsyncRetrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

from app.utils.errors import ErrorCategory, ProbeError

_T = TypeVar("_T")


@dataclass
class HTTPResponse:
    """归一化 HTTP 响应。"""

    status: int
    headers: dict[str, str]
    text: str
    json_body: Any | None  # 解析后的 JSON（解析失败为 None）
    elapsed_ms: int  # 往返耗时（毫秒）


def _is_retryable_error(error: BaseException) -> bool:
    """tenacity 重试判定：仅可重试类别的 ProbeError 触发重试。"""
    return isinstance(error, ProbeError) and error.retryable


class HTTPClient:
    """异步 HTTP 客户端，作为异步上下文管理器使用。"""

    def __init__(
        self,
        *,
        timeout_seconds: float = 30.0,
        max_retries: int = 2,
        retry_initial_seconds: float = 0.5,
        retry_max_seconds: float = 8.0,
    ) -> None:
        self._timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self._max_retries = max_retries
        self._retry_initial = retry_initial_seconds
        self._retry_max = retry_max_seconds
        self._session: aiohttp.ClientSession | None = None

    async def __aenter__(self) -> "HTTPClient":
        self._session = aiohttp.ClientSession(timeout=self._timeout)
        return self

    async def __aexit__(self, *_exc_info: object) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    async def _run_with_retry(
        self, factory: Callable[[], Awaitable[_T]]
    ) -> _T:
        """以 tenacity 包裹一次可重试的异步调用。

        factory 每次被调用应发起一次全新请求；不可重试错误立即抛出（reraise）。
        """
        async for attempt in AsyncRetrying(
            retry=retry_if_exception(_is_retryable_error),
            stop=stop_after_attempt(self._max_retries + 1),
            wait=wait_exponential_jitter(
                initial=self._retry_initial, max=self._retry_max
            ),
            reraise=True,
        ):
            with attempt:
                return await factory()
        raise AssertionError("unreachable")  # pragma: no cover

    def _require_session(self) -> aiohttp.ClientSession:
        if self._session is None:
            raise RuntimeError("HTTPClient 未进入上下文：请使用 async with HTTPClient()")
        return self._session

    async def request_json(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        json_payload: Any | None = None,
    ) -> HTTPResponse:
        """发起 JSON 请求并按状态码归类错误（可重试类自动退避重试）。"""

        async def _send() -> HTTPResponse:
            session = self._require_session()
            started = time.perf_counter()
            try:
                async with session.request(
                    method, url, headers=headers, json=json_payload
                ) as response:
                    text = await response.text()
                    elapsed_ms = int((time.perf_counter() - started) * 1000)
                    json_body = _safe_json(text)
                    if response.status >= 400:
                        raise ProbeError.from_status(
                            response.status,
                            f"HTTP {response.status}: {text[:500]}",
                            raw_excerpt=text[:500],
                            retry_after=_parse_retry_after(response.headers),
                        )
                    return HTTPResponse(
                        status=response.status,
                        headers=dict(response.headers),
                        text=text,
                        json_body=json_body,
                        elapsed_ms=elapsed_ms,
                    )
            except aiohttp.ClientConnectorError as error:
                raise ProbeError(
                    ErrorCategory.CONNECTIVITY, f"连接失败: {error}"
                ) from error
            except asyncio.TimeoutError as error:
                raise ProbeError(ErrorCategory.TIMEOUT, "请求超时") from error
            except aiohttp.ClientError as error:
                raise ProbeError(
                    ErrorCategory.CONNECTIVITY, f"客户端错误: {error}"
                ) from error

        return await self._run_with_retry(_send)

    async def stream_lines(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        json_payload: Any | None = None,
    ) -> AsyncIterator[tuple[int, bytes]]:
        """流式逐行读取，产出 (自请求起算的毫秒, 行字节)。

        首个非空数据行的毫秒即 TTFT。流式不走自动重试（半途重试语义不清），
        由探针层按成功率降级处理。
        """
        session = self._require_session()
        started = time.perf_counter()
        try:
            async with session.request(
                method, url, headers=headers, json=json_payload
            ) as response:
                if response.status >= 400:
                    body = await response.text()
                    raise ProbeError.from_status(
                        response.status,
                        f"HTTP {response.status}: {body[:500]}",
                        raw_excerpt=body[:500],
                    )
                async for raw_line in response.content:
                    line = raw_line.strip()
                    if not line:
                        continue
                    elapsed_ms = int((time.perf_counter() - started) * 1000)
                    yield elapsed_ms, line
        except aiohttp.ClientConnectorError as error:
            raise ProbeError(
                ErrorCategory.CONNECTIVITY, f"连接失败: {error}"
            ) from error
        except asyncio.TimeoutError as error:
            raise ProbeError(ErrorCategory.TIMEOUT, "流式请求超时") from error


def _safe_json(text: str) -> Any | None:
    """容错 JSON 解析：失败返回 None，不抛错。"""
    import json

    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return None


def _parse_retry_after(headers: object) -> float | None:
    """解析 Retry-After 响应头（仅支持秒数形式），无则 None。"""
    try:
        value = headers.get("Retry-After")  # type: ignore[union-attr]
    except AttributeError:
        return None
    if not value:
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None

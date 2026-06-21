"""测试用伪 HTTP 客户端：鸭子类型替身，零网络。

与 app.utils.http_client.HTTPClient 暴露相同的 request_json / stream_lines 接口，
供适配器单测注入，避免真实上游调用。
"""
import json
from collections.abc import AsyncIterator
from typing import Any

from app.utils.http_client import HTTPResponse


class FakeHTTPClient:
    """可配置返回的伪客户端，记录调用以便断言。"""

    def __init__(
        self,
        *,
        json_response: Any = None,
        status: int = 200,
        stream_lines: list[bytes] | None = None,
    ) -> None:
        self._json_response = json_response
        self._status = status
        self._stream_lines = stream_lines or []
        self.calls: list[tuple[str, str, Any]] = []

    async def request_json(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        json_payload: Any | None = None,
    ) -> HTTPResponse:
        self.calls.append((method, url, json_payload))
        return HTTPResponse(
            status=self._status,
            headers={},
            text=json.dumps(self._json_response),
            json_body=self._json_response,
            elapsed_ms=5,
        )

    async def stream_lines(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        json_payload: Any | None = None,
    ) -> AsyncIterator[tuple[int, bytes]]:
        self.calls.append((method, url, json_payload))
        for index, line in enumerate(self._stream_lines):
            yield index * 10, line

"""测试用伪 Provider 适配器：可脚本化 chat/stream 结果，零网络。

支持顺序脚本（chat_sequence：逐次返回响应或抛错，用于稳定性多次调用）与流式分帧。
"""
from collections.abc import AsyncIterator

from app.providers.base import (
    AdapterRequest,
    AdapterResponse,
    ModelInfo,
    ProviderAdapter,
    StreamChunk,
)


class FakeAdapter(ProviderAdapter):
    """可配置的伪适配器替身。"""

    protocol = "fake"
    access_mode = "native"

    def __init__(
        self,
        *,
        chat_response: AdapterResponse | None = None,
        chat_error: Exception | None = None,
        chat_sequence: list[AdapterResponse | Exception] | None = None,
        stream_chunks: list[StreamChunk] | None = None,
        stream_error: Exception | None = None,
    ) -> None:
        self._chat_response = chat_response
        self._chat_error = chat_error
        self._chat_sequence = chat_sequence
        self._stream_chunks = stream_chunks or []
        self._stream_error = stream_error
        self.chat_calls = 0
        self.stream_calls = 0

    async def chat(self, request: AdapterRequest) -> AdapterResponse:
        index = self.chat_calls
        self.chat_calls += 1
        if self._chat_sequence is not None:
            item = self._chat_sequence[index % len(self._chat_sequence)]
            if isinstance(item, Exception):
                raise item
            return item
        if self._chat_error is not None:
            raise self._chat_error
        assert self._chat_response is not None, "未配置 chat_response"
        return self._chat_response

    async def stream_chat(
        self, request: AdapterRequest
    ) -> AsyncIterator[StreamChunk]:
        self.stream_calls += 1
        if self._stream_error is not None:
            raise self._stream_error
        for chunk in self._stream_chunks:
            yield chunk

    async def fetch_models(self) -> list[ModelInfo]:
        return []


class FakeClock:
    """脚本化时钟：每次调用返回序列中下一值（耗尽后保持最后值），确定性测耗时。"""

    def __init__(self, values: list[float]) -> None:
        self._values = list(values)
        self._index = 0
        self._last = values[0] if values else 0.0

    def __call__(self) -> float:
        if self._index < len(self._values):
            self._last = self._values[self._index]
            self._index += 1
        return self._last

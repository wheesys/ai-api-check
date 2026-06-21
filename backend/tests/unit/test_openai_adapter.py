"""OpenAI 适配器单元测试：纯解析函数 + 注入伪客户端的编排路径。

零网络；夹具无真实 Key。
"""
import pytest

from app.providers.adapter_factory import AdapterFactory
from app.providers.base import AdapterRequest, ChatMessage
from app.providers.openai_adapter import (
    OpenAIAdapter,
    build_chat_payload,
    parse_chat_response,
    parse_models,
    parse_stream_line,
)
from app.utils.errors import ProbeError
from app.utils.http_client import HTTPResponse
from tests.fixtures.fake_http import FakeHTTPClient
from tests.fixtures.openai_responses import (
    CHAT_COMPLETION,
    MODELS_LIST,
    STREAM_LINES,
)


def _request(stream: bool = False) -> AdapterRequest:
    return AdapterRequest(
        model_name="gpt-4o",
        messages=[ChatMessage(role="user", content="hi")],
        stream=stream,
    )


def test_build_chat_payload_basic():
    payload = build_chat_payload(_request())
    assert payload["model"] == "gpt-4o"
    assert payload["messages"][0] == {"role": "user", "content": "hi"}
    assert payload["max_tokens"] == 16
    assert "stream" not in payload


def test_build_chat_payload_stream_includes_usage_option():
    payload = build_chat_payload(_request(stream=True))
    assert payload["stream"] is True
    assert payload["stream_options"] == {"include_usage": True}


def test_parse_chat_response_extracts_content_and_usage():
    response = HTTPResponse(
        status=200, headers={}, text="", json_body=CHAT_COMPLETION, elapsed_ms=5
    )
    result = parse_chat_response(response)
    assert result.success is True
    assert result.content == "Hello!"
    assert result.usage.total_tokens == 11


def test_parse_chat_response_invalid_structure_raises():
    response = HTTPResponse(
        status=200, headers={}, text="", json_body={"foo": "bar"}, elapsed_ms=5
    )
    with pytest.raises(ProbeError):
        parse_chat_response(response)


def test_parse_models():
    models = parse_models(MODELS_LIST)
    names = [model.model_name for model in models]
    assert names == ["gpt-4o", "gpt-4o-mini"]
    assert all(model.protocol == "openai" for model in models)
    assert all(model.access_mode == "native" for model in models)


def test_parse_stream_line_content():
    chunk = parse_stream_line(STREAM_LINES[0])
    assert chunk is not None
    assert chunk.delta_text == "Hel"


def test_parse_stream_line_done_returns_none():
    assert parse_stream_line(b"data: [DONE]") is None


def test_parse_stream_line_usage_only():
    chunk = parse_stream_line(STREAM_LINES[2])
    assert chunk is not None
    assert chunk.delta_text == ""
    assert chunk.usage.total_tokens == 11


def test_parse_stream_line_non_data_returns_none():
    assert parse_stream_line(b": keep-alive") is None


async def test_adapter_chat_orchestration():
    client = FakeHTTPClient(json_response=CHAT_COMPLETION)
    adapter = OpenAIAdapter("https://relay.example/", "fake-key", client)
    result = await adapter.chat(_request())
    assert result.content == "Hello!"
    # 校验请求落到正确端点
    assert client.calls[0][1] == "https://relay.example/v1/chat/completions"


async def test_adapter_fetch_models_orchestration():
    client = FakeHTTPClient(json_response=MODELS_LIST)
    adapter = OpenAIAdapter("https://relay.example", "fake-key", client)
    models = await adapter.fetch_models()
    assert len(models) == 2
    assert client.calls[0][1] == "https://relay.example/v1/models"


async def test_adapter_stream_chat_collects_chunks():
    client = FakeHTTPClient(stream_lines=STREAM_LINES)
    adapter = OpenAIAdapter("https://relay.example", "fake-key", client)
    chunks = [chunk async for chunk in adapter.stream_chat(_request(stream=True))]
    text = "".join(chunk.delta_text for chunk in chunks)
    assert text == "Hello"  # "Hel" + "lo"
    assert any(chunk.usage is not None for chunk in chunks)


def test_factory_registers_openai_native():
    client = FakeHTTPClient(json_response=CHAT_COMPLETION)
    adapter = AdapterFactory.create(
        "openai", "native", base_url="https://x", api_key="k", http_client=client
    )
    assert isinstance(adapter, OpenAIAdapter)

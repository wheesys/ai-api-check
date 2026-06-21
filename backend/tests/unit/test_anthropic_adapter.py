"""Anthropic 适配器单元测试：纯解析函数 + 注入伪客户端的编排路径。

零网络；夹具无真实 Key。
"""
import pytest

from app.providers.adapter_factory import AdapterFactory
from app.providers.anthropic_adapter import (
    ANTHROPIC_VERSION,
    AnthropicAdapter,
    build_messages_payload,
    parse_message_response,
    parse_models,
    parse_stream_event,
    parse_usage,
)
from app.providers.base import AdapterRequest, ChatMessage
from app.utils.errors import ProbeError
from app.utils.http_client import HTTPResponse
from tests.fixtures.anthropic_responses import (
    MESSAGE_RESPONSE,
    MODELS_LIST,
    STREAM_LINES,
)
from tests.fixtures.fake_http import FakeHTTPClient


def _request(stream: bool = False, with_system: bool = False) -> AdapterRequest:
    messages = [ChatMessage(role="user", content="hi")]
    if with_system:
        messages.insert(0, ChatMessage(role="system", content="be brief"))
    return AdapterRequest(model_name="claude-3-5-haiku-20241022", messages=messages, stream=stream)


def test_build_payload_extracts_system():
    payload = build_messages_payload(_request(with_system=True))
    assert payload["system"] == "be brief"
    # system 角色不应残留在 messages 中
    assert all(message["role"] != "system" for message in payload["messages"])


def test_build_payload_without_system_has_no_system_key():
    payload = build_messages_payload(_request())
    assert "system" not in payload


def test_parse_usage_maps_input_output():
    usage = parse_usage({"input_tokens": 9, "output_tokens": 2})
    assert usage.prompt_tokens == 9
    assert usage.completion_tokens == 2
    assert usage.total_tokens == 11


def test_parse_message_response():
    response = HTTPResponse(
        status=200, headers={}, text="", json_body=MESSAGE_RESPONSE, elapsed_ms=5
    )
    result = parse_message_response(response)
    assert result.content == "Hello!"
    assert result.usage.total_tokens == 11


def test_parse_message_response_invalid_raises():
    response = HTTPResponse(status=200, headers={}, text="", json_body={}, elapsed_ms=5)
    with pytest.raises(ProbeError):
        parse_message_response(response)


def test_parse_models():
    models = parse_models(MODELS_LIST)
    assert [model.model_name for model in models] == [
        "claude-3-5-sonnet-20241022",
        "claude-3-5-haiku-20241022",
    ]
    assert all(model.protocol == "anthropic" for model in models)
    assert models[0].display_name == "Claude 3.5 Sonnet"


def test_parse_stream_event_text_delta():
    chunk = parse_stream_event(STREAM_LINES[3])
    assert chunk is not None
    assert chunk.delta_text == "Hel"


def test_parse_stream_event_message_start_usage():
    chunk = parse_stream_event(STREAM_LINES[1])
    assert chunk is not None
    assert chunk.usage.prompt_tokens == 9


def test_parse_stream_event_ignores_event_lines():
    assert parse_stream_event(b"event: message_start") is None


async def test_adapter_chat_uses_version_header_and_endpoint():
    client = FakeHTTPClient(json_response=MESSAGE_RESPONSE)
    adapter = AnthropicAdapter("https://relay.example/", "fake-key", client)
    result = await adapter.chat(_request())
    assert result.content == "Hello!"
    assert client.calls[0][1] == "https://relay.example/v1/messages"
    assert adapter._headers()["anthropic-version"] == ANTHROPIC_VERSION


async def test_adapter_stream_chat_collects_text_and_usage():
    client = FakeHTTPClient(stream_lines=STREAM_LINES)
    adapter = AnthropicAdapter("https://relay.example", "fake-key", client)
    chunks = [chunk async for chunk in adapter.stream_chat(_request(stream=True))]
    text = "".join(chunk.delta_text for chunk in chunks)
    assert text == "Hello"
    assert any(chunk.usage is not None for chunk in chunks)


def test_factory_registers_anthropic_native():
    client = FakeHTTPClient(json_response=MESSAGE_RESPONSE)
    adapter = AdapterFactory.create(
        "anthropic", "native", base_url="https://x", api_key="k", http_client=client
    )
    assert isinstance(adapter, AnthropicAdapter)

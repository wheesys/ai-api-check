"""Gemini 适配器单元测试：三路径纯解析函数 + 注入伪客户端的编排路径。

覆盖原生 Developer / Vertex 风格端点路由、功能性指纹字段提取、OpenAI 兼容层复用。
零网络；夹具无真实 Key。
"""
import pytest

from app.providers.adapter_factory import AdapterFactory
from app.providers.base import AdapterRequest, ChatMessage
from app.providers.gemini_adapter import (
    GeminiNativeAdapter,
    GeminiOpenAICompatAdapter,
    build_generate_payload,
    extract_feature_flags,
    parse_compat_models,
    parse_generate_response,
    parse_models,
    parse_stream_chunk,
    parse_usage,
    to_gemini_contents,
)
from app.utils.errors import ProbeError
from app.utils.http_client import HTTPResponse
from tests.fixtures.fake_http import FakeHTTPClient
from tests.fixtures.gemini_responses import (
    COMPAT_CHAT_COMPLETION,
    COMPAT_MODELS_LIST,
    COUNT_TOKENS,
    GENERATE_CONTENT,
    GENERATE_CONTENT_CODE_EXEC,
    MODELS_LIST,
    STREAM_LINES,
)


def _request(stream: bool = False, **extra) -> AdapterRequest:
    return AdapterRequest(
        model_name="gemini-2.5-pro",
        messages=[
            ChatMessage(role="system", content="be brief"),
            ChatMessage(role="user", content="hi"),
        ],
        stream=stream,
        extra=extra,
    )


def _http(body) -> HTTPResponse:
    return HTTPResponse(status=200, headers={}, text="", json_body=body, elapsed_ms=5)


# ---------- 纯函数：消息转换与请求构造 ----------

def test_to_gemini_contents_maps_roles_and_system():
    contents, system_instruction = to_gemini_contents(
        [
            ChatMessage(role="system", content="sys"),
            ChatMessage(role="user", content="u"),
            ChatMessage(role="assistant", content="a"),
        ]
    )
    # system 提升为 systemInstruction；assistant 映射为 model
    assert system_instruction == {"parts": [{"text": "sys"}]}
    assert contents == [
        {"role": "user", "parts": [{"text": "u"}]},
        {"role": "model", "parts": [{"text": "a"}]},
    ]


def test_build_generate_payload_structure():
    payload = build_generate_payload(_request())
    assert payload["contents"][0]["role"] == "user"
    assert payload["generationConfig"]["maxOutputTokens"] == 16
    assert payload["systemInstruction"] == {"parts": [{"text": "be brief"}]}


def test_build_generate_payload_merges_extra_generation_config():
    # 探针注入的 generationConfig 应合并而非覆盖 maxOutputTokens
    payload = build_generate_payload(
        _request(generationConfig={"responseMimeType": "application/json"})
    )
    assert payload["generationConfig"]["maxOutputTokens"] == 16
    assert payload["generationConfig"]["responseMimeType"] == "application/json"


def test_build_generate_payload_passes_extra_tools():
    payload = build_generate_payload(_request(tools=[{"google_search": {}}]))
    assert payload["tools"] == [{"google_search": {}}]


# ---------- 纯函数：响应与用量解析 ----------

def test_parse_usage_maps_usage_metadata():
    usage = parse_usage(GENERATE_CONTENT["usageMetadata"])
    assert usage.prompt_tokens == 5
    assert usage.completion_tokens == 4
    assert usage.total_tokens == 9
    # 思考用量保留在 raw 供指纹探针使用
    assert usage.raw["thoughtsTokenCount"] == 12


def test_parse_generate_response_extracts_content_usage_flags():
    result = parse_generate_response(_http(GENERATE_CONTENT))
    assert result.success is True
    assert result.content == "Hello from Gemini!"
    assert result.usage.total_tokens == 9
    assert result.feature_flags["thoughts_token_count"] == 12
    assert result.feature_flags["model_version"] == "gemini-2.5-pro"
    assert result.feature_flags["safety_ratings"]


def test_parse_generate_response_invalid_structure_raises():
    with pytest.raises(ProbeError):
        parse_generate_response(_http({"foo": "bar"}))


def test_extract_feature_flags_code_execution():
    flags = extract_feature_flags(GENERATE_CONTENT_CODE_EXEC)
    # 代码执行特有结构化字段被捕获
    assert "code_execution" in flags
    assert any("executableCode" in part for part in flags["code_execution"])


# ---------- 纯函数：模型列表 ----------

def test_parse_models_developer_strips_prefix():
    models = parse_models(MODELS_LIST, "gemini_developer")
    names = [model.model_name for model in models]
    assert names == ["gemini-2.5-pro", "gemini-2.5-flash"]
    assert all(model.protocol == "gemini" for model in models)
    assert all(model.access_mode == "native" for model in models)
    assert models[0].gemini_endpoint_style == "gemini_developer"


def test_parse_compat_models_filters_gemini():
    models = parse_compat_models(COMPAT_MODELS_LIST)
    # 仅保留 gemini-* 模型，标记为兼容层
    assert [model.model_name for model in models] == ["gemini-2.5-pro"]
    assert models[0].access_mode == "openai_compat"
    assert models[0].protocol == "gemini"


# ---------- 纯函数：流式解析 ----------

def test_parse_stream_chunk_text_delta():
    chunk = parse_stream_chunk(STREAM_LINES[0])
    assert chunk is not None
    assert chunk.delta_text == "Hello"


def test_parse_stream_chunk_usage_only():
    chunk = parse_stream_chunk(STREAM_LINES[2])
    assert chunk is not None
    assert chunk.delta_text == ""
    assert chunk.usage.total_tokens == 9


def test_parse_stream_chunk_non_data_returns_none():
    assert parse_stream_chunk(b": keep-alive") is None


# ---------- 编排：原生 Developer 风格 ----------

async def test_native_developer_chat_orchestration():
    client = FakeHTTPClient(json_response=GENERATE_CONTENT)
    adapter = GeminiNativeAdapter("https://relay.example/", "fake-key", client)
    result = await adapter.chat(_request())
    assert result.content == "Hello from Gemini!"
    # Developer 风格端点
    assert client.calls[0][1] == (
        "https://relay.example/v1beta/models/gemini-2.5-pro:generateContent"
    )


async def test_native_developer_fetch_models_orchestration():
    client = FakeHTTPClient(json_response=MODELS_LIST)
    adapter = GeminiNativeAdapter("https://relay.example", "fake-key", client)
    models = await adapter.fetch_models()
    assert len(models) == 2
    assert client.calls[0][1] == "https://relay.example/v1beta/models"


async def test_native_stream_chat_collects_chunks():
    client = FakeHTTPClient(stream_lines=STREAM_LINES)
    adapter = GeminiNativeAdapter("https://relay.example", "fake-key", client)
    chunks = [chunk async for chunk in adapter.stream_chat(_request(stream=True))]
    text = "".join(chunk.delta_text for chunk in chunks)
    assert text == "Hello Gemini"
    assert any(chunk.usage is not None for chunk in chunks)
    # 流式端点带 alt=sse
    assert "streamGenerateContent?alt=sse" in client.calls[0][1]


async def test_native_count_tokens_orchestration():
    client = FakeHTTPClient(json_response=COUNT_TOKENS)
    adapter = GeminiNativeAdapter("https://relay.example", "fake-key", client)
    total = await adapter.count_tokens(_request())
    assert total == 9
    assert client.calls[0][1].endswith(":countTokens")


# ---------- 编排：原生 Vertex 风格 ----------

async def test_native_vertex_chat_uses_vertex_url_and_bearer():
    client = FakeHTTPClient(json_response=GENERATE_CONTENT)
    adapter = GeminiNativeAdapter(
        "https://vertex.example",
        "fake-token",
        client,
        endpoint_style="vertex",
        project="proj-1",
        location="us-central1",
    )
    await adapter.chat(_request())
    assert client.calls[0][1] == (
        "https://vertex.example/v1/projects/proj-1/locations/us-central1"
        "/publishers/google/models/gemini-2.5-pro:generateContent"
    )


def test_native_vertex_requires_project_and_location():
    client = FakeHTTPClient()
    with pytest.raises(ValueError):
        GeminiNativeAdapter(
            "https://vertex.example", "t", client, endpoint_style="vertex"
        )


# ---------- 编排：OpenAI 兼容层 ----------

async def test_compat_chat_reuses_openai_parsing():
    client = FakeHTTPClient(json_response=COMPAT_CHAT_COMPLETION)
    adapter = GeminiOpenAICompatAdapter("https://relay.example", "fake-key", client)
    result = await adapter.chat(_request())
    assert result.content == "Hi via compat layer"
    assert client.calls[0][1] == "https://relay.example/v1/chat/completions"


async def test_compat_fetch_models_filters_gemini():
    client = FakeHTTPClient(json_response=COMPAT_MODELS_LIST)
    adapter = GeminiOpenAICompatAdapter("https://relay.example", "fake-key", client)
    models = await adapter.fetch_models()
    assert [model.model_name for model in models] == ["gemini-2.5-pro"]


# ---------- 工厂注册 ----------

def test_factory_registers_gemini_native():
    client = FakeHTTPClient(json_response=GENERATE_CONTENT)
    adapter = AdapterFactory.create(
        "gemini", "native", base_url="https://x", api_key="k", http_client=client
    )
    assert isinstance(adapter, GeminiNativeAdapter)


def test_factory_registers_gemini_openai_compat():
    client = FakeHTTPClient(json_response=COMPAT_CHAT_COMPLETION)
    adapter = AdapterFactory.create(
        "gemini",
        "openai_compat",
        base_url="https://x",
        api_key="k",
        http_client=client,
    )
    assert isinstance(adapter, GeminiOpenAICompatAdapter)

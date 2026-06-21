"""Anthropic 协议适配器（原生路径，设计 §7/§8.4）。

端点：POST {base}/v1/messages、GET {base}/v1/models。
鉴权头 x-api-key + anthropic-version。用量字段为 input_tokens/output_tokens，
归一化为 TokenUsage 的 prompt/completion。流式按 SSE 事件类型解析增量与用量；
流式缺 usage 时由计费探针走本地估算兜底（设计 §8.4，不在适配器内处理）。
"""
import json
from collections.abc import AsyncIterator

from app.providers.adapter_factory import AdapterFactory
from app.providers.base import (
    AdapterRequest,
    AdapterResponse,
    ModelInfo,
    ProviderAdapter,
    StreamChunk,
    TokenUsage,
)
from app.utils.errors import ErrorCategory, ProbeError
from app.utils.http_client import HTTPClient, HTTPResponse

# Anthropic 要求的 API 版本头（稳定值）
ANTHROPIC_VERSION = "2023-06-01"


def build_messages_payload(request: AdapterRequest) -> dict:
    """构造 /v1/messages 请求体；system 角色消息提升为顶层 system 字段。"""
    system_parts = [
        message.content for message in request.messages if message.role == "system"
    ]
    chat_messages = [
        {"role": message.role, "content": message.content}
        for message in request.messages
        if message.role != "system"
    ]
    payload: dict = {
        "model": request.model_name,
        "max_tokens": request.max_tokens,
        "temperature": request.temperature,
        "messages": chat_messages,
    }
    if system_parts:
        payload["system"] = "\n".join(system_parts)
    if request.stream:
        payload["stream"] = True
    payload.update(request.extra)
    return payload


def parse_usage(raw_usage: dict | None) -> TokenUsage | None:
    """解析 Anthropic usage（input_tokens/output_tokens）。"""
    if not raw_usage:
        return None
    input_tokens = raw_usage.get("input_tokens")
    output_tokens = raw_usage.get("output_tokens")
    total = None
    if input_tokens is not None or output_tokens is not None:
        total = (input_tokens or 0) + (output_tokens or 0)
    return TokenUsage(
        prompt_tokens=input_tokens,
        completion_tokens=output_tokens,
        total_tokens=total,
        raw=raw_usage,
    )


def parse_message_response(response: HTTPResponse) -> AdapterResponse:
    """将非流式 message 响应归一化；结构非法抛 parse_error。"""
    body = response.json_body
    if not isinstance(body, dict) or not isinstance(body.get("content"), list):
        raise ProbeError(
            ErrorCategory.PARSE,
            "Anthropic 响应缺少 content 数组",
            http_status=response.status,
        )
    text = "".join(
        block.get("text", "")
        for block in body["content"]
        if isinstance(block, dict) and block.get("type") == "text"
    )
    return AdapterResponse(
        http_status=response.status,
        success=True,
        content=text,
        usage=parse_usage(body.get("usage")),
        raw_excerpt={"stop_reason": body.get("stop_reason")},
    )


def parse_models(body: dict | None) -> list[ModelInfo]:
    """解析 /v1/models 列表。"""
    if not isinstance(body, dict) or not isinstance(body.get("data"), list):
        raise ProbeError(ErrorCategory.PARSE, "Anthropic 模型列表结构非法")
    models: list[ModelInfo] = []
    for item in body["data"]:
        model_id = item.get("id")
        if model_id:
            models.append(
                ModelInfo(
                    model_name=model_id,
                    protocol="anthropic",
                    access_mode="native",
                    display_name=item.get("display_name") or model_id,
                )
            )
    return models


def parse_stream_event(line: bytes) -> StreamChunk | None:
    """解析单条 SSE data 行；按事件 type 分派增量/用量。event 行返回 None。"""
    text = line.decode("utf-8", errors="replace").strip()
    if not text.startswith("data:"):
        return None
    data = text[len("data:") :].strip()
    if not data:
        return None
    try:
        event = json.loads(data)
    except ValueError:
        return None
    event_type = event.get("type")
    if event_type == "content_block_delta":
        delta = event.get("delta") or {}
        if delta.get("type") == "text_delta":
            return StreamChunk(delta_text=delta.get("text") or "")
    elif event_type == "message_start":
        usage = parse_usage(((event.get("message") or {}).get("usage")))
        if usage is not None:
            return StreamChunk(delta_text="", usage=usage)
    elif event_type == "message_delta":
        usage = parse_usage(event.get("usage"))
        if usage is not None:
            return StreamChunk(delta_text="", usage=usage)
    return None


@AdapterFactory.register("anthropic", "native")
class AnthropicAdapter(ProviderAdapter):
    """Anthropic 原生协议适配器。"""

    protocol = "anthropic"
    access_mode = "native"

    def __init__(self, base_url: str, api_key: str, http_client: HTTPClient) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._client = http_client

    def _headers(self) -> dict[str, str]:
        # x-api-key 仅用于服务端→上游鉴权（设计 §10.5 授权）；错误日志经 sanitizer 脱敏
        return {
            "x-api-key": self._api_key,
            "anthropic-version": ANTHROPIC_VERSION,
            "Content-Type": "application/json",
        }

    async def chat(self, request: AdapterRequest) -> AdapterResponse:
        response = await self._client.request_json(
            "POST",
            f"{self._base_url}/v1/messages",
            headers=self._headers(),
            json_payload=build_messages_payload(request),
        )
        return parse_message_response(response)

    async def stream_chat(
        self, request: AdapterRequest
    ) -> AsyncIterator[StreamChunk]:
        stream_request = AdapterRequest(
            model_name=request.model_name,
            messages=request.messages,
            max_tokens=request.max_tokens,
            temperature=request.temperature,
            stream=True,
            extra=request.extra,
        )
        async for _elapsed_ms, line in self._client.stream_lines(
            "POST",
            f"{self._base_url}/v1/messages",
            headers=self._headers(),
            json_payload=build_messages_payload(stream_request),
        ):
            chunk = parse_stream_event(line)
            if chunk is not None:
                yield chunk

    async def fetch_models(self) -> list[ModelInfo]:
        response = await self._client.request_json(
            "GET", f"{self._base_url}/v1/models", headers=self._headers()
        )
        return parse_models(response.json_body)

"""OpenAI 协议适配器（原生路径，设计 §7/§8）。

端点：POST {base}/v1/chat/completions、GET {base}/v1/models。
协议细节（请求构造、响应/流式/用量解析）抽为纯函数，便于零网络单测；
适配器类仅做 I/O 编排，注入 HTTPClient（连接生命周期由调用方管理）。
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

_STREAM_DONE = "[DONE]"


def build_chat_payload(request: AdapterRequest) -> dict:
    """构造 chat/completions 请求体。"""
    payload: dict = {
        "model": request.model_name,
        "messages": [
            {"role": message.role, "content": message.content}
            for message in request.messages
        ],
        "max_tokens": request.max_tokens,
        "temperature": request.temperature,
    }
    if request.stream:
        payload["stream"] = True
        # 要求上游在流式末帧附带 usage，便于计费一致性核对
        payload["stream_options"] = {"include_usage": True}
    payload.update(request.extra)
    return payload


def parse_usage(raw_usage: dict | None) -> TokenUsage | None:
    """解析 usage 字段；缺失返回 None。"""
    if not raw_usage:
        return None
    return TokenUsage(
        prompt_tokens=raw_usage.get("prompt_tokens"),
        completion_tokens=raw_usage.get("completion_tokens"),
        total_tokens=raw_usage.get("total_tokens"),
        raw=raw_usage,
    )


def parse_chat_response(response: HTTPResponse) -> AdapterResponse:
    """将非流式响应归一化；结构非法抛 parse_error。"""
    body = response.json_body
    if not isinstance(body, dict):
        raise ProbeError(
            ErrorCategory.PARSE, "OpenAI 响应非 JSON 对象", http_status=response.status
        )
    try:
        content = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as error:
        raise ProbeError(
            ErrorCategory.PARSE,
            "OpenAI 响应缺少 choices[0].message.content",
            http_status=response.status,
        ) from error
    return AdapterResponse(
        http_status=response.status,
        success=True,
        content=content,
        usage=parse_usage(body.get("usage")),
        raw_excerpt={"finish_reason": body["choices"][0].get("finish_reason")},
    )


def parse_models(body: dict | None) -> list[ModelInfo]:
    """解析 /v1/models 列表。"""
    if not isinstance(body, dict) or not isinstance(body.get("data"), list):
        raise ProbeError(ErrorCategory.PARSE, "OpenAI 模型列表结构非法")
    models: list[ModelInfo] = []
    for item in body["data"]:
        model_id = item.get("id")
        if model_id:
            models.append(
                ModelInfo(
                    model_name=model_id,
                    protocol="openai",
                    access_mode="native",
                    display_name=model_id,
                )
            )
    return models


def parse_stream_line(line: bytes) -> StreamChunk | None:
    """解析单条 SSE 行；非 data 行、[DONE]、空增量返回 None。"""
    text = line.decode("utf-8", errors="replace").strip()
    if not text.startswith("data:"):
        return None
    data = text[len("data:") :].strip()
    if data == _STREAM_DONE or not data:
        return None
    try:
        chunk = json.loads(data)
    except ValueError:
        return None
    choices = chunk.get("choices") or []
    delta_text = ""
    if choices:
        delta_text = (choices[0].get("delta") or {}).get("content") or ""
    usage = parse_usage(chunk.get("usage"))
    if not delta_text and usage is None:
        return None
    return StreamChunk(delta_text=delta_text, usage=usage, raw=None)


@AdapterFactory.register("openai", "native")
class OpenAIAdapter(ProviderAdapter):
    """OpenAI 原生协议适配器。"""

    protocol = "openai"
    access_mode = "native"

    def __init__(self, base_url: str, api_key: str, http_client: HTTPClient) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._client = http_client

    def _headers(self) -> dict[str, str]:
        # api_key 仅用于服务端→上游鉴权（设计 §10.5 授权）；错误日志经 sanitizer 脱敏
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    async def chat(self, request: AdapterRequest) -> AdapterResponse:
        response = await self._client.request_json(
            "POST",
            f"{self._base_url}/v1/chat/completions",
            headers=self._headers(),
            json_payload=build_chat_payload(request),
        )
        return parse_chat_response(response)

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
            f"{self._base_url}/v1/chat/completions",
            headers=self._headers(),
            json_payload=build_chat_payload(stream_request),
        ):
            chunk = parse_stream_line(line)
            if chunk is not None:
                yield chunk

    async def fetch_models(self) -> list[ModelInfo]:
        response = await self._client.request_json(
            "GET", f"{self._base_url}/v1/models", headers=self._headers()
        )
        return parse_models(response.json_body)

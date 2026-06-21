"""Gemini 协议适配器（原生 Developer/Vertex 双风格 + OpenAI 兼容层，设计 §7）。

三条路径统一 ProviderAdapter 抽象（SOLID-D），新增不改现有代码（SOLID-O）：
  - ("gemini", "native")        原生协议，内部按 endpoint_style 路由 Developer/Vertex
  - ("gemini", "openai_compat") 兼容层转出，复用 OpenAI 协议解析（DRY）

协议细节（请求构造、响应/流式/用量解析、功能性指纹字段提取）抽为纯函数，便于
零网络单测；适配器类仅做 I/O 编排，注入 HTTPClient（连接生命周期由调用方管理）。
"""
import json
from collections.abc import AsyncIterator

from app.providers import openai_adapter as openai_proto
from app.providers.adapter_factory import AdapterFactory
from app.providers.base import (
    AdapterRequest,
    AdapterResponse,
    ChatMessage,
    ModelInfo,
    ProviderAdapter,
    StreamChunk,
    TokenUsage,
)
from app.utils.errors import ErrorCategory, ProbeError
from app.utils.http_client import HTTPClient, HTTPResponse

ENDPOINT_DEVELOPER = "gemini_developer"
ENDPOINT_VERTEX = "vertex"


# ---------- 纯函数：请求构造 ----------

def to_gemini_contents(
    messages: list[ChatMessage],
) -> tuple[list[dict], dict | None]:
    """将协议无关消息转为 Gemini contents 与 systemInstruction（设计 §7.2）。

    Gemini 角色仅 user/model：assistant 映射为 model，system 提升为 systemInstruction。
    """
    contents: list[dict] = []
    system_parts: list[str] = []
    for message in messages:
        if message.role == "system":
            system_parts.append(message.content)
            continue
        role = "model" if message.role == "assistant" else "user"
        contents.append({"role": role, "parts": [{"text": message.content}]})
    system_instruction = (
        {"parts": [{"text": "\n".join(system_parts)}]} if system_parts else None
    )
    return contents, system_instruction


def build_generate_payload(request: AdapterRequest) -> dict:
    """构造 generateContent 请求体；extra 中的 generationConfig 合并而非覆盖。"""
    contents, system_instruction = to_gemini_contents(request.messages)
    payload: dict = {
        "contents": contents,
        "generationConfig": {
            "maxOutputTokens": request.max_tokens,
            "temperature": request.temperature,
        },
    }
    if system_instruction is not None:
        payload["systemInstruction"] = system_instruction
    # 探针特有参数（tools/thinkingConfig/responseMimeType 等）；
    # generationConfig 单独浅合并，避免抹掉 maxOutputTokens
    extra = dict(request.extra)
    extra_generation_config = extra.pop("generationConfig", None)
    if extra_generation_config:
        payload["generationConfig"].update(extra_generation_config)
    payload.update(extra)
    return payload


# ---------- 纯函数：响应解析 ----------

def parse_usage(raw_usage: dict | None) -> TokenUsage | None:
    """解析 usageMetadata；raw 保留 thoughtsTokenCount 等指纹字段。"""
    if not raw_usage:
        return None
    return TokenUsage(
        prompt_tokens=raw_usage.get("promptTokenCount"),
        completion_tokens=raw_usage.get("candidatesTokenCount"),
        total_tokens=raw_usage.get("totalTokenCount"),
        raw=raw_usage,
    )


def _candidate_text(candidate: dict) -> str:
    """拼接 candidate 中 parts[].text 文本块。"""
    parts = (candidate.get("content") or {}).get("parts") or []
    return "".join(
        part.get("text", "")
        for part in parts
        if isinstance(part, dict) and "text" in part
    )


def extract_feature_flags(body: dict) -> dict:
    """提取 Gemini 功能性指纹原始信号（设计 §7.5），供指纹探针判定真伪。

    仅搬运特有结构化字段，不做判定（判定属探针/评分层职责，SOLID-S）。
    """
    flags: dict = {}
    usage = body.get("usageMetadata") or {}
    if usage.get("thoughtsTokenCount") is not None:
        flags["thoughts_token_count"] = usage["thoughtsTokenCount"]
    if usage.get("cachedContentTokenCount") is not None:
        flags["cached_content_token_count"] = usage["cachedContentTokenCount"]
    if body.get("modelVersion"):
        flags["model_version"] = body["modelVersion"]
    if body.get("promptFeedback"):
        flags["prompt_feedback"] = body["promptFeedback"]
    candidates = body.get("candidates") or []
    if candidates:
        first = candidates[0]
        if first.get("safetyRatings"):
            flags["safety_ratings"] = first["safetyRatings"]
        if first.get("groundingMetadata"):
            flags["grounding_metadata"] = first["groundingMetadata"]
        url_context = first.get("urlContextMetadata") or first.get(
            "url_context_metadata"
        )
        if url_context:
            flags["url_context_metadata"] = url_context
        parts = (first.get("content") or {}).get("parts") or []
        executable = [
            part
            for part in parts
            if isinstance(part, dict)
            and ("executableCode" in part or "codeExecutionResult" in part)
        ]
        if executable:
            flags["code_execution"] = executable
    return flags


def parse_generate_response(response: HTTPResponse) -> AdapterResponse:
    """将原生 generateContent 响应归一化；结构非法抛 parse_error。"""
    body = response.json_body
    if not isinstance(body, dict) or not isinstance(body.get("candidates"), list):
        raise ProbeError(
            ErrorCategory.PARSE,
            "Gemini 响应缺少 candidates 数组",
            http_status=response.status,
        )
    candidates = body["candidates"]
    text = ""
    finish_reason = None
    if candidates:
        text = _candidate_text(candidates[0])
        finish_reason = candidates[0].get("finishReason")
    return AdapterResponse(
        http_status=response.status,
        success=True,
        content=text,
        usage=parse_usage(body.get("usageMetadata")),
        feature_flags=extract_feature_flags(body),
        raw_excerpt={
            "finish_reason": finish_reason,
            "model_version": body.get("modelVersion"),
        },
    )


def parse_models(body: dict | None, endpoint_style: str) -> list[ModelInfo]:
    """解析原生模型列表；name 形如 models/gemini-2.5-pro，取末段为模型标识。"""
    if not isinstance(body, dict) or not isinstance(body.get("models"), list):
        raise ProbeError(ErrorCategory.PARSE, "Gemini 模型列表结构非法")
    models: list[ModelInfo] = []
    for item in body["models"]:
        name = item.get("name")
        if not name:
            continue
        model_name = name.split("/")[-1]
        models.append(
            ModelInfo(
                model_name=model_name,
                protocol="gemini",
                access_mode="native",
                display_name=item.get("displayName") or model_name,
                gemini_endpoint_style=endpoint_style,
            )
        )
    return models


def parse_compat_models(body: dict | None) -> list[ModelInfo]:
    """解析兼容层 /v1/models，仅保留 gemini-* 并标记 openai_compat（设计 §10.6）。"""
    if not isinstance(body, dict) or not isinstance(body.get("data"), list):
        raise ProbeError(ErrorCategory.PARSE, "Gemini 兼容层模型列表结构非法")
    models: list[ModelInfo] = []
    for item in body["data"]:
        model_id = item.get("id")
        if model_id and "gemini" in model_id.lower():
            models.append(
                ModelInfo(
                    model_name=model_id,
                    protocol="gemini",
                    access_mode="openai_compat",
                    display_name=model_id,
                )
            )
    return models


def parse_stream_chunk(line: bytes) -> StreamChunk | None:
    """解析单条原生 SSE data 行（每帧为完整 GenerateContentResponse 增量）。"""
    text = line.decode("utf-8", errors="replace").strip()
    if not text.startswith("data:"):
        return None
    data = text[len("data:") :].strip()
    if not data:
        return None
    try:
        chunk = json.loads(data)
    except ValueError:
        return None
    delta_text = ""
    candidates = chunk.get("candidates") or []
    if candidates:
        delta_text = _candidate_text(candidates[0])
    usage = parse_usage(chunk.get("usageMetadata"))
    if not delta_text and usage is None:
        return None
    return StreamChunk(delta_text=delta_text, usage=usage)


# ---------- 适配器：原生 Developer/Vertex 双风格 ----------

@AdapterFactory.register("gemini", "native")
class GeminiNativeAdapter(ProviderAdapter):
    """Gemini 原生协议适配器，按 endpoint_style 路由 Developer/Vertex 端点。"""

    protocol = "gemini"
    access_mode = "native"

    def __init__(
        self,
        base_url: str,
        api_key: str,
        http_client: HTTPClient,
        *,
        endpoint_style: str = ENDPOINT_DEVELOPER,
        project: str | None = None,
        location: str | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._client = http_client
        self._endpoint_style = endpoint_style
        self._project = project
        self._location = location
        if endpoint_style == ENDPOINT_VERTEX and not (project and location):
            # Vertex 风格端点缺 project/location 无法构造（设计 §10.5 应在建任务时校验）
            raise ValueError("Vertex 风格需提供 project 与 location")

    def _headers(self) -> dict[str, str]:
        # Developer 走 x-goog-api-key；Vertex 走 Bearer（设计 §7.1）；错误日志经 sanitizer 脱敏
        if self._endpoint_style == ENDPOINT_VERTEX:
            return {
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            }
        return {
            "x-goog-api-key": self._api_key,
            "Content-Type": "application/json",
        }

    def _model_url(self, model_name: str, method: str) -> str:
        if self._endpoint_style == ENDPOINT_VERTEX:
            return (
                f"{self._base_url}/v1/projects/{self._project}"
                f"/locations/{self._location}/publishers/google/models/"
                f"{model_name}:{method}"
            )
        return f"{self._base_url}/v1beta/models/{model_name}:{method}"

    def _models_url(self) -> str:
        if self._endpoint_style == ENDPOINT_VERTEX:
            return (
                f"{self._base_url}/v1/projects/{self._project}"
                f"/locations/{self._location}/publishers/google/models"
            )
        return f"{self._base_url}/v1beta/models"

    async def chat(self, request: AdapterRequest) -> AdapterResponse:
        response = await self._client.request_json(
            "POST",
            self._model_url(request.model_name, "generateContent"),
            headers=self._headers(),
            json_payload=build_generate_payload(request),
        )
        return parse_generate_response(response)

    async def stream_chat(
        self, request: AdapterRequest
    ) -> AsyncIterator[StreamChunk]:
        # alt=sse 让两种风格均返回标准 SSE，统一交 parse_stream_chunk 解析
        url = self._model_url(request.model_name, "streamGenerateContent") + "?alt=sse"
        async for _elapsed_ms, line in self._client.stream_lines(
            "POST",
            url,
            headers=self._headers(),
            json_payload=build_generate_payload(request),
        ):
            chunk = parse_stream_chunk(line)
            if chunk is not None:
                yield chunk

    async def fetch_models(self) -> list[ModelInfo]:
        response = await self._client.request_json(
            "GET", self._models_url(), headers=self._headers()
        )
        return parse_models(response.json_body, self._endpoint_style)

    async def count_tokens(self, request: AdapterRequest) -> int | None:
        """调用 :countTokens 精确计数，用于校验 usageMetadata 真实性（设计 §7.3）。"""
        contents, _system_instruction = to_gemini_contents(request.messages)
        response = await self._client.request_json(
            "POST",
            self._model_url(request.model_name, "countTokens"),
            headers=self._headers(),
            json_payload={"contents": contents},
        )
        body = response.json_body
        if not isinstance(body, dict):
            return None
        return body.get("totalTokens")


# ---------- 适配器：OpenAI 兼容层转出 ----------

@AdapterFactory.register("gemini", "openai_compat")
class GeminiOpenAICompatAdapter(ProviderAdapter):
    """Gemini 兼容层适配器：走 /v1/chat/completions，复用 OpenAI 协议解析（DRY）。"""

    protocol = "gemini"
    access_mode = "openai_compat"

    def __init__(self, base_url: str, api_key: str, http_client: HTTPClient) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._client = http_client

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    async def chat(self, request: AdapterRequest) -> AdapterResponse:
        response = await self._client.request_json(
            "POST",
            f"{self._base_url}/v1/chat/completions",
            headers=self._headers(),
            json_payload=openai_proto.build_chat_payload(request),
        )
        return openai_proto.parse_chat_response(response)

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
            json_payload=openai_proto.build_chat_payload(stream_request),
        ):
            chunk = openai_proto.parse_stream_line(line)
            if chunk is not None:
                yield chunk

    async def fetch_models(self) -> list[ModelInfo]:
        response = await self._client.request_json(
            "GET", f"{self._base_url}/v1/models", headers=self._headers()
        )
        return parse_compat_models(response.json_body)

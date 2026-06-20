"""Provider 适配器抽象（适配器模式，设计 §8.2 + §7）。

职责边界：适配器只负责把"协议无关的请求"翻译为各上游协议（OpenAI/Anthropic/
Gemini 原生 + 兼容层）的具体收发，并将响应归一化。探针不直接拼协议细节，统一经
适配器收发（SOLID-D：依赖抽象而非具体协议）。新增协议只需实现本抽象并注册
（SOLID-O：对扩展开放、对修改封闭）。
"""
import abc
from collections.abc import AsyncIterator
from dataclasses import dataclass, field


@dataclass
class ChatMessage:
    """协议无关的对话消息。"""

    role: str  # system / user / assistant
    content: str


@dataclass
class AdapterRequest:
    """一次上游调用的协议无关描述。"""

    model_name: str  # 上游模型标识
    messages: list[ChatMessage]  # 对话消息序列
    max_tokens: int = 16  # 最大输出 token（探测请求默认极小，控成本）
    temperature: float = 0.0  # 采样温度，探测默认确定性输出
    stream: bool = False  # 是否流式
    extra: dict = field(default_factory=dict)  # 协议特有参数（如 gemini tools/思考开关）


@dataclass
class TokenUsage:
    """归一化的 token 用量；raw 保留协议特有字段（如 thoughtsTokenCount）。"""

    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    raw: dict | None = None


@dataclass
class StreamChunk:
    """流式响应的单帧增量。"""

    delta_text: str = ""  # 本帧文本增量
    usage: TokenUsage | None = None  # 部分协议在末帧给 usage
    raw: dict | None = None  # 原始帧（脱敏后）


@dataclass
class AdapterResponse:
    """协议无关的归一化响应。"""

    http_status: int  # HTTP 状态码
    success: bool  # 是否成功（2xx 且结构合法）
    content: str | None = None  # 文本输出
    usage: TokenUsage | None = None  # 申报用量
    ttft_ms: int | None = None  # 首 token 时延（流式时有意义）
    feature_flags: dict = field(default_factory=dict)  # 功能性指纹原始信号（Gemini 等）
    raw_excerpt: dict | None = None  # 原始响应片段（脱敏，禁含 Key）
    error_message: str | None = None  # 错误简述（脱敏）


@dataclass
class ModelInfo:
    """模型列表拉取结果项（设计 §10.6）。"""

    model_name: str  # 模型标识
    protocol: str  # 归属协议
    access_mode: str  # native / openai_compat
    display_name: str | None = None
    gemini_endpoint_style: str | None = None  # gemini_developer / vertex


class ProviderAdapter(abc.ABC):
    """协议适配器抽象基类。

    子类须声明类属性 `protocol` 与 `access_mode`，并经 AdapterFactory 注册。
    """

    protocol: str
    access_mode: str

    @abc.abstractmethod
    async def chat(self, request: AdapterRequest) -> AdapterResponse:
        """发起非流式对话请求，返回归一化响应。"""
        raise NotImplementedError

    @abc.abstractmethod
    async def stream_chat(self, request: AdapterRequest) -> AsyncIterator[StreamChunk]:
        """发起流式对话请求，逐帧产出增量（用于 TTFT/吞吐探针）。"""
        raise NotImplementedError

    @abc.abstractmethod
    async def fetch_models(self) -> list[ModelInfo]:
        """拉取上游支持的模型列表；失败由调用方回退手动录入。"""
        raise NotImplementedError

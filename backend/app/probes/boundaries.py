"""探针边界条件处理（设计 §11.5）。

错误分类（九类）/重试退避/脱敏已由 `app.utils.errors` 与 `app.utils.http_client`
统一提供，本模块**不重复定义错误类别**，只补足 §11.5 尚未覆盖的边界判定，供探针与
引擎复用（DRY）：

  - finish_reason 正常截断 vs 异常截断归一（跨 OpenAI/Anthropic/Gemini 协议键名与取值）
  - 流式中断/不完整检测：已收 token 计入、标注 incomplete、不据残缺响应做能力/真实性定性
  - 空响应判定：区分"返回空内容"，不静默当成功

usage 缺失兜底已在计费探针（§8.4）就地处理（DEGRADED + 低置信度），不在此重复。
"""
from collections.abc import AsyncIterator
from enum import Enum

from app.providers.base import StreamChunk, TokenUsage
from app.utils.errors import ProbeError


class FinishReasonKind(str, Enum):
    """归一化的结束原因类别（设计 §11.5 正常截断 vs 异常截断）。"""

    STOP = "stop"  # 自然结束（stop/end_turn/tool_use 等）——正常
    LENGTH = "length"  # 长度截断（length/max_tokens）——正常截断，不计失败
    ABNORMAL = "abnormal"  # 异常截断（safety/recitation/refusal/error）——计失败
    UNKNOWN = "unknown"  # 缺失或无法识别

    @property
    def is_failure(self) -> bool:
        """是否应计为失败：仅异常截断；自然停止/长度截断不计失败（§11.5）。"""
        return self is FinishReasonKind.ABNORMAL


# 跨协议 finish_reason 取值归一（小写匹配）
_STOP_REASONS = frozenset(
    {"stop", "end_turn", "stop_sequence", "tool_use", "tool_calls"}
)
_LENGTH_REASONS = frozenset({"length", "max_tokens"})
_ABNORMAL_REASONS = frozenset(
    {
        "safety",
        "content_filter",
        "recitation",
        "refusal",
        "error",
        "blocklist",
        "prohibited_content",
        "spii",
        "malformed_function_call",
        "other",
    }
)
# raw_excerpt 中可能承载结束原因的键名：OpenAI/Gemini=finish_reason，Anthropic=stop_reason
_FINISH_KEYS = ("finish_reason", "stop_reason")


def classify_finish_reason(raw_excerpt: dict | None) -> FinishReasonKind:
    """从响应 raw_excerpt 提取并归一结束原因（大小写/协议无关）。"""
    if not raw_excerpt:
        return FinishReasonKind.UNKNOWN
    raw_value = None
    for key in _FINISH_KEYS:
        if raw_excerpt.get(key):
            raw_value = raw_excerpt[key]
            break
    if not raw_value:
        return FinishReasonKind.UNKNOWN
    token = str(raw_value).strip().lower()
    if token in _STOP_REASONS:
        return FinishReasonKind.STOP
    if token in _LENGTH_REASONS:
        return FinishReasonKind.LENGTH
    if token in _ABNORMAL_REASONS:
        return FinishReasonKind.ABNORMAL
    return FinishReasonKind.UNKNOWN


class StreamOutcome:
    """一次流式收集的归一结果（设计 §11.5 流式中途断连）。

    incomplete=True 表示流被中断（产出部分帧后异常断连）；已收 token 仍计入，但调用方
    不应据残缺响应做能力/真实性定性，仅供 stability 记一次失败、报告标注 incomplete。
    """

    def __init__(self) -> None:
        self.frames: int = 0  # 收到的内容帧数
        self.text: str = ""  # 累计文本
        self.usage: TokenUsage | None = None  # 末帧 usage（可空）
        self.incomplete: bool = False  # 是否中途断连/未正常收尾
        self.error_category: str | None = None  # 中断时的错误类别（脱敏，便于报告）

    @property
    def has_content(self) -> bool:
        """是否收到有效内容（至少一帧且文本非空）。"""
        return self.frames > 0 and bool(self.text)


async def collect_stream(chunks: AsyncIterator[StreamChunk]) -> StreamOutcome:
    """防御式收集流式分帧：产出部分帧后中断，保留已收内容并标注 incomplete。

    语义边界（§11.5 / §11.4）：
      - 已产出至少一帧后断连 → incomplete 降级（不整体 fail，已收 token 计入）；
      - 一帧未出即失败 → 属请求级错误（auth/quota/parse 等），向上抛由调用方按类别
        处置（含致命短路），不在此静默吞掉。
    """
    outcome = StreamOutcome()
    try:
        async for chunk in chunks:
            if chunk.delta_text:
                outcome.frames += 1
                outcome.text += chunk.delta_text
            if chunk.usage is not None:
                outcome.usage = chunk.usage
    except ProbeError as error:
        if outcome.frames == 0:
            # 尚未产出任何帧：非"中途断连"，交上层按错误类别处置（含致命短路）
            raise
        outcome.incomplete = True
        outcome.error_category = error.category.value
    return outcome


def is_empty_response(content: str | None) -> bool:
    """模型是否返回空内容（仅空白亦视为空，§11.5 不静默当成功）。"""
    return not (content and content.strip())

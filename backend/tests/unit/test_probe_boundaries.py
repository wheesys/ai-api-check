"""探针边界条件单元测试（Task 14，设计 §11.5）。

覆盖：finish_reason 跨协议正误截断归一、流式中断/不完整检测（部分帧后断连降级、
零帧失败上抛）、空响应判定。零网络：以内联异步生成器模拟流式分帧。
"""
import pytest

from app.probes.boundaries import (
    FinishReasonKind,
    StreamOutcome,
    classify_finish_reason,
    collect_stream,
    is_empty_response,
)
from app.providers.base import StreamChunk, TokenUsage
from app.utils.errors import ErrorCategory, ProbeError


# ---------- finish_reason 归一（跨协议正常 vs 异常截断） ----------

def test_finish_reason_stop_normal():
    assert classify_finish_reason({"finish_reason": "stop"}) is FinishReasonKind.STOP
    assert classify_finish_reason({"stop_reason": "end_turn"}) is FinishReasonKind.STOP
    assert classify_finish_reason({"stop_reason": "tool_use"}) is FinishReasonKind.STOP


def test_finish_reason_length_normal_truncation():
    assert classify_finish_reason({"finish_reason": "length"}) is FinishReasonKind.LENGTH
    # Anthropic max_tokens / Gemini MAX_TOKENS（大写）
    assert classify_finish_reason({"stop_reason": "max_tokens"}) is FinishReasonKind.LENGTH
    assert classify_finish_reason({"finish_reason": "MAX_TOKENS"}) is FinishReasonKind.LENGTH


def test_finish_reason_abnormal():
    assert classify_finish_reason({"finish_reason": "content_filter"}) is FinishReasonKind.ABNORMAL
    assert classify_finish_reason({"finish_reason": "SAFETY"}) is FinishReasonKind.ABNORMAL
    assert classify_finish_reason({"stop_reason": "refusal"}) is FinishReasonKind.ABNORMAL


def test_finish_reason_unknown():
    assert classify_finish_reason(None) is FinishReasonKind.UNKNOWN
    assert classify_finish_reason({}) is FinishReasonKind.UNKNOWN
    assert classify_finish_reason({"finish_reason": None}) is FinishReasonKind.UNKNOWN
    assert classify_finish_reason({"finish_reason": "天马行空"}) is FinishReasonKind.UNKNOWN


def test_finish_reason_is_failure_only_abnormal():
    assert FinishReasonKind.ABNORMAL.is_failure is True
    assert FinishReasonKind.STOP.is_failure is False
    assert FinishReasonKind.LENGTH.is_failure is False  # 正常截断不计失败
    assert FinishReasonKind.UNKNOWN.is_failure is False


# ---------- 流式收集与中断检测 ----------

async def _gen(chunks, *, error: Exception | None = None):
    """内联异步生成器：依次产出分帧，末尾可抛错模拟断连。"""
    for chunk in chunks:
        yield chunk
    if error is not None:
        raise error


async def test_collect_stream_complete():
    chunks = [
        StreamChunk(delta_text="你"),
        StreamChunk(delta_text="好"),
        StreamChunk(delta_text="", usage=TokenUsage(prompt_tokens=3, completion_tokens=2)),
    ]
    outcome = await collect_stream(_gen(chunks))
    assert outcome.incomplete is False
    assert outcome.frames == 2
    assert outcome.text == "你好"
    assert outcome.usage.completion_tokens == 2
    assert outcome.has_content is True


async def test_collect_stream_interrupted_after_frames_degrades():
    # 产出两帧后断连 → incomplete 降级，已收 token 仍计入（§11.5）
    chunks = [StreamChunk(delta_text="部"), StreamChunk(delta_text="分")]
    error = ProbeError(ErrorCategory.CONNECTIVITY, "连接断开")
    outcome = await collect_stream(_gen(chunks, error=error))
    assert outcome.incomplete is True
    assert outcome.frames == 2
    assert outcome.text == "部分"
    assert outcome.error_category == ErrorCategory.CONNECTIVITY.value


async def test_collect_stream_zero_frame_error_reraises():
    # 一帧未出即失败 → 属请求级错误，向上抛由调用方按类别短路处置
    error = ProbeError(ErrorCategory.AUTH, "Key 失效", http_status=401)
    with pytest.raises(ProbeError) as exc_info:
        await collect_stream(_gen([], error=error))
    assert exc_info.value.category is ErrorCategory.AUTH


async def test_collect_stream_empty_has_no_content():
    outcome = await collect_stream(_gen([StreamChunk(delta_text="")]))
    assert outcome.frames == 0
    assert outcome.has_content is False
    assert outcome.incomplete is False


# ---------- 空响应判定 ----------

def test_is_empty_response():
    assert is_empty_response(None) is True
    assert is_empty_response("") is True
    assert is_empty_response("   \n\t ") is True
    assert is_empty_response("有内容") is False

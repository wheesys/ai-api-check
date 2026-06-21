"""计费一致性与能力探针单元测试：偏差三态 + 成本核算 + 能力判定/skipped 不计负分。

零网络：注入 FakeAdapter（脚本化响应）与 StubEstimator（确定性估算）。
"""
import json

from app.probes.base import BudgetCounter, ProbeContext, ProbeStatus
from app.probes.billing import BillingConsistencyProbe
from app.probes.capability import (
    CapContextLengthProbe,
    CapFunctionCallProbe,
    CapJsonModeProbe,
    CapMultimodalProbe,
    CapStreamingProbe,
)
from app.probes.registry import ProbeRegistry
from app.providers.base import AdapterResponse, StreamChunk, TokenUsage
from app.utils.errors import ErrorCategory, ProbeError
from tests.fixtures.fake_adapter import FakeAdapter


class StubEstimator:
    """确定性 token 估算器替身。"""

    def __init__(self, value: int, *, exact: bool = True) -> None:
        self._value = value
        self.is_exact = exact

    def estimate_messages(self, _messages) -> int:
        return self._value


def _ctx(adapter, **overrides) -> ProbeContext:
    base = {"adapter": adapter, "model_name": "demo"}
    base.update(overrides)
    return ProbeContext(**base)


def _ok(content: str = "ok", **kwargs) -> AdapterResponse:
    return AdapterResponse(http_status=200, success=True, content=content, **kwargs)


# ---------- 计费一致性 ----------

async def test_billing_pass_within_threshold():
    adapter = FakeAdapter(chat_response=_ok(usage=TokenUsage(prompt_tokens=11)))
    probe = BillingConsistencyProbe(estimator=StubEstimator(10))
    result = await probe.run(_ctx(adapter))
    assert result.status is ProbeStatus.PASS  # 偏差 0.1 ≤ 0.15
    assert result.metrics["deviation"] == 0.1


async def test_billing_fail_on_large_deviation():
    adapter = FakeAdapter(chat_response=_ok(usage=TokenUsage(prompt_tokens=20)))
    probe = BillingConsistencyProbe(estimator=StubEstimator(10))
    result = await probe.run(_ctx(adapter))
    assert result.status is ProbeStatus.FAIL  # 偏差 1.0 > 0.40


async def test_billing_degraded_when_usage_missing():
    adapter = FakeAdapter(chat_response=_ok(usage=None))
    probe = BillingConsistencyProbe(estimator=StubEstimator(10))
    result = await probe.run(_ctx(adapter))
    assert result.status is ProbeStatus.DEGRADED
    assert result.evidence["confidence"] == "low"


async def test_billing_fail_on_probe_error():
    adapter = FakeAdapter(chat_error=ProbeError(ErrorCategory.AUTH, "Key 失效"))
    probe = BillingConsistencyProbe(estimator=StubEstimator(10))
    result = await probe.run(_ctx(adapter))
    assert result.status is ProbeStatus.FAIL


async def test_billing_cost_uses_decimal():
    usage = TokenUsage(prompt_tokens=100, completion_tokens=50)
    adapter = FakeAdapter(chat_response=_ok(usage=usage))
    probe = BillingConsistencyProbe(estimator=StubEstimator(100))
    ctx = _ctx(
        adapter,
        thresholds={"input_price": "0.000001", "output_price": "0.000002"},
    )
    result = await probe.run(ctx)
    assert result.status is ProbeStatus.PASS
    # 100*0.000001 + 50*0.000002 = 0.000200，Decimal 精确不丢精度
    assert result.metrics["declared_cost"] == "0.000200"


async def test_billing_skipped_when_budget_exhausted():
    adapter = FakeAdapter(chat_response=_ok(usage=TokenUsage(prompt_tokens=10)))
    probe = BillingConsistencyProbe(estimator=StubEstimator(10))
    result = await probe.run(_ctx(adapter, budget=BudgetCounter(max_requests=0)))
    assert result.status is ProbeStatus.SKIPPED


# ---------- 流式能力 ----------

async def test_cap_streaming_pass():
    adapter = FakeAdapter(stream_chunks=[StreamChunk(delta_text="Hi")])
    result = await CapStreamingProbe().run(_ctx(adapter))
    assert result.status is ProbeStatus.PASS
    assert result.metrics["content_frames"] == 1


async def test_cap_streaming_fail_no_frames():
    adapter = FakeAdapter(stream_chunks=[StreamChunk(delta_text="")])
    result = await CapStreamingProbe().run(_ctx(adapter))
    assert result.status is ProbeStatus.FAIL


async def test_cap_streaming_fail_on_error():
    adapter = FakeAdapter(stream_error=ProbeError(ErrorCategory.TIMEOUT, "超时"))
    result = await CapStreamingProbe().run(_ctx(adapter))
    assert result.status is ProbeStatus.FAIL


# ---------- 函数调用 ----------

async def test_cap_function_call_pass():
    response = _ok(feature_flags={"tool_calls": [{"name": "get_weather"}]})
    adapter = FakeAdapter(chat_response=response)
    result = await CapFunctionCallProbe().run(_ctx(adapter))
    assert result.status is ProbeStatus.PASS
    assert result.metrics["tool_call_count"] == 1


async def test_cap_function_call_degraded_without_structured_call():
    adapter = FakeAdapter(chat_response=_ok(content="北京天气晴"))
    result = await CapFunctionCallProbe().run(_ctx(adapter))
    assert result.status is ProbeStatus.DEGRADED


async def test_cap_function_call_skipped_on_capability_error():
    error = ProbeError(ErrorCategory.CAPABILITY, "不支持工具", http_status=400)
    adapter = FakeAdapter(chat_error=error)
    result = await CapFunctionCallProbe().run(_ctx(adapter))
    assert result.status is ProbeStatus.SKIPPED  # 不计负分


async def test_cap_function_call_fail_on_other_error():
    adapter = FakeAdapter(chat_error=ProbeError(ErrorCategory.UPSTREAM_5XX, "502"))
    result = await CapFunctionCallProbe().run(_ctx(adapter))
    assert result.status is ProbeStatus.FAIL


# ---------- 受控 JSON ----------

async def test_cap_json_mode_pass():
    adapter = FakeAdapter(chat_response=_ok(content=json.dumps({"city": "北京"})))
    result = await CapJsonModeProbe().run(_ctx(adapter))
    assert result.status is ProbeStatus.PASS


async def test_cap_json_mode_degraded_on_non_json():
    adapter = FakeAdapter(chat_response=_ok(content="这是一段普通文本"))
    result = await CapJsonModeProbe().run(_ctx(adapter))
    assert result.status is ProbeStatus.DEGRADED


async def test_cap_json_mode_skipped_on_capability_error():
    error = ProbeError(ErrorCategory.CAPABILITY, "不支持 JSON 模式", http_status=400)
    adapter = FakeAdapter(chat_error=error)
    result = await CapJsonModeProbe().run(_ctx(adapter))
    assert result.status is ProbeStatus.SKIPPED


# ---------- 多模态 ----------

async def test_cap_multimodal_skipped_when_not_declared():
    adapter = FakeAdapter(chat_response=_ok())
    result = await CapMultimodalProbe().run(_ctx(adapter))
    assert result.status is ProbeStatus.SKIPPED  # 未声明，不计负分


async def test_cap_multimodal_pass_when_declared():
    adapter = FakeAdapter(chat_response=_ok(content="图中是一只猫"))
    ctx = _ctx(adapter, declared_capabilities={"multimodal"})
    result = await CapMultimodalProbe().run(ctx)
    assert result.status is ProbeStatus.PASS


async def test_cap_multimodal_skipped_on_capability_error():
    error = ProbeError(ErrorCategory.CAPABILITY, "拒绝图像", http_status=400)
    adapter = FakeAdapter(chat_error=error)
    ctx = _ctx(adapter, declared_capabilities={"vision"})
    result = await CapMultimodalProbe().run(ctx)
    assert result.status is ProbeStatus.SKIPPED


# ---------- 上下文长度（二分逼近） ----------

async def test_cap_context_length_skipped_without_declared():
    adapter = FakeAdapter(chat_response=_ok())
    result = await CapContextLengthProbe().run(_ctx(adapter))
    assert result.status is ProbeStatus.SKIPPED


async def test_cap_context_length_pass_near_declared():
    # 申报 64，上游实际可接受 60 字符（1 字符/单位）→ ratio 0.9375 PASS
    adapter = FakeAdapter(chat_response=_ok(), fail_above_chars=60)
    probe = CapContextLengthProbe(padder=lambda units: "x" * units)
    ctx = _ctx(adapter, thresholds={"declared_context": 64})
    result = await probe.run(ctx)
    assert result.status is ProbeStatus.PASS
    assert result.metrics["measured_context"] == 60


async def test_cap_context_length_fail_far_below_declared():
    adapter = FakeAdapter(chat_response=_ok(), fail_above_chars=10)
    probe = CapContextLengthProbe(padder=lambda units: "x" * units)
    ctx = _ctx(adapter, thresholds={"declared_context": 64})
    result = await probe.run(ctx)
    assert result.status is ProbeStatus.FAIL  # ratio 10/64 ≈ 0.156


async def test_cap_context_length_respects_budget():
    adapter = FakeAdapter(chat_response=_ok())
    probe = CapContextLengthProbe(padder=lambda units: "x" * units)
    budget = BudgetCounter(max_requests=3)
    ctx = _ctx(adapter, thresholds={"declared_context": 1024}, budget=budget)
    result = await probe.run(ctx)
    assert result.metrics["probes_used"] == 3
    assert budget.exhausted is True


# ---------- 注册 ----------

def test_task11_probes_registered():
    keys = set(ProbeRegistry.all_keys())
    assert {
        "billing_consistency",
        "cap_streaming",
        "cap_function_call",
        "cap_json_mode",
        "cap_multimodal",
        "cap_context_length",
    } <= keys

"""连通性与性能探针单元测试：三态判定 + 预算/取消 + 耗时测量。

零网络：注入 FakeAdapter（脚本化响应）与 FakeClock（确定性耗时）。
"""
from app.probes.base import BudgetCounter, ProbeContext, ProbeStatus
from app.probes.connectivity import ConnectivityProbe
from app.probes.performance import StabilityProbe, ThroughputProbe, TTFTProbe
from app.providers.base import AdapterResponse, StreamChunk, TokenUsage
from app.utils.errors import ErrorCategory, ProbeError
from tests.fixtures.fake_adapter import FakeAdapter, FakeClock


def _ctx(adapter, **overrides) -> ProbeContext:
    base = {"adapter": adapter, "model_name": "demo"}
    base.update(overrides)
    return ProbeContext(**base)


def _ok(content: str = "ok") -> AdapterResponse:
    return AdapterResponse(http_status=200, success=True, content=content)


# ---------- 连通性 ----------

async def test_connectivity_pass():
    adapter = FakeAdapter(chat_response=_ok())
    result = await ConnectivityProbe().run(_ctx(adapter))
    assert result.status is ProbeStatus.PASS
    assert result.metrics["http_status"] == 200


async def test_connectivity_fail_on_probe_error():
    error = ProbeError(ErrorCategory.AUTH, "Key 失效", http_status=401)
    adapter = FakeAdapter(chat_error=error)
    result = await ConnectivityProbe().run(_ctx(adapter))
    assert result.status is ProbeStatus.FAIL
    assert result.evidence["error_category"] == "auth_error"
    assert result.metrics["http_status"] == 401


async def test_connectivity_fail_on_empty_content():
    adapter = FakeAdapter(
        chat_response=AdapterResponse(http_status=200, success=True, content=None)
    )
    result = await ConnectivityProbe().run(_ctx(adapter))
    assert result.status is ProbeStatus.FAIL


async def test_connectivity_skipped_when_budget_exhausted():
    adapter = FakeAdapter(chat_response=_ok())
    budget = BudgetCounter(max_requests=0)
    result = await ConnectivityProbe().run(_ctx(adapter, budget=budget))
    assert result.status is ProbeStatus.SKIPPED


# ---------- TTFT ----------

async def test_ttft_pass_with_injected_clock():
    # 时钟：start=0.0，首内容帧=0.2s → 200ms ≤ pass(800)
    adapter = FakeAdapter(stream_chunks=[StreamChunk(delta_text="Hi")])
    clock = FakeClock([0.0, 0.2])
    ctx = _ctx(adapter, thresholds={"ttft_samples": 1})
    result = await TTFTProbe(clock=clock).run(ctx)
    assert result.status is ProbeStatus.PASS
    assert result.metrics["ttft_ms"] == 200


async def test_ttft_degraded_by_threshold():
    adapter = FakeAdapter(stream_chunks=[StreamChunk(delta_text="Hi")])
    clock = FakeClock([0.0, 1.5])  # 1500ms：pass(800)<x≤degraded(2500)
    ctx = _ctx(adapter, thresholds={"ttft_samples": 1})
    result = await TTFTProbe(clock=clock).run(ctx)
    assert result.status is ProbeStatus.DEGRADED


async def test_ttft_fail_when_no_content_frame():
    # 仅 usage 帧、无内容 → 无法测 TTFT
    adapter = FakeAdapter(
        stream_chunks=[StreamChunk(delta_text="", usage=TokenUsage(total_tokens=1))]
    )
    ctx = _ctx(adapter, thresholds={"ttft_samples": 1})
    result = await TTFTProbe(clock=FakeClock([0.0])).run(ctx)
    assert result.status is ProbeStatus.FAIL


# ---------- 吞吐 ----------

async def test_throughput_pass_uses_declared_usage():
    # 输出 40 token / 2s = 20 tps ≥ pass(20)
    chunks = [
        StreamChunk(delta_text="a"),
        StreamChunk(delta_text="b", usage=TokenUsage(completion_tokens=40)),
    ]
    adapter = FakeAdapter(stream_chunks=chunks)
    clock = FakeClock([0.0, 2.0])
    result = await ThroughputProbe(clock=clock).run(_ctx(adapter))
    assert result.status is ProbeStatus.PASS
    assert result.metrics["output_tokens"] == 40
    assert result.metrics["tokens_per_sec"] == 20.0


async def test_throughput_fail_on_stream_error():
    adapter = FakeAdapter(stream_error=ProbeError(ErrorCategory.TIMEOUT, "超时"))
    result = await ThroughputProbe(clock=FakeClock([0.0, 1.0])).run(_ctx(adapter))
    assert result.status is ProbeStatus.FAIL


# ---------- 稳定性 ----------

async def test_stability_pass_all_success():
    adapter = FakeAdapter(chat_response=_ok())
    clock = FakeClock([0.0, 0.1, 0.1, 0.2, 0.2, 0.3, 0.3, 0.4, 0.4, 0.5])
    ctx = _ctx(adapter, thresholds={"stability_repeats": 5})
    result = await StabilityProbe(clock=clock).run(ctx)
    assert result.status is ProbeStatus.PASS
    assert result.metrics["success_rate"] == 1.0
    assert result.metrics["attempts"] == 5


async def test_stability_degraded_on_partial_failure():
    # 5 次中 2 次抛错 → 成功率 0.6：degraded(0.8)>x，FAIL? 0.6<0.8 → FAIL
    sequence = [
        _ok(),
        ProbeError(ErrorCategory.UPSTREAM_5XX, "502"),
        _ok(),
        ProbeError(ErrorCategory.UPSTREAM_5XX, "502"),
        _ok(),
    ]
    adapter = FakeAdapter(chat_sequence=sequence)
    ctx = _ctx(adapter, thresholds={"stability_repeats": 5})
    result = await StabilityProbe(clock=FakeClock([0.0])).run(ctx)
    assert result.metrics["success_rate"] == 0.6
    assert result.status is ProbeStatus.FAIL


async def test_stability_degraded_band():
    # 5 次中 1 次失败 → 0.8 命中 degraded 下界
    sequence = [_ok(), _ok(), _ok(), _ok(), ProbeError(ErrorCategory.TIMEOUT, "t")]
    adapter = FakeAdapter(chat_sequence=sequence)
    ctx = _ctx(adapter, thresholds={"stability_repeats": 5})
    result = await StabilityProbe(clock=FakeClock([0.0])).run(ctx)
    assert result.metrics["success_rate"] == 0.8
    assert result.status is ProbeStatus.DEGRADED


async def test_stability_respects_budget():
    adapter = FakeAdapter(chat_response=_ok())
    budget = BudgetCounter(max_requests=2)
    ctx = _ctx(adapter, thresholds={"stability_repeats": 5}, budget=budget)
    result = await StabilityProbe(clock=FakeClock([0.0])).run(ctx)
    # 预算仅允许 2 次
    assert result.metrics["attempts"] == 2
    assert budget.exhausted is True

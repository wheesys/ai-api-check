"""检测任务执行器集成测试（Task 15，设计 §8.1 / §11.4 / §11.6）。

覆盖：正常完成 + 事件序列、连通性短路、取消、单点失败隔离、任务级超时、评分钩子、
不适用探针 skipped、类别内并发上限。零网络：以 FakeProbe 替身（覆写 run，不触网）。
"""
import asyncio

from app.engine.executor import (
    ExecutionResult,
    SSEEvent,
    TaskExecutor,
    TaskStatus,
)
from app.probes.base import Probe, ProbeCategory, ProbeContext, ProbeResult, ProbeStatus
from tests.fixtures.fake_adapter import FakeAdapter


class FakeProbe(Probe):
    """可脚本化的探针替身：覆写 run，不触网。"""

    def __init__(
        self,
        key: str,
        category: str,
        status: ProbeStatus = ProbeStatus.PASS,
        *,
        raises: Exception | None = None,
        delay: float = 0.0,
        applicable_flag: bool = True,
        on_run=None,
    ) -> None:
        self.key = key
        self.category = category
        self.name = key
        self.weight = 1.0
        self._status = status
        self._raises = raises
        self._delay = delay
        self._applicable = applicable_flag
        self._on_run = on_run
        self.ran = False

    def applicable(self, ctx: ProbeContext) -> bool:
        return self._applicable

    async def run(self, ctx: ProbeContext) -> ProbeResult:
        self.ran = True
        if self._on_run is not None:
            self._on_run()
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._raises is not None:
            raise self._raises
        return self.make_result(self._status, score=1.0)


def _ctx(*, cancelled: bool = False) -> ProbeContext:
    return ProbeContext(
        adapter=FakeAdapter(),
        model_name="demo",
        is_cancelled=lambda: cancelled,
    )


async def _collect(probes, ctx, **kwargs) -> tuple[ExecutionResult, list[SSEEvent]]:
    events: list[SSEEvent] = []

    async def emit(event: SSEEvent) -> None:
        events.append(event)

    executor = kwargs.pop("executor", TaskExecutor())
    result = await executor.run(probes, ctx, emit=emit, **kwargs)
    return result, events


# ---------- 正常完成 + 事件序列 ----------

async def test_full_run_completes_with_events():
    probes = [
        FakeProbe("connectivity", ProbeCategory.CONNECTIVITY.value),
        FakeProbe("ttft", ProbeCategory.PERFORMANCE.value),
        FakeProbe("cap_streaming", ProbeCategory.CAPABILITY.value),
    ]
    result, events = await _collect(probes, _ctx())
    assert result.status is TaskStatus.COMPLETED
    assert result.progress == 1.0
    assert len(result.probe_results) == 3
    types = [event.type for event in events]
    assert types[0] == "task.started"
    assert types[-1] == "task.completed"
    assert types.count("probe.completed") == 3
    assert events[0].data["total_probes"] == 3


async def test_progress_increments_monotonically():
    probes = [
        FakeProbe("connectivity", ProbeCategory.CONNECTIVITY.value),
        FakeProbe("ttft", ProbeCategory.PERFORMANCE.value),
    ]
    _result, events = await _collect(probes, _ctx())
    progresses = [e.data["progress"] for e in events if e.type == "probe.completed"]
    assert progresses == [0.5, 1.0]


# ---------- 连通性短路 ----------

async def test_connectivity_failure_short_circuits():
    others_ran = []
    probes = [
        FakeProbe("connectivity", ProbeCategory.CONNECTIVITY.value, ProbeStatus.FAIL),
        FakeProbe(
            "ttft",
            ProbeCategory.PERFORMANCE.value,
            on_run=lambda: others_ran.append("ttft"),
        ),
    ]
    result, events = await _collect(probes, _ctx())
    assert result.status is TaskStatus.FAILED
    assert result.short_circuited is True
    assert result.failure_reason == "connectivity_failed"
    assert result.progress == 1.0
    assert others_ran == []  # 后续探针未执行
    assert events[-1].type == "task.failed"


# ---------- 取消 ----------

async def test_cancel_before_start():
    probes = [FakeProbe("connectivity", ProbeCategory.CONNECTIVITY.value)]
    result, events = await _collect(probes, _ctx(cancelled=True))
    assert result.status is TaskStatus.CANCELED
    assert events[-1].type == "task.canceled"


async def test_cancel_between_categories():
    # 连通性通过后取消 → 其余类别不再执行
    cancel_state = {"flag": False}
    ctx = ProbeContext(
        adapter=FakeAdapter(),
        model_name="demo",
        is_cancelled=lambda: cancel_state["flag"],
    )

    def trip_cancel():
        cancel_state["flag"] = True

    probes = [
        FakeProbe(
            "connectivity", ProbeCategory.CONNECTIVITY.value, on_run=trip_cancel
        ),
        FakeProbe("ttft", ProbeCategory.PERFORMANCE.value),
    ]
    result, _events = await _collect(probes, ctx)
    assert result.status is TaskStatus.CANCELED
    assert len(result.probe_results) == 1  # 仅连通性落库


# ---------- 单点失败隔离 ----------

async def test_probe_exception_isolated_and_continues():
    probes = [
        FakeProbe("connectivity", ProbeCategory.CONNECTIVITY.value),
        FakeProbe(
            "billing_consistency",
            ProbeCategory.BILLING.value,
            raises=RuntimeError("boom"),
        ),
        FakeProbe("cap_streaming", ProbeCategory.CAPABILITY.value),
    ]
    result, _events = await _collect(probes, _ctx())
    assert result.status is TaskStatus.COMPLETED
    by_key = {r.key: r for r in result.probe_results}
    assert by_key["billing_consistency"].status is ProbeStatus.FAIL
    assert "异常" in by_key["billing_consistency"].evidence["reason"]
    assert by_key["cap_streaming"].status is ProbeStatus.PASS  # 后续仍执行


# ---------- 任务级超时 ----------

async def test_task_timeout_fails():
    probes = [
        FakeProbe("connectivity", ProbeCategory.CONNECTIVITY.value),
        FakeProbe("ttft", ProbeCategory.PERFORMANCE.value, delay=0.5),
    ]
    executor = TaskExecutor(task_timeout_seconds=0.05)
    result, events = await _collect(probes, _ctx(), executor=executor)
    assert result.status is TaskStatus.FAILED
    assert result.failure_reason == "task_timeout"
    assert events[-1].type == "task.failed"


# ---------- 评分钩子 ----------

async def test_scorer_hook_emits_scored_event():
    probes = [FakeProbe("connectivity", ProbeCategory.CONNECTIVITY.value)]

    def scorer(results):
        return {"overall": 88, "count": len(results)}

    result, events = await _collect(probes, _ctx(), scorer=scorer)
    assert result.score == {"overall": 88, "count": 1}
    scored = [e for e in events if e.type == "task.scored"]
    assert scored and scored[0].data["score"]["overall"] == 88


async def test_async_scorer_supported():
    probes = [FakeProbe("connectivity", ProbeCategory.CONNECTIVITY.value)]

    async def scorer(results):
        return {"overall": 70}

    result, _events = await _collect(probes, _ctx(), scorer=scorer)
    assert result.score == {"overall": 70}


# ---------- 不适用探针 ----------

async def test_inapplicable_probe_skipped_without_running():
    inapplicable = FakeProbe(
        "gemini_thinking",
        ProbeCategory.AUTHENTICITY.value,
        applicable_flag=False,
    )
    probes = [
        FakeProbe("connectivity", ProbeCategory.CONNECTIVITY.value),
        inapplicable,
    ]
    result, _events = await _collect(probes, _ctx())
    assert result.status is TaskStatus.COMPLETED
    assert inapplicable.ran is False  # 未触网
    by_key = {r.key: r for r in result.probe_results}
    assert by_key["gemini_thinking"].status is ProbeStatus.SKIPPED


# ---------- 类别内并发上限 ----------

async def test_within_category_concurrency_capped():
    active = {"now": 0, "max": 0}

    def enter():
        active["now"] += 1
        active["max"] = max(active["max"], active["now"])

    class ConcProbe(FakeProbe):
        async def run(self, ctx):
            enter()
            await asyncio.sleep(0.02)
            active["now"] -= 1
            return self.make_result(ProbeStatus.PASS, score=1.0)

    probes = [
        ConcProbe(f"cap_{i}", ProbeCategory.CAPABILITY.value) for i in range(5)
    ]
    executor = TaskExecutor(max_concurrency=2)
    result, _events = await _collect(probes, _ctx(), executor=executor)
    assert result.status is TaskStatus.COMPLETED
    assert active["max"] <= 2  # 并发不超过上限

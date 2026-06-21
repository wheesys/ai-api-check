"""检测任务执行器（设计 §8.1 / §11.4 / §11.6）。

编排单个 (站点, 模型) 的一次检测：连通性先行短路 → 其余探针按类别串行、类别内受控
并发 → 逐探针发事件 → 取消/超时边界 → 进度计算。

职责边界（SOLID-S）：执行器只负责编排与 SSE 事件发射，**不感知持久化**（落库由服务层
做）；评分经可选 `scorer` 钩子注入（SOLID-O/-D，Task 18-19 接入），不在执行器内耦合
评分细节。单点探针失败被隔离为一条 fail 结果并继续（§11.4），仅连通性失败触发全任务
致命短路。
"""
import asyncio
import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum

from app.probes.base import Probe, ProbeCategory, ProbeContext, ProbeResult, ProbeStatus
from app.security.sanitizer import ErrorSanitizer

# 非连通性探针的类别执行顺序（连通性单独先行短路，见 _execute）
_CATEGORY_ORDER = (
    ProbeCategory.PERFORMANCE.value,
    ProbeCategory.BILLING.value,
    ProbeCategory.CAPABILITY.value,
    ProbeCategory.AUTHENTICITY.value,
)


class TaskStatus(str, Enum):
    """任务生命周期终态（设计 §8.1 / §11.6）。"""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"  # 致命短路 / 任务级超时
    CANCELED = "canceled"  # 主动取消


@dataclass
class SSEEvent:
    """一条 SSE 进度事件（设计 §8.1 第 4 步 / §10 接口契约）。"""

    type: str  # task.started/probe.completed/task.scored/task.completed/task.failed/task.canceled
    data: dict = field(default_factory=dict)


# 事件接收器：异步回调（默认 no-op）；服务层注入真正的 SSE 推送
EventSink = Callable[[SSEEvent], Awaitable[None]]
# 评分钩子：对探针结果求评分，可同步或异步（Task 18-19 注入）
Scorer = Callable[[list[ProbeResult]], object]


@dataclass
class ExecutionResult:
    """一次检测执行的归一结果（供服务层落库与报告）。"""

    status: TaskStatus
    probe_results: list[ProbeResult]
    progress: float
    score: object | None = None
    short_circuited: bool = False
    failure_reason: str | None = None


async def _noop_emit(_event: SSEEvent) -> None:
    """默认事件接收器：丢弃（无监听时不阻塞执行）。"""
    return None


def _progress(done: int, total: int) -> float:
    """进度 = 已完成 / 启用总数；总数为 0 视为已满。"""
    if total <= 0:
        return 1.0
    return round(done / total, 4)


def _final_event_type(status: TaskStatus) -> str:
    """终态对应的 SSE 事件类型。"""
    if status is TaskStatus.COMPLETED:
        return "task.completed"
    if status is TaskStatus.CANCELED:
        return "task.canceled"
    return "task.failed"


class TaskExecutor:
    """检测任务执行器：两级并发的第一级（任务内类别并发）在此实现。

    任务间全局并发由 Scheduler（Task 16）维护。
    """

    def __init__(
        self, *, max_concurrency: int = 2, task_timeout_seconds: float | None = None
    ) -> None:
        # 类别内并发上限（受 token 预算进一步约束，预算耗尽时探针自行 skipped）
        self._max_concurrency = max(1, max_concurrency)
        # 任务级总超时兜底（防个别探针卡死拖垮整任务，§11.6）；None 表示不设
        self._task_timeout = task_timeout_seconds

    async def run(
        self,
        probes: list[Probe],
        ctx: ProbeContext,
        *,
        emit: EventSink | None = None,
        scorer: Scorer | None = None,
    ) -> ExecutionResult:
        """执行整条探针序列，返回归一结果并沿途发射 SSE 事件。"""
        emit = emit or _noop_emit
        total = len(probes)
        completed: list[ProbeResult] = []
        await emit(SSEEvent("task.started", {"total_probes": total}))

        try:
            coro = self._execute(probes, ctx, emit, completed, total)
            if self._task_timeout is not None:
                outcome = await asyncio.wait_for(coro, timeout=self._task_timeout)
            else:
                outcome = await coro
        except asyncio.TimeoutError:
            result = ExecutionResult(
                status=TaskStatus.FAILED,
                probe_results=completed,
                progress=_progress(len(completed), total),
                failure_reason="task_timeout",
            )
            await emit(
                SSEEvent("task.failed", {"status": result.status.value, "reason": "task_timeout"})
            )
            return result

        # 仅正常完成才评分（短路/取消的残缺结果不强行给分，§11.7）
        if outcome.status is TaskStatus.COMPLETED and scorer is not None:
            score = scorer(outcome.probe_results)
            if inspect.isawaitable(score):
                score = await score
            outcome.score = score
            await emit(SSEEvent("task.scored", {"score": score}))

        payload = {"status": outcome.status.value, "progress": outcome.progress}
        if outcome.failure_reason:
            payload["reason"] = outcome.failure_reason
        await emit(SSEEvent(_final_event_type(outcome.status), payload))
        return outcome

    async def _execute(
        self,
        probes: list[Probe],
        ctx: ProbeContext,
        emit: EventSink,
        completed: list[ProbeResult],
        total: int,
    ) -> ExecutionResult:
        """探针主循环：连通性先行短路，其余按类别串行 + 类别内并发。"""
        if ctx.cancelled():
            return ExecutionResult(
                TaskStatus.CANCELED, completed, _progress(len(completed), total)
            )

        connectivity = ProbeCategory.CONNECTIVITY.value
        conn_probes = [p for p in probes if p.category == connectivity]
        other_probes = [p for p in probes if p.category != connectivity]

        # 1. 连通性先行：任一失败即致命短路（后续探针无意义，§8.1 第 2 步）
        for probe in conn_probes:
            result = await self._run_one(probe, ctx)
            completed.append(result)
            await self._emit_probe(emit, result, len(completed), total)
            if result.status is ProbeStatus.FAIL:
                return ExecutionResult(
                    TaskStatus.FAILED,
                    completed,
                    1.0,  # 短路时进度直接置满（§8.1 进度计算）
                    short_circuited=True,
                    failure_reason="connectivity_failed",
                )

        # 2. 其余探针按类别串行、类别内受控并发
        grouped = _group_by_category(other_probes)
        for category in _CATEGORY_ORDER:
            group = grouped.get(category)
            if not group:
                continue
            if ctx.cancelled():
                return ExecutionResult(
                    TaskStatus.CANCELED, completed, _progress(len(completed), total)
                )
            results = await self._run_category(group, ctx)
            for result in results:
                completed.append(result)
                await self._emit_probe(emit, result, len(completed), total)

        return ExecutionResult(TaskStatus.COMPLETED, completed, 1.0)

    async def _run_category(
        self, group: list[Probe], ctx: ProbeContext
    ) -> list[ProbeResult]:
        """类别内受控并发执行；返回保持输入顺序的结果（便于确定性落库/测试）。"""
        semaphore = asyncio.Semaphore(self._max_concurrency)

        async def _guarded(probe: Probe) -> ProbeResult:
            async with semaphore:
                if ctx.cancelled():
                    return probe.skipped("任务已取消")
                return await self._run_one(probe, ctx)

        return list(await asyncio.gather(*[_guarded(probe) for probe in group]))

    async def _run_one(self, probe: Probe, ctx: ProbeContext) -> ProbeResult:
        """单探针执行：不适用则 skipped；异常被隔离为 fail（§11.4 单点失败隔离）。"""
        try:
            if not probe.applicable(ctx):
                return probe.skipped("不适用该模型/接入形态")
            return await probe.run(ctx)
        except Exception as error:  # noqa: BLE001 单点隔离：任何异常不得拖垮整任务
            # CancelledError 属 BaseException，不会被此捕获（保留超时/取消语义）
            return probe.make_result(
                ProbeStatus.FAIL,
                score=0.0,
                evidence={
                    "reason": "探针执行异常",
                    "detail": ErrorSanitizer.sanitize(str(error)) or error.__class__.__name__,
                },
            )

    async def _emit_probe(
        self, emit: EventSink, result: ProbeResult, done: int, total: int
    ) -> None:
        """发射单探针完成事件（含归一进度）。"""
        await emit(
            SSEEvent(
                "probe.completed",
                {
                    "strategy_key": result.key,
                    "category": result.category,
                    "status": result.status.value,
                    "progress": _progress(done, total),
                },
            )
        )


def _group_by_category(probes: list[Probe]) -> dict[str, list[Probe]]:
    """按 category 分组，保持各组内原始顺序。"""
    grouped: dict[str, list[Probe]] = {}
    for probe in probes:
        grouped.setdefault(probe.category, []).append(probe)
    return grouped

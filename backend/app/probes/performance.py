"""性能探针：TTFT / 吞吐 / 稳定性（设计 §8.3）。

均基于注入的 ProviderAdapter 流式/非流式收发；耗时测量用可注入时钟（默认
time.perf_counter），便于零网络确定性单测。阈值取 ctx.thresholds，缺省内置兜底。
"""
import time
from collections.abc import Callable

from app.probes._common import (
    PERF_PROMPT_MESSAGES,
    PING_MESSAGES,
    grade_higher_better,
    grade_lower_better,
    median,
    percentile,
)
from app.probes.base import Probe, ProbeCategory, ProbeContext, ProbeResult, ProbeStatus
from app.probes.registry import ProbeRegistry
from app.providers.base import AdapterRequest
from app.utils.errors import ProbeError

# 缺省阈值兜底（可被 ctx.thresholds 覆盖；正式阈值在 Task 17 标定）
_DEFAULT_TTFT_MS = {"pass": 800.0, "degraded": 2500.0}
_DEFAULT_THROUGHPUT_TPS = {"pass": 20.0, "degraded": 5.0}
_DEFAULT_STABILITY = {"pass": 0.99, "degraded": 0.8}

Clock = Callable[[], float]


@ProbeRegistry.register
class TTFTProbe(Probe):
    """首 token 时延：流式取首个有效内容帧耗时，多次取中位。"""

    key = "ttft"
    category = ProbeCategory.PERFORMANCE.value
    name = "首 token 时延"
    weight = 1.0

    def __init__(self, clock: Clock = time.perf_counter) -> None:
        self._clock = clock

    async def run(self, ctx: ProbeContext) -> ProbeResult:
        samples_n = int(ctx.thresholds.get("ttft_samples", 3))
        request = AdapterRequest(
            model_name=ctx.model_name,
            messages=PERF_PROMPT_MESSAGES,
            max_tokens=64,
            stream=True,
        )
        samples: list[float] = []
        for _ in range(samples_n):
            if ctx.cancelled() or (ctx.budget is not None and ctx.budget.exhausted):
                break
            ttft_ms = await self._measure_once(ctx, request)
            if ctx.budget is not None:
                ctx.budget.consume()
            if ttft_ms is not None:
                samples.append(ttft_ms)
        if not samples:
            return self.make_result(
                ProbeStatus.FAIL, score=0.0, evidence={"reason": "未获取到内容帧"}
            )
        value = median(samples)
        thresholds = ctx.thresholds.get("ttft_ms", _DEFAULT_TTFT_MS)
        status = grade_lower_better(
            value, thresholds["pass"], thresholds["degraded"]
        )
        return self.make_result(
            status,
            metrics={"ttft_ms": int(value), "samples": len(samples)},
        )

    async def _measure_once(
        self, ctx: ProbeContext, request: AdapterRequest
    ) -> float | None:
        """单次测量：返回首个内容帧的毫秒耗时；失败/无内容返回 None。"""
        start = self._clock()
        try:
            async for chunk in ctx.adapter.stream_chat(request):
                if chunk.delta_text:
                    return (self._clock() - start) * 1000
        except ProbeError:
            return None
        return None


@ProbeRegistry.register
class ThroughputProbe(Probe):
    """吞吐：流式统计输出 token/s（优先用申报 usage，缺失退化为帧计数）。"""

    key = "throughput"
    category = ProbeCategory.PERFORMANCE.value
    name = "吞吐"
    weight = 1.0

    def __init__(self, clock: Clock = time.perf_counter) -> None:
        self._clock = clock

    async def run(self, ctx: ProbeContext) -> ProbeResult:
        if ctx.budget is not None and ctx.budget.exhausted:
            return self.skipped("预算耗尽")
        request = AdapterRequest(
            model_name=ctx.model_name,
            messages=PERF_PROMPT_MESSAGES,
            max_tokens=128,
            stream=True,
        )
        start = self._clock()
        content_frames = 0
        last_completion_tokens: int | None = None
        try:
            async for chunk in ctx.adapter.stream_chat(request):
                if chunk.delta_text:
                    content_frames += 1
                if chunk.usage is not None and chunk.usage.completion_tokens:
                    last_completion_tokens = chunk.usage.completion_tokens
        except ProbeError as error:
            return self.make_result(
                ProbeStatus.FAIL,
                score=0.0,
                evidence={"error_category": error.category.value},
            )
        finally:
            if ctx.budget is not None:
                ctx.budget.consume()
        elapsed = self._clock() - start
        if elapsed <= 0 or content_frames == 0:
            return self.make_result(
                ProbeStatus.FAIL, score=0.0, evidence={"reason": "无有效输出"}
            )
        # 优先申报 token 数，缺失时以内容帧数兜底估算
        output_tokens = last_completion_tokens or content_frames
        tokens_per_sec = output_tokens / elapsed
        thresholds = ctx.thresholds.get("throughput_tps", _DEFAULT_THROUGHPUT_TPS)
        status = grade_higher_better(
            tokens_per_sec, thresholds["pass"], thresholds["degraded"]
        )
        return self.make_result(
            status,
            metrics={
                "tokens_per_sec": round(tokens_per_sec, 2),
                "output_tokens": output_tokens,
                "elapsed_ms": int(elapsed * 1000),
            },
        )


@ProbeRegistry.register
class StabilityProbe(Probe):
    """稳定性：重复 N 次最小请求，统计成功率与 p95 延迟。"""

    key = "stability"
    category = ProbeCategory.PERFORMANCE.value
    name = "稳定性"
    weight = 1.0

    def __init__(self, clock: Clock = time.perf_counter) -> None:
        self._clock = clock

    async def run(self, ctx: ProbeContext) -> ProbeResult:
        repeats = int(ctx.thresholds.get("stability_repeats", 5))
        request = AdapterRequest(
            model_name=ctx.model_name, messages=PING_MESSAGES, max_tokens=1
        )
        latencies: list[float] = []
        successes = 0
        attempts = 0
        for _ in range(repeats):
            if ctx.cancelled() or (ctx.budget is not None and ctx.budget.exhausted):
                break
            attempts += 1
            start = self._clock()
            try:
                response = await ctx.adapter.chat(request)
                if response.success:
                    successes += 1
                    latencies.append((self._clock() - start) * 1000)
            except ProbeError:
                pass
            finally:
                if ctx.budget is not None:
                    ctx.budget.consume()
        if attempts == 0:
            return self.skipped("预算耗尽或已取消")
        success_rate = successes / attempts
        p95_ms = percentile(latencies, 0.95)
        thresholds = ctx.thresholds.get("stability", _DEFAULT_STABILITY)
        status = grade_higher_better(
            success_rate, thresholds["pass"], thresholds["degraded"]
        )
        return self.make_result(
            status,
            metrics={
                "success_rate": round(success_rate, 4),
                "p95_ms": int(p95_ms),
                "attempts": attempts,
            },
        )

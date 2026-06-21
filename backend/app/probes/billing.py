"""计费一致性探针（key: `billing_consistency`，设计 §8.4）。

发起固定提示词请求，取上游申报 usage，与本地 tokenizer 估算比对偏差率，
并（在已配置单价时）以 Decimal 精确核算成本。偏差大或 usage 缺失时降级，
作为真实性"套壳换底"的弱信号联动（设计 §9.3 `shell_usage_missing`）。
"""
from decimal import Decimal, InvalidOperation

from app.probes._common import BILLING_PROMPT_MESSAGES, grade_lower_better
from app.probes.base import Probe, ProbeCategory, ProbeContext, ProbeResult, ProbeStatus
from app.probes.registry import ProbeRegistry
from app.providers.base import AdapterRequest
from app.utils.errors import ProbeError
from app.utils.tokenizer import TokenEstimator

# 缺省偏差阈值（占本地估算比例，越小越好；正式标定在 Task 17）
_DEFAULT_DEVIATION = {"pass": 0.15, "degraded": 0.40}


@ProbeRegistry.register
class BillingConsistencyProbe(Probe):
    """计费一致性：申报输入 token 数 vs 本地估算偏差，附 Decimal 成本核算。"""

    key = "billing_consistency"
    category = ProbeCategory.BILLING.value
    name = "计费一致性"
    weight = 1.0

    def __init__(self, estimator: TokenEstimator | None = None) -> None:
        # 估算器可注入，便于测试与替换协议专属 tokenizer（SOLID-D）
        self._estimator = estimator or TokenEstimator()

    async def run(self, ctx: ProbeContext) -> ProbeResult:
        if ctx.budget is not None and ctx.budget.exhausted:
            return self.skipped("预算耗尽")
        request = AdapterRequest(
            model_name=ctx.model_name,
            messages=BILLING_PROMPT_MESSAGES,
            max_tokens=32,
        )
        try:
            response = await ctx.adapter.chat(request)
        except ProbeError as error:
            return self.make_result(
                ProbeStatus.FAIL,
                score=0.0,
                evidence={"error_category": error.category.value},
            )
        finally:
            if ctx.budget is not None:
                ctx.budget.consume()

        estimated_prompt = self._estimator.estimate_messages(BILLING_PROMPT_MESSAGES)
        usage = response.usage
        declared_prompt = usage.prompt_tokens if usage is not None else None

        # usage 缺失（如 Anthropic 流式默认无 usage）：降级并下调置信度，不判 FAIL
        if declared_prompt is None:
            return self.make_result(
                ProbeStatus.DEGRADED,
                metrics={"estimated_prompt_tokens": estimated_prompt},
                evidence={
                    "reason": "申报 usage 缺失，仅本地估算兜底",
                    "confidence": "low",
                },
            )

        deviation = abs(declared_prompt - estimated_prompt) / max(estimated_prompt, 1)
        thresholds = ctx.thresholds.get("billing_deviation", _DEFAULT_DEVIATION)
        status = grade_lower_better(
            deviation, thresholds["pass"], thresholds["degraded"]
        )
        metrics = {
            "declared_prompt_tokens": declared_prompt,
            "estimated_prompt_tokens": estimated_prompt,
            "deviation": round(deviation, 4),
        }
        cost = self._compute_cost(ctx, usage)
        if cost is not None:
            metrics["declared_cost"] = str(cost)
        return self.make_result(
            status,
            metrics=metrics,
            evidence={
                "confidence": "exact" if self._estimator.is_exact else "approx"
            },
        )

    @staticmethod
    def _compute_cost(ctx: ProbeContext, usage) -> Decimal | None:
        """以 Decimal 核算申报成本（单价取阈值表，缺失则返回 None，不强制）。"""
        input_price = ctx.thresholds.get("input_price")
        output_price = ctx.thresholds.get("output_price")
        if input_price is None or output_price is None:
            return None
        try:
            prompt = Decimal(usage.prompt_tokens or 0)
            completion = Decimal(usage.completion_tokens or 0)
            return prompt * Decimal(str(input_price)) + completion * Decimal(
                str(output_price)
            )
        except (InvalidOperation, TypeError):
            return None

"""维度与总分聚合（设计 §8.8 / §11.7）。

将探针三态结果按维度聚合，再加权合成总分：
  - 维度分 = 该类别非 skipped 策略的加权平均（PASS=1 / DEGRADED=0.5 / FAIL=0；
    skipped 不计入分母——不扣分；策略自带 score 时优先用 score）。
  - 总分 = 五维度加权平均（维度权重可配；全 skipped 的维度记"未检测"，不入总分）。
  - 真实性维度特殊：用 `AuthenticityScorer` 的 `min(shell, direct)` 注入，而非朴素平均。
  - 连通性致命短路：连通性 FAIL → 标"不可用"，总分置空仅呈现连通性结论（§11.7）。
"""
from dataclasses import dataclass, field

from app.probes.base import ProbeCategory, ProbeResult, ProbeStatus
from app.scoring.signal_aggregator import clamp

# 默认五维度权重（可配；按实际样本校准，§9.7）
DEFAULT_DIMENSION_WEIGHTS = {
    ProbeCategory.CONNECTIVITY.value: 1.0,
    ProbeCategory.PERFORMANCE.value: 1.0,
    ProbeCategory.BILLING.value: 1.0,
    ProbeCategory.CAPABILITY.value: 1.0,
    ProbeCategory.AUTHENTICITY.value: 1.0,
}
# 三态归一分（策略未提供 score 时回退）
_STATUS_SCORE = {
    ProbeStatus.PASS: 1.0,
    ProbeStatus.DEGRADED: 0.5,
    ProbeStatus.FAIL: 0.0,
}


@dataclass
class DimensionScore:
    """单维度聚合结果。"""

    category: str
    score: float | None  # 0~100；None 表示该维度全 skipped（未检测）
    weight: float
    strategy_count: int  # 该维度策略总数
    counted: int  # 计入分母的策略数（非 skipped）


@dataclass
class OverallScore:
    """总分聚合结果（§11.7）。"""

    overall: float | None  # 0~100；None 表示不可用（连通性短路）或无可计维度
    available: bool  # 是否可用（连通性未失败）
    dimensions: dict[str, DimensionScore] = field(default_factory=dict)


class ScoreAggregator:
    """维度/总分聚合器。"""

    def __init__(self, dimension_weights: dict[str, float] | None = None) -> None:
        self._weights = dimension_weights or dict(DEFAULT_DIMENSION_WEIGHTS)

    def dimension_score(
        self, category: str, results: list[ProbeResult]
    ) -> DimensionScore:
        """计算单维度加权平均分（skipped 不计入分母，§11.7）。"""
        weight = self._weights.get(category, 1.0)
        counted = [r for r in results if r.status is not ProbeStatus.SKIPPED]
        if not counted:
            return DimensionScore(category, None, weight, len(results), 0)
        total_weight = sum(r.weight for r in counted)
        if total_weight <= 0:
            return DimensionScore(category, None, weight, len(results), len(counted))
        achieved = sum(r.weight * self._normalized(r) for r in counted)
        score = round(achieved / total_weight * 100, 2)
        return DimensionScore(category, score, weight, len(results), len(counted))

    @staticmethod
    def _normalized(result: ProbeResult) -> float:
        """策略归一分 [0,1]：优先用自带 score，否则按三态回退。"""
        if result.score is not None:
            return clamp(result.score, 0.0, 1.0)
        return _STATUS_SCORE.get(result.status, 0.0)

    def aggregate(
        self,
        results_by_category: dict[str, list[ProbeResult]],
        *,
        authenticity_score: float | None = None,
    ) -> OverallScore:
        """聚合全部维度并合成总分。

        authenticity_score：若提供，则真实性维度直接采用该值（双子分短板，§9.1），
        而非对真实性 ProbeResult 做朴素平均。
        """
        dimensions: dict[str, DimensionScore] = {}
        for category in self._weights:
            results = results_by_category.get(category, [])
            dimensions[category] = self.dimension_score(category, results)

        # 真实性维度注入双子分短板值（覆盖朴素平均）
        if authenticity_score is not None:
            auth = ProbeCategory.AUTHENTICITY.value
            prev = dimensions.get(auth)
            dimensions[auth] = DimensionScore(
                category=auth,
                score=round(clamp(authenticity_score), 2),
                weight=self._weights.get(auth, 1.0),
                strategy_count=prev.strategy_count if prev else 0,
                counted=prev.counted if prev else 0,
            )

        # 连通性致命短路：失败 → 不可用，总分置空（§11.7）
        connectivity = dimensions.get(ProbeCategory.CONNECTIVITY.value)
        available = not (connectivity is not None and connectivity.score == 0.0)
        if not available:
            return OverallScore(overall=None, available=False, dimensions=dimensions)

        overall = self._weighted_overall(dimensions)
        return OverallScore(overall=overall, available=True, dimensions=dimensions)

    def _weighted_overall(self, dimensions: dict[str, DimensionScore]) -> float | None:
        """对有分（非 None）的维度做加权平均；全未检测返回 None。"""
        numerator = 0.0
        denominator = 0.0
        for dimension in dimensions.values():
            if dimension.score is None:
                continue  # 未检测维度不入总分
            numerator += dimension.weight * dimension.score
            denominator += dimension.weight
        if denominator <= 0:
            return None
        return round(numerator / denominator, 2)

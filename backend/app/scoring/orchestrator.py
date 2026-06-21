"""评分编排器（粘合 §9 全链路：信号提取 → 双子分 → 维度/总分 → 置信度分级）。

把 Task 17-20 的四个评分单元串成一次完整评分：输入一次检测的 ProbeResult 列表 +
真实性证据 + 接入形态，输出可落库/可上报的结构化评分报告。

复用既有单元（不重复算法）：
  - AuthenticityRegistry（Task 12 提取器）对证据求 Signal；
  - AuthenticityScorer（Task 18）桥接 Gemini 功能性结果并合成 shell/direct/authenticity；
  - ScoreAggregator（Task 19）算维度分与总分（真实性维度注入短板值）；
  - ConfidenceGrader（Task 20）算置信度并三级分级。
"""
from dataclasses import dataclass, field

from app.probes.authenticity import AuthenticityEvidence, AuthenticityRegistry
from app.probes.base import ProbeCategory, ProbeResult
from app.scoring.aggregator import OverallScore, ScoreAggregator
from app.scoring.authenticity_scorer import AuthenticityScore, AuthenticityScorer
from app.scoring.confidence import ConfidenceGrader, GradingResult


@dataclass
class ScoreReport:
    """一次检测的完整评分报告。"""

    overall: OverallScore
    authenticity: AuthenticityScore
    grading: GradingResult
    confidence: float
    refute_signal_count: int = 0
    dimension_scores: dict[str, float | None] = field(default_factory=dict)

    def to_dict(self) -> dict:
        """序列化为可落库/SSE 的字典（脱敏：仅含分值与信号摘要）。"""
        return {
            "overall_score": self.overall.overall,
            "available": self.overall.available,
            "dimension_scores": self.dimension_scores,
            "authenticity": {
                "shell_score": self.authenticity.shell_score,
                "direct_score": self.authenticity.direct_score,
                "authenticity_score": self.authenticity.authenticity_score,
                "b_group_confirmed": self.authenticity.b_group_confirmed,
                "level": self.grading.level.value,
                "confidence": self.confidence,
                "high_threshold": self.grading.high_threshold,
                "low_threshold": self.grading.low_threshold,
                "signals": [
                    {
                        "key": c.key,
                        "direction": c.direction,
                        "contribution": c.contribution,
                        "evidence": c.evidence,
                    }
                    for c in (
                        self.authenticity.shell_breakdown.contributions
                        + self.authenticity.direct_breakdown.contributions
                    )
                ],
            },
        }


class ScoringOrchestrator:
    """评分编排：一次性产出完整评分报告。"""

    def __init__(
        self,
        *,
        aggregator: ScoreAggregator | None = None,
        scorer: AuthenticityScorer | None = None,
        grader: ConfidenceGrader | None = None,
    ) -> None:
        self._aggregator = aggregator or ScoreAggregator()
        self._scorer = scorer or AuthenticityScorer()
        self._grader = grader or ConfidenceGrader()

    def score(
        self,
        probe_results: list[ProbeResult],
        *,
        access_mode: str = "native",
        evidence: AuthenticityEvidence | None = None,
        sample_coverage: float = 1.0,
    ) -> ScoreReport:
        """聚合全部结果为评分报告。"""
        results_by_category = _group_by_category(probe_results)

        # 1) 真实性：提取器信号 + Gemini 功能性结果 → 双子分
        signals = self._extract_signals(evidence)
        gemini_results = results_by_category.get(ProbeCategory.AUTHENTICITY.value, [])
        authenticity = self._scorer.score(signals, gemini_results=gemini_results)

        # 2) 维度/总分（真实性维度注入短板值）
        overall = self._aggregator.aggregate(
            results_by_category,
            authenticity_score=authenticity.authenticity_score,
        )

        # 3) 置信度 + 分级（统计有效证伪信号数，应用单信号误报控制）
        refute_count = _count_refute_hits(authenticity)
        confidence = self._grader.confidence(
            access_mode=access_mode, sample_coverage=sample_coverage
        )
        grading = self._grader.grade(
            authenticity.authenticity_score,
            confidence,
            refute_signal_count=refute_count,
        )

        dimension_scores = {
            category: dim.score for category, dim in overall.dimensions.items()
        }
        return ScoreReport(
            overall=overall,
            authenticity=authenticity,
            grading=grading,
            confidence=confidence,
            refute_signal_count=refute_count,
            dimension_scores=dimension_scores,
        )

    @staticmethod
    def _extract_signals(evidence: AuthenticityEvidence | None) -> list:
        """对证据运行全部已注册提取器，产出适用的 Signal。"""
        if evidence is None:
            return []
        signals = []
        for extractor in AuthenticityRegistry.create_all():
            if extractor.applicable(evidence):
                signals.append(extractor.extract(evidence))
        return signals


def _group_by_category(
    probe_results: list[ProbeResult],
) -> dict[str, list[ProbeResult]]:
    grouped: dict[str, list[ProbeResult]] = {}
    for result in probe_results:
        grouped.setdefault(result.category, []).append(result)
    return grouped


def _count_refute_hits(authenticity: AuthenticityScore) -> int:
    """统计有效证伪贡献数（contribution < 0），供单信号误报控制。"""
    contributions = (
        authenticity.shell_breakdown.contributions
        + authenticity.direct_breakdown.contributions
    )
    return sum(1 for c in contributions if c.contribution < 0)

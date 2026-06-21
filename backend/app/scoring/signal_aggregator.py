"""信号加权聚合（设计 §9.2）。

复用 `app.probes.signals.Signal` 模型（Task 12 已定义，不重复），在此实现"无罪推定 +
从满分扣减"的子分计算算术：

    base = 100
    refute：score -= Σ(weight × severity × confidence)
    confirm：score += Σ(weight × severity × confidence × 回补系数)，封顶 100
    score = clamp(score, 0, 100)

聚合产出逐信号贡献明细（可解释性，§9.7），供报告按贡献降序展示 Top 信号与人工复核。
本层只做算术，不含接入形态/样本量的 confidence 政策（那属 §9.6，由 Task 20 confidence
模块统一处理），signal.confidence 在此按既定值参与计算。
"""
from dataclasses import dataclass, field

from app.probes.signals import Signal, SignalDirection

# 证真回补系数（§9.2）：证真信号回补力度弱于证伪扣减，避免轻易"洗白"
DEFAULT_CONFIRM_FACTOR = 0.5
_BASE_SCORE = 100.0


def clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    """将值夹到 [low, high]。"""
    return max(low, min(high, value))


@dataclass
class SignalContribution:
    """单条信号对子分的贡献明细（脱敏，§9.7 逐条可溯源）。"""

    key: str
    direction: str  # confirm / refute
    severity: float
    weight: float
    confidence: float
    contribution: float  # 带符号贡献（refute 为负，confirm 为正）
    evidence: dict = field(default_factory=dict)


@dataclass
class SubscoreBreakdown:
    """一个子分（shell 或 direct）的聚合结果。"""

    score: float  # clamp 后的子分（0~100）
    contributions: list[SignalContribution] = field(default_factory=list)
    refute_total: float = 0.0  # 证伪扣减合计（正数表示扣了多少）
    confirm_total: float = 0.0  # 证真回补合计


class SignalAggregator:
    """信号加权聚合器：把一组作用于同一子分的 Signal 聚合为子分 + 贡献明细。"""

    def __init__(
        self, *, confirm_factor: float = DEFAULT_CONFIRM_FACTOR, base: float = _BASE_SCORE
    ) -> None:
        self._confirm_factor = confirm_factor
        self._base = base

    def aggregate(self, signals: list[Signal]) -> SubscoreBreakdown:
        """对信号列表求子分（无罪推定，从满分扣减；证真回补封顶 100）。"""
        score = self._base
        contributions: list[SignalContribution] = []
        refute_total = 0.0
        confirm_total = 0.0
        for signal in signals:
            magnitude = signal.weight * signal.severity * signal.confidence
            if signal.direction is SignalDirection.REFUTE:
                signed = -magnitude
                refute_total += magnitude
            else:  # CONFIRM
                signed = magnitude * self._confirm_factor
                confirm_total += signed
            score += signed
            contributions.append(
                SignalContribution(
                    key=signal.key,
                    direction=signal.direction.value,
                    severity=signal.severity,
                    weight=signal.weight,
                    confidence=signal.confidence,
                    contribution=round(signed, 4),
                    evidence=signal.evidence,
                )
            )
        return SubscoreBreakdown(
            score=round(clamp(score), 2),
            contributions=contributions,
            refute_total=round(refute_total, 4),
            confirm_total=round(confirm_total, 4),
        )

    @staticmethod
    def top_contributions(
        breakdown: SubscoreBreakdown, limit: int = 5
    ) -> list[SignalContribution]:
        """按贡献绝对值降序取 Top N（报告展示用，§9.7）。"""
        return sorted(
            breakdown.contributions, key=lambda c: abs(c.contribution), reverse=True
        )[:limit]

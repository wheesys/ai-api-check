"""真实性双子分评分（设计 §9.1 / §9.3 / §9.4 / §9.5）。

合成两个独立子分并取短板：
  - `shell_score`（非套壳可信度）：反"套壳换底"
  - `direct_score`（官方直供可信度）：反"逆向/工具转出"
  - `authenticity_score = min(shell_score, direct_score)`（取短板，任一高度可疑都拉低总体）

输入信号两类：
  1) AuthenticitySignalExtractor 产出的 Signal（Task 12，已按 target=shell/direct 划分）；
  2) Gemini 功能性探针的 ProbeResult（Task 13），在此按 §9.5 桥接为证真/证伪信号：
     - A 组 PASS → shell 强证真（shell_feature_confirm）；FAIL/DEGRADED → shell 证伪（保留原 key 溯源）；SKIPPED 忽略
     - B 组 PASS → 一票确证：shell_score 锁定 ≥H，并在 direct 记官方直供旁证；非 PASS 忽略（不误伤）

confidence 的接入形态/样本量政策属 §9.6（Task 20 confidence 模块），本层按信号既定
confidence 计算；桥接信号仅在 native 路径产生（兼容层功能探针整组 skipped）。
"""
from dataclasses import dataclass

from app.probes.base import ProbeResult, ProbeStatus
from app.probes.gemini_features import A_GROUP_KEYS, B_GROUP_KEYS
from app.probes.signals import Signal, SignalDirection, SignalTarget
from app.scoring.signal_aggregator import (
    DEFAULT_CONFIRM_FACTOR,
    SignalAggregator,
    SubscoreBreakdown,
)

# 默认分级阈值（§9.1，可调）：正常 ≥H / 可能可疑 L~H / 高度可疑 <L
DEFAULT_HIGH_THRESHOLD = 75.0
# Gemini 桥接信号默认权重（§9.5：A 组强证真/证伪；B 组直供旁证较弱）
_GEMINI_A_CONFIRM_WEIGHT = 25.0
_GEMINI_A_REFUTE_WEIGHT = 20.0
_GEMINI_B_DIRECT_CONFIRM_WEIGHT = 15.0
_DEGRADED_SEVERITY = 0.5


@dataclass
class AuthenticityScore:
    """真实性评分聚合结果（§9.1）。"""

    shell_score: float
    direct_score: float
    authenticity_score: float  # min(shell, direct)
    shell_breakdown: SubscoreBreakdown
    direct_breakdown: SubscoreBreakdown
    b_group_confirmed: bool = False  # 是否触发 B 组一票确证


class AuthenticityScorer:
    """真实性评分器：信号 + Gemini 功能性结果 → shell/direct/authenticity。"""

    def __init__(
        self,
        *,
        high_threshold: float = DEFAULT_HIGH_THRESHOLD,
        confirm_factor: float = DEFAULT_CONFIRM_FACTOR,
    ) -> None:
        self._high = high_threshold
        self._aggregator = SignalAggregator(confirm_factor=confirm_factor)

    def score(
        self,
        signals: list[Signal],
        gemini_results: list[ProbeResult] | None = None,
    ) -> AuthenticityScore:
        """聚合所有真实性信号为双子分。"""
        shell_signals = [s for s in signals if s.target is SignalTarget.SHELL]
        direct_signals = [s for s in signals if s.target is SignalTarget.DIRECT]

        derived_shell, derived_direct, b_confirmed = self._bridge_gemini(
            gemini_results or []
        )
        shell_breakdown = self._aggregator.aggregate(shell_signals + derived_shell)
        direct_breakdown = self._aggregator.aggregate(direct_signals + derived_direct)

        shell_score = shell_breakdown.score
        if b_confirmed:
            # B 组一票确证：平台特有功能别的模型绝无法返回，锁定 shell 高可信（§9.5）
            shell_score = max(shell_score, self._high)

        authenticity = min(shell_score, direct_breakdown.score)
        return AuthenticityScore(
            shell_score=round(shell_score, 2),
            direct_score=direct_breakdown.score,
            authenticity_score=round(authenticity, 2),
            shell_breakdown=shell_breakdown,
            direct_breakdown=direct_breakdown,
            b_group_confirmed=b_confirmed,
        )

    def _bridge_gemini(
        self, results: list[ProbeResult]
    ) -> tuple[list[Signal], list[Signal], bool]:
        """将 Gemini 功能性探针结果桥接为证真/证伪信号（§9.5）。"""
        derived_shell: list[Signal] = []
        derived_direct: list[Signal] = []
        b_confirmed = False
        for result in results:
            if result.key in A_GROUP_KEYS:
                signal = self._bridge_a_group(result)
                if signal is not None:
                    derived_shell.append(signal)
            elif result.key in B_GROUP_KEYS and result.status is ProbeStatus.PASS:
                b_confirmed = True
                derived_direct.append(self._b_group_direct_confirm(result))
        return derived_shell, derived_direct, b_confirmed

    def _bridge_a_group(self, result: ProbeResult) -> Signal | None:
        """A 组：PASS→shell 强证真；FAIL→证伪；DEGRADED→半严重度证伪；SKIPPED→忽略。"""
        if result.status is ProbeStatus.PASS:
            return Signal(
                key="shell_feature_confirm",
                name="Gemini 功能性证真",
                target=SignalTarget.SHELL,
                direction=SignalDirection.CONFIRM,
                severity=1.0,
                weight=_GEMINI_A_CONFIRM_WEIGHT,
                evidence={"source": result.key},
            )
        if result.status is ProbeStatus.FAIL:
            return Signal(
                key=result.key,
                name=result.name,
                target=SignalTarget.SHELL,
                direction=SignalDirection.REFUTE,
                severity=1.0,
                weight=_GEMINI_A_REFUTE_WEIGHT,
                evidence={"source": result.key, "status": result.status.value},
            )
        if result.status is ProbeStatus.DEGRADED:
            return Signal(
                key=result.key,
                name=result.name,
                target=SignalTarget.SHELL,
                direction=SignalDirection.REFUTE,
                severity=_DEGRADED_SEVERITY,
                weight=_GEMINI_A_REFUTE_WEIGHT,
                evidence={"source": result.key, "status": result.status.value},
            )
        return None  # SKIPPED 不计

    @staticmethod
    def _b_group_direct_confirm(result: ProbeResult) -> Signal:
        """B 组 PASS：在 direct 子分记官方直供旁证（§9.5）。"""
        return Signal(
            key="direct_platform_confirm",
            name="平台特有功能可用（官方直供旁证）",
            target=SignalTarget.DIRECT,
            direction=SignalDirection.CONFIRM,
            severity=1.0,
            weight=_GEMINI_B_DIRECT_CONFIRM_WEIGHT,
            evidence={"source": result.key},
        )

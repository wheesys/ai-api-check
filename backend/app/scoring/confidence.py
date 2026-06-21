"""置信度调节与真实性分级（设计 §9.1 / §9.6）。

- 分级阈值（可调）：正常 ≥H / 可能可疑 L~H / 高度可疑 <L，默认 H=75 / L=45。
- 置信度调节（§9.6）：
  1) 兼容层抹平原生指纹 → confidence × 0.6；
  2) 样本不足（重复次数低 / 功能探针采样未覆盖）→ 按覆盖率折算；
  3) 单信号定性误报控制：仅凭单一 refute 信号不下"高度可疑"结论，降一档为"可能可疑"。
  4) 模型版本不匹配的 unsupported 不计 refute——已在信号层处理（skipped 不入分），此处不重复。
- 分级须结合 confidence 一并展示（报告，§9.7）。
"""
from dataclasses import dataclass
from enum import Enum

from app.scoring.signal_aggregator import clamp

# 默认分级阈值（§9.1，可调）
DEFAULT_HIGH_THRESHOLD = 75.0
DEFAULT_LOW_THRESHOLD = 45.0
# 兼容层置信度折扣（§9.6 规则 1）
COMPAT_CONFIDENCE_FACTOR = 0.6
_COMPAT_MODE = "openai_compat"


class AuthenticityLevel(str, Enum):
    """真实性三级分级（§9.1）。"""

    NORMAL = "normal"  # 正常 ≥H
    SUSPICIOUS = "suspicious"  # 可能可疑 L~H
    HIGHLY_SUSPICIOUS = "highly_suspicious"  # 高度可疑 <L


@dataclass
class GradingResult:
    """分级结果（含阈值快照，便于报告与复核）。"""

    level: AuthenticityLevel
    score: float
    confidence: float
    high_threshold: float
    low_threshold: float


def _clamp01(value: float) -> float:
    return clamp(value, 0.0, 1.0)


class ConfidenceGrader:
    """置信度计算与分级（阈值可配，便于按样本校准）。"""

    def __init__(
        self,
        *,
        high_threshold: float = DEFAULT_HIGH_THRESHOLD,
        low_threshold: float = DEFAULT_LOW_THRESHOLD,
    ) -> None:
        if low_threshold > high_threshold:
            raise ValueError("low_threshold 不得大于 high_threshold")
        self._high = high_threshold
        self._low = low_threshold

    def confidence(
        self,
        *,
        access_mode: str = "native",
        sample_coverage: float = 1.0,
    ) -> float:
        """计算展示置信度（§9.6 规则 1-2）。

        access_mode：openai_compat → ×0.6（原生指纹丢失）。
        sample_coverage：实际覆盖 / 计划覆盖（0~1），按覆盖率折算。
        """
        confidence = 1.0
        if access_mode == _COMPAT_MODE:
            confidence *= COMPAT_CONFIDENCE_FACTOR
        confidence *= _clamp01(sample_coverage)
        return round(confidence, 3)

    def grade(
        self,
        score: float,
        confidence: float,
        *,
        refute_signal_count: int | None = None,
    ) -> GradingResult:
        """据子分与阈值分级；应用单信号误报控制（§9.6 规则 3）。"""
        level = self._raw_level(score)
        # 单信号定性误报控制：仅一个 refute 信号时不下"高度可疑"，降一档为可疑
        if (
            level is AuthenticityLevel.HIGHLY_SUSPICIOUS
            and refute_signal_count is not None
            and refute_signal_count <= 1
        ):
            level = AuthenticityLevel.SUSPICIOUS
        return GradingResult(
            level=level,
            score=round(score, 2),
            confidence=round(confidence, 3),
            high_threshold=self._high,
            low_threshold=self._low,
        )

    def _raw_level(self, score: float) -> AuthenticityLevel:
        if score >= self._high:
            return AuthenticityLevel.NORMAL
        if score >= self._low:
            return AuthenticityLevel.SUSPICIOUS
        return AuthenticityLevel.HIGHLY_SUSPICIOUS

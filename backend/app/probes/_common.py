"""探针共享工具（设计 §8.3）。

集中阈值分级与统计函数，避免连通性/性能/稳定性探针重复实现（DRY）。
"""
import math

from app.probes.base import ProbeStatus
from app.providers.base import ChatMessage

# 最小探测消息（连通性/稳定性：1 token 输出，控成本）
PING_MESSAGES = [ChatMessage(role="user", content="ping")]
# 性能探测提示（要求一定长度输出以测 TTFT/吞吐）
PERF_PROMPT_MESSAGES = [
    ChatMessage(role="user", content="用一句话介绍你自己。")
]


def grade_lower_better(
    value: float, pass_max: float, degraded_max: float
) -> ProbeStatus:
    """值越小越好（TTFT/延迟）：≤pass_max→PASS，≤degraded_max→DEGRADED，否则 FAIL。"""
    if value <= pass_max:
        return ProbeStatus.PASS
    if value <= degraded_max:
        return ProbeStatus.DEGRADED
    return ProbeStatus.FAIL


def grade_higher_better(
    value: float, pass_min: float, degraded_min: float
) -> ProbeStatus:
    """值越大越好（吞吐/成功率）：≥pass_min→PASS，≥degraded_min→DEGRADED，否则 FAIL。"""
    if value >= pass_min:
        return ProbeStatus.PASS
    if value >= degraded_min:
        return ProbeStatus.DEGRADED
    return ProbeStatus.FAIL


def percentile(values: list[float], pct: float) -> float:
    """线性插值百分位（pct ∈ [0,1]）；空列表返回 0.0。"""
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * pct
    low = math.floor(rank)
    high = math.ceil(rank)
    if low == high:
        return ordered[int(rank)]
    return ordered[low] * (high - rank) + ordered[high] * (rank - low)


def median(values: list[float]) -> float:
    """中位数（基于 percentile 0.5）。"""
    return percentile(values, 0.5)

"""真实性信号模型（设计 §9.2）。

真实性探针/特征提取器不发起新网络调用，而是对一次检测已采集的证据做特征提取，
每项产出一个 Signal，汇入双子分评分（shell_score / direct_score，设计 §9.3/§9.4）。
信号与主动探针（Probe）是不同职责：前者证据分析、后者主动收发，故独立建模。
"""
from dataclasses import dataclass, field
from enum import Enum

# 严重度三态映射（设计 §9.2：hit=1 / degraded=0.5 / miss=0）
SEVERITY_HIT = 1.0
SEVERITY_DEGRADED = 0.5
SEVERITY_MISS = 0.0


class SignalTarget(str, Enum):
    """信号作用的子分。"""

    SHELL = "shell"  # 非套壳可信度（反"套壳换底"）
    DIRECT = "direct"  # 官方直供可信度（反"逆向/工具转出"）


class SignalDirection(str, Enum):
    """信号方向。"""

    CONFIRM = "confirm"  # 证真：回补可信度
    REFUTE = "refute"  # 证伪：扣减可信度


@dataclass
class Signal:
    """单条真实性信号（设计 §9.2）。

    贡献分值由评分引擎按 weight × severity × confidence 计算（§9.2），本模型只承载
    归一化信号本身；evidence 须脱敏（仅命中/缺失字段名与原始片段引用，禁含 Key）。
    """

    key: str  # 特征项标识，对应 strategy_result.strategy_key
    name: str  # 中文名
    target: SignalTarget  # 作用子分
    direction: SignalDirection  # 证真/证伪
    severity: float  # 0~1 命中程度
    weight: float  # 基础权重
    confidence: float = 1.0  # 0~1 本信号可信度（兼容层/样本不足时下调）
    evidence: dict = field(default_factory=dict)  # 脱敏证据

    @property
    def hit(self) -> bool:
        """是否构成有效命中（severity > 0），便于报告筛选 Top 信号。"""
        return self.severity > SEVERITY_MISS

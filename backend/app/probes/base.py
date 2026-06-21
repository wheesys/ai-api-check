"""探针统一抽象（适配器模式 + 策略模式，设计 §8.2）。

每个探针是一条检测策略：声明 key/category/name/weight，判断是否适用某模型/接入
形态，经注入的 ProviderAdapter 收发（不直接拼协议细节，SOLID-D），产出 ProbeResult。
新增探针只需实现本抽象并注册，不改引擎（SOLID-O）。
"""
import abc
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum

from app.providers.base import ProviderAdapter


class ProbeCategory(str, Enum):
    """探针大类，对应 strategy_result.strategy_category。"""

    CONNECTIVITY = "connectivity"
    PERFORMANCE = "performance"
    BILLING = "billing"
    CAPABILITY = "capability"
    AUTHENTICITY = "authenticity"


class ProbeStatus(str, Enum):
    """探针三态结果，对应 strategy_result.result_status。

    功能性探针的 supported/degraded/unsupported 映射至 PASS/DEGRADED/FAIL。
    """

    PASS = "pass"
    DEGRADED = "degraded"
    FAIL = "fail"
    SKIPPED = "skipped"


@dataclass
class BudgetCounter:
    """预算计数器：限制单次检测的上游请求次数，防止探针失控刷量（设计 §8.1）。"""

    max_requests: int
    used: int = 0

    def consume(self, count: int = 1) -> None:
        self.used += count

    @property
    def remaining(self) -> int:
        return max(0, self.max_requests - self.used)

    @property
    def exhausted(self) -> bool:
        return self.used >= self.max_requests


@dataclass
class ProbeContext:
    """探针执行上下文（设计 §8.2）。

    持有已按协议/接入形态选定的适配器、模型配置、预算计数器、取消判定与阈值表。
    探针只依赖本上下文，不感知具体协议实现。
    """

    adapter: ProviderAdapter  # 已选定协议/接入形态的适配器
    model_name: str  # 目标模型标识
    access_mode: str = "native"  # native / openai_compat（决定功能性探针是否适用）
    thresholds: dict = field(default_factory=dict)  # 阈值表（ttft/吞吐/偏差率等）
    budget: BudgetCounter | None = None  # 预算计数器；None 表示不限制
    is_cancelled: Callable[[], bool] = lambda: False  # 取消判定回调
    declared_capabilities: set[str] = field(default_factory=set)  # 模型声明能力，裁剪能力探针

    def cancelled(self) -> bool:
        """是否已被取消（引擎在用户中止时翻转）。"""
        return bool(self.is_cancelled())


@dataclass
class ProbeResult:
    """探针产出，一条对应一行 strategy_result（设计 §8.2）。

    evidence 须脱敏：仅记录命中/缺失字段名与原始片段引用，禁含 Key。
    """

    key: str  # strategy_key
    category: str  # strategy_category
    name: str  # strategy_name（中文）
    status: ProbeStatus  # result_status
    weight: float  # 在所属维度的权重快照
    score: float | None = None  # 该策略得分，可空
    metrics: dict = field(default_factory=dict)  # metrics_json：量化指标
    evidence: dict = field(default_factory=dict)  # evidence_json：脱敏判定证据


class Probe(abc.ABC):
    """检测策略抽象基类。

    子类须声明类属性 key/category/name/weight，并经 ProbeRegistry 注册。
    """

    key: str
    category: str
    name: str
    weight: float = 1.0

    def applicable(self, ctx: ProbeContext) -> bool:
        """该模型/接入形态是否适用本探针；默认全适用。

        例：Gemini 功能性指纹探针仅 native 路径适用，兼容层返回 False。
        """
        return True

    @abc.abstractmethod
    async def run(self, ctx: ProbeContext) -> ProbeResult:
        """执行探测并产出归一化结果。"""
        raise NotImplementedError

    def make_result(
        self,
        status: ProbeStatus,
        *,
        score: float | None = None,
        metrics: dict | None = None,
        evidence: dict | None = None,
    ) -> ProbeResult:
        """以本探针的 key/category/name/weight 构造结果（减少子类样板）。"""
        return ProbeResult(
            key=self.key,
            category=self.category,
            name=self.name,
            status=status,
            weight=self.weight,
            score=score,
            metrics=metrics or {},
            evidence=evidence or {},
        )

    def skipped(self, reason: str) -> ProbeResult:
        """构造跳过结果（如不适用或预算耗尽），证据记录原因。"""
        return self.make_result(ProbeStatus.SKIPPED, evidence={"reason": reason})

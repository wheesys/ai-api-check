"""真实性特征提取器（套壳换底 + 逆向/工具转出，设计 §7.4 / §8.6 / §9.3-9.4）。

每个提取器对一次检测已采集的证据（AuthenticityEvidence）做纯特征分析，产出一个
Signal（证真/证伪 + 严重度 + 置信度），由评分引擎汇入 shell_score / direct_score
（取短板 min，设计 §9.1）。本层不发起网络调用，亦不感知协议收发细节。

原则（设计 §9.7 红线）：仅给可信度分级，非铁证；兼容层抹平原生指纹时 confidence
自动下调（§9.6）；能力本不属该版本范围的不计证伪（避免误伤）。
"""
import re
from dataclasses import dataclass, field

from app.probes.signals import (
    SEVERITY_DEGRADED,
    SEVERITY_HIT,
    SEVERITY_MISS,
    Signal,
    SignalDirection,
    SignalTarget,
)
from app.providers.base import TokenUsage

# 兼容层抹平原生指纹时的置信度折扣（设计 §9.6 规则 1）
_COMPAT_CONFIDENCE_FACTOR = 0.6
_COMPAT_MODE = "openai_compat"

# 各协议预期的特有字段（feature_flags 中的归一化键名，snake_case）
_PROTOCOL_SPECIAL_FIELDS = {
    "gemini": ("safety_ratings", "model_version"),
    "openai": ("system_fingerprint",),
    "anthropic": ("stop_reason",),
}
# 各协议官方直供响应头特征片段（小写匹配）
_PROTOCOL_HEADER_HINTS = {
    "gemini": ("x-goog",),
    "openai": ("x-request-id", "openai"),
    "anthropic": ("anthropic", "request-id"),
}
# 注入壳/工具转出痕迹正则（命中即视为逆向工具残留）
_SHELL_ARTIFACT_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"<\|im_(start|end)\|>",  # ChatML 壳残留
        r"you are (a |an )?(coding|ai) (assistant|agent)",  # 注入 system 人设
        r"system prompt",
        r"\b(codex|cline|cursor|copilot|windsurf|antigravity)\b",  # IDE 工具名泄漏
    )
)


@dataclass
class AuthenticityEvidence:
    """一次检测采集的真实性证据包（由引擎在主动探针后汇总，Task 15+）。

    全部字段可空：缺失即对应提取器不适用（applicable 返回 False），不强求采集。
    """

    protocol: str  # openai / anthropic / gemini
    declared_model: str  # 声称的模型标识
    access_mode: str = "native"  # native / openai_compat
    sample_usage: TokenUsage | None = None  # 申报用量
    sample_content: str | None = None  # 响应文本（壳痕迹分析）
    system_echo: str | None = None  # 回显的 system/工具壳文本
    feature_flags: dict = field(default_factory=dict)  # 协议特有字段归一化信号
    response_headers: dict | None = None  # 响应头（直供特征）
    billing_deviation: float | None = None  # 计费探针的 tokenizer 偏差率
    capability_results: dict | None = None  # {cap_key: status} 能力探针状态
    declared_capabilities: set = field(default_factory=set)  # 声称能力
    rate_limit_observations: dict | None = None  # 限流观测（订阅档/免费档特征）


class AuthenticitySignalExtractor:
    """真实性信号提取器基类。

    子类声明 key/name/target/direction/weight，实现 applicable 与 _evaluate；
    经 AuthenticityRegistry 注册。新增特征只需实现并注册，不改评分引擎（SOLID-O）。
    """

    key: str
    name: str
    target: SignalTarget
    direction: SignalDirection
    weight: float
    native_dependent: bool = True  # 是否依赖原生指纹（兼容层下调置信度）

    def applicable(self, evidence: AuthenticityEvidence) -> bool:
        """证据是否足以评估本特征；不适用则引擎跳过、不产信号。"""
        return True

    def _evaluate(self, evidence: AuthenticityEvidence) -> tuple[float, dict]:
        """返回 (severity, evidence_dict)；子类实现具体判定。"""
        raise NotImplementedError

    def extract(self, evidence: AuthenticityEvidence) -> Signal:
        """评估并构造归一化 Signal（自动按接入形态折算置信度）。"""
        severity, detail = self._evaluate(evidence)
        return Signal(
            key=self.key,
            name=self.name,
            target=self.target,
            direction=self.direction,
            severity=severity,
            weight=self.weight,
            confidence=self._confidence(evidence),
            evidence=detail,
        )

    def _confidence(self, evidence: AuthenticityEvidence) -> float:
        """置信度：原生指纹相关特征在兼容层下调（设计 §9.6 规则 1）。"""
        if self.native_dependent and evidence.access_mode == _COMPAT_MODE:
            return _COMPAT_CONFIDENCE_FACTOR
        return 1.0


class AuthenticityRegistry:
    """真实性提取器注册表（注册表模式），引擎据此枚举全部特征。"""

    _registry: dict[str, type[AuthenticitySignalExtractor]] = {}

    @classmethod
    def register(
        cls, extractor_cls: type[AuthenticitySignalExtractor]
    ) -> type[AuthenticitySignalExtractor]:
        key = extractor_cls.key
        if not key:
            raise ValueError(f"提取器 {extractor_cls.__name__} 未声明 key")
        if key in cls._registry:
            raise ValueError(f"提取器 key 重复注册：{key!r}")
        cls._registry[key] = extractor_cls
        return extractor_cls

    @classmethod
    def all_keys(cls) -> list[str]:
        return list(cls._registry)

    @classmethod
    def create_all(cls) -> list[AuthenticitySignalExtractor]:
        """实例化全部提取器，供引擎逐一对证据求值。"""
        return [extractor_cls() for extractor_cls in cls._registry.values()]


# ============ 套壳换底信号（shell_score，设计 §9.3） ============


@AuthenticityRegistry.register
class ShellUsageMissingExtractor(AuthenticitySignalExtractor):
    """usage 缺失/字段不全（声称思考模型却无 thoughtsTokenCount）。"""

    key = "shell_usage_missing"
    name = "用量字段缺失"
    target = SignalTarget.SHELL
    direction = SignalDirection.REFUTE
    weight = 30.0

    def _evaluate(self, evidence: AuthenticityEvidence) -> tuple[float, dict]:
        usage = evidence.sample_usage
        if usage is None:
            return SEVERITY_HIT, {"reason": "申报 usage 完全缺失"}
        missing = [
            field_name
            for field_name in ("prompt_tokens", "completion_tokens", "total_tokens")
            if getattr(usage, field_name) is None
        ]
        # 声称 Gemini 2.5 思考模型却无 thoughts_token_count → 字段不全
        claims_thinking = "2.5" in evidence.declared_model and evidence.protocol == "gemini"
        thoughts_absent = "thoughts_token_count" not in evidence.feature_flags
        if claims_thinking and thoughts_absent:
            missing.append("thoughts_token_count")
        if missing:
            return SEVERITY_DEGRADED, {"missing_fields": missing}
        return SEVERITY_MISS, {}


@AuthenticityRegistry.register
class ShellSpecialFieldAbsentExtractor(AuthenticitySignalExtractor):
    """协议特有字段缺失（safetyRatings/system_fingerprint 等）。"""

    key = "shell_special_field_absent"
    name = "协议特有字段缺失"
    target = SignalTarget.SHELL
    direction = SignalDirection.REFUTE
    weight = 25.0

    def applicable(self, evidence: AuthenticityEvidence) -> bool:
        return evidence.protocol in _PROTOCOL_SPECIAL_FIELDS

    def _evaluate(self, evidence: AuthenticityEvidence) -> tuple[float, dict]:
        expected = _PROTOCOL_SPECIAL_FIELDS.get(evidence.protocol, ())
        absent = [name for name in expected if name not in evidence.feature_flags]
        if not absent:
            return SEVERITY_MISS, {}
        if len(absent) == len(expected):
            return SEVERITY_HIT, {"absent_fields": absent}
        return SEVERITY_DEGRADED, {"absent_fields": absent}


@AuthenticityRegistry.register
class ShellTokenizerMismatchExtractor(AuthenticitySignalExtractor):
    """本地 tokenizer 切分与申报 usage 偏差显著（联动计费探针 §8.4）。"""

    key = "shell_tokenizer_mismatch"
    name = "分词切分不符"
    target = SignalTarget.SHELL
    direction = SignalDirection.REFUTE
    weight = 20.0
    native_dependent = False  # 偏差分析在兼容层同样可做

    def applicable(self, evidence: AuthenticityEvidence) -> bool:
        return evidence.billing_deviation is not None

    def _evaluate(self, evidence: AuthenticityEvidence) -> tuple[float, dict]:
        deviation = evidence.billing_deviation or 0.0
        detail = {"deviation": round(deviation, 4)}
        if deviation >= 0.4:
            return SEVERITY_HIT, detail
        if deviation >= 0.15:
            return SEVERITY_DEGRADED, detail
        return SEVERITY_MISS, detail


@AuthenticityRegistry.register
class ShellCapabilityGapExtractor(AuthenticitySignalExtractor):
    """声称高能力模型但能力探针大面积 fail。"""

    key = "shell_capability_gap"
    name = "能力探针大面积失败"
    target = SignalTarget.SHELL
    direction = SignalDirection.REFUTE
    weight = 15.0
    native_dependent = False

    def applicable(self, evidence: AuthenticityEvidence) -> bool:
        return bool(self._considered(evidence))

    def _evaluate(self, evidence: AuthenticityEvidence) -> tuple[float, dict]:
        considered = self._considered(evidence)
        fails = sum(1 for status in considered.values() if status == "fail")
        fail_rate = fails / len(considered)
        detail = {"fail_rate": round(fail_rate, 3), "considered": len(considered)}
        if fail_rate >= 0.5:
            return SEVERITY_HIT, detail
        if fail_rate >= 0.25:
            return SEVERITY_DEGRADED, detail
        return SEVERITY_MISS, detail

    @staticmethod
    def _considered(evidence: AuthenticityEvidence) -> dict:
        """仅计入有效判定（排除 skipped，避免误伤版本差异）。"""
        results = evidence.capability_results or {}
        return {key: status for key, status in results.items() if status != "skipped"}


# ============ 逆向/工具转出信号（direct_score，设计 §9.4） ============


@AuthenticityRegistry.register
class ReverseShellArtifactExtractor(AuthenticitySignalExtractor):
    """注入的 system/工具壳痕迹（CLI/IDE 工具会话包装残留）。"""

    key = "reverse_shell_artifact"
    name = "工具壳痕迹"
    target = SignalTarget.DIRECT
    direction = SignalDirection.REFUTE
    weight = 25.0
    native_dependent = False

    def _evaluate(self, evidence: AuthenticityEvidence) -> tuple[float, dict]:
        text = f"{evidence.sample_content or ''}\n{evidence.system_echo or ''}"
        for pattern in _SHELL_ARTIFACT_PATTERNS:
            match = pattern.search(text)
            if match is not None:
                # 仅记录命中的模式（脱敏：不回显完整响应文本）
                return SEVERITY_HIT, {"matched_pattern": pattern.pattern}
        return SEVERITY_MISS, {}


@AuthenticityRegistry.register
class ReverseVersionAnomalyExtractor(AuthenticitySignalExtractor):
    """modelVersion/system_fingerprint 缺失或与官方不符。"""

    key = "reverse_version_anomaly"
    name = "版本指纹异常"
    target = SignalTarget.DIRECT
    direction = SignalDirection.REFUTE
    weight = 20.0

    def applicable(self, evidence: AuthenticityEvidence) -> bool:
        return evidence.protocol in ("gemini", "openai")

    def _evaluate(self, evidence: AuthenticityEvidence) -> tuple[float, dict]:
        if evidence.protocol == "gemini":
            model_version = evidence.feature_flags.get("model_version")
            if not model_version:
                return SEVERITY_HIT, {"reason": "缺失 modelVersion"}
            # 回显版本与声称型号族不一致 → 弱异常
            family = evidence.declared_model.split("-latest")[0]
            if family and not str(model_version).startswith(family[:8]):
                return SEVERITY_DEGRADED, {"model_version": str(model_version)}
            return SEVERITY_MISS, {"model_version": str(model_version)}
        fingerprint = evidence.feature_flags.get("system_fingerprint")
        if not fingerprint:
            return SEVERITY_HIT, {"reason": "缺失 system_fingerprint"}
        return SEVERITY_MISS, {"system_fingerprint": str(fingerprint)}


@AuthenticityRegistry.register
class ReverseRateLimitPatternExtractor(AuthenticitySignalExtractor):
    """限流模式贴近 C 端订阅额度而非 API 配额。"""

    key = "reverse_ratelimit_pattern"
    name = "限流模式异常"
    target = SignalTarget.DIRECT
    direction = SignalDirection.REFUTE
    weight = 20.0
    native_dependent = False

    def applicable(self, evidence: AuthenticityEvidence) -> bool:
        return evidence.rate_limit_observations is not None

    def _evaluate(self, evidence: AuthenticityEvidence) -> tuple[float, dict]:
        observations = evidence.rate_limit_observations or {}
        if observations.get("subscription_style"):
            return SEVERITY_HIT, {"reason": "限流贴近订阅档配额"}
        return SEVERITY_MISS, {}


@AuthenticityRegistry.register
class ReverseHeaderMissingExtractor(AuthenticitySignalExtractor):
    """响应头缺官方直供特征。"""

    key = "reverse_header_missing"
    name = "直供响应头缺失"
    target = SignalTarget.DIRECT
    direction = SignalDirection.REFUTE
    weight = 15.0

    def applicable(self, evidence: AuthenticityEvidence) -> bool:
        return evidence.response_headers is not None

    def _evaluate(self, evidence: AuthenticityEvidence) -> tuple[float, dict]:
        hints = _PROTOCOL_HEADER_HINTS.get(evidence.protocol, ())
        if not hints:
            return SEVERITY_MISS, {}
        header_keys = " ".join(k.lower() for k in (evidence.response_headers or {}))
        present = any(hint in header_keys for hint in hints)
        if present:
            return SEVERITY_MISS, {}
        return SEVERITY_HIT, {"reason": "缺官方直供响应头", "expected": list(hints)}


@AuthenticityRegistry.register
class ReverseStudioSignatureExtractor(AuthenticitySignalExtractor):
    """Gemini：免费档配额特征 / safetyRatings 被裁剪（贴近 AI Studio 逆向）。"""

    key = "reverse_studio_signature"
    name = "AI Studio 逆向特征"
    target = SignalTarget.DIRECT
    direction = SignalDirection.REFUTE
    weight = 15.0

    def applicable(self, evidence: AuthenticityEvidence) -> bool:
        return evidence.protocol == "gemini"

    def _evaluate(self, evidence: AuthenticityEvidence) -> tuple[float, dict]:
        free_tier = bool((evidence.rate_limit_observations or {}).get("free_tier"))
        safety_trimmed = "safety_ratings" not in evidence.feature_flags
        if free_tier:
            return SEVERITY_HIT, {
                "reason": "免费档配额特征",
                "safety_ratings_trimmed": safety_trimmed,
            }
        if safety_trimmed:
            return SEVERITY_DEGRADED, {"reason": "safetyRatings 被裁剪"}
        return SEVERITY_MISS, {}

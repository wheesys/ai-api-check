"""真实性特征提取器单元测试：套壳/逆向信号严重度三态 + 置信度折扣 + 适用性裁剪。

纯特征分析、零网络：直接构造 AuthenticityEvidence 证据包求值。
"""
from app.probes.authenticity import (
    AuthenticityEvidence,
    AuthenticityRegistry,
    ReverseHeaderMissingExtractor,
    ReverseShellArtifactExtractor,
    ReverseStudioSignatureExtractor,
    ReverseVersionAnomalyExtractor,
    ShellCapabilityGapExtractor,
    ShellSpecialFieldAbsentExtractor,
    ShellTokenizerMismatchExtractor,
    ShellUsageMissingExtractor,
)
from app.probes.signals import SignalDirection, SignalTarget
from app.providers.base import TokenUsage


def _evidence(**overrides) -> AuthenticityEvidence:
    base = {"protocol": "gemini", "declared_model": "gemini-2.5-pro"}
    base.update(overrides)
    return AuthenticityEvidence(**base)


# ---------- shell_usage_missing ----------

def test_usage_missing_hit_when_absent():
    signal = ShellUsageMissingExtractor().extract(_evidence(sample_usage=None))
    assert signal.severity == 1.0
    assert signal.target is SignalTarget.SHELL
    assert signal.direction is SignalDirection.REFUTE


def test_usage_missing_degraded_when_thinking_thoughts_absent():
    usage = TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15)
    # 声称 2.5 思考模型却无 thoughts_token_count → 字段不全
    signal = ShellUsageMissingExtractor().extract(_evidence(sample_usage=usage))
    assert signal.severity == 0.5
    assert "thoughts_token_count" in signal.evidence["missing_fields"]


def test_usage_missing_miss_when_complete():
    usage = TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15)
    evidence = _evidence(
        sample_usage=usage, feature_flags={"thoughts_token_count": 3}
    )
    signal = ShellUsageMissingExtractor().extract(evidence)
    assert signal.severity == 0.0


# ---------- shell_special_field_absent ----------

def test_special_field_hit_all_absent():
    signal = ShellSpecialFieldAbsentExtractor().extract(_evidence(feature_flags={}))
    assert signal.severity == 1.0


def test_special_field_degraded_some_absent():
    evidence = _evidence(feature_flags={"safety_ratings": []})
    signal = ShellSpecialFieldAbsentExtractor().extract(evidence)
    assert signal.severity == 0.5
    assert signal.evidence["absent_fields"] == ["model_version"]


def test_special_field_miss_all_present():
    evidence = _evidence(
        feature_flags={"safety_ratings": [], "model_version": "gemini-2.5-pro-001"}
    )
    signal = ShellSpecialFieldAbsentExtractor().extract(evidence)
    assert signal.severity == 0.0


def test_special_field_not_applicable_unknown_protocol():
    extractor = ShellSpecialFieldAbsentExtractor()
    assert extractor.applicable(_evidence(protocol="mystery")) is False


# ---------- shell_tokenizer_mismatch ----------

def test_tokenizer_mismatch_grades_by_deviation():
    extractor = ShellTokenizerMismatchExtractor()
    assert extractor.extract(_evidence(billing_deviation=0.5)).severity == 1.0
    assert extractor.extract(_evidence(billing_deviation=0.2)).severity == 0.5
    assert extractor.extract(_evidence(billing_deviation=0.05)).severity == 0.0


def test_tokenizer_mismatch_not_applicable_without_deviation():
    assert ShellTokenizerMismatchExtractor().applicable(_evidence()) is False


# ---------- shell_capability_gap ----------

def test_capability_gap_hit_high_fail_rate():
    results = {"cap_function_call": "fail", "cap_json_mode": "fail", "cap_streaming": "pass"}
    signal = ShellCapabilityGapExtractor().extract(_evidence(capability_results=results))
    assert signal.severity == 1.0  # 2/3 ≈ 0.67 ≥ 0.5


def test_capability_gap_excludes_skipped():
    results = {"cap_multimodal": "skipped", "cap_streaming": "pass"}
    extractor = ShellCapabilityGapExtractor()
    signal = extractor.extract(_evidence(capability_results=results))
    assert signal.severity == 0.0
    assert signal.evidence["considered"] == 1  # skipped 不计入


# ---------- reverse_shell_artifact ----------

def test_shell_artifact_hit_on_injected_system():
    evidence = _evidence(sample_content="You are a coding assistant built by Cursor.")
    signal = ReverseShellArtifactExtractor().extract(evidence)
    assert signal.severity == 1.0
    assert signal.target is SignalTarget.DIRECT
    assert "matched_pattern" in signal.evidence


def test_shell_artifact_miss_on_clean_text():
    evidence = _evidence(sample_content="你好，今天天气不错。")
    signal = ReverseShellArtifactExtractor().extract(evidence)
    assert signal.severity == 0.0


# ---------- reverse_version_anomaly ----------

def test_version_anomaly_hit_when_missing():
    signal = ReverseVersionAnomalyExtractor().extract(_evidence(feature_flags={}))
    assert signal.severity == 1.0


def test_version_anomaly_miss_when_present():
    evidence = _evidence(feature_flags={"model_version": "gemini-2.5-pro-001"})
    signal = ReverseVersionAnomalyExtractor().extract(evidence)
    assert signal.severity == 0.0


def test_version_anomaly_openai_fingerprint():
    extractor = ReverseVersionAnomalyExtractor()
    missing = extractor.extract(
        _evidence(protocol="openai", declared_model="gpt-4o", feature_flags={})
    )
    assert missing.severity == 1.0
    present = extractor.extract(
        _evidence(
            protocol="openai",
            declared_model="gpt-4o",
            feature_flags={"system_fingerprint": "fp_abc123"},
        )
    )
    assert present.severity == 0.0


# ---------- reverse_header_missing ----------

def test_header_missing_hit_without_official_headers():
    evidence = _evidence(response_headers={"content-type": "application/json"})
    signal = ReverseHeaderMissingExtractor().extract(evidence)
    assert signal.severity == 1.0


def test_header_missing_miss_with_official_header():
    evidence = _evidence(response_headers={"X-Goog-Request-Id": "abc"})
    signal = ReverseHeaderMissingExtractor().extract(evidence)
    assert signal.severity == 0.0


# ---------- reverse_studio_signature ----------

def test_studio_signature_hit_on_free_tier():
    evidence = _evidence(rate_limit_observations={"free_tier": True})
    signal = ReverseStudioSignatureExtractor().extract(evidence)
    assert signal.severity == 1.0


def test_studio_signature_degraded_on_trimmed_safety():
    evidence = _evidence(feature_flags={"model_version": "x"})
    signal = ReverseStudioSignatureExtractor().extract(evidence)
    assert signal.severity == 0.5


def test_studio_signature_only_gemini():
    assert ReverseStudioSignatureExtractor().applicable(_evidence(protocol="openai")) is False


# ---------- 置信度折扣 ----------

def test_compat_layer_downgrades_confidence():
    evidence = _evidence(access_mode="openai_compat", feature_flags={})
    native = ShellSpecialFieldAbsentExtractor().extract(_evidence(feature_flags={}))
    compat = ShellSpecialFieldAbsentExtractor().extract(evidence)
    assert native.confidence == 1.0
    assert compat.confidence == 0.6  # 兼容层抹平原生指纹 → ×0.6


def test_non_native_signal_keeps_confidence_on_compat():
    evidence = _evidence(access_mode="openai_compat", billing_deviation=0.5)
    signal = ShellTokenizerMismatchExtractor().extract(evidence)
    assert signal.confidence == 1.0  # 偏差分析不依赖原生指纹


# ---------- 注册 ----------

def test_authenticity_extractors_registered():
    keys = set(AuthenticityRegistry.all_keys())
    assert {
        "shell_usage_missing",
        "shell_special_field_absent",
        "shell_tokenizer_mismatch",
        "shell_capability_gap",
        "reverse_shell_artifact",
        "reverse_version_anomaly",
        "reverse_ratelimit_pattern",
        "reverse_header_missing",
        "reverse_studio_signature",
    } == keys
    assert len(AuthenticityRegistry.create_all()) == 9

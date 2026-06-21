"""真实性双子分评分单元测试（Task 18，设计 §9.1/§9.3/§9.4/§9.5）。"""
from app.probes.base import ProbeCategory, ProbeResult, ProbeStatus
from app.probes.signals import Signal, SignalDirection, SignalTarget
from app.scoring.authenticity_scorer import AuthenticityScorer


def _signal(key, target, direction, weight, severity=1.0, confidence=1.0) -> Signal:
    return Signal(
        key=key,
        name=key,
        target=target,
        direction=direction,
        severity=severity,
        weight=weight,
        confidence=confidence,
    )


def _shell_refute(key, weight, **kw) -> Signal:
    return _signal(key, SignalTarget.SHELL, SignalDirection.REFUTE, weight, **kw)


def _direct_refute(key, weight, **kw) -> Signal:
    return _signal(key, SignalTarget.DIRECT, SignalDirection.REFUTE, weight, **kw)


def _gemini(key, status) -> ProbeResult:
    return ProbeResult(
        key=key,
        category=ProbeCategory.AUTHENTICITY.value,
        name=key,
        status=status,
        weight=1.0,
    )


# ---------- 基础双子分 ----------

def test_clean_model_full_scores():
    result = AuthenticityScorer().score([])
    assert result.shell_score == 100.0
    assert result.direct_score == 100.0
    assert result.authenticity_score == 100.0


def test_shell_refute_lowers_shell_only():
    result = AuthenticityScorer().score([_shell_refute("shell_usage_missing", 30)])
    assert result.shell_score == 70.0
    assert result.direct_score == 100.0
    assert result.authenticity_score == 70.0  # min


def test_direct_refute_lowers_direct_only():
    result = AuthenticityScorer().score([_direct_refute("reverse_shell_artifact", 25)])
    assert result.direct_score == 75.0
    assert result.shell_score == 100.0
    assert result.authenticity_score == 75.0


def test_authenticity_takes_short_board():
    # shell 扣 40 → 60，direct 扣 20 → 80，取短板 60
    result = AuthenticityScorer().score(
        [_shell_refute("a", 40), _direct_refute("b", 20)]
    )
    assert result.authenticity_score == 60.0


# ---------- Gemini A 组桥接 ----------

def test_gemini_a_pass_confirms_shell():
    # 先扣 50 → 50，A 组 PASS 证真回补 25*0.5=12.5 → 62.5
    result = AuthenticityScorer().score(
        [_shell_refute("a", 50)],
        gemini_results=[_gemini("gemini_thinking", ProbeStatus.PASS)],
    )
    assert result.shell_score == 62.5


def test_gemini_a_fail_refutes_shell():
    # A 组 FAIL（声称却给不出）→ shell 证伪 20 → 80
    result = AuthenticityScorer().score(
        [], gemini_results=[_gemini("gemini_thinking", ProbeStatus.FAIL)]
    )
    assert result.shell_score == 80.0


def test_gemini_a_degraded_half_severity():
    # DEGRADED severity 0.5：20*0.5=10 → 90
    result = AuthenticityScorer().score(
        [], gemini_results=[_gemini("gemini_code_execution", ProbeStatus.DEGRADED)]
    )
    assert result.shell_score == 90.0


def test_gemini_a_skipped_ignored():
    result = AuthenticityScorer().score(
        [], gemini_results=[_gemini("gemini_thinking", ProbeStatus.SKIPPED)]
    )
    assert result.shell_score == 100.0


# ---------- Gemini B 组一票确证 ----------

def test_gemini_b_pass_locks_shell_high():
    # shell 被扣到很低，但 B 组 PASS 一票确证锁定 ≥H(75)
    result = AuthenticityScorer(high_threshold=75.0).score(
        [_shell_refute("a", 60)],  # shell → 40
        gemini_results=[_gemini("gemini_url_context", ProbeStatus.PASS)],
    )
    assert result.shell_score == 75.0  # 锁定 ≥H
    assert result.b_group_confirmed is True


def test_gemini_b_pass_adds_direct_confirm():
    result = AuthenticityScorer().score(
        [_direct_refute("reverse_header_missing", 30)],  # direct → 70
        gemini_results=[_gemini("gemini_vertex_rag", ProbeStatus.PASS)],
    )
    # direct 证真回补 15*0.5=7.5 → 77.5
    assert result.direct_score == 77.5


def test_gemini_b_skipped_no_lock():
    result = AuthenticityScorer().score(
        [_shell_refute("a", 60)],
        gemini_results=[_gemini("gemini_url_context", ProbeStatus.SKIPPED)],
    )
    assert result.shell_score == 40.0
    assert result.b_group_confirmed is False


# ---------- 可解释性 ----------

def test_breakdown_traces_contributions():
    result = AuthenticityScorer().score([_shell_refute("shell_usage_missing", 30)])
    keys = [c.key for c in result.shell_breakdown.contributions]
    assert "shell_usage_missing" in keys

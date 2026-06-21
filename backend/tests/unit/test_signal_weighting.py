"""信号加权聚合单元测试（Task 17，设计 §9.2）。"""
from app.probes.signals import Signal, SignalDirection, SignalTarget
from app.scoring.signal_aggregator import SignalAggregator, clamp


def _refute(key, weight, severity=1.0, confidence=1.0) -> Signal:
    return Signal(
        key=key,
        name=key,
        target=SignalTarget.SHELL,
        direction=SignalDirection.REFUTE,
        severity=severity,
        weight=weight,
        confidence=confidence,
    )


def _confirm(key, weight, severity=1.0, confidence=1.0) -> Signal:
    return Signal(
        key=key,
        name=key,
        target=SignalTarget.SHELL,
        direction=SignalDirection.CONFIRM,
        severity=severity,
        weight=weight,
        confidence=confidence,
    )


def test_clamp_bounds():
    assert clamp(-5) == 0.0
    assert clamp(150) == 100.0
    assert clamp(60) == 60.0


def test_no_signals_stays_full():
    result = SignalAggregator().aggregate([])
    assert result.score == 100.0
    assert result.contributions == []


def test_refute_deducts():
    # 30 + 25*0.5 = 42.5 扣减 → 57.5
    result = SignalAggregator().aggregate(
        [_refute("a", 30), _refute("b", 25, severity=0.5)]
    )
    assert result.score == 57.5
    assert result.refute_total == 42.5


def test_confidence_scales_contribution():
    # 兼容层 confidence=0.6：30*1*0.6 = 18 扣减 → 82
    result = SignalAggregator().aggregate([_refute("a", 30, confidence=0.6)])
    assert result.score == 82.0


def test_confirm_backfills_with_factor():
    # 先扣 40，再证真回补 20*0.5=10 → 70
    result = SignalAggregator(confirm_factor=0.5).aggregate(
        [_refute("a", 40), _confirm("c", 20)]
    )
    assert result.score == 70.0
    assert result.confirm_total == 10.0


def test_score_floored_at_zero():
    result = SignalAggregator().aggregate([_refute("a", 200)])
    assert result.score == 0.0


def test_confirm_capped_at_hundred():
    result = SignalAggregator().aggregate([_confirm("c", 200)])
    assert result.score == 100.0


def test_contribution_signs():
    result = SignalAggregator().aggregate([_refute("a", 30), _confirm("c", 20)])
    by_key = {c.key: c for c in result.contributions}
    assert by_key["a"].contribution == -30.0
    assert by_key["c"].contribution == 10.0  # 20*0.5


def test_top_contributions_sorted_by_magnitude():
    result = SignalAggregator().aggregate(
        [_refute("small", 5), _refute("big", 30), _refute("mid", 15)]
    )
    top = SignalAggregator.top_contributions(result, limit=2)
    assert [c.key for c in top] == ["big", "mid"]

"""维度与总分聚合单元测试（Task 19，设计 §8.8/§11.7）。"""
from app.probes.base import ProbeCategory, ProbeResult, ProbeStatus
from app.scoring.aggregator import ScoreAggregator


def _result(key, category, status, *, weight=1.0, score=None) -> ProbeResult:
    return ProbeResult(
        key=key, category=category, name=key, status=status, weight=weight, score=score
    )


_PERF = ProbeCategory.PERFORMANCE.value
_CAP = ProbeCategory.CAPABILITY.value
_CONN = ProbeCategory.CONNECTIVITY.value
_AUTH = ProbeCategory.AUTHENTICITY.value


# ---------- 维度分：三态处置 ----------

def test_dimension_all_pass():
    results = [_result("a", _PERF, ProbeStatus.PASS), _result("b", _PERF, ProbeStatus.PASS)]
    dim = ScoreAggregator().dimension_score(_PERF, results)
    assert dim.score == 100.0
    assert dim.counted == 2


def test_dimension_degraded_half():
    results = [_result("a", _PERF, ProbeStatus.PASS), _result("b", _PERF, ProbeStatus.DEGRADED)]
    dim = ScoreAggregator().dimension_score(_PERF, results)
    assert dim.score == 75.0  # (1 + 0.5)/2


def test_dimension_fail_counts_zero_in_denominator():
    results = [_result("a", _CAP, ProbeStatus.PASS), _result("b", _CAP, ProbeStatus.FAIL)]
    dim = ScoreAggregator().dimension_score(_CAP, results)
    assert dim.score == 50.0  # (1 + 0)/2


def test_dimension_skipped_excluded_from_denominator():
    # skipped 不计入分母：PASS + SKIPPED → 100（不被稀释）
    results = [_result("a", _CAP, ProbeStatus.PASS), _result("b", _CAP, ProbeStatus.SKIPPED)]
    dim = ScoreAggregator().dimension_score(_CAP, results)
    assert dim.score == 100.0
    assert dim.counted == 1
    assert dim.strategy_count == 2


def test_dimension_all_skipped_is_none():
    results = [_result("a", _CAP, ProbeStatus.SKIPPED)]
    dim = ScoreAggregator().dimension_score(_CAP, results)
    assert dim.score is None


def test_dimension_uses_explicit_score():
    results = [_result("ttft", _PERF, ProbeStatus.PASS, score=0.8)]
    dim = ScoreAggregator().dimension_score(_PERF, results)
    assert dim.score == 80.0


def test_dimension_weighted_by_strategy_weight():
    results = [
        _result("a", _PERF, ProbeStatus.PASS, weight=3.0),
        _result("b", _PERF, ProbeStatus.FAIL, weight=1.0),
    ]
    dim = ScoreAggregator().dimension_score(_PERF, results)
    assert dim.score == 75.0  # (3*1 + 1*0)/4


# ---------- 总分聚合 ----------

def test_overall_weighted_average():
    by_cat = {
        _CONN: [_result("connectivity", _CONN, ProbeStatus.PASS)],
        _PERF: [_result("ttft", _PERF, ProbeStatus.DEGRADED)],
    }
    overall = ScoreAggregator().aggregate(by_cat)
    assert overall.available is True
    # 等权：(100 + 50)/2 = 75
    assert overall.overall == 75.0


def test_overall_excludes_undetected_dimensions():
    by_cat = {
        _CONN: [_result("connectivity", _CONN, ProbeStatus.PASS)],
        _PERF: [_result("ttft", _PERF, ProbeStatus.SKIPPED)],  # 未检测，不入总分
    }
    overall = ScoreAggregator().aggregate(by_cat)
    assert overall.overall == 100.0  # 仅连通性计入


def test_authenticity_score_injected():
    by_cat = {
        _CONN: [_result("connectivity", _CONN, ProbeStatus.PASS)],
        _AUTH: [_result("gemini_thinking", _AUTH, ProbeStatus.FAIL)],  # 朴素平均会是 0
    }
    # 注入双子分短板 60 覆盖朴素平均
    overall = ScoreAggregator().aggregate(by_cat, authenticity_score=60.0)
    assert overall.dimensions[_AUTH].score == 60.0
    assert overall.overall == 80.0  # (100 + 60)/2


# ---------- 连通性短路 ----------

def test_connectivity_failure_marks_unavailable():
    by_cat = {
        _CONN: [_result("connectivity", _CONN, ProbeStatus.FAIL)],
        _PERF: [_result("ttft", _PERF, ProbeStatus.PASS)],
    }
    overall = ScoreAggregator().aggregate(by_cat)
    assert overall.available is False
    assert overall.overall is None  # 不可用不强行给分


def test_custom_dimension_weights():
    by_cat = {
        _CONN: [_result("connectivity", _CONN, ProbeStatus.PASS)],  # 100
        _PERF: [_result("ttft", _PERF, ProbeStatus.FAIL)],  # 0
    }
    aggregator = ScoreAggregator(dimension_weights={_CONN: 3.0, _PERF: 1.0})
    overall = aggregator.aggregate(by_cat)
    assert overall.overall == 75.0  # (3*100 + 1*0)/4

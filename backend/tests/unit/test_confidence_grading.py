"""置信度调节与分级单元测试（Task 20，设计 §9.1/§9.6）。"""
import pytest

from app.scoring.confidence import (
    COMPAT_CONFIDENCE_FACTOR,
    AuthenticityLevel,
    ConfidenceGrader,
)


# ---------- 置信度调节 ----------

def test_native_full_confidence():
    assert ConfidenceGrader().confidence() == 1.0


def test_compat_downgrades_confidence():
    c = ConfidenceGrader().confidence(access_mode="openai_compat")
    assert c == COMPAT_CONFIDENCE_FACTOR  # 0.6


def test_sample_coverage_scales_confidence():
    assert ConfidenceGrader().confidence(sample_coverage=0.5) == 0.5


def test_compat_and_coverage_combine():
    # 0.6 * 0.5 = 0.3
    c = ConfidenceGrader().confidence(access_mode="openai_compat", sample_coverage=0.5)
    assert c == 0.3


def test_coverage_clamped():
    assert ConfidenceGrader().confidence(sample_coverage=2.0) == 1.0
    assert ConfidenceGrader().confidence(sample_coverage=-1.0) == 0.0


# ---------- 分级阈值 ----------

def test_grade_normal():
    result = ConfidenceGrader().grade(80.0, 1.0)
    assert result.level is AuthenticityLevel.NORMAL


def test_grade_suspicious():
    result = ConfidenceGrader().grade(60.0, 1.0)
    assert result.level is AuthenticityLevel.SUSPICIOUS


def test_grade_highly_suspicious():
    result = ConfidenceGrader().grade(30.0, 1.0, refute_signal_count=3)
    assert result.level is AuthenticityLevel.HIGHLY_SUSPICIOUS


def test_grade_boundary_inclusive():
    grader = ConfidenceGrader(high_threshold=75.0, low_threshold=45.0)
    assert grader.grade(75.0, 1.0).level is AuthenticityLevel.NORMAL
    assert grader.grade(45.0, 1.0).level is AuthenticityLevel.SUSPICIOUS


# ---------- 单信号误报控制 ----------

def test_single_signal_no_highly_suspicious():
    # 分值落在高度可疑区，但仅 1 个 refute 信号 → 降为可能可疑（§9.6 规则 3）
    result = ConfidenceGrader().grade(20.0, 1.0, refute_signal_count=1)
    assert result.level is AuthenticityLevel.SUSPICIOUS


def test_multi_signal_allows_highly_suspicious():
    result = ConfidenceGrader().grade(20.0, 1.0, refute_signal_count=2)
    assert result.level is AuthenticityLevel.HIGHLY_SUSPICIOUS


def test_no_signal_count_keeps_raw_level():
    # 未提供信号数 → 不应用误报控制，保持原始分级
    result = ConfidenceGrader().grade(20.0, 1.0)
    assert result.level is AuthenticityLevel.HIGHLY_SUSPICIOUS


# ---------- 阈值校验 ----------

def test_invalid_thresholds_rejected():
    with pytest.raises(ValueError):
        ConfidenceGrader(high_threshold=40.0, low_threshold=60.0)


def test_thresholds_snapshot_in_result():
    grader = ConfidenceGrader(high_threshold=80.0, low_threshold=50.0)
    result = grader.grade(90.0, 0.6)
    assert result.high_threshold == 80.0
    assert result.low_threshold == 50.0
    assert result.confidence == 0.6

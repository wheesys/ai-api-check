"""探针基类框架单元测试：抽象约束 + 上下文/预算 + 注册表分组。

零网络；用最小桩探针验证框架契约。
"""
import pytest

from app.probes.base import (
    BudgetCounter,
    Probe,
    ProbeCategory,
    ProbeContext,
    ProbeResult,
    ProbeStatus,
)
from app.probes.registry import ProbeRegistry


class _StubProbe(Probe):
    key = "unit_test_probe"
    category = ProbeCategory.CONNECTIVITY.value
    name = "单测桩探针"
    weight = 2.0

    async def run(self, ctx: ProbeContext) -> ProbeResult:
        return self.make_result(
            ProbeStatus.PASS, score=1.0, metrics={"http_status": 200}
        )


def _ctx(**overrides) -> ProbeContext:
    base = {"adapter": object(), "model_name": "demo"}
    base.update(overrides)
    return ProbeContext(**base)


def test_probe_is_abstract():
    with pytest.raises(TypeError):
        Probe()  # type: ignore[abstract]


def test_make_result_carries_probe_identity():
    probe = _StubProbe()
    result = probe.make_result(ProbeStatus.PASS, score=0.8)
    assert result.key == "unit_test_probe"
    assert result.category == "connectivity"
    assert result.name == "单测桩探针"
    assert result.weight == 2.0
    assert result.status is ProbeStatus.PASS
    assert result.score == 0.8


def test_skipped_helper_records_reason():
    probe = _StubProbe()
    result = probe.skipped("兼容层不适用")
    assert result.status is ProbeStatus.SKIPPED
    assert result.evidence["reason"] == "兼容层不适用"


def test_applicable_defaults_true():
    assert _StubProbe().applicable(_ctx()) is True


async def test_run_returns_result():
    result = await _StubProbe().run(_ctx())
    assert result.status is ProbeStatus.PASS
    assert result.metrics["http_status"] == 200


def test_context_cancelled_callback():
    ctx = _ctx(is_cancelled=lambda: True)
    assert ctx.cancelled() is True
    assert _ctx().cancelled() is False


def test_budget_counter_consume_and_exhaust():
    budget = BudgetCounter(max_requests=2)
    assert budget.remaining == 2
    budget.consume()
    assert budget.remaining == 1
    assert budget.exhausted is False
    budget.consume()
    assert budget.exhausted is True
    assert budget.remaining == 0


def test_registry_register_get_create():
    @ProbeRegistry.register
    class _RegProbe(_StubProbe):
        key = "reg_probe"

    assert "reg_probe" in ProbeRegistry.all_keys()
    assert ProbeRegistry.get("reg_probe") is _RegProbe
    assert isinstance(ProbeRegistry.create("reg_probe"), _RegProbe)


def test_registry_unknown_key_raises():
    with pytest.raises(KeyError):
        ProbeRegistry.get("does_not_exist")


def test_registry_duplicate_key_raises():
    @ProbeRegistry.register
    class _DupA(_StubProbe):
        key = "dup_key"

    with pytest.raises(ValueError, match="重复注册"):

        @ProbeRegistry.register
        class _DupB(_StubProbe):
            key = "dup_key"


def test_registry_by_category_groups():
    @ProbeRegistry.register
    class _CatProbe(_StubProbe):
        key = "cat_probe"
        category = ProbeCategory.PERFORMANCE.value

    grouped = ProbeRegistry.by_category()
    assert _CatProbe in grouped[ProbeCategory.PERFORMANCE.value]

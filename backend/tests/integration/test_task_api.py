"""检测任务服务集成测试（Task 22，设计 §8.1/§10 三层落库 + 评分编排）。

零网络：注入 FakeAdapter + Fake 探针，验证任务生命周期、strategy_result/detection_result
落库、SSE 事件经 broker 推送、取消语义。
"""
import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


def _key_manager() -> "KeyManager":
    """固定主密钥的 KeyManager，测试隔离（不触发本地密钥文件）。"""
    return KeyManager(master_key=Fernet.generate_key().decode())

from app.engine.events import broker
from app.engine.executor import TaskStatus
from app.models.database import (
    Base,
    DetectionResult,
    DetectionTask,
    Model,
    RelayStation,
    StrategyResult,
)
from app.models.schemas import DetectionTaskCreate
from app.probes.base import Probe, ProbeCategory, ProbeContext, ProbeResult, ProbeStatus
from app.security.crypto import KeyManager
from app.services.station_service import StationService
from app.services.task_service import TaskService
from tests.fixtures.fake_adapter import FakeAdapter


@pytest.fixture
def db_factory():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(bind=engine)
    yield sessionmaker(bind=engine, autocommit=False, autoflush=False)
    engine.dispose()


class FakeProbe(Probe):
    """脚本化探针替身。"""

    def __init__(self, key, category, status=ProbeStatus.PASS, score=1.0):
        self.key = key
        self.category = category
        self.name = key
        self.weight = 1.0
        self._status = status
        self._score = score

    async def run(self, ctx: ProbeContext) -> ProbeResult:
        return self.make_result(self._status, score=self._score)


def _seed(db, key_manager) -> tuple[int, int]:
    """建一个站点 + 模型，返回 (station_id, model_id)。"""
    station = RelayStation(
        name="站",
        protocols='["openai"]',
        base_url="https://relay.example.com",
        api_key_encrypted=key_manager.encrypt("sk-test"),
        status="active",
    )
    db.add(station)
    db.commit()
    db.refresh(station)
    model = Model(
        station_id=station.id,
        protocol="openai",
        access_mode="native",
        model_name="gpt-4o",
        source="manual",
        enabled=True,
    )
    db.add(model)
    db.commit()
    db.refresh(model)
    return station.id, model.id


def _service() -> TaskService:
    # 注入固定主密钥的 KeyManager，测试隔离
    return TaskService(station_service=StationService(_key_manager()))


def _probes() -> list[Probe]:
    return [
        FakeProbe("connectivity", ProbeCategory.CONNECTIVITY.value),
        FakeProbe("ttft", ProbeCategory.PERFORMANCE.value),
        FakeProbe("cap_streaming", ProbeCategory.CAPABILITY.value),
    ]


# ---------- 创建校验 ----------

def test_create_validates_model_belongs_to_station(db_factory):
    db = db_factory()
    km = _key_manager()
    station_id, model_id = _seed(db, km)
    service = TaskService(station_service=StationService(km))
    task = service.create(db, DetectionTaskCreate(station_id=station_id, model_id=model_id))
    assert task.status == "pending"


def test_create_rejects_foreign_model(db_factory):
    db = db_factory()
    km = _key_manager()
    station_id, _model_id = _seed(db, km)
    service = TaskService(station_service=StationService(km))
    with pytest.raises(ValueError):
        service.create(db, DetectionTaskCreate(station_id=station_id, model_id=9999))


# ---------- 执行与落库 ----------

async def test_run_persists_three_tier_results(db_factory):
    db = db_factory()
    km = _key_manager()
    station_id, model_id = _seed(db, km)
    service = TaskService(station_service=StationService(km))
    task = service.create(db, DetectionTaskCreate(station_id=station_id, model_id=model_id))

    outcome = await service.run(
        db, task.id, adapter=FakeAdapter(), probes=_probes()
    )
    assert outcome.status is TaskStatus.COMPLETED

    # 任务终态
    refreshed = db.get(DetectionTask, task.id)
    assert refreshed.status == "completed"
    assert refreshed.progress == 100
    assert refreshed.finished_at is not None

    # 策略结果中层：3 条
    strategies = list(db.scalars(select(StrategyResult).where(StrategyResult.task_id == task.id)))
    assert len(strategies) == 3

    # 结果汇总：含评分
    result = db.scalar(select(DetectionResult).where(DetectionResult.task_id == task.id))
    assert result is not None
    assert result.overall_score is not None
    assert result.authenticity_score is not None


async def test_run_connectivity_failure_marks_failed(db_factory):
    db = db_factory()
    km = _key_manager()
    station_id, model_id = _seed(db, km)
    service = TaskService(station_service=StationService(km))
    task = service.create(db, DetectionTaskCreate(station_id=station_id, model_id=model_id))

    probes = [
        FakeProbe("connectivity", ProbeCategory.CONNECTIVITY.value, ProbeStatus.FAIL, 0.0),
        FakeProbe("ttft", ProbeCategory.PERFORMANCE.value),
    ]
    outcome = await service.run(db, task.id, adapter=FakeAdapter(), probes=probes)
    assert outcome.status is TaskStatus.FAILED
    refreshed = db.get(DetectionTask, task.id)
    assert refreshed.status == "failed"
    assert refreshed.error_message == "connectivity_failed"
    # 短路：未落 detection_result 汇总分
    assert db.scalar(select(DetectionResult).where(DetectionResult.task_id == task.id)) is None


async def test_run_emits_sse_events(db_factory):
    db = db_factory()
    km = _key_manager()
    station_id, model_id = _seed(db, km)
    service = TaskService(station_service=StationService(km))
    task = service.create(db, DetectionTaskCreate(station_id=station_id, model_id=model_id))

    collected = []

    async def consume():
        async for event in broker.subscribe(task.id):
            collected.append(event.type)

    import asyncio

    consumer = asyncio.create_task(consume())
    await asyncio.sleep(0)  # 让订阅者先注册通道
    await service.run(db, task.id, adapter=FakeAdapter(), probes=_probes())
    await asyncio.wait_for(consumer, timeout=2.0)
    assert collected[0] == "task.started"
    assert collected[-1] == "task.completed"
    assert "task.scored" in collected

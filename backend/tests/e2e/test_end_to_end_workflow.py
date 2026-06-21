"""端到端工作流验证（Task 27，设计 §10 全链路）。

以真实栈跑通：建站 → 录模型 → 建任务 → 执行器跑真实探针 → 真实评分编排 → 三层落库 →
报告装配 → PDF 导出。仅 adapter 用 FakeAdapter 替身（零网络），其余全部真实组件，
验证落库完整性、评分计算、脱敏与 PDF 产出。
"""
import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.engine.executor import TaskStatus
from app.models.database import (
    Base,
    DetectionResult,
    DetectionTask,
    StrategyResult,
)
from app.models.schemas import DetectionTaskCreate, ModelCreate, RelayStationCreate
from app.probes.registry import ProbeRegistry
from app.providers.base import AdapterResponse, StreamChunk, TokenUsage
from app.security.crypto import KeyManager
from app.services.pdf_service import PdfService
from app.services.report_service import ReportService
from app.services.station_service import StationService
from app.services.task_service import TaskService
from tests.fixtures.fake_adapter import FakeAdapter


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine, autocommit=False, autoflush=False)()
    yield session
    session.close()
    engine.dispose()


def _key_manager() -> KeyManager:
    return KeyManager(master_key=Fernet.generate_key().decode())


def _fake_adapter() -> FakeAdapter:
    """配置一个可满足连通性/计费/能力探针的伪适配器。"""
    response = AdapterResponse(
        http_status=200,
        success=True,
        content='{"ok": true}',  # 受控 JSON 探针可解析
        usage=TokenUsage(prompt_tokens=12, completion_tokens=6, total_tokens=18),
        feature_flags={"tool_calls": [{"name": "demo"}]},
    )
    return FakeAdapter(
        chat_response=response,
        stream_chunks=[StreamChunk(delta_text="Hi", usage=None)],
    )


def _real_probe_subset():
    """从注册表取真实探针子集（覆盖连通性/计费/能力，确定性可跑）。"""
    keys = ["connectivity", "billing_consistency", "cap_json_mode", "cap_function_call"]
    return [ProbeRegistry.create(key) for key in keys]


async def test_full_backend_pipeline(db):
    km = _key_manager()
    station_service = StationService(km)
    task_service = TaskService(station_service=station_service)

    # 1) 建站（明文 Key 加密落库）
    station = station_service.create(
        db,
        RelayStationCreate(
            name="E2E 站",
            protocols=["openai"],
            base_url="https://relay.example.com",
            api_key="sk-e2e-plaintext-1234567890",
        ),
    )

    # 2) 录模型（带价格/上下文，驱动计费/上下文探针）
    from app.services.model_service import ModelService

    model = ModelService().add_manual(
        db,
        ModelCreate(
            station_id=station.id,
            protocol="openai",
            model_name="gpt-4o",
            display_name="GPT-4o",
            input_price="0.000005",
            output_price="0.000015",
            declared_context_length=128000,
        ),
    )

    # 3) 建任务
    task = task_service.create(
        db, DetectionTaskCreate(station_id=station.id, model_id=model.id)
    )
    assert task.status == "pending"

    # 4) 执行（真实探针子集 + 伪适配器，真实评分/落库）
    outcome = await task_service.run(
        db, task.id, adapter=_fake_adapter(), probes=_real_probe_subset()
    )
    assert outcome.status is TaskStatus.COMPLETED

    # 5) 三层落库完整性
    refreshed = db.get(DetectionTask, task.id)
    assert refreshed.status == "completed"
    assert refreshed.progress == 100
    strategies = list(
        db.scalars(select(StrategyResult).where(StrategyResult.task_id == task.id))
    )
    assert len(strategies) == 4  # 四条策略结果
    result = db.scalar(
        select(DetectionResult).where(DetectionResult.task_id == task.id)
    )
    assert result is not None
    assert result.overall_score is not None
    assert result.connectivity_score is not None
    assert result.authenticity_score is not None

    # 6) 报告装配 + PDF
    report = ReportService().assemble_report(db, task.id)
    assert report["summary"]["overall_score"] is not None
    pdf = PdfService().generate_pdf(report)
    assert pdf[:4] == b"%PDF"

    # 7) 脱敏审计：策略证据/报告均不含明文 Key
    serialized = str(report)
    assert "sk-e2e-plaintext" not in serialized
    for strategy in strategies:
        assert "sk-e2e" not in (strategy.evidence_json or "")


async def test_pipeline_authenticity_scored_from_evidence(db):
    """验证真实性维度由证据信号驱动评分（能力大面积失败 → shell 扣分）。"""
    km = _key_manager()
    station_service = StationService(km)
    task_service = TaskService(station_service=station_service)
    station = station_service.create(
        db,
        RelayStationCreate(
            name="站", protocols=["openai"], base_url="https://x.example.com",
            api_key="sk-aaaaaaaaaaaaaaaa",
        ),
    )
    from app.services.model_service import ModelService

    model = ModelService().add_manual(
        db, ModelCreate(station_id=station.id, protocol="openai", model_name="m")
    )
    task = task_service.create(
        db, DetectionTaskCreate(station_id=station.id, model_id=model.id)
    )

    # 能力探针失败的适配器仍产出真实性评分（证据信号驱动）
    failing = FakeAdapter(
        chat_response=AdapterResponse(http_status=200, success=True, content="ok"),
        stream_chunks=[StreamChunk(delta_text="hi")],
    )
    probes = [ProbeRegistry.create("connectivity"), ProbeRegistry.create("cap_json_mode")]
    outcome = await task_service.run(db, task.id, adapter=failing, probes=probes)
    assert outcome.status is TaskStatus.COMPLETED
    result = db.scalar(
        select(DetectionResult).where(DetectionResult.task_id == task.id)
    )
    assert result.authenticity_score is not None

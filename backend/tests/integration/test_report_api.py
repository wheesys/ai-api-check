"""报告服务、PDF 生成与报告 API 集成测试（Task 23 + 25，设计 §10/§12）。

零网络：内存 SQLite 直接构造三层结果，验证报告装配、PDF 渲染（HTML + weasyprint 字节）、
脱敏（不含 Key）。
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.database import (
    Base,
    DetectionResult,
    DetectionTask,
    Model,
    StrategyResult,
)
from app.services.pdf_service import PdfService
from app.services.report_service import ReportService


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


def _seed_full(db) -> int:
    """构造一个完成的任务 + 模型 + 结果 + 策略，返回 task_id。"""
    model = Model(
        station_id=1, protocol="gemini", access_mode="native",
        model_name="gemini-2.5-pro", display_name="Gemini 2.5 Pro", source="manual",
    )
    db.add(model)
    db.commit()
    db.refresh(model)
    task = DetectionTask(
        station_id=1, model_id=model.id, status="completed", progress=100
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    db.add(
        DetectionResult(
            task_id=task.id, model_id=model.id, overall_score=82.5,
            connectivity_score=100.0, performance_score=80.0, billing_score=75.0,
            capability_score=90.0, authenticity_score=68.0,
            authenticity_subscores_json='{"shell_score": 68.0, "direct_score": 90.0, '
            '"authenticity_score": 68.0, "level": "suspicious", "confidence": 1.0, '
            '"high_threshold": 75.0, "low_threshold": 45.0, "b_group_confirmed": false, '
            '"signals": [{"key": "gemini_thinking", "direction": "refute", '
            '"contribution": -20.0, "evidence": {"source": "gemini_thinking"}}]}',
            details_json='{"dimension_scores": {"connectivity": 100.0}}',
        )
    )
    db.add(
        StrategyResult(
            task_id=task.id, model_id=model.id, strategy_category="connectivity",
            strategy_key="connectivity", strategy_name="连通性", result_status="pass",
            score=1.0, weight=1.0, metrics_json='{"http_status": 200}',
        )
    )
    db.add(
        StrategyResult(
            task_id=task.id, model_id=model.id, strategy_category="authenticity",
            strategy_key="gemini_thinking", strategy_name="Gemini 思考",
            result_status="fail", score=0.0, weight=1.0,
            evidence_json='{"reason": "thoughtsTokenCount 缺失"}',
        )
    )
    db.commit()
    return task.id


# ---------- 报告装配 ----------

def test_assemble_report_structure(db):
    task_id = _seed_full(db)
    report = ReportService().assemble_report(db, task_id)
    assert report["task"]["status"] == "completed"
    assert report["model"]["model_name"] == "gemini-2.5-pro"
    assert report["summary"]["authenticity_score"] == 68.0
    assert report["summary"]["authenticity_subscores"]["level"] == "suspicious"
    assert len(report["strategies"]) == 2


def test_assemble_report_missing_task(db):
    assert ReportService().assemble_report(db, 999) is None


def test_get_strategies_ordered(db):
    task_id = _seed_full(db)
    strategies = ReportService().get_strategies(db, task_id)
    assert [s.strategy_key for s in strategies] == ["connectivity", "gemini_thinking"]


# ---------- PDF 渲染 ----------

def test_render_html_contains_scores(db):
    task_id = _seed_full(db)
    report = ReportService().assemble_report(db, task_id)
    html = PdfService().render_html(report)
    assert "综合评分" in html
    assert "82.5" in html
    assert "Gemini 2.5 Pro" in html
    assert "来源真实性分析" in html
    assert "suspicious" in html


def test_render_html_no_key_leak(db):
    task_id = _seed_full(db)
    report = ReportService().assemble_report(db, task_id)
    html = PdfService().render_html(report)
    assert "sk-" not in html
    assert "api_key" not in html.lower()


def test_generate_pdf_returns_bytes(db):
    task_id = _seed_full(db)
    report = ReportService().assemble_report(db, task_id)
    pdf = PdfService().generate_pdf(report)
    assert pdf[:4] == b"%PDF"  # PDF 魔数
    assert len(pdf) > 1000


def test_render_html_with_chart_image(db):
    task_id = _seed_full(db)
    report = ReportService().assemble_report(db, task_id)
    charts = {"radar": "data:image/png;base64,AAAA"}
    html = PdfService().render_html(report, charts)
    assert "data:image/png;base64,AAAA" in html

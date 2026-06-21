"""结果与报告 API（设计 §10 / §12，落实 Task 23 + Task 25 端点）。

端点：结果汇总、策略明细（可下钻探针记录）、PDF 导出（POST 带前端图表 / GET 纯数据回退）。
所有响应数据源自三层结果模型，落库即脱敏，绝不含 Key。
"""
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.models.schemas import (
    DetectionResultResponse,
    ProbeRecordResponse,
    StrategyResultResponse,
)
from app.services.pdf_service import PdfService
from app.services.report_service import ReportService

router = APIRouter(prefix="/api/tasks", tags=["reports"])

_report_service = ReportService()
_pdf_service = PdfService()


class PdfExportRequest(BaseModel):
    """PDF 导出请求：前端 ECharts 图表以 {名称: data-uri} 内联传入（可空）。"""

    charts: dict[str, str] = Field(default_factory=dict, description="图表 base64 data-uri")


@router.get("/{task_id}/result", response_model=DetectionResultResponse)
def get_result(task_id: int, db: Session = Depends(get_db)) -> DetectionResultResponse:
    """取任务结果汇总（含真实性子分）。"""
    result = _report_service.get_result(db, task_id)
    if result is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "结果尚未就绪或任务不存在")
    return DetectionResultResponse.model_validate(result, from_attributes=True)


@router.get("/{task_id}/strategies", response_model=list[StrategyResultResponse])
def list_strategies(
    task_id: int, db: Session = Depends(get_db)
) -> list[StrategyResultResponse]:
    """取任务全部策略结果（可下钻探针记录）。"""
    return [
        StrategyResultResponse.model_validate(s, from_attributes=True)
        for s in _report_service.get_strategies(db, task_id)
    ]


@router.get(
    "/{task_id}/strategies/{strategy_id}/probes",
    response_model=list[ProbeRecordResponse],
)
def list_probe_records(
    task_id: int, strategy_id: int, db: Session = Depends(get_db)
) -> list[ProbeRecordResponse]:
    """下钻某策略结果的探针原始记录。"""
    return [
        ProbeRecordResponse.model_validate(p, from_attributes=True)
        for p in _report_service.get_probe_records(db, strategy_id)
    ]


@router.post("/{task_id}/report.pdf")
def export_pdf_with_charts(
    task_id: int, payload: PdfExportRequest, db: Session = Depends(get_db)
) -> Response:
    """带前端图表的 PDF 导出。"""
    return _build_pdf_response(db, task_id, payload.charts)


@router.get("/{task_id}/report.pdf")
def export_pdf_data_only(task_id: int, db: Session = Depends(get_db)) -> Response:
    """纯数据 PDF 导出（无图表回退）。"""
    return _build_pdf_response(db, task_id, {})


def _build_pdf_response(db: Session, task_id: int, charts: dict[str, str]) -> Response:
    """装配报告数据并生成 PDF 响应。"""
    report = _report_service.assemble_report(db, task_id)
    if report is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "任务不存在")
    pdf_bytes = _pdf_service.generate_pdf(report, charts)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="report-task-{task_id}.pdf"'
        },
    )

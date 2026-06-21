"""报告服务（设计 §10 / §12 报告结构）。

从三层结果模型装配报告数据：detection_result 汇总 + strategy_result 中层（可下钻
probe_record）。所有数据源已在落库时脱敏（禁含 Key），本层只做读取与结构组装。
"""
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.database import (
    DetectionResult,
    DetectionTask,
    Model,
    ProbeRecord,
    StrategyResult,
)


def _load_json(raw: str | None) -> dict | list | None:
    """容错解析 JSON 文本列（落库时已脱敏）。"""
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return None


class ReportService:
    """报告数据装配服务。"""

    def get_result(self, db: Session, task_id: int) -> DetectionResult | None:
        """取某任务的结果汇总。"""
        return db.scalar(
            select(DetectionResult).where(DetectionResult.task_id == task_id)
        )

    def get_strategies(self, db: Session, task_id: int) -> list[StrategyResult]:
        """取某任务全部策略结果（按 id 升序，便于稳定展示）。"""
        return list(
            db.scalars(
                select(StrategyResult)
                .where(StrategyResult.task_id == task_id)
                .order_by(StrategyResult.id)
            )
        )

    def get_probe_records(
        self, db: Session, strategy_result_id: int
    ) -> list[ProbeRecord]:
        """下钻某策略结果的探针原始记录。"""
        return list(
            db.scalars(
                select(ProbeRecord)
                .where(ProbeRecord.strategy_result_id == strategy_result_id)
                .order_by(ProbeRecord.id)
            )
        )

    def assemble_report(self, db: Session, task_id: int) -> dict | None:
        """装配完整报告数据（供 PDF 渲染与前端展示，§12 九区块来源）。

        任务不存在返回 None；结果未就绪时 result 为空但仍返回任务与策略骨架。
        """
        task = db.get(DetectionTask, task_id)
        if task is None:
            return None
        model = db.get(Model, task.model_id)
        result = self.get_result(db, task_id)
        strategies = self.get_strategies(db, task_id)
        return {
            "task": {
                "id": task.id,
                "status": task.status,
                "progress": task.progress,
                "error_message": task.error_message,
                "created_at": task.created_at.isoformat() if task.created_at else None,
                "finished_at": (
                    task.finished_at.isoformat() if task.finished_at else None
                ),
            },
            "model": (
                {
                    "model_name": model.model_name,
                    "display_name": model.display_name,
                    "protocol": model.protocol,
                    "access_mode": model.access_mode,
                }
                if model is not None
                else None
            ),
            "summary": self._result_to_dict(result),
            "strategies": [self._strategy_to_dict(s) for s in strategies],
        }

    @staticmethod
    def _result_to_dict(result: DetectionResult | None) -> dict | None:
        if result is None:
            return None
        return {
            "overall_score": result.overall_score,
            "connectivity_score": result.connectivity_score,
            "performance_score": result.performance_score,
            "billing_score": result.billing_score,
            "capability_score": result.capability_score,
            "authenticity_score": result.authenticity_score,
            "authenticity_subscores": _load_json(result.authenticity_subscores_json),
            "details": _load_json(result.details_json),
        }

    @staticmethod
    def _strategy_to_dict(strategy: StrategyResult) -> dict:
        return {
            "id": strategy.id,
            "category": strategy.strategy_category,
            "key": strategy.strategy_key,
            "name": strategy.strategy_name,
            "status": strategy.result_status,
            "score": strategy.score,
            "weight": strategy.weight,
            "metrics": _load_json(strategy.metrics_json),
            "evidence": _load_json(strategy.evidence_json),
        }

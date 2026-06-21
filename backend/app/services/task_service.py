"""检测任务服务（设计 §8.1 / §10，三层结果落库 + 评分编排）。

编排一次检测的完整生命周期：
  创建任务 → 构建适配器/探针上下文 → 执行器跑探针（SSE 事件经 broker 推送）→
  评分编排 → 落库（detection_task 终态 + strategy_result 中层 + detection_result 汇总）。

可测试性（SOLID-D）：`run` 允许注入 adapter 与 probes，零网络单测可用 Fake 替身；
生产路径据 station+model 配置构建真实适配器并在 HTTPClient 生命周期内执行。
"""
from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.engine.events import broker
from app.engine.executor import ExecutionResult, TaskExecutor, TaskStatus
from app.models.database import (
    DetectionResult,
    DetectionTask,
    Model,
    RelayStation,
    StrategyResult,
)
from app.models.schemas import DetectionTaskCreate
from app.probes.authenticity import AuthenticityEvidence
from app.probes.base import BudgetCounter, Probe, ProbeContext, ProbeResult
from app.probes.registry import ProbeRegistry
from app.providers.adapter_factory import AdapterFactory
from app.providers.base import ProviderAdapter
from app.providers.gemini_adapter import ENDPOINT_DEVELOPER
from app.scoring.orchestrator import ScoreReport, ScoringOrchestrator
from app.services.station_service import StationService
from app.utils.http_client import HTTPClient

# 任务取消标志注册表（进程级；ProbeContext 在探针边界读取）
_cancel_flags: dict[int, bool] = {}
# 默认单任务请求预算上限（防探针失控刷量）
_DEFAULT_TASK_BUDGET = 60


class TaskService:
    """检测任务生命周期服务。"""

    def __init__(
        self,
        station_service: StationService | None = None,
        orchestrator: ScoringOrchestrator | None = None,
    ) -> None:
        self._stations = station_service or StationService()
        self._orchestrator = orchestrator or ScoringOrchestrator()

    # ---------- 创建与查询 ----------

    def create(self, db: Session, payload: DetectionTaskCreate) -> DetectionTask:
        """创建检测任务（校验 model ∈ station，落 pending + 配置快照）。"""
        station = db.get(RelayStation, payload.station_id)
        if station is None:
            raise ValueError("中转站不存在")
        model = db.get(Model, payload.model_id)
        if model is None or model.station_id != payload.station_id:
            raise ValueError("模型不存在或不属于该中转站")
        task = DetectionTask(
            station_id=payload.station_id,
            model_id=payload.model_id,
            status="pending",
            progress=0,
            config_json=(
                json.dumps(payload.config, ensure_ascii=False)
                if payload.config is not None
                else None
            ),
        )
        db.add(task)
        db.commit()
        db.refresh(task)
        return task

    def get(self, db: Session, task_id: int) -> DetectionTask | None:
        return db.get(DetectionTask, task_id)

    def list(self, db: Session) -> list[DetectionTask]:
        return list(
            db.scalars(select(DetectionTask).order_by(DetectionTask.id.desc()))
        )

    def request_cancel(self, task_id: int) -> None:
        """置取消标志（执行器在探针边界感知）。"""
        _cancel_flags[task_id] = True

    # ---------- 执行 ----------

    async def run(
        self,
        db: Session,
        task_id: int,
        *,
        adapter: ProviderAdapter | None = None,
        probes: list[Probe] | None = None,
    ) -> ExecutionResult:
        """执行检测任务：跑探针、评分、落库；返回执行结果。"""
        task = db.get(DetectionTask, task_id)
        if task is None:
            raise ValueError("任务不存在")
        station = db.get(RelayStation, task.station_id)
        model = db.get(Model, task.model_id)
        if station is None or model is None:
            raise ValueError("任务关联的站点或模型缺失")

        _cancel_flags.setdefault(task_id, False)
        task.status = "running"
        task.started_at = _now(db)
        db.commit()

        if adapter is not None:
            outcome = await self._execute(db, task, model, adapter, probes)
        else:
            api_key = self._stations.decrypt_key(station)
            async with HTTPClient(
                timeout_seconds=settings.request_timeout_seconds
            ) as client:
                built = _build_adapter(station, model, api_key, client)
                outcome = await self._execute(db, task, model, built, probes)

        self._finalize(db, task, outcome)
        _cancel_flags.pop(task_id, None)
        return outcome

    async def _execute(
        self,
        db: Session,
        task: DetectionTask,
        model: Model,
        adapter: ProviderAdapter,
        probes: list[Probe] | None,
    ) -> ExecutionResult:
        """构建上下文并跑执行器，评分经 orchestrator 注入。"""
        selected = probes if probes is not None else _all_probes()
        ctx = _build_context_with_adapter(task, model, adapter)
        report_holder: dict[str, ScoreReport] = {}

        def scorer(results: list[ProbeResult]) -> dict:
            report = self._orchestrator.score(
                results,
                access_mode=model.access_mode,
                evidence=_build_evidence(model, results),
            )
            report_holder["report"] = report
            return report.to_dict()

        executor = TaskExecutor(
            max_concurrency=settings.default_max_concurrency_per_task,
            task_timeout_seconds=settings.task_timeout_seconds,
        )
        outcome = await executor.run(
            selected, ctx, emit=broker.make_sink(task.id), scorer=scorer
        )
        # 落策略结果中层
        self._persist_strategies(db, task, outcome.probe_results)
        # 正常完成才落汇总分
        if "report" in report_holder:
            self._persist_result(db, task, report_holder["report"])
        return outcome

    # ---------- 落库 ----------

    def _persist_strategies(
        self, db: Session, task: DetectionTask, results: list[ProbeResult]
    ) -> None:
        """每条 ProbeResult 落一行 strategy_result（脱敏 JSON）。"""
        for result in results:
            db.add(
                StrategyResult(
                    task_id=task.id,
                    model_id=task.model_id,
                    strategy_category=result.category,
                    strategy_key=result.key,
                    strategy_name=result.name,
                    result_status=result.status.value,
                    score=result.score,
                    weight=result.weight,
                    metrics_json=_dump(result.metrics),
                    evidence_json=_dump(result.evidence),
                )
            )
        db.commit()

    def _persist_result(
        self, db: Session, task: DetectionTask, report: ScoreReport
    ) -> None:
        """评分报告落 detection_result 汇总。"""
        dims = report.dimension_scores
        db.add(
            DetectionResult(
                task_id=task.id,
                model_id=task.model_id,
                overall_score=report.overall.overall,
                connectivity_score=dims.get("connectivity"),
                performance_score=dims.get("performance"),
                billing_score=dims.get("billing"),
                capability_score=dims.get("capability"),
                authenticity_score=report.authenticity.authenticity_score,
                authenticity_subscores_json=_dump(report.to_dict()["authenticity"]),
                details_json=_dump({"dimension_scores": dims}),
            )
        )
        db.commit()

    def _finalize(
        self, db: Session, task: DetectionTask, outcome: ExecutionResult
    ) -> None:
        """写任务终态与进度。"""
        task.status = outcome.status.value
        task.progress = int(round(outcome.progress * 100))
        task.finished_at = _now(db)
        if outcome.status is TaskStatus.FAILED and outcome.failure_reason:
            task.error_message = outcome.failure_reason
        db.commit()


# ---------- 模块级辅助 ----------


def _all_probes() -> list[Probe]:
    """实例化全部已注册探针。"""
    return [ProbeRegistry.create(key) for key in ProbeRegistry.all_keys()]


def _build_context(task: DetectionTask, model: Model) -> ProbeContext:
    """据任务配置与模型构建探针上下文。"""
    config = json.loads(task.config_json) if task.config_json else {}
    thresholds = dict(config.get("thresholds", {}))
    if model.declared_context_length:
        thresholds.setdefault("declared_context", model.declared_context_length)
    if model.input_price is not None:
        thresholds.setdefault("input_price", model.input_price)
    if model.output_price is not None:
        thresholds.setdefault("output_price", model.output_price)
    budget = BudgetCounter(
        max_requests=int(config.get("max_requests", _DEFAULT_TASK_BUDGET))
    )
    return ProbeContext(
        adapter=None,  # 由调用方在 _execute 前置换为真实适配器
        model_name=model.model_name,
        access_mode=model.access_mode,
        thresholds=thresholds,
        budget=budget,
        is_cancelled=lambda: _cancel_flags.get(task.id, False),
        declared_capabilities=set(config.get("declared_capabilities", [])),
    )


def _build_evidence(model: Model, results: list[ProbeResult]) -> AuthenticityEvidence:
    """从探针结果装配最小真实性证据（供提取器求信号）。"""
    capability_results = {
        r.key: r.status.value for r in results if r.category == "capability"
    }
    billing = next((r for r in results if r.key == "billing_consistency"), None)
    deviation = billing.metrics.get("deviation") if billing else None
    return AuthenticityEvidence(
        protocol=model.protocol,
        declared_model=model.model_name,
        access_mode=model.access_mode,
        capability_results=capability_results or None,
        billing_deviation=deviation,
    )


def _build_adapter(
    station: RelayStation, model: Model, api_key: str, client: HTTPClient
) -> ProviderAdapter:
    """据模型协议/接入形态构建适配器（Gemini 原生附 endpoint/vertex 配置）。"""
    kwargs: dict = {
        "base_url": station.base_url,
        "api_key": api_key,
        "http_client": client,
    }
    if model.protocol == "gemini" and model.access_mode == "native":
        kwargs["endpoint_style"] = model.gemini_endpoint_style or ENDPOINT_DEVELOPER
        if model.gemini_vertex_json:
            vertex = json.loads(model.gemini_vertex_json)
            kwargs["project"] = vertex.get("project")
            kwargs["location"] = vertex.get("location")
    return AdapterFactory.create(model.protocol, model.access_mode, **kwargs)


def _build_context_with_adapter(
    task: DetectionTask, model: Model, adapter: ProviderAdapter
) -> ProbeContext:
    """构建上下文并绑定适配器（_execute 使用）。"""
    ctx = _build_context(task, model)
    ctx.adapter = adapter
    return ctx


def _dump(value: dict | None) -> str | None:
    """字典 → 脱敏 JSON 文本；空返回 None。"""
    if not value:
        return None
    return json.dumps(value, ensure_ascii=False, default=str)


def _now(db: Session):
    """当前 UTC 时间（复用 ORM 的时钟，避免弃用告警）。"""
    from app.models.database import _utcnow

    return _utcnow()

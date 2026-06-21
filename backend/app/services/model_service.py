"""模型服务（设计 §10.6 模型拉取编排）。

承载模型 CRUD 与多协议拉取编排：
  - 逐适配器（协议 × 接入形态）并行拉取上游模型列表；
  - 以 `(protocol, model_name, access_mode)` 为唯一键合并去重（含与库内既有模型去重）；
  - 部分失败不阻断其他适配器，返回逐项失败原因（脱敏），失败项回退手动录入。

职责边界（SOLID-S/-D）：服务只做编排与落库，适配器由调用方（API 层）在 HTTPClient
生命周期内构建并注入，服务不感知协议收发细节，便于零网络单测。
"""
import asyncio
import json
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.database import Model
from app.models.schemas import ModelCreate
from app.providers.base import ProviderAdapter
from app.utils.errors import ProbeError


@dataclass
class FetchFailure:
    """单适配器拉取失败记录（脱敏）。"""

    protocol: str
    access_mode: str
    reason: str


@dataclass
class FetchOutcome:
    """拉取编排结果（对应 §10.6 响应结构）。"""

    fetched: list[Model] = field(default_factory=list)
    failures: list[FetchFailure] = field(default_factory=list)
    fallback_manual: bool = False


def _dedup_key(protocol: str, model_name: str, access_mode: str) -> tuple[str, str, str]:
    """模型唯一键：同名模型在不同协议/接入形态视为不同条目（§10.6-4）。"""
    return (protocol, model_name, access_mode)


class ModelService:
    """模型 CRUD 与拉取编排服务。"""

    def add_manual(self, db: Session, payload: ModelCreate) -> Model:
        """手动录入模型（source 强制 manual，不覆盖拉取来源）。"""
        model = Model(
            station_id=payload.station_id,
            protocol=payload.protocol,
            access_mode=payload.access_mode,
            gemini_endpoint_style=payload.gemini_endpoint_style,
            gemini_vertex_json=(
                json.dumps(payload.gemini_vertex_json, ensure_ascii=False)
                if payload.gemini_vertex_json is not None
                else None
            ),
            model_name=payload.model_name,
            display_name=payload.display_name,
            source="manual",
            input_price=str(payload.input_price) if payload.input_price is not None else None,
            output_price=(
                str(payload.output_price) if payload.output_price is not None else None
            ),
            declared_context_length=payload.declared_context_length,
            enabled=payload.enabled,
        )
        db.add(model)
        db.commit()
        db.refresh(model)
        return model

    def get(self, db: Session, model_id: int) -> Model | None:
        return db.get(Model, model_id)

    def list_for_station(self, db: Session, station_id: int) -> list[Model]:
        """列出某站点全部模型（按 id 升序）。"""
        return list(
            db.scalars(
                select(Model).where(Model.station_id == station_id).order_by(Model.id)
            )
        )

    async def fetch_and_store(
        self, db: Session, station_id: int, adapters: list[ProviderAdapter]
    ) -> FetchOutcome:
        """并行拉取多适配器模型列表，合并去重后落库（§10.6）。"""
        outcome = FetchOutcome()
        if not adapters:
            outcome.fallback_manual = True
            return outcome

        # 逐适配器并行拉取，单点失败不抛出（gather 收集异常）
        results = await asyncio.gather(
            *[self._fetch_one(adapter) for adapter in adapters],
            return_exceptions=False,
        )

        existing = self._existing_keys(db, station_id)
        seen: set[tuple[str, str, str]] = set()
        for adapter, (models, failure) in zip(adapters, results, strict=True):
            if failure is not None:
                outcome.failures.append(failure)
                continue
            for info in models:
                key = _dedup_key(info.protocol, info.model_name, info.access_mode)
                if key in existing or key in seen:
                    continue  # 与库内或本批已拉取去重
                seen.add(key)
                model = Model(
                    station_id=station_id,
                    protocol=info.protocol,
                    access_mode=info.access_mode,
                    gemini_endpoint_style=info.gemini_endpoint_style,
                    model_name=info.model_name,
                    display_name=info.display_name,
                    source="fetched",
                    enabled=True,
                )
                db.add(model)
                outcome.fetched.append(model)
        if outcome.fetched:
            db.commit()
            for model in outcome.fetched:
                db.refresh(model)
        # 全部适配器失败 → 建议回退手动录入
        outcome.fallback_manual = bool(outcome.failures) and not outcome.fetched
        return outcome

    @staticmethod
    async def _fetch_one(
        adapter: ProviderAdapter,
    ) -> tuple[list, FetchFailure | None]:
        """拉取单适配器模型列表；失败归一为脱敏 FetchFailure（不抛出）。"""
        try:
            models = await adapter.fetch_models()
            return models, None
        except ProbeError as error:
            return [], FetchFailure(
                protocol=adapter.protocol,
                access_mode=adapter.access_mode,
                reason=error.message,
            )
        except Exception as error:  # noqa: BLE001 单点失败隔离，原因脱敏
            from app.security.sanitizer import ErrorSanitizer

            return [], FetchFailure(
                protocol=adapter.protocol,
                access_mode=adapter.access_mode,
                reason=ErrorSanitizer.sanitize(str(error)) or "拉取失败",
            )

    @staticmethod
    def _existing_keys(db: Session, station_id: int) -> set[tuple[str, str, str]]:
        """库内既有模型的唯一键集合，用于去重。"""
        rows = db.scalars(select(Model).where(Model.station_id == station_id))
        return {_dedup_key(m.protocol, m.model_name, m.access_mode) for m in rows}

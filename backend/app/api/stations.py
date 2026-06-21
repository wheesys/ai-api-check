"""中转站与模型 API（设计 §10，落实安全规则 3.5）。

端点：中转站 CRUD、模型拉取编排、模型列表与手动录入。所有响应经服务层 `to_response`
脱敏，绝不回显 api_key。拉取时在 HTTPClient 生命周期内构建各协议/接入形态适配器并注入
模型服务编排。
"""
import json

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.config import settings
from app.database.session import get_db
from app.models.database import RelayStation
from app.models.schemas import (
    ModelCreate,
    ModelResponse,
    RelayStationCreate,
    RelayStationResponse,
    RelayStationUpdate,
)
from app.providers.adapter_factory import AdapterFactory
from app.providers.base import ProviderAdapter
from app.providers.gemini_adapter import ENDPOINT_DEVELOPER
from app.services.model_service import FetchOutcome, ModelService
from app.services.station_service import StationService, to_response
from app.utils.http_client import HTTPClient

router = APIRouter(prefix="/api", tags=["stations"])

_station_service = StationService()
_model_service = ModelService()


def _deserialize_protocols(raw: str) -> list[str]:
    try:
        value = json.loads(raw)
    except (ValueError, TypeError):
        return []
    return value if isinstance(value, list) else []


# ---------- 中转站 CRUD ----------


@router.post(
    "/stations", response_model=RelayStationResponse, status_code=status.HTTP_201_CREATED
)
def create_station(
    payload: RelayStationCreate, db: Session = Depends(get_db)
) -> RelayStationResponse:
    """创建中转站（明文 Key 加密落库，不回显）。"""
    station = _station_service.create(db, payload)
    return to_response(station)


@router.get("/stations", response_model=list[RelayStationResponse])
def list_stations(db: Session = Depends(get_db)) -> list[RelayStationResponse]:
    """列出全部中转站。"""
    return [to_response(s) for s in _station_service.list(db)]


@router.get("/stations/{station_id}", response_model=RelayStationResponse)
def get_station(
    station_id: int, db: Session = Depends(get_db)
) -> RelayStationResponse:
    """按 id 取中转站。"""
    station = _station_service.get(db, station_id)
    if station is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "中转站不存在")
    return to_response(station)


@router.put("/stations/{station_id}", response_model=RelayStationResponse)
def update_station(
    station_id: int, payload: RelayStationUpdate, db: Session = Depends(get_db)
) -> RelayStationResponse:
    """更新中转站（api_key 省略则不改）。"""
    station = _station_service.update(db, station_id, payload)
    if station is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "中转站不存在")
    return to_response(station)


@router.delete("/stations/{station_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_station(station_id: int, db: Session = Depends(get_db)) -> None:
    """删除中转站。"""
    if not _station_service.delete(db, station_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "中转站不存在")


# ---------- 模型列表与拉取 ----------


@router.get("/stations/{station_id}/models", response_model=list[ModelResponse])
def list_models(
    station_id: int, db: Session = Depends(get_db)
) -> list[ModelResponse]:
    """列出某站点全部模型。"""
    if _station_service.get(db, station_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "中转站不存在")
    return [
        ModelResponse.model_validate(m, from_attributes=True)
        for m in _model_service.list_for_station(db, station_id)
    ]


@router.post("/stations/{station_id}/models", response_model=ModelResponse, status_code=201)
def add_model(
    station_id: int, payload: ModelCreate, db: Session = Depends(get_db)
) -> ModelResponse:
    """手动录入模型（拉取失败时回退路径）。"""
    if _station_service.get(db, station_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "中转站不存在")
    if payload.station_id != station_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "station_id 与路径不一致")
    model = _model_service.add_manual(db, payload)
    return ModelResponse.model_validate(model, from_attributes=True)


@router.post("/stations/{station_id}/models/fetch")
async def fetch_models(station_id: int, db: Session = Depends(get_db)) -> dict:
    """多协议并行拉取模型并落库（§10.6），返回逐协议拉取状态。"""
    station = _station_service.get(db, station_id)
    if station is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "中转站不存在")
    api_key = _station_service.decrypt_key(station)
    protocols = _deserialize_protocols(station.protocols)

    async with HTTPClient(timeout_seconds=settings.request_timeout_seconds) as client:
        adapters = _build_adapters(station, protocols, api_key, client)
        outcome = await _model_service.fetch_and_store(db, station_id, adapters)
    return _fetch_outcome_to_dict(outcome)


def _build_adapters(
    station: RelayStation, protocols: list[str], api_key: str, client: HTTPClient
) -> list[ProviderAdapter]:
    """据站点协议集合构建待拉取适配器；Gemini 走原生 Developer + 兼容层双路径（§10.6）。"""
    adapters: list[ProviderAdapter] = []
    for protocol in protocols:
        if protocol == "gemini":
            adapters.append(
                AdapterFactory.create(
                    "gemini",
                    "native",
                    base_url=station.base_url,
                    api_key=api_key,
                    http_client=client,
                    endpoint_style=ENDPOINT_DEVELOPER,
                )
            )
            adapters.append(
                AdapterFactory.create(
                    "gemini",
                    "openai_compat",
                    base_url=station.base_url,
                    api_key=api_key,
                    http_client=client,
                )
            )
        else:
            adapters.append(
                AdapterFactory.create(
                    protocol,
                    "native",
                    base_url=station.base_url,
                    api_key=api_key,
                    http_client=client,
                )
            )
    return adapters


def _fetch_outcome_to_dict(outcome: FetchOutcome) -> dict:
    """FetchOutcome → §10.6 响应结构。"""
    return {
        "fetched": [
            {
                "id": m.id,
                "protocol": m.protocol,
                "access_mode": m.access_mode,
                "model_name": m.model_name,
                "display_name": m.display_name,
            }
            for m in outcome.fetched
        ],
        "failures": [
            {"protocol": f.protocol, "access_mode": f.access_mode, "reason": f.reason}
            for f in outcome.failures
        ],
        "fallback_manual": outcome.fallback_manual,
    }

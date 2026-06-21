"""中转站 API 与模型拉取编排集成测试（Task 21，设计 §10.5/§10.6，安全规则 3.5）。

零网络：内存 SQLite（StaticPool 跨请求共享）+ 依赖覆盖；拉取编排以 FakeAdapter 注入。
"""
from collections.abc import AsyncIterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import stations as stations_api
from app.database.session import get_db
from app.main import app
from app.models.database import Base
from app.models.schemas import ModelCreate
from app.providers.base import AdapterRequest, ModelInfo, ProviderAdapter
from app.services.model_service import ModelService
from app.utils.errors import ErrorCategory, ProbeError


@pytest.fixture
def db_session():
    """内存 SQLite 会话工厂（StaticPool 保证多请求共享同一库）。"""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    yield factory
    engine.dispose()


@pytest.fixture
def api_client(db_session):
    """覆盖 get_db 依赖的 TestClient。"""

    def _override():
        db = db_session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override
    yield TestClient(app)
    app.dependency_overrides.clear()


def _create_payload(**overrides) -> dict:
    base = {
        "name": "测试站",
        "protocols": ["openai"],
        "base_url": "https://relay.example.com",
        "api_key": "sk-secret-plaintext",
    }
    base.update(overrides)
    return base


# ---------- 中转站 CRUD ----------

def test_create_station_never_returns_key(api_client):
    resp = api_client.post("/api/stations", json=_create_payload())
    assert resp.status_code == 201
    body = resp.json()
    assert body["has_api_key"] is True
    assert "api_key" not in body  # 严禁回显明文
    assert "api_key_encrypted" not in body  # 亦不回显密文
    assert body["protocols"] == ["openai"]


def test_create_dedupes_protocols(api_client):
    resp = api_client.post(
        "/api/stations",
        json=_create_payload(protocols=["openai", "openai", "anthropic"]),
    )
    assert resp.json()["protocols"] == ["openai", "anthropic"]


def test_list_and_get_station(api_client):
    created = api_client.post("/api/stations", json=_create_payload()).json()
    assert len(api_client.get("/api/stations").json()) == 1
    fetched = api_client.get(f"/api/stations/{created['id']}").json()
    assert fetched["name"] == "测试站"


def test_get_missing_station_404(api_client):
    assert api_client.get("/api/stations/999").status_code == 404


def test_update_station_partial(api_client):
    created = api_client.post("/api/stations", json=_create_payload()).json()
    resp = api_client.put(
        f"/api/stations/{created['id']}", json={"name": "改名站"}
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "改名站"
    assert resp.json()["protocols"] == ["openai"]  # 未改字段保留


def test_delete_station(api_client):
    created = api_client.post("/api/stations", json=_create_payload()).json()
    assert api_client.delete(f"/api/stations/{created['id']}").status_code == 204
    assert api_client.get(f"/api/stations/{created['id']}").status_code == 404


def test_create_rejects_empty_protocols(api_client):
    resp = api_client.post("/api/stations", json=_create_payload(protocols=[]))
    assert resp.status_code == 422  # Pydantic 校验


# ---------- 模型手动录入与列表 ----------

def test_add_and_list_manual_model(api_client):
    station = api_client.post("/api/stations", json=_create_payload()).json()
    sid = station["id"]
    payload = {
        "station_id": sid,
        "protocol": "openai",
        "model_name": "gpt-4o",
        "display_name": "GPT-4o",
    }
    resp = api_client.post(f"/api/stations/{sid}/models", json=payload)
    assert resp.status_code == 201
    assert resp.json()["source"] == "manual"
    models = api_client.get(f"/api/stations/{sid}/models").json()
    assert len(models) == 1
    assert models[0]["model_name"] == "gpt-4o"


def test_add_model_station_id_mismatch_400(api_client):
    station = api_client.post("/api/stations", json=_create_payload()).json()
    sid = station["id"]
    payload = {"station_id": sid + 99, "protocol": "openai", "model_name": "x"}
    resp = api_client.post(f"/api/stations/{sid}/models", json=payload)
    assert resp.status_code == 400


# ---------- 模型拉取编排（服务级，零网络） ----------


class _FetchAdapter(ProviderAdapter):
    """脚本化拉取适配器替身。"""

    def __init__(self, protocol, access_mode, models=None, error=None):
        self.protocol = protocol
        self.access_mode = access_mode
        self._models = models or []
        self._error = error

    async def chat(self, request: AdapterRequest):  # pragma: no cover
        raise NotImplementedError

    async def stream_chat(self, request: AdapterRequest) -> AsyncIterator:  # pragma: no cover
        raise NotImplementedError
        yield

    async def fetch_models(self):
        if self._error is not None:
            raise self._error
        return self._models


def _model_info(name, protocol="openai", access_mode="native"):
    return ModelInfo(model_name=name, protocol=protocol, access_mode=access_mode)


async def test_fetch_merges_and_dedupes(db_session):
    db = db_session()
    adapters = [
        _FetchAdapter("openai", "native", [_model_info("gpt-4o"), _model_info("gpt-4o")]),
        _FetchAdapter(
            "gemini", "openai_compat", [_model_info("gemini-2.5-pro", "gemini", "openai_compat")]
        ),
    ]
    outcome = await ModelService().fetch_and_store(db, station_id=1, adapters=adapters)
    names = sorted(m.model_name for m in outcome.fetched)
    assert names == ["gemini-2.5-pro", "gpt-4o"]  # 同名去重，跨协议保留
    assert outcome.fallback_manual is False


async def test_fetch_partial_failure_records_reason(db_session):
    db = db_session()
    adapters = [
        _FetchAdapter("openai", "native", [_model_info("gpt-4o")]),
        _FetchAdapter(
            "anthropic", "native", error=ProbeError(ErrorCategory.AUTH, "Key 失效")
        ),
    ]
    outcome = await ModelService().fetch_and_store(db, station_id=1, adapters=adapters)
    assert len(outcome.fetched) == 1
    assert len(outcome.failures) == 1
    assert outcome.failures[0].protocol == "anthropic"


async def test_fetch_all_fail_suggests_manual(db_session):
    db = db_session()
    adapters = [
        _FetchAdapter("openai", "native", error=ProbeError(ErrorCategory.CONNECTIVITY, "不可达"))
    ]
    outcome = await ModelService().fetch_and_store(db, station_id=1, adapters=adapters)
    assert outcome.fallback_manual is True


async def test_fetch_skips_existing_models(db_session):
    db = db_session()
    service = ModelService()
    service.add_manual(
        db, ModelCreate(station_id=1, protocol="openai", model_name="gpt-4o")
    )
    adapters = [_FetchAdapter("openai", "native", [_model_info("gpt-4o"), _model_info("gpt-4o-mini")])]
    outcome = await service.fetch_and_store(db, station_id=1, adapters=adapters)
    # gpt-4o 已存在 → 仅新增 gpt-4o-mini
    assert [m.model_name for m in outcome.fetched] == ["gpt-4o-mini"]


# ---------- 拉取 HTTP 端点（注入 fake 适配器） ----------

def test_fetch_endpoint_with_injected_adapters(api_client, db_session, monkeypatch):
    station = api_client.post(
        "/api/stations", json=_create_payload(protocols=["openai"])
    ).json()
    sid = station["id"]

    def fake_build(station_obj, protocols, api_key, client):
        return [_FetchAdapter("openai", "native", [_model_info("gpt-4o")])]

    monkeypatch.setattr(stations_api, "_build_adapters", fake_build)
    resp = api_client.post(f"/api/stations/{sid}/models/fetch")
    assert resp.status_code == 200
    body = resp.json()
    assert body["fetched"][0]["model_name"] == "gpt-4o"
    assert body["failures"] == []

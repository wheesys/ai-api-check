"""中转站服务（设计 §10.5，落实安全规则 3.5）。

承载中转站 CRUD 业务编排：明文 api_key 仅在此加密落库，响应一律经 `to_response`
转换——剥离密文、解析 protocols JSON、仅暴露 `has_api_key: bool`，绝不回显 Key。
"""
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.database import RelayStation
from app.models.schemas import (
    RelayStationCreate,
    RelayStationResponse,
    RelayStationUpdate,
)
from app.security.crypto import KeyManager, get_key_manager


def _serialize_protocols(protocols: list[str]) -> str:
    """协议集合 → JSON 文本（落库）。"""
    return json.dumps(protocols, ensure_ascii=False)


def _deserialize_protocols(raw: str) -> list[str]:
    """JSON 文本 → 协议集合（容错：非法时返回空列表）。"""
    try:
        value = json.loads(raw)
    except (ValueError, TypeError):
        return []
    return value if isinstance(value, list) else []


def to_response(station: RelayStation) -> RelayStationResponse:
    """ORM → 响应 DTO：解析 protocols、剥离密文、附 has_api_key（不回显 Key）。"""
    return RelayStationResponse(
        id=station.id,
        name=station.name,
        protocols=_deserialize_protocols(station.protocols),
        base_url=station.base_url,
        status=station.status,
        has_api_key=bool(station.api_key_encrypted),
        created_at=station.created_at,
        updated_at=station.updated_at,
    )


class StationService:
    """中转站 CRUD 服务。"""

    def __init__(self, key_manager: KeyManager | None = None) -> None:
        # 加密器可注入，便于测试隔离主密钥（SOLID-D）
        self._keys = key_manager or get_key_manager()

    def create(self, db: Session, payload: RelayStationCreate) -> RelayStation:
        """创建中转站：明文 Key 加密落库，protocols 序列化为 JSON。"""
        station = RelayStation(
            name=payload.name,
            protocols=_serialize_protocols(payload.protocols),
            base_url=payload.base_url,
            api_key_encrypted=self._keys.encrypt(payload.api_key),
            status=payload.status,
        )
        db.add(station)
        db.commit()
        db.refresh(station)
        return station

    def get(self, db: Session, station_id: int) -> RelayStation | None:
        """按 id 取中转站；不存在返回 None。"""
        return db.get(RelayStation, station_id)

    def list(self, db: Session) -> list[RelayStation]:
        """列出全部中转站（按 id 升序）。"""
        return list(db.scalars(select(RelayStation).order_by(RelayStation.id)))

    def update(
        self, db: Session, station_id: int, payload: RelayStationUpdate
    ) -> RelayStation | None:
        """部分更新；api_key 为 None 表示不修改（保留原密文）。"""
        station = db.get(RelayStation, station_id)
        if station is None:
            return None
        if payload.name is not None:
            station.name = payload.name
        if payload.protocols is not None:
            station.protocols = _serialize_protocols(payload.protocols)
        if payload.base_url is not None:
            station.base_url = payload.base_url
        if payload.status is not None:
            station.status = payload.status
        if payload.api_key is not None:
            station.api_key_encrypted = self._keys.encrypt(payload.api_key)
        db.commit()
        db.refresh(station)
        return station

    def delete(self, db: Session, station_id: int) -> bool:
        """删除中转站；不存在返回 False。"""
        station = db.get(RelayStation, station_id)
        if station is None:
            return False
        db.delete(station)
        db.commit()
        return True

    def decrypt_key(self, station: RelayStation) -> str:
        """解密 Key 供内部调用上游（绝不外发/打印；安全规则 3.5）。"""
        return self._keys.decrypt(station.api_key_encrypted)

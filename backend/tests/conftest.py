"""pytest 全局夹具。

核心原则（设计 v1.3 第 13 节）：单元测试零网络，所有上游交互以录制夹具打桩；
夹具与测试代码严禁包含真实 Key。
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine

from app.main import app


@pytest.fixture
def client():
    """FastAPI 测试客户端（基于 httpx，不触真实网络）。"""
    return TestClient(app)


@pytest.fixture
def test_db() -> Engine:
    """内存 SQLite 引擎：每个测试独立、即用即弃，零落盘零网络。"""
    return create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )

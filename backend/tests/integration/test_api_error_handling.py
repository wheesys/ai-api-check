"""全局异常处理集成测试（Task 24，设计 §11.2，安全规则 3.5）。

验证 ProbeError 按类别映射状态码、ValueError→400、未捕获异常→500 兜底，且响应均脱敏。
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.exceptions import register_exception_handlers
from app.utils.errors import ErrorCategory, ProbeError


@pytest.fixture
def error_app():
    """临时应用：挂载会抛各类异常的端点，验证全局处理器。"""
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/raise/probe/{category}")
    async def _raise_probe(category: str):
        raise ProbeError(
            ErrorCategory(category), "Authorization: Bearer sk-leak 失效"
        )

    @app.get("/raise/value")
    async def _raise_value():
        raise ValueError("非法参数 key=sk-abcdefghij1234567890")

    @app.get("/raise/unexpected")
    async def _raise_unexpected():
        raise RuntimeError("boom token=sk-zyxwvu9876543210abcd")

    # 关闭 server 异常重抛，使兜底处理器生效
    return TestClient(app, raise_server_exceptions=False)


def test_probe_auth_maps_401_and_sanitized(error_app):
    resp = error_app.get(f"/raise/probe/{ErrorCategory.AUTH.value}")
    assert resp.status_code == 401
    body = resp.json()
    assert body["error"] == "auth_error"
    assert "sk-leak" not in body["message"]  # 脱敏


def test_probe_rate_limit_maps_429(error_app):
    resp = error_app.get(f"/raise/probe/{ErrorCategory.RATE_LIMIT.value}")
    assert resp.status_code == 429


def test_probe_capability_maps_400(error_app):
    resp = error_app.get(f"/raise/probe/{ErrorCategory.CAPABILITY.value}")
    assert resp.status_code == 400


def test_probe_upstream_maps_502(error_app):
    resp = error_app.get(f"/raise/probe/{ErrorCategory.UPSTREAM_5XX.value}")
    assert resp.status_code == 502


def test_value_error_maps_400_sanitized(error_app):
    resp = error_app.get("/raise/value")
    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_request"
    assert "sk-abcdefghij1234567890" not in resp.json()["message"]


def test_unexpected_maps_500_sanitized(error_app):
    resp = error_app.get("/raise/unexpected")
    assert resp.status_code == 500
    body = resp.json()
    assert body["error"] == "internal_error"
    assert "sk-zyxwvu9876543210abcd" not in body["message"]  # 不泄露


def test_health_still_ok(client):
    assert client.get("/health").json() == {"status": "ok"}

"""冒烟测试：验证 FastAPI 应用可启动且健康检查可用。"""


def test_health_check(client):
    """/health 返回 200 与 status=ok。"""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

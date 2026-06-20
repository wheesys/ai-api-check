"""Provider 适配器框架单元测试：抽象约束 + 工厂注册/创建。

零网络；用最小桩适配器验证框架契约，不触真实上游。
"""
import pytest

from app.providers.base import (
    AdapterRequest,
    AdapterResponse,
    ChatMessage,
    ModelInfo,
    ProviderAdapter,
)
from app.providers.adapter_factory import AdapterFactory


def test_provider_adapter_is_abstract():
    """抽象基类不可直接实例化。"""
    with pytest.raises(TypeError):
        ProviderAdapter()  # type: ignore[abstract]


def test_adapter_request_defaults():
    """请求 DTO 默认值符合"低成本探测"约定。"""
    request = AdapterRequest(
        model_name="demo", messages=[ChatMessage(role="user", content="hi")]
    )
    assert request.max_tokens == 16
    assert request.temperature == 0.0
    assert request.stream is False
    assert request.extra == {}


def test_factory_register_and_create():
    """注册桩适配器后可经工厂按组合创建。"""

    @AdapterFactory.register("unit_test_proto", "native")
    class _StubAdapter(ProviderAdapter):
        protocol = "unit_test_proto"
        access_mode = "native"

        def __init__(self, base_url: str = "", api_key: str = "") -> None:
            self.base_url = base_url
            self.api_key = api_key

        async def chat(self, request: AdapterRequest) -> AdapterResponse:
            return AdapterResponse(http_status=200, success=True, content="ok")

        async def stream_chat(self, request: AdapterRequest):
            yield  # pragma: no cover - 桩实现不被调用

        async def fetch_models(self) -> list[ModelInfo]:
            return []

    adapter = AdapterFactory.create(
        "unit_test_proto", "native", base_url="http://x", api_key="k"
    )
    assert isinstance(adapter, _StubAdapter)
    assert adapter.base_url == "http://x"
    assert ("unit_test_proto", "native") in AdapterFactory.registered_keys()


def test_factory_unknown_combo_raises():
    """未注册组合应抛 ValueError。"""
    with pytest.raises(ValueError, match="未注册的适配器组合"):
        AdapterFactory.create("does_not_exist", "native")

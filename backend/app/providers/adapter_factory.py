"""适配器工厂：按 (protocol, access_mode) 注册与创建适配器（SOLID-O）。

适配器实现模块（Task 6-8）通过 @AdapterFactory.register(...) 装饰器自注册；
引擎/服务层只依赖本工厂与 ProviderAdapter 抽象，不感知具体实现。
"""
from app.providers.base import ProviderAdapter

# (protocol, access_mode) -> 适配器类
AdapterKey = tuple[str, str]


class AdapterFactory:
    """适配器注册表与创建入口。"""

    _registry: dict[AdapterKey, type[ProviderAdapter]] = {}

    @classmethod
    def register(cls, protocol: str, access_mode: str):
        """类装饰器：登记某 (protocol, access_mode) 组合的适配器实现。"""

        def decorator(adapter_cls: type[ProviderAdapter]) -> type[ProviderAdapter]:
            cls._registry[(protocol, access_mode)] = adapter_cls
            return adapter_cls

        return decorator

    @classmethod
    def create(
        cls, protocol: str, access_mode: str, **kwargs
    ) -> ProviderAdapter:
        """按组合创建适配器实例；未注册组合抛 ValueError。"""
        key = (protocol, access_mode)
        adapter_cls = cls._registry.get(key)
        if adapter_cls is None:
            raise ValueError(
                f"未注册的适配器组合：protocol={protocol!r}, access_mode={access_mode!r}"
            )
        return adapter_cls(**kwargs)

    @classmethod
    def registered_keys(cls) -> list[AdapterKey]:
        """返回已注册的全部组合（便于自检/测试）。"""
        return list(cls._registry)

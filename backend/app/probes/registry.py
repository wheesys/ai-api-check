"""探针注册表（注册表模式，设计 §8.2/§8.8）。

探针实现模块通过 @ProbeRegistry.register 自注册；引擎按 key 选取/按 category 分组
聚合，只依赖 Probe 抽象与本注册表，不感知具体探针实现（SOLID-O/-D）。
"""
from app.probes.base import Probe


class ProbeRegistry:
    """探针注册表与创建入口，按 strategy_key 唯一登记。"""

    _registry: dict[str, type[Probe]] = {}

    @classmethod
    def register(cls, probe_cls: type[Probe]) -> type[Probe]:
        """类装饰器：以探针自身 key 登记；key 重复即编程错误，抛 ValueError。"""
        key = probe_cls.key
        if not key:
            raise ValueError(f"探针 {probe_cls.__name__} 未声明 key")
        if key in cls._registry:
            raise ValueError(f"探针 key 重复注册：{key!r}")
        cls._registry[key] = probe_cls
        return probe_cls

    @classmethod
    def get(cls, key: str) -> type[Probe]:
        """按 key 取探针类；未注册抛 KeyError。"""
        probe_cls = cls._registry.get(key)
        if probe_cls is None:
            raise KeyError(f"未注册的探针 key：{key!r}")
        return probe_cls

    @classmethod
    def create(cls, key: str, **kwargs) -> Probe:
        """按 key 创建探针实例。"""
        return cls.get(key)(**kwargs)

    @classmethod
    def all_keys(cls) -> list[str]:
        """返回已注册的全部 key（便于自检/枚举可选策略）。"""
        return list(cls._registry)

    @classmethod
    def by_category(cls) -> dict[str, list[type[Probe]]]:
        """按 category 分组返回探针类，供引擎分维度聚合评分。"""
        grouped: dict[str, list[type[Probe]]] = {}
        for probe_cls in cls._registry.values():
            grouped.setdefault(probe_cls.category, []).append(probe_cls)
        return grouped

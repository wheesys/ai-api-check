"""Provider 适配器包。

导入即触发各适配器经 @AdapterFactory.register 自注册（注册表模式），
使引擎/服务层 `import app.providers` 后即可经工厂创建任意已实现的协议组合，
无需感知具体实现模块（SOLID-D/-O）。
"""
from app.providers import (  # noqa: F401  仅为触发装饰器注册副作用
    anthropic_adapter,
    gemini_adapter,
    openai_adapter,
)

__all__ = ["anthropic_adapter", "gemini_adapter", "openai_adapter"]

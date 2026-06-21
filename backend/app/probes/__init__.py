"""探针包。

导入即触发各探针经 @ProbeRegistry.register 自注册（注册表模式），
使引擎 import app.probes 后即可按 key 选取/按 category 分组聚合，
无需感知具体探针实现模块（SOLID-O/-D）。
"""
from app.probes import (  # noqa: F401  触发注册副作用
    billing,
    capability,
    connectivity,
    performance,
)

__all__ = ["billing", "capability", "connectivity", "performance"]

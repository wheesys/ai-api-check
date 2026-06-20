"""建表初始化。

阶段一采用 `Base.metadata.create_all` 直接建表（无独立迁移工具，YAGNI）；
导入 database 模块确保全部 ORM 模型已注册到 `Base.metadata`。
"""
from app.database.session import engine
from app.models.database import Base


def init_db() -> None:
    """根据 ORM 元数据创建所有尚不存在的表（幂等）。"""
    Base.metadata.create_all(bind=engine)

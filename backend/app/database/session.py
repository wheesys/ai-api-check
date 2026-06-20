"""数据库引擎与会话工厂。

SQLite 单文件部署，`check_same_thread=False` 允许 FastAPI 多线程依赖复用连接；
若切换至 PostgreSQL/MySQL，仅 `database_url` 变化，无需改动本模块其余逻辑（SOLID-D）。
"""
from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings

# 仅 SQLite 需要 check_same_thread；其他数据库传空 connect_args，保持驱动默认行为
_connect_args = (
    {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
)

engine = create_engine(
    settings.database_url,
    connect_args=_connect_args,
    pool_pre_ping=True,  # 取连接前探活，避免使用已被数据库回收的失效连接
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db() -> Generator[Session, None, None]:
    """FastAPI 依赖：产出一个请求级会话，请求结束后确保关闭。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

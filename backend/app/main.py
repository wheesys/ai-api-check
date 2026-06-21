"""FastAPI 应用入口。

启动时（lifespan）初始化数据库表结构，注册 CORS 与路由。
采用 lifespan 而非已弃用的 on_event，规避 FastAPI 弃用告警。
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import stations, tasks
from app.config import settings
from app.database.migrations import init_db


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """应用生命周期：启动时建表，关闭时无额外清理。"""
    init_db()
    yield


app = FastAPI(
    title="中转站模型质量检测平台",
    description="检测中转站不同模型的连通性、性能、计费一致性、能力与来源真实性",
    version="0.1.0",
    debug=settings.debug,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", tags=["system"])
async def health_check():
    """健康检查端点，用于探活。"""
    return {"status": "ok"}


app.include_router(stations.router)
app.include_router(tasks.router)

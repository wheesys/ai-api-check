"""API 路由聚合（设计 §10）。

将各资源路由（中转站/模型、任务、报告）聚合为单一入口，main 仅挂载本聚合器，
新增资源路由只改本文件，不动应用装配（SOLID-O）。
"""
from fastapi import APIRouter

from app.api import reports, stations, tasks

api_router = APIRouter()
api_router.include_router(stations.router)
api_router.include_router(tasks.router)
api_router.include_router(reports.router)

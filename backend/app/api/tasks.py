"""检测任务 API（设计 §10：任务生命周期 + SSE 进度流）。

端点：创建任务（异步触发执行）、列表/详情、取消、SSE 事件流。任务执行经全局调度器
排队（任务间并发上限），进度经 EventBroker 推送。
"""
import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.database.session import SessionLocal, get_db
from app.engine.events import broker
from app.engine.scheduler import Scheduler
from app.models.schemas import DetectionTaskCreate, DetectionTaskResponse
from app.services.task_service import TaskService

router = APIRouter(prefix="/api/tasks", tags=["tasks"])

_task_service = TaskService()
# 进程级全局调度器（任务间并发上限来自配置）
_scheduler = Scheduler()


async def _run_task_in_background(task_id: int) -> None:
    """后台执行：用独立会话，避免与请求级会话生命周期纠缠。"""
    db = SessionLocal()
    try:
        await _task_service.run(db, task_id)
    finally:
        db.close()


@router.post("", response_model=DetectionTaskResponse, status_code=status.HTTP_201_CREATED)
def create_task(
    payload: DetectionTaskCreate, db: Session = Depends(get_db)
) -> DetectionTaskResponse:
    """创建检测任务并经调度器异步触发执行。"""
    try:
        task = _task_service.create(db, payload)
    except ValueError as error:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(error)) from error
    _scheduler.submit(str(task.id), lambda: _run_task_in_background(task.id))
    return DetectionTaskResponse.model_validate(task, from_attributes=True)


@router.get("", response_model=list[DetectionTaskResponse])
def list_tasks(db: Session = Depends(get_db)) -> list[DetectionTaskResponse]:
    """列出全部任务（按 id 倒序）。"""
    return [
        DetectionTaskResponse.model_validate(t, from_attributes=True)
        for t in _task_service.list(db)
    ]


@router.get("/{task_id}", response_model=DetectionTaskResponse)
def get_task(task_id: int, db: Session = Depends(get_db)) -> DetectionTaskResponse:
    """查询任务状态与进度。"""
    task = _task_service.get(db, task_id)
    if task is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "任务不存在")
    return DetectionTaskResponse.model_validate(task, from_attributes=True)


@router.post("/{task_id}/cancel", status_code=status.HTTP_202_ACCEPTED)
def cancel_task(task_id: int, db: Session = Depends(get_db)) -> dict:
    """请求取消任务（执行器在探针边界感知）。"""
    task = _task_service.get(db, task_id)
    if task is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "任务不存在")
    _task_service.request_cancel(task_id)
    return {"task_id": task_id, "cancel_requested": True}


@router.get("/{task_id}/events")
async def task_events(task_id: int) -> StreamingResponse:
    """SSE 进度流：逐条推送探针/任务事件，终态后结束。"""

    async def _event_stream() -> AsyncIterator[bytes]:
        async for event in broker.subscribe(task_id):
            payload = json.dumps(event.data, ensure_ascii=False, default=str)
            yield f"event: {event.type}\ndata: {payload}\n\n".encode()

    return StreamingResponse(
        _event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

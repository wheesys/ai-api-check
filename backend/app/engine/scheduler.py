"""全局任务调度器（设计 §8.1 两级并发之第二级：任务间）。

维护全局任务池：同时运行的检测任务数受 `max_concurrent_tasks` 约束（本地场景建议
2~3），超出则排队，避免多任务并发耗尽中转站额度或集中触发限流。

职责边界（SOLID-S）：调度器只管"任务间"槽位分配与生命周期跟踪；"任务内"的探针并发
由 `TaskExecutor`（Task 15）封装，经提交的协程工厂调用，调度器不感知其内部细节。
"""
import asyncio
from collections.abc import Awaitable, Callable
from enum import Enum

# 任务工厂：被调用时才创建协程（确保排队期间不提前启动）
TaskFactory = Callable[[], Awaitable[object]]


class SchedulerState(str, Enum):
    """单个被调度任务的状态。"""

    QUEUED = "queued"  # 已提交、等待槽位
    RUNNING = "running"  # 占用槽位执行中
    DONE = "done"  # 正常完成
    FAILED = "failed"  # 抛异常结束
    CANCELED = "canceled"  # 被取消


class Scheduler:
    """异步任务调度器：以全局信号量限制并发任务数，超出排队。"""

    def __init__(self, max_concurrent_tasks: int = 2) -> None:
        self._max = max(1, max_concurrent_tasks)
        self._semaphore = asyncio.Semaphore(self._max)
        self._tasks: dict[str, asyncio.Task] = {}
        self._states: dict[str, SchedulerState] = {}
        self._running = 0

    def submit(self, task_id: str, factory: TaskFactory) -> asyncio.Task:
        """提交任务：立即排队，待槽位空出后由工厂创建协程并执行。

        task_id 唯一；重复提交抛 ValueError。返回底层 asyncio.Task 供 await/cancel。
        """
        if task_id in self._tasks:
            raise ValueError(f"任务已提交：{task_id!r}")
        self._states[task_id] = SchedulerState.QUEUED
        task = asyncio.create_task(self._run(task_id, factory))
        self._tasks[task_id] = task
        return task

    async def _run(self, task_id: str, factory: TaskFactory) -> object:
        """信号量门控的任务体：占槽→运行→记终态。"""
        async with self._semaphore:
            self._states[task_id] = SchedulerState.RUNNING
            self._running += 1
            try:
                result = await factory()
                self._states[task_id] = SchedulerState.DONE
                return result
            except asyncio.CancelledError:
                self._states[task_id] = SchedulerState.CANCELED
                raise
            except Exception:
                self._states[task_id] = SchedulerState.FAILED
                raise
            finally:
                self._running -= 1

    def state(self, task_id: str) -> SchedulerState | None:
        """查询任务状态；未知任务返回 None。"""
        return self._states.get(task_id)

    def cancel(self, task_id: str) -> bool:
        """请求取消任务；任务不存在或已结束返回 False。"""
        task = self._tasks.get(task_id)
        if task is None or task.done():
            return False
        return task.cancel()

    async def join(self, task_id: str) -> object:
        """等待指定任务结束并返回其结果（异常会向上抛出）。"""
        task = self._tasks.get(task_id)
        if task is None:
            raise KeyError(f"未知任务：{task_id!r}")
        return await task

    async def wait_all(self) -> None:
        """等待当前所有已提交任务结束（异常被吞，仅用于优雅收尾）。"""
        if self._tasks:
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)

    @property
    def running_count(self) -> int:
        """当前占用槽位（运行中）的任务数。"""
        return self._running

    @property
    def max_concurrent(self) -> int:
        return self._max

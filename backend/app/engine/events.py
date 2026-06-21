"""SSE 事件代理（任务进度实时推送，设计 §8.1 第 4 步 / §10）。

每个任务一条内存通道（asyncio.Queue）：执行器经 `publish` 投递事件，SSE 端点经
`subscribe` 逐条消费直至终态。本地单进程场景足够；多实例部署可替换为 Redis 等
（仅需替换本类，调用方不变，SOLID-D）。
"""
import asyncio
from collections.abc import AsyncIterator

from app.engine.executor import SSEEvent

# 终态事件类型：消费到任一即结束订阅
_TERMINAL_TYPES = frozenset({"task.completed", "task.failed", "task.canceled"})


class EventBroker:
    """按 task_id 分发 SSE 事件的内存代理。"""

    def __init__(self) -> None:
        self._channels: dict[int, asyncio.Queue] = {}

    def _channel(self, task_id: int) -> asyncio.Queue:
        return self._channels.setdefault(task_id, asyncio.Queue())

    def make_sink(self, task_id: int):
        """返回绑定到某任务的事件接收器（供执行器 emit）。"""

        async def _sink(event: SSEEvent) -> None:
            await self._channel(task_id).put(event)

        return _sink

    async def subscribe(self, task_id: int) -> AsyncIterator[SSEEvent]:
        """订阅某任务事件流，消费到终态事件后结束并清理通道。"""
        queue = self._channel(task_id)
        try:
            while True:
                event = await queue.get()
                yield event
                if event.type in _TERMINAL_TYPES:
                    break
        finally:
            self._channels.pop(task_id, None)

    def has_channel(self, task_id: int) -> bool:
        return task_id in self._channels


# 进程级单例（本地单进程部署）
broker = EventBroker()

"""全局任务调度器集成测试（Task 16，设计 §8.1 任务间并发）。

覆盖：全局并发上限、超额排队、状态流转、失败/取消传播、join 返回结果、重复提交拒绝。
零网络：以可控异步工厂模拟任务体。
"""
import asyncio

import pytest

from app.engine.scheduler import Scheduler, SchedulerState


# ---------- 全局并发上限 ----------

async def test_global_concurrency_capped():
    active = {"now": 0, "max": 0}

    async def body():
        active["now"] += 1
        active["max"] = max(active["max"], active["now"])
        await asyncio.sleep(0.02)
        active["now"] -= 1
        return "ok"

    scheduler = Scheduler(max_concurrent_tasks=2)
    for i in range(5):
        scheduler.submit(f"t{i}", body)
    await scheduler.wait_all()
    assert active["max"] <= 2  # 任意时刻并发不超过 2


async def test_excess_tasks_queue_then_run():
    gate = asyncio.Event()
    started: list[str] = []

    def make(name):
        async def body():
            started.append(name)
            await gate.wait()
            return name

        return body

    scheduler = Scheduler(max_concurrent_tasks=1)
    scheduler.submit("a", make("a"))
    scheduler.submit("b", make("b"))
    await asyncio.sleep(0.01)  # 让 a 占槽启动
    assert started == ["a"]  # b 仍在排队
    assert scheduler.state("b") is SchedulerState.QUEUED
    gate.set()
    await scheduler.wait_all()
    assert started == ["a", "b"]


# ---------- 状态流转 ----------

async def test_state_transitions_done():
    async def body():
        return 42

    scheduler = Scheduler()
    scheduler.submit("x", body)
    result = await scheduler.join("x")
    assert result == 42
    assert scheduler.state("x") is SchedulerState.DONE


async def test_failure_state_and_propagation():
    async def body():
        raise RuntimeError("boom")

    scheduler = Scheduler()
    scheduler.submit("x", body)
    with pytest.raises(RuntimeError):
        await scheduler.join("x")
    assert scheduler.state("x") is SchedulerState.FAILED


async def test_cancel_sets_canceled_state():
    async def body():
        await asyncio.sleep(1.0)

    scheduler = Scheduler()
    scheduler.submit("x", body)
    await asyncio.sleep(0.01)
    assert scheduler.cancel("x") is True
    with pytest.raises(asyncio.CancelledError):
        await scheduler.join("x")
    assert scheduler.state("x") is SchedulerState.CANCELED


# ---------- 边界 ----------

async def test_duplicate_submit_rejected():
    async def body():
        return 1

    scheduler = Scheduler()
    scheduler.submit("dup", body)
    with pytest.raises(ValueError):
        scheduler.submit("dup", body)
    await scheduler.wait_all()


async def test_join_unknown_task_raises():
    scheduler = Scheduler()
    with pytest.raises(KeyError):
        await scheduler.join("nope")


async def test_cancel_unknown_returns_false():
    scheduler = Scheduler()
    assert scheduler.cancel("nope") is False


def test_max_concurrent_floor():
    assert Scheduler(max_concurrent_tasks=0).max_concurrent == 1  # 下限 1

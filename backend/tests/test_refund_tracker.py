"""refund_tracker 单元 + 装饰器集成测试

覆盖:
- register / try_refund 基本流程(成功退、未注册返 0、二次调用返 0)
- 多 tab 并发幂等(threads.gather 模拟,断言只退一次)
- TTL 过期不退
- require_credits 装饰器集成:result 含 task_id → 自动 register;
  无 task_id → 不 register(同步任务走原 except 路径,不会进 refund_tracker)
"""
import asyncio
import threading
import time

import pytest

from app.services import refund_tracker
from app.services.billing import get_user_credits
from app.services.auth import create_user, set_user_credits


@pytest.fixture(autouse=True)
def _reset_tracker():
    refund_tracker._clear_for_test()
    yield
    refund_tracker._clear_for_test()


def _mk_user(email: str, credits: int = 100) -> dict:
    user = create_user(email=email, password="secret123", name=email.split("@")[0])
    assert user is not None
    set_user_credits(user["id"], credits)
    return user


# === 基本流程 ===

def test_register_then_refund_succeeds():
    user = _mk_user("rt-basic@example.com", 50)
    refund_tracker.register("fal_task_1", user["id"], 10)

    refunded = refund_tracker.try_refund("fal_task_1")
    assert refunded == 10
    assert get_user_credits(user["id"]) == 60


def test_unregistered_task_returns_zero_no_refund():
    user = _mk_user("rt-unreg@example.com", 50)
    refunded = refund_tracker.try_refund("never_seen_task")
    assert refunded == 0
    assert get_user_credits(user["id"]) == 50  # 余额不变


def test_double_refund_returns_zero_second_time():
    """同 task_id 两次 try_refund:第一次退,第二次 noop(防双退)"""
    user = _mk_user("rt-double@example.com", 50)
    refund_tracker.register("fal_task_double", user["id"], 15)

    first = refund_tracker.try_refund("fal_task_double")
    second = refund_tracker.try_refund("fal_task_double")
    assert first == 15
    assert second == 0
    assert get_user_credits(user["id"]) == 65  # 只退一次


def test_register_invalid_args_noop():
    """参数无效时静默(空 task_id / 空 user_id / cost <= 0)"""
    refund_tracker.register("", "u1", 10)  # 空 task_id
    refund_tracker.register("t1", "", 10)  # 空 user_id
    refund_tracker.register("t2", "u1", 0)  # cost <= 0
    refund_tracker.register("t3", "u1", -5)
    # 都没真注册 → try_refund 返 0
    for tid in ("t1", "t2", "t3"):
        assert refund_tracker.try_refund(tid) == 0


def test_peek_does_not_consume():
    user = _mk_user("rt-peek@example.com", 50)
    refund_tracker.register("fal_peek", user["id"], 8)

    rec = refund_tracker.peek("fal_peek")
    assert rec == (user["id"], 8)
    # peek 后余额不变,后续 try_refund 仍能退
    assert get_user_credits(user["id"]) == 50
    assert refund_tracker.try_refund("fal_peek") == 8
    assert get_user_credits(user["id"]) == 58


# === 并发幂等(核心) ===

def test_concurrent_refund_only_once():
    """10 个线程同时 try_refund 同一 task_id:总退款 = cost,余额涨一份"""
    user = _mk_user("rt-race@example.com", 100)
    refund_tracker.register("fal_race", user["id"], 20)

    results: list[int] = []
    barrier = threading.Barrier(10)

    def worker():
        barrier.wait()  # 同时起跑
        results.append(refund_tracker.try_refund("fal_race"))

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sum(results) == 20, f"双退:{results}"
    assert results.count(20) == 1, f"恰好一个线程退到 20"
    assert results.count(0) == 9, f"其他都是 0"
    assert get_user_credits(user["id"]) == 120


# === TTL ===

def test_ttl_expired_returns_zero(monkeypatch):
    user = _mk_user("rt-ttl@example.com", 50)
    monkeypatch.setattr(refund_tracker, "_TTL_SECONDS", 1)
    refund_tracker.register("fal_ttl", user["id"], 10)

    time.sleep(1.1)
    refunded = refund_tracker.try_refund("fal_ttl")
    assert refunded == 0
    assert get_user_credits(user["id"]) == 50  # TTL 过期不退


# === 装饰器集成 ===

def test_decorator_registers_on_async_task():
    """require_credits 装饰器:业务返 dict 含 task_id → 自动 register 进 refund_tracker"""
    from app.services.decorators import require_credits

    user = _mk_user("rt-deco-async@example.com", 100)

    @require_credits("video/image-to-video")  # cost=10
    async def handler(**kw):
        return {"task_id": "fal_from_decorator", "status": "pending"}

    asyncio.run(handler(current_user=user))
    # 扣费成功 → 余额减 10
    assert get_user_credits(user["id"]) == 90
    # refund_tracker 已注册
    rec = refund_tracker.peek("fal_from_decorator")
    assert rec == (user["id"], 10)
    # 失败 polling 触发 try_refund → 余额恢复
    refunded = refund_tracker.try_refund("fal_from_decorator")
    assert refunded == 10
    assert get_user_credits(user["id"]) == 100


def test_decorator_no_register_when_no_task_id():
    """同步任务(返 dict 无 task_id)→ 不 register;失败靠 except 路径退"""
    from app.services.decorators import require_credits

    user = _mk_user("rt-deco-sync@example.com", 100)

    @require_credits("image/style")  # cost=2
    async def handler(**kw):
        return {"url": "data:image/png;base64,..."}  # 同步成功,无 task_id

    asyncio.run(handler(current_user=user))
    # 扣费成功
    assert get_user_credits(user["id"]) == 98
    # 但没 register(同步任务无需 refund_tracker)
    assert refund_tracker.peek("any_task_id") is None

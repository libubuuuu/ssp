"""异步任务失败退款追踪器 — 防扣费后异步失败漏退 + 多 tab 双退

背景:
- `require_credits` 装饰器在任务**提交**时扣费,业务函数返回后写 generation_history
- 但 image-to-video / video/replace / video/clone 是**异步**任务,提交成功 ≠ 生成成功
- FAL 真实生成失败要靠后续 polling(HTTP /api/tasks/status 或 WS _poll_fal_task)发现
- 历史代码 tasks.py 的退款逻辑死的:SELECT generation_history WHERE id = fal_task_id —
  但 generation_history 用 uuid4 作 id,跟 FAL task_id 不匹配,永远找不到 row → 永远不退

方案:
- 装饰器拿到 fal task_id 时 register(task_id, user_id, cost)
- polling 检测到 failed 时 try_refund(task_id) — 原子 pop + add_credits
- pop 自身是 dict 原子操作,确保**只退一次**;多 tab 并发 polling、HTTP+WS 双轨触发都安全

限制(已记入 TODO):
- 进程级 dict,backend 重启 → 退款记录丢失。失败任务不退,需人工补
- multi-worker 时每个 worker 各自一份,但当前 uvicorn 单 worker
- 30 分钟 TTL 覆盖 FAL 最长任务时长
"""
import threading
import time
from typing import Optional

_TTL_SECONDS = 30 * 60

# {task_id: (user_id, cost, registered_at_epoch)}
_pending: dict[str, tuple[str, int, float]] = {}
_lock = threading.Lock()


def _gc_locked() -> None:
    """删过期项,调用方必须持有 _lock。"""
    now = time.time()
    expired = [tid for tid, (_, _, ts) in _pending.items() if now - ts > _TTL_SECONDS]
    for tid in expired:
        _pending.pop(tid, None)


def register(task_id: str, user_id: str, cost: int) -> None:
    """装饰器扣费成功 + 业务返回 fal task_id 时调,登记退款备案。"""
    if not task_id or not user_id or cost <= 0:
        return
    with _lock:
        _pending[task_id] = (str(user_id), int(cost), time.time())
        if len(_pending) % 50 == 0:
            _gc_locked()


def try_refund(task_id: str) -> int:
    """polling 检测到 failed 时调。原子 pop + add_credits,返回实退积分。

    返回 0 表示:已退过 / 未注册 / TTL 过期 / 任务无积分。
    多次调用幂等(第二次起 pop 拿不到 entry)。

    pop 是 dict 原子操作,确保**只退一次**。即使 add_credits 抛异常,
    pop 已经把 entry 拿走,后续重试不会找到 entry → 不会双退(代价:那次失败的退款丢了)。
    """
    if not task_id:
        return 0
    with _lock:
        rec = _pending.pop(task_id, None)
    if rec is None:
        return 0
    user_id, cost, ts = rec
    if time.time() - ts > _TTL_SECONDS:
        return 0
    from .billing import add_credits
    add_credits(user_id, cost)
    return cost


def peek(task_id: str) -> Optional[tuple[str, int]]:
    """读但不退,测试 / 调试用。返回 (user_id, cost) 或 None。"""
    with _lock:
        rec = _pending.get(task_id)
    if rec is None:
        return None
    user_id, cost, ts = rec
    if time.time() - ts > _TTL_SECONDS:
        return None
    return user_id, cost


def _clear_for_test() -> None:
    """仅测试用,重置注册表。"""
    with _lock:
        _pending.clear()

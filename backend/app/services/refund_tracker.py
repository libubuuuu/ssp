"""异步任务失败退款追踪器 — SQLite 持久化版

背景:
- `require_credits` 装饰器在任务**提交**时扣费,业务函数返回后写 generation_history
- 但 image-to-video / video/replace / video/clone 是**异步**任务,提交成功 ≠ 生成成功
- FAL 真实生成失败要靠后续 polling(HTTP /api/tasks/status 或 WS _poll_fal_task)发现
- 旧代码 tasks.py 退款逻辑死的:SELECT generation_history WHERE id = fal_task_id —
  但 generation_history 用 uuid4 作 id,跟 FAL task_id 不匹配,永远找不到 row → 永远不退

方案:
- 装饰器拿到 fal task_id 时 register(task_id, user_id, cost) → INSERT pending_refunds
- polling 检测到 failed 时 try_refund(task_id):
    UPDATE pending_refunds SET refunded=1 WHERE task_id=? AND refunded=0
    rowcount==1(SQL 原子,UPDATE WHERE 是 atomic) → SELECT user_id, cost + add_credits
    否则返 0(已退过 / 未注册 / TTL 过期都返 0)
- 多 tab 并发 polling、HTTP+WS 双轨触发都靠 SQL UPDATE 的原子性保证只退一次

跟 v1 进程内存版差别:
- ✅ backend 重启不丢退款记录(SQLite 持久化)
- ✅ multi-worker 安全(SQL 层原子)
- 限制:仍 30 分钟 TTL,过期 entries 不退(不假设 fal 任务超 30 分钟还在生成)
"""
import time
from typing import Optional

_TTL_SECONDS = 30 * 60
_GC_TRIGGER_PROB = 50  # 每 50 次 register 触发一次惰性 GC


def register(task_id: str, user_id: str, cost: int) -> None:
    """装饰器扣费成功 + 业务返回 fal task_id 时调,登记退款备案。

    INSERT OR IGNORE 防同 task_id 重复 register(理论上 fal task_id 唯一,
    但保险:同一 task_id 撞了不会破坏既存记录,也不会双计费)。
    """
    if not task_id or not user_id or cost <= 0:
        return
    from ..database import get_db
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT OR IGNORE INTO pending_refunds
                (task_id, user_id, cost, registered_at, refunded)
            VALUES (?, ?, ?, ?, 0)
            """,
            (task_id, str(user_id), int(cost), time.time()),
        )
        # 惰性 GC:删 TTL 过期的 entries,触发概率 1/50 写入
        if cursor.lastrowid and cursor.lastrowid % _GC_TRIGGER_PROB == 0:
            cursor.execute(
                "DELETE FROM pending_refunds WHERE registered_at < ?",
                (time.time() - _TTL_SECONDS,),
            )
        conn.commit()


def try_refund(task_id: str) -> int:
    """polling 检测到 failed 时调。SQL 层原子 UPDATE 保证只退一次。

    返回实退积分,0 表示:已退过 / 未注册 / TTL 过期。
    多次调用幂等(第二次 UPDATE rowcount=0)。

    步骤:
    1. UPDATE pending_refunds SET refunded=1 WHERE task_id=? AND refunded=0 AND registered_at>=?
       SQL 层原子 — 两个并发 UPDATE 只有一个 rowcount==1
    2. 如果 rowcount==1:SELECT 拿 user_id+cost,add_credits 退款
    3. 否则返 0
    """
    if not task_id:
        return 0
    from ..database import get_db
    cutoff = time.time() - _TTL_SECONDS
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE pending_refunds
               SET refunded = 1
             WHERE task_id = ? AND refunded = 0 AND registered_at >= ?
            """,
            (task_id, cutoff),
        )
        if cursor.rowcount != 1:
            conn.commit()
            return 0
        cursor.execute(
            "SELECT user_id, cost FROM pending_refunds WHERE task_id = ?",
            (task_id,),
        )
        row = cursor.fetchone()
        conn.commit()
    if not row:
        return 0
    user_id, cost = row[0], int(row[1])
    from .billing import add_credits
    add_credits(user_id, cost)
    return cost


def peek(task_id: str) -> Optional[tuple[str, int]]:
    """读但不退,测试 / 调试用。返回 (user_id, cost) 或 None。
    refunded=1 / TTL 过期 都返 None(已不可退)。
    """
    if not task_id:
        return None
    from ..database import get_db
    cutoff = time.time() - _TTL_SECONDS
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT user_id, cost FROM pending_refunds
             WHERE task_id = ? AND refunded = 0 AND registered_at >= ?
            """,
            (task_id, cutoff),
        )
        row = cursor.fetchone()
    if not row:
        return None
    return row[0], int(row[1])


def _clear_for_test() -> None:
    """仅测试用,重置表。"""
    from ..database import get_db
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM pending_refunds")
        conn.commit()

"""充值订单 GC：超时 pending 订单自动取消

超过 EXPIRE_MINUTES 分钟仍是 pending 的 credit_orders → 标 cancelled。
在 lifespan startup 跑一次，之后每 INTERVAL_SECONDS 秒跑一次。
"""
import asyncio

EXPIRE_MINUTES = 30
INTERVAL_SECONDS = 600  # 10 分钟


def cancel_expired_pending_orders() -> int:
    """同步：UPDATE credit_orders status='cancelled' WHERE pending 且超时。返回取消条数。"""
    from ..database import get_db
    from .logger import log_info
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE credit_orders
               SET status = 'cancelled'
             WHERE status = 'pending'
               AND created_at < datetime('now', ? )
            """,
            (f"-{EXPIRE_MINUTES} minutes",),
        )
        n = cursor.rowcount
        conn.commit()
    if n > 0:
        log_info(f"order_gc: 取消 {n} 个超时 pending 订单（>{EXPIRE_MINUTES} 分钟）")
    return n


async def order_gc_loop() -> None:
    """后台无限循环：启动后每 INTERVAL_SECONDS 秒清理一次。"""
    from .logger import log_error
    while True:
        try:
            cancel_expired_pending_orders()
        except Exception as e:
            log_error(f"order_gc_loop 异常: {e}")
        await asyncio.sleep(INTERVAL_SECONDS)

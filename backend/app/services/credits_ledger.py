"""
P157(2026-05-06)积分流水表 helper

任何积分变化(扣费/退款/admin 调账/充值)都通过 record_credits_change 入 ledger。
balance_after 字段用于对账:SUM(delta) WHERE user_id=? 必须等于 users.credits

5 个写入点:
- billing.deduct_credits → reason='task_charge', ref_id=task_id, module='image/style' 等
- billing.add_credits → reason='task_refund' / 'admin_adjust' / 'recharge_*'
- admin.adjust_credits → reason='admin_adjust', ref_id=audit_log_id
- 微信支付/支付宝回调 → reason='recharge_wx' / 'recharge_alipay', ref_id=order_id
- refund_tracker.try_refund → reason='task_refund', ref_id=task_id

设计原则:
- 失败不阻塞业务(写 ledger 失败只 log warning,不抛异常)
- 跟主操作(扣费/加费/调账)在同一事务里写,保证原子
- 不暴露 ledger 写入异常给上层
"""
from __future__ import annotations

import sqlite3
import time
import uuid
from typing import Optional

from ..database import get_db
from .logger import log_warning, log_info


def record_credits_change(
    user_id: str,
    delta: int,
    balance_after: int,
    reason: str,
    ref_id: Optional[str] = None,
    module: Optional[str] = None,
) -> bool:
    """记录一条积分变化到 ledger。

    Args:
        user_id: 用户 ID
        delta: 变化量(正=入账,负=扣费)
        balance_after: 该笔后用户余额(对账用)
        reason: 'task_charge' / 'task_refund' / 'admin_adjust' / 'recharge_wx' / 'recharge_alipay'
        ref_id: 关联 ID(task_id / order_id / actor_admin_id)
        module: 业务模块(image/style / oral_broadcast/standard 等)

    Returns:
        True 写入成功,False 失败(不抛异常)
    """
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO credits_ledger (id, user_id, delta, balance_after, reason, ref_id, module, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                uuid.uuid4().hex,
                user_id,
                int(delta),
                int(balance_after),
                reason,
                ref_id,
                module,
                time.time(),
            ))
            conn.commit()
        log_info(
            f"credits_ledger user={user_id[:8]} delta={delta:+d} balance={balance_after} "
            f"reason={reason} ref={(ref_id or '')[:12]} module={module or '-'}"
        )
        return True
    except Exception as e:
        log_warning(f"credits_ledger 写入失败 user={user_id[:8]} delta={delta} reason={reason}: {e}")
        return False


def query_user_ledger(user_id: str, limit: int = 100) -> list:
    """查用户最近 N 条流水(给 admin / personal center 用)"""
    try:
        with get_db() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, delta, balance_after, reason, ref_id, module, created_at
                FROM credits_ledger
                WHERE user_id = ?
                ORDER BY created_at DESC
                LIMIT ?
            """, (user_id, limit))
            return [dict(r) for r in cursor.fetchall()]
    except Exception as e:
        log_warning(f"query_user_ledger 失败: {e}")
        return []


def reconcile_user_balance(user_id: str) -> dict:
    """对账:SUM(ledger.delta) 应等于 users.credits

    Returns:
        {"ledger_sum": int, "user_credits": int, "diff": int, "ok": bool}
    """
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COALESCE(SUM(delta), 0) FROM credits_ledger WHERE user_id = ?", (user_id,))
            ledger_sum = cursor.fetchone()[0]
            cursor.execute("SELECT credits FROM users WHERE id = ?", (user_id,))
            row = cursor.fetchone()
            user_credits = row[0] if row else 0
            diff = user_credits - ledger_sum
            return {
                "ledger_sum": ledger_sum,
                "user_credits": user_credits,
                "diff": diff,
                "ok": diff == 0,
            }
    except Exception as e:
        log_warning(f"reconcile_user_balance 失败: {e}")
        return {"error": str(e)}

"""P157 credits_ledger helper 测试 — 验 record / query / reconcile"""
import os
import time
import sqlite3

os.environ.setdefault("JWT_SECRET", "test_only")

from app.services.credits_ledger import (
    record_credits_change,
    query_user_ledger,
    reconcile_user_balance,
)
from app.database import get_db


def _make_user(user_id: str, credits: int = 100):
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            "INSERT OR REPLACE INTO users (id, email, password_hash, role, credits, created_at) VALUES (?, ?, 'x', 'user', ?, CURRENT_TIMESTAMP)",
            (user_id, f"{user_id}@test.com", credits),
        )
        conn.commit()


def _clear_ledger(user_id: str):
    with get_db() as conn:
        c = conn.cursor()
        c.execute("DELETE FROM credits_ledger WHERE user_id = ?", (user_id,))
        conn.commit()


def test_record_credits_change_basic():
    user_id = "test_ledger_basic"
    _make_user(user_id, credits=100)
    _clear_ledger(user_id)

    ok = record_credits_change(
        user_id=user_id,
        delta=-30,
        balance_after=70,
        reason="task_charge",
        ref_id="task_xxx",
        module="image/style",
    )
    assert ok is True

    rows = query_user_ledger(user_id, limit=10)
    assert len(rows) == 1
    assert rows[0]["delta"] == -30
    assert rows[0]["balance_after"] == 70
    assert rows[0]["reason"] == "task_charge"
    assert rows[0]["ref_id"] == "task_xxx"
    assert rows[0]["module"] == "image/style"


def test_record_multiple_entries_ordered_by_time_desc():
    user_id = "test_ledger_multi"
    _make_user(user_id, credits=100)
    _clear_ledger(user_id)

    record_credits_change(user_id, delta=-10, balance_after=90, reason="task_charge")
    time.sleep(0.01)
    record_credits_change(user_id, delta=+10, balance_after=100, reason="task_refund")
    time.sleep(0.01)
    record_credits_change(user_id, delta=+50, balance_after=150, reason="recharge_wx")

    rows = query_user_ledger(user_id, limit=10)
    assert len(rows) == 3
    # 最新在最前
    assert rows[0]["reason"] == "recharge_wx"
    assert rows[0]["delta"] == 50
    assert rows[2]["reason"] == "task_charge"


def test_reconcile_balance_matches():
    user_id = "test_reconcile_ok"
    _make_user(user_id, credits=100)
    _clear_ledger(user_id)

    # 模拟一系列变化(用户初始 100,这里假设迁移前流水 ledger_sum=100)
    # 真生产场景应该:用户从 0 开始,所有变化都 ledger 记录
    record_credits_change(user_id, delta=100, balance_after=100, reason="init_migration")

    res = reconcile_user_balance(user_id)
    assert res["ok"] is True
    assert res["diff"] == 0
    assert res["ledger_sum"] == 100
    assert res["user_credits"] == 100


def test_reconcile_detects_drift():
    user_id = "test_reconcile_drift"
    _make_user(user_id, credits=100)
    _clear_ledger(user_id)

    # ledger 记 50,但 users.credits=100 → diff=50(说明 50 积分来路不明)
    record_credits_change(user_id, delta=50, balance_after=50, reason="task_refund")

    res = reconcile_user_balance(user_id)
    assert res["ok"] is False
    assert res["diff"] == 50  # users.credits 比 ledger sum 多 50

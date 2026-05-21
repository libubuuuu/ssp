"""db_backup.reconcile_credits 单元测试

覆盖:
- 无用户时返回 0
- 所有用户 ledger 一致 → 返回 0，不推送
- 余额不一致用户 → 返回 mismatch 数，触发 _push_alert
- 有积分但无 ledger 记录（游离用户）→ 也计入 mismatch，触发推送
- _push_alert 异常静默（不让对账崩溃）
- reconcile 自身 DB 异常 → 静默返回 0（不抛）
"""
import sqlite3
import time
import uuid

import pytest

from app.services import db_backup
from app.services.db_backup import reconcile_credits


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_db(tmp_path):
    """创建最小 schema（users + credits_ledger），返回 db path。"""
    db = tmp_path / "test_reconcile.db"
    conn = sqlite3.connect(str(db))
    conn.executescript("""
        CREATE TABLE users (
            id TEXT PRIMARY KEY,
            email TEXT NOT NULL,
            credits INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE credits_ledger (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            delta INTEGER NOT NULL,
            balance_after INTEGER NOT NULL,
            reason TEXT,
            ref_id TEXT,
            module TEXT,
            created_at REAL
        );
    """)
    conn.commit()
    conn.close()
    return str(db)


def _add_user(db_path, email, credits):
    uid = uuid.uuid4().hex
    conn = sqlite3.connect(db_path)
    conn.execute("INSERT INTO users (id, email, credits) VALUES (?, ?, ?)", (uid, email, credits))
    conn.commit()
    conn.close()
    return uid


def _add_ledger(db_path, user_id, delta, balance_after):
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO credits_ledger (id, user_id, delta, balance_after, reason, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (uuid.uuid4().hex, user_id, delta, balance_after, "test", time.time()),
    )
    conn.commit()
    conn.close()


@pytest.fixture()
def patch_db(tmp_path, monkeypatch):
    """让 db_backup._get_db_path 返回临时测试库。"""
    db_path = _make_db(tmp_path)
    monkeypatch.setattr(db_backup, "_get_db_path", lambda: db_path)
    return db_path


# ── tests ─────────────────────────────────────────────────────────────────────

def test_reconcile_empty_db_returns_zero(patch_db, monkeypatch):
    """没有任何用户 → 0 mismatches，无推送。"""
    push_calls = []
    monkeypatch.setattr(db_backup, "_push_alert", lambda t, b: push_calls.append((t, b)))

    result = reconcile_credits()
    assert result == 0
    assert len(push_calls) == 0


def test_reconcile_consistent_users_returns_zero(patch_db, monkeypatch):
    """ledger_sum == actual_credits → 0 mismatches。"""
    db = patch_db
    uid = _add_user(db, "ok@test.com", 80)
    _add_ledger(db, uid, 100, 100)
    _add_ledger(db, uid, -20, 80)

    push_calls = []
    monkeypatch.setattr(db_backup, "_push_alert", lambda t, b: push_calls.append((t, b)))

    result = reconcile_credits()
    assert result == 0
    assert len(push_calls) == 0


def test_reconcile_mismatch_triggers_push(patch_db, monkeypatch):
    """ledger_sum ≠ actual_credits → mismatch=1，推送被调。"""
    db = patch_db
    uid = _add_user(db, "bad@test.com", 100)   # actual=100
    _add_ledger(db, uid, 80, 80)               # ledger_sum=80

    push_calls = []
    monkeypatch.setattr(db_backup, "_push_alert", lambda t, b: push_calls.append((t, b)))

    result = reconcile_credits()
    assert result == 1
    assert len(push_calls) == 1
    title, body = push_calls[0]
    assert "积分对账异常" in title
    assert "bad@test.com" in body
    assert "+20" in body  # diff = 100-80 = +20


def test_reconcile_no_ledger_orphan_triggers_push(patch_db, monkeypatch):
    """有积分但零 ledger 记录的用户 → 计入 mismatch，推送。"""
    db = patch_db
    _add_user(db, "orphan@test.com", 50)  # 无 ledger

    push_calls = []
    monkeypatch.setattr(db_backup, "_push_alert", lambda t, b: push_calls.append((t, b)))

    result = reconcile_credits()
    assert result == 1
    assert len(push_calls) == 1
    body = push_calls[0][1]
    assert "orphan@test.com" in body
    assert "无任何 ledger" in body


def test_reconcile_zero_credits_orphan_ignored(patch_db, monkeypatch):
    """有用户但积分=0 且无 ledger → 不计入 mismatch（初始状态正常）。"""
    db = patch_db
    _add_user(db, "new@test.com", 0)

    push_calls = []
    monkeypatch.setattr(db_backup, "_push_alert", lambda t, b: push_calls.append((t, b)))

    result = reconcile_credits()
    assert result == 0
    assert len(push_calls) == 0


def test_reconcile_mixed_returns_total(patch_db, monkeypatch):
    """1 mismatch + 1 orphan → total=2，一次推送包含两类问题。"""
    db = patch_db
    uid_bad = _add_user(db, "mismatch@test.com", 100)
    _add_ledger(db, uid_bad, 50, 50)   # ledger_sum=50, actual=100 → diff=+50
    _add_user(db, "orphan2@test.com", 30)  # 无 ledger

    push_calls = []
    monkeypatch.setattr(db_backup, "_push_alert", lambda t, b: push_calls.append((t, b)))

    result = reconcile_credits()
    assert result == 2
    assert len(push_calls) == 1
    body = push_calls[0][1]
    assert "余额不一致" in body
    assert "无 ledger 用户" in body


def test_push_alert_failure_does_not_crash_reconcile(patch_db, monkeypatch):
    """_push_alert 内部异常不能让对账挂掉。"""
    db = patch_db
    uid = _add_user(db, "crash@test.com", 100)
    _add_ledger(db, uid, 50, 50)  # mismatch

    def boom(t, b):
        raise RuntimeError("subprocess failed")

    monkeypatch.setattr(db_backup, "_push_alert", boom)

    # 不应抛异常
    result = reconcile_credits()
    assert result == 1


def test_reconcile_db_error_returns_zero(monkeypatch):
    """_get_db_path 指向不存在的路径 → 静默返回 0，不抛。"""
    monkeypatch.setattr(db_backup, "_get_db_path", lambda: "/nonexistent/path/db.sqlite")
    push_calls = []
    monkeypatch.setattr(db_backup, "_push_alert", lambda t, b: push_calls.append((t, b)))

    result = reconcile_credits()
    assert result == 0
    assert len(push_calls) == 0

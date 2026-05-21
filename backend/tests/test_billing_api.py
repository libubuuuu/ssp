"""app/api/billing.py — GET /api/billing/ledger 接口测试

当前覆盖率 0%，目标 90%+。

覆盖路径：
- 未登录 → 403
- 已登录无流水 → 空列表
- 已登录有流水 → 正确条目、时间倒序、label 映射
- 分页：page/limit 参数、pages 计算
- reason → label 转换（已知 / 未知）
- delta 正负都能返回
"""
import time
import uuid
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import auth as auth_module, billing as billing_module
from app.database import get_db
from app.services.auth import create_user, set_user_credits


# ── 专用 app fixture（conftest 的 app 不含 billing router）──────────────────

@pytest.fixture(scope="module")
def billing_app():
    app = FastAPI()
    app.include_router(auth_module.router, prefix="/api/auth")
    app.include_router(billing_module.router, prefix="/api/billing")
    return app


@pytest.fixture()
def bclient(billing_app):
    return TestClient(billing_app)


# ── helpers ───────────────────────────────────────────────────────────────────

def _reg(bclient, email: str, credits: int = 500) -> tuple[str, dict]:
    """注册 + 注入 email 验证码，返回 (token, user)。"""
    auth_module._EMAIL_CODES[email] = {
        "code": "888888",
        "expires_at": time.time() + 300,
        "sent_at": time.time(),
        "purpose": "register",
    }
    r = bclient.post("/api/auth/register", json={"email": email, "password": "pass1234", "code": "888888"})
    assert r.status_code == 200, r.text
    data = r.json()
    bclient.cookies.clear()
    user = data["user"]
    set_user_credits(user["id"], credits)
    return data["token"], user


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _insert_ledger(user_id: str, delta: int, balance_after: int, reason: str = "task_charge"):
    with get_db() as conn:
        conn.execute(
            "INSERT INTO credits_ledger (id, user_id, delta, balance_after, reason, created_at) VALUES (?,?,?,?,?,?)",
            (uuid.uuid4().hex, user_id, delta, balance_after, reason, time.time()),
        )
        conn.commit()


# ── tests ─────────────────────────────────────────────────────────────────────

class TestLedgerAuth:
    def test_unauthenticated_returns_403(self, bclient):
        r = bclient.get("/api/billing/ledger")
        assert r.status_code in (401, 403)

    def test_authenticated_empty_ledger(self, bclient):
        token, _ = _reg(bclient, "bl-empty@test.com")
        r = bclient.get("/api/billing/ledger", headers=_auth(token))
        assert r.status_code == 200
        body = r.json()
        assert body["records"] == []
        assert body["total"] == 0
        assert body["page"] == 1
        assert body["pages"] == 1


class TestLedgerContents:
    def test_returns_own_records_only(self, bclient):
        tok_a, ua = _reg(bclient, "bl-a@test.com")
        tok_b, ub = _reg(bclient, "bl-b@test.com")
        _insert_ledger(ua["id"], -20, 480)
        _insert_ledger(ub["id"], -50, 450)

        r = bclient.get("/api/billing/ledger", headers=_auth(tok_a))
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 1
        assert body["records"][0]["delta"] == -20

    def test_records_sorted_descending_by_time(self, bclient):
        token, user = _reg(bclient, "bl-order@test.com")
        for i in range(3):
            _insert_ledger(user["id"], -(i + 1) * 10, 500, "task_charge")
            time.sleep(0.01)  # 保证 created_at 不同

        r = bclient.get("/api/billing/ledger", headers=_auth(token))
        records = r.json()["records"]
        assert len(records) == 3
        # 最新的在前
        times = [rec["created_at"] for rec in records]
        assert times == sorted(times, reverse=True)

    def test_label_known_reason(self, bclient):
        token, user = _reg(bclient, "bl-label@test.com")
        _insert_ledger(user["id"], -20, 480, "task_charge")
        _insert_ledger(user["id"], +20, 500, "task_refund")
        _insert_ledger(user["id"], +100, 600, "recharge_wx")

        r = bclient.get("/api/billing/ledger", headers=_auth(token))
        records = r.json()["records"]
        by_reason = {rec["reason"]: rec["label"] for rec in records}
        assert by_reason["task_charge"] == "扣费"
        assert by_reason["task_refund"] == "退款"
        assert by_reason["recharge_wx"] == "微信充值"

    def test_label_unknown_reason_returns_raw(self, bclient):
        token, user = _reg(bclient, "bl-unknown@test.com")
        _insert_ledger(user["id"], +5, 505, "some_future_reason")
        r = bclient.get("/api/billing/ledger", headers=_auth(token))
        record = r.json()["records"][0]
        assert record["label"] == "some_future_reason"

    def test_positive_and_negative_delta(self, bclient):
        token, user = _reg(bclient, "bl-delta@test.com")
        _insert_ledger(user["id"], +100, 600, "recharge_manual")
        _insert_ledger(user["id"], -50, 550, "task_charge")

        r = bclient.get("/api/billing/ledger", headers=_auth(token))
        deltas = {rec["delta"] for rec in r.json()["records"]}
        assert 100 in deltas
        assert -50 in deltas


class TestLedgerPagination:
    def test_pagination_limit(self, bclient):
        token, user = _reg(bclient, "bl-page@test.com")
        for i in range(5):
            _insert_ledger(user["id"], -10, 500 - i * 10)

        r = bclient.get("/api/billing/ledger?limit=2", headers=_auth(token))
        body = r.json()
        assert len(body["records"]) == 2
        assert body["total"] == 5
        assert body["pages"] == 3

    def test_pagination_page2(self, bclient):
        token, user = _reg(bclient, "bl-page2@test.com")
        for i in range(5):
            _insert_ledger(user["id"], -10, 500 - i * 10)

        r = bclient.get("/api/billing/ledger?limit=3&page=2", headers=_auth(token))
        body = r.json()
        assert len(body["records"]) == 2
        assert body["page"] == 2

    def test_pagination_default_20_limit(self, bclient):
        token, user = _reg(bclient, "bl-page3@test.com")
        for i in range(25):
            _insert_ledger(user["id"], -1, 500 - i)

        r = bclient.get("/api/billing/ledger", headers=_auth(token))
        body = r.json()
        assert len(body["records"]) == 20
        assert body["total"] == 25
        assert body["pages"] == 2

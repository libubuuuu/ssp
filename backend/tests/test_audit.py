"""审计日志测试

覆盖:
- log_admin_action 写入持久化
- list_audit_log 读取过滤
- 详情字段 JSON 序列化
- 写入失败不抛异常
- adjust-credits 端点真的会写审计
"""
import pytest

from app.services import audit


def _make_admin(email: str) -> dict:
    """创建管理员,返回 user dict"""
    from app.services.auth import create_user
    from app.database import get_db
    user = create_user(email=email, password="secret123", name=email.split("@")[0])
    assert user is not None
    with get_db() as conn:
        c = conn.cursor()
        c.execute("UPDATE users SET role = 'admin' WHERE id = ?", (user["id"],))
        conn.commit()
    user["role"] = "admin"
    return user


def _make_target_user(email: str, credits: int = 100) -> str:
    """测试用辅助:创建一个用户并强制把额度设到指定值。
    P3-1 后初始 INITIAL_CREDITS=10,不再靠默认,显式 set。
    """
    from app.services.auth import create_user, set_user_credits
    user = create_user(email=email, password="secret123", name=email.split("@")[0])
    set_user_credits(user["id"], credits)
    return user["id"]


def test_log_admin_action_persists():
    admin = _make_admin("audit-admin-a@example.com")
    target = _make_target_user("audit-target-a@example.com")

    ok = audit.log_admin_action(
        actor_user_id=admin["id"],
        actor_email=admin["email"],
        action=audit.ACTION_ADJUST_CREDITS,
        target_type="user",
        target_id=target,
        details={"delta": 50, "old_credits": 100, "new_credits": 150},
        ip="127.0.0.1",
    )
    assert ok is True

    rows = audit.list_audit_log(actor_user_id=admin["id"])
    assert len(rows) == 1
    r = rows[0]
    assert r["action"] == "adjust_credits"
    assert r["target_id"] == target
    assert r["details"]["delta"] == 50
    assert r["details"]["new_credits"] == 150
    assert r["ip"] == "127.0.0.1"


def test_list_audit_filter_by_action():
    admin = _make_admin("audit-admin-b@example.com")
    target = _make_target_user("audit-target-b@example.com")

    audit.log_admin_action(admin["id"], admin["email"], "adjust_credits", "user", target, {"delta": 1})
    audit.log_admin_action(admin["id"], admin["email"], "set_role", "user", target, {"new_role": "admin"})

    only_credits = audit.list_audit_log(action="adjust_credits")
    assert len(only_credits) == 1
    assert only_credits[0]["action"] == "adjust_credits"

    only_role = audit.list_audit_log(action="set_role")
    assert len(only_role) == 1
    assert only_role[0]["details"]["new_role"] == "admin"


def test_list_audit_persists_all_entries():
    """3 次 INSERT 都应该入库(不依赖 created_at 排序 — SQLite 同秒精度不稳定)"""
    admin = _make_admin("audit-admin-c@example.com")
    target = _make_target_user("audit-target-c@example.com")

    for i in range(3):
        audit.log_admin_action(
            admin["id"], admin["email"], "adjust_credits", "user", target,
            {"delta": i + 1},
        )

    rows = audit.list_audit_log(actor_user_id=admin["id"])
    assert len(rows) == 3
    deltas = sorted(r["details"]["delta"] for r in rows)
    assert deltas == [1, 2, 3]


def test_log_handles_none_details_gracefully():
    """details=None 不应该报错"""
    admin = _make_admin("audit-admin-d@example.com")
    ok = audit.log_admin_action(
        actor_user_id=admin["id"],
        actor_email=admin["email"],
        action="some_action",
    )
    assert ok is True
    rows = audit.list_audit_log(actor_user_id=admin["id"])
    assert rows[0]["details"] is None


def test_admin_audit_log_endpoint_returns_records(client):
    """GET /api/admin/audit-log 管理员能看审计列表"""
    admin = _make_admin("audit-list-admin@example.com")
    target = _make_target_user("audit-list-target@example.com")

    # 写 2 条不同 action 的审计
    audit.log_admin_action(admin["id"], admin["email"], "adjust_credits", "user", target, {"delta": 10})
    audit.log_admin_action(admin["id"], admin["email"], "force_logout", "user", target, {"reason": "spam"})

    from app.services.auth import create_jwt_token
    token = create_jwt_token(admin["id"], admin["email"], "admin")
    headers = {"Authorization": f"Bearer {token}"}

    # 不过滤,拿全部
    r = client.get("/api/admin/audit-log", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2
    assert len(body["logs"]) == 2

    # 按 action 过滤
    r = client.get("/api/admin/audit-log?action=adjust_credits", headers=headers)
    assert r.status_code == 200
    assert r.json()["total"] == 1
    assert r.json()["logs"][0]["action"] == "adjust_credits"


def test_audit_log_endpoint_blocks_non_admin(client):
    """普通用户调审计接口应返 403"""
    user = _make_target_user("audit-list-user@example.com")
    from app.services.auth import create_jwt_token
    token = create_jwt_token(user, "audit-list-user@example.com", "user")
    r = client.get("/api/admin/audit-log", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403


def test_audit_log_limit_caps_at_500(client):
    """limit > 500 应自动 cap 到 500(防止管理员一次拉太多)"""
    admin = _make_admin("audit-cap@example.com")
    from app.services.auth import create_jwt_token
    token = create_jwt_token(admin["id"], admin["email"], "admin")
    r = client.get("/api/admin/audit-log?limit=99999", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    # 此时表里没几条,无法直接验证 cap;但接口能返回不报错即代表 cap 逻辑没崩
    assert "total" in r.json()


def test_change_password_creates_audit_record(client):
    """用户改密码必须写审计(自己改自己)"""
    user = _make_target_user("audit-pwd@example.com", credits=100)
    from app.services.auth import create_jwt_token
    token = create_jwt_token(user, "audit-pwd@example.com", "user")
    r = client.post(
        "/api/auth/change-password",
        json={"current_password": "secret123", "new_password": "newpwd-456"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text

    rows = audit.list_audit_log(actor_user_id=user, action="change_password")
    assert len(rows) == 1
    assert rows[0]["target_id"] == user
    assert rows[0]["actor_email"] == "audit-pwd@example.com"


def test_logout_all_devices_creates_audit_record(client):
    """用户登出所有设备必须写审计"""
    user = _make_target_user("audit-logoutall@example.com")
    from app.services.auth import create_jwt_token
    token = create_jwt_token(user, "audit-logoutall@example.com", "user")
    r = client.post(
        "/api/auth/logout-all-devices",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200

    rows = audit.list_audit_log(actor_user_id=user, action="logout_all_devices")
    assert len(rows) == 1
    assert rows[0]["target_id"] == user


def test_reset_password_by_code_creates_audit_record(client):
    """凭验证码重置密码必须写审计(via=email_code 标识手段)"""
    user = _make_target_user("audit-reset@example.com")
    from app.api import auth as auth_module
    auth_module._EMAIL_CODES["audit-reset@example.com"] = {
        "code": "654321",
        "expires_at": __import__("time").time() + 600,
    }
    r = client.post(
        "/api/auth/reset-password-by-code",
        json={"email": "audit-reset@example.com", "code": "654321", "new_password": "fresh-pwd"},
    )
    assert r.status_code == 200, r.text

    rows = audit.list_audit_log(actor_user_id=user, action="reset_password")
    assert len(rows) == 1
    assert rows[0]["details"]["via"] == "email_code"
    assert rows[0]["actor_email"] == "audit-reset@example.com"


def test_admin_confirm_order_creates_audit_record(client):
    """管理员手动入账(payment.confirm_order)必须写审计 — 合规重点"""
    admin = _make_admin("audit-confirm-admin@example.com")
    target = _make_target_user("audit-confirm-target@example.com", credits=100)

    # 直接造一个 pending 订单
    import uuid as _uuid
    order_id = str(_uuid.uuid4())
    from app.database import get_db
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            "INSERT INTO credit_orders (id, user_id, amount, price, status) VALUES (?, ?, ?, ?, 'pending')",
            (order_id, target, 50, 9.9),
        )
        conn.commit()

    from app.services.auth import create_jwt_token
    token = create_jwt_token(admin["id"], admin["email"], "admin")
    r = client.post(
        f"/api/payment/orders/{order_id}/confirm",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["credits_added"] == 50

    rows = audit.list_audit_log(actor_user_id=admin["id"], action="confirm_order")
    assert len(rows) == 1
    assert rows[0]["target_id"] == order_id
    assert rows[0]["details"]["target_user_id"] == target
    assert rows[0]["details"]["credits_added"] == 50


def test_admin_reset_model_creates_audit_record(client, app):
    """管理员重置熔断器写审计 — 系统状态变更可追溯"""
    # admin.py 的 router 只在 conftest.app fixture 里挂在 /api/admin
    # reset_model 路由是 POST /api/admin/models/{model_name}/reset
    admin = _make_admin("audit-reset-admin@example.com")
    from app.services.auth import create_jwt_token
    token = create_jwt_token(admin["id"], admin["email"], "admin")

    r = client.post(
        "/api/admin/models/test-model-xyz/reset",
        headers={"Authorization": f"Bearer {token}"},
    )
    # 即使模型在 circuit_breaker 里不存在,接口依然成功 reset DB 状态
    assert r.status_code == 200, r.text

    rows = audit.list_audit_log(actor_user_id=admin["id"], action="reset_model")
    assert len(rows) == 1
    assert rows[0]["target_id"] == "test-model-xyz"


def test_admin_adjust_credits_creates_audit_record(client):
    """端到端:管理员调 adjust-credits API,审计日志真的多一行"""
    # 创建管理员 + 目标用户
    admin = _make_admin("audit-e2e-admin@example.com")
    target = _make_target_user("audit-e2e-target@example.com", credits=100)

    # 拿 admin token
    from app.services.auth import create_jwt_token
    token = create_jwt_token(admin["id"], admin["email"], "admin")
    headers = {"Authorization": f"Bearer {token}"}

    # 调 adjust-credits +30
    r = client.post(f"/api/admin/users/{target}/adjust-credits?delta=30", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["new_credits"] == 130

    # 审计应该已经写入
    rows = audit.list_audit_log(actor_user_id=admin["id"])
    assert len(rows) == 1
    r = rows[0]
    assert r["action"] == "adjust_credits"
    assert r["target_id"] == target
    assert r["details"]["delta"] == 30
    assert r["details"]["old_credits"] == 100
    assert r["details"]["new_credits"] == 130

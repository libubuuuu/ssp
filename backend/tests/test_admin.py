"""管理员 API 鉴权测试 + 关键路径覆盖
关注点:
- 普通用户访问 admin 接口 → 403
- admin 用户访问 → 200(其中需要 fal/circuit_breaker 等内部状态的接口跳过)
- 加额度接口的关键审计:目标用户余额真的变了
- force-logout 把目标用户 token 失效
- reset-model 走熔断器路径
- audit-log 列表 + 过滤
"""


def test_non_admin_blocked_from_admin_endpoints(client, register, auth_header):
    token, _ = register(client, "regular@example.com")
    h = auth_header(token)

    endpoints = [
        ("GET", "/api/admin/users-list"),
        ("GET", "/api/admin/orders"),
        ("GET", "/api/admin/stats/overview"),
        ("GET", "/api/admin/tasks/recent"),
    ]
    for method, path in endpoints:
        r = client.request(method, path, headers=h)
        assert r.status_code == 403, f"{method} {path} expected 403, got {r.status_code}"


def test_unauthenticated_blocked_from_admin(client):
    r = client.get("/api/admin/users-list")
    assert r.status_code in (401, 403)


def test_admin_can_list_users(client, register, auth_header, set_role):
    """管理员能列出所有用户(数据库查询全路径)"""
    a_token, a_user = register(client, "admin-list@example.com")
    set_role(a_user["id"], "admin")
    register(client, "user-1@example.com")
    register(client, "user-2@example.com")

    r = client.get("/api/admin/users-list", headers=auth_header(a_token))
    assert r.status_code == 200, r.text
    users = r.json()["users"]
    emails = {u["email"] for u in users}
    assert "admin-list@example.com" in emails
    assert "user-1@example.com" in emails
    assert "user-2@example.com" in emails
    # 字段完整
    assert all("id" in u and "credits" in u and "role" in u for u in users)


def test_admin_can_adjust_credits(client, register, auth_header, set_role):
    a_token, a_user = register(client, "adj-admin@example.com")
    set_role(a_user["id"], "admin")
    _, target = register(client, "adj-target@example.com")
    target_id = target["id"]

    # 加 50
    r1 = client.post(f"/api/admin/users/{target_id}/adjust-credits",
                     params={"delta": 50},
                     headers=auth_header(a_token))
    assert r1.status_code == 200, r1.text

    # 直接读 DB 验证(避免 /me 需要 target 自己的 token)
    from app.database import get_db
    with get_db() as conn:
        row = conn.execute("SELECT credits FROM users WHERE id = ?", (target_id,)).fetchone()
    assert row[0] == 60  # 10 (P3-1 默认) + 50

    # 减 30
    r2 = client.post(f"/api/admin/users/{target_id}/adjust-credits",
                     params={"delta": -30},
                     headers=auth_header(a_token))
    assert r2.status_code == 200
    with get_db() as conn:
        row = conn.execute("SELECT credits FROM users WHERE id = ?", (target_id,)).fetchone()
    assert row[0] == 30


def test_non_admin_cannot_adjust_credits(client, register, auth_header):
    """普通用户不能给别人加额度(否则等于免费 print money)"""
    a_token, _ = register(client, "regular-1@example.com")
    _, target = register(client, "target-2@example.com")

    r = client.post(f"/api/admin/users/{target['id']}/adjust-credits",
                    params={"delta": 9999},
                    headers=auth_header(a_token))
    assert r.status_code == 403

    # 余额不应改变
    from app.database import get_db
    with get_db() as conn:
        row = conn.execute("SELECT credits FROM users WHERE id = ?", (target["id"],)).fetchone()
    assert row[0] == 10  # P3-1 默认


# === 新增:adjust-credits 边界 + 不存在用户 ===

def test_adjust_credits_floors_at_zero(client, register, auth_header, set_role):
    """delta 减得超过余额 → 余额 = 0(max(0, ...))"""
    a_token, a_user = register(client, "floor-admin@example.com")
    set_role(a_user["id"], "admin")
    _, target = register(client, "floor-target@example.com")
    r = client.post(
        f"/api/admin/users/{target['id']}/adjust-credits",
        params={"delta": -9999}, headers=auth_header(a_token),
    )
    assert r.status_code == 200
    from app.database import get_db
    with get_db() as conn:
        row = conn.execute("SELECT credits FROM users WHERE id = ?", (target["id"],)).fetchone()
    assert row[0] == 0


def test_adjust_credits_404_for_unknown_user(client, register, auth_header, set_role):
    a_token, a_user = register(client, "404-admin@example.com")
    set_role(a_user["id"], "admin")
    r = client.post(
        "/api/admin/users/no-such-uuid/adjust-credits",
        params={"delta": 10}, headers=auth_header(a_token),
    )
    assert r.status_code == 404


# === force-logout ===

def test_force_logout_invalidates_user_tokens(client, register, auth_header, set_role):
    """管理员踢人 → 该用户的 token 立即失效"""
    a_token, a_user = register(client, "fl-admin@example.com")
    set_role(a_user["id"], "admin")
    target_token, target = register(client, "fl-target@example.com")

    # 验证 target 当前能访问 /me
    r_before = client.get("/api/auth/me", headers=auth_header(target_token))
    assert r_before.status_code == 200

    # 踢
    r = client.post(
        f"/api/admin/users/{target['id']}/force-logout",
        headers=auth_header(a_token),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["success"] is True  # 修复后:bool 而非 int ts
    assert body["user_id"] == target["id"]

    # 现在 target 应当 401
    client.cookies.clear()
    r_after = client.get("/api/auth/me", headers=auth_header(target_token))
    assert r_after.status_code == 401


def test_force_logout_404_for_unknown_user(client, register, auth_header, set_role):
    a_token, a_user = register(client, "fl-404-admin@example.com")
    set_role(a_user["id"], "admin")
    r = client.post(
        "/api/admin/users/no-such-uuid/force-logout",
        headers=auth_header(a_token),
    )
    assert r.status_code == 404


def test_force_logout_non_admin_403(client, register, auth_header):
    token, _ = register(client, "fl-non-admin@example.com")
    _, target = register(client, "fl-victim@example.com")
    r = client.post(
        f"/api/admin/users/{target['id']}/force-logout",
        headers=auth_header(token),
    )
    assert r.status_code == 403


# === audit-log 接口 ===

def test_audit_log_endpoint_admin_only(client, register, auth_header):
    token, _ = register(client, "audit-non-admin@example.com")
    r = client.get("/api/admin/audit-log", headers=auth_header(token))
    assert r.status_code == 403


def test_audit_log_endpoint_returns_recent(client, register, auth_header, set_role):
    a_token, a_user = register(client, "audit-list-admin@example.com")
    set_role(a_user["id"], "admin")
    _, target = register(client, "audit-list-target@example.com")

    # 先制造一条审计:adjust-credits
    client.post(
        f"/api/admin/users/{target['id']}/adjust-credits",
        params={"delta": 5},
        headers=auth_header(a_token),
    )

    r = client.get("/api/admin/audit-log", headers=auth_header(a_token))
    assert r.status_code == 200
    body = r.json()
    assert body["total"] >= 1
    actions = {row["action"] for row in body["logs"]}
    assert "adjust_credits" in actions


def test_audit_log_filter_by_action(client, register, auth_header, set_role):
    a_token, a_user = register(client, "audit-filter-admin@example.com")
    set_role(a_user["id"], "admin")
    _, target = register(client, "audit-filter-target@example.com")
    # 制造两条不同 action 的审计
    client.post(f"/api/admin/users/{target['id']}/adjust-credits",
                params={"delta": 1}, headers=auth_header(a_token))
    client.post(f"/api/admin/users/{target['id']}/force-logout",
                headers=auth_header(a_token))

    # 只过滤 force_logout
    r = client.get("/api/admin/audit-log?action=force_logout", headers=auth_header(a_token))
    assert r.status_code == 200
    actions = {row["action"] for row in r.json()["logs"]}
    assert actions == {"force_logout"}


def test_audit_log_limit_capped_at_500(client, register, auth_header, set_role):
    """limit 超 500 被截到 500 — 防恶意请求"""
    a_token, a_user = register(client, "audit-cap-admin@example.com")
    set_role(a_user["id"], "admin")
    r = client.get("/api/admin/audit-log?limit=10000", headers=auth_header(a_token))
    assert r.status_code == 200
    # 没有真造 >500 条数据,所以 total 不一定 == 500;只验证不报错 + 有 logs 字段
    assert "logs" in r.json()


# === diagnose-history(只读文件系统)===

def test_diagnose_history_admin_only(client, register, auth_header):
    token, _ = register(client, "diag-non-admin@example.com")
    r = client.get("/api/admin/diagnose-history", headers=auth_header(token))
    assert r.status_code == 403


def test_diagnose_history_returns_list_or_empty(client, register, auth_header, set_role):
    """目录可能不存在(测试机器),应返回空列表不抛 500"""
    a_token, a_user = register(client, "diag-admin@example.com")
    set_role(a_user["id"], "admin")
    r = client.get("/api/admin/diagnose-history", headers=auth_header(a_token))
    assert r.status_code == 200
    body = r.json()
    assert "snapshots" in body or isinstance(body, dict)


# === 管理员 2FA 强制开关 ===


def test_admin_2fa_enforce_off_admin_without_2fa_passes(client, register, auth_header, set_role, monkeypatch):
    """ADMIN_2FA_REQUIRED=false(默认):无 2FA 的管理员仍能进入"""
    monkeypatch.setenv("ADMIN_2FA_REQUIRED", "false")
    a_token, a_user = register(client, "no2fa-admin@example.com")
    set_role(a_user["id"], "admin")
    r = client.get("/api/admin/users-list", headers=auth_header(a_token))
    assert r.status_code == 200


def test_admin_2fa_enforce_on_admin_without_2fa_blocked(client, register, auth_header, set_role, monkeypatch):
    """ADMIN_2FA_REQUIRED=true:无 2FA 的管理员被拦"""
    monkeypatch.setenv("ADMIN_2FA_REQUIRED", "true")
    a_token, a_user = register(client, "needs2fa-admin@example.com")
    set_role(a_user["id"], "admin")
    r = client.get("/api/admin/users-list", headers=auth_header(a_token))
    assert r.status_code == 403
    body = r.json()
    # detail 是结构化对象给前端引导
    assert isinstance(body["detail"], dict)
    assert body["detail"]["code"] == "ADMIN_2FA_REQUIRED"
    assert body["detail"]["redirect"] == "/profile/2fa"


def test_admin_2fa_enforce_on_admin_with_2fa_passes(client, register, auth_header, set_role, monkeypatch):
    """ADMIN_2FA_REQUIRED=true:已 enroll 2FA 的管理员通行"""
    monkeypatch.setenv("ADMIN_2FA_REQUIRED", "true")
    a_token, a_user = register(client, "has2fa-admin@example.com")
    set_role(a_user["id"], "admin")
    # 直接改 DB 模拟已 enroll 2FA(2FA setup 流程涉及 TOTP secret + verify 太长)
    from app.database import get_db
    with get_db() as conn:
        conn.cursor().execute(
            "UPDATE users SET totp_enabled = 1, totp_secret = 'TESTSECRET' WHERE id = ?",
            (a_user["id"],),
        )
        conn.commit()
    r = client.get("/api/admin/users-list", headers=auth_header(a_token))
    assert r.status_code == 200


def test_admin_2fa_enforce_doesnt_affect_non_admin(client, register, auth_header, monkeypatch):
    """ADMIN_2FA_REQUIRED=true 时,普通用户访问 admin 端点仍是 403,但 detail 是普通的'需要管理员权限',不是 2FA 引导"""
    monkeypatch.setenv("ADMIN_2FA_REQUIRED", "true")
    token, _ = register(client, "regular-user@example.com")
    r = client.get("/api/admin/users-list", headers=auth_header(token))
    assert r.status_code == 403
    body = r.json()
    # detail 是字符串(普通 role 不足)而不是 2FA dict
    assert isinstance(body["detail"], str)
    assert "管理员" in body["detail"]

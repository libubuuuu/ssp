"""
管理员 API 鉴权测试
关注点:
- 普通用户访问 admin 接口 → 403
- admin 用户访问 → 200(其中需要 fal/circuit_breaker 等内部状态的接口跳过)
- 加额度接口的关键审计:目标用户余额真的变了
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
    token, user = register(client, "admin-x@example.com")
    set_role(user["id"], "admin")

    # 再造个普通用户,期望出现在列表里
    register(client, "joe@example.com")

    r = client.get("/api/admin/users-list", headers=auth_header(token))
    assert r.status_code == 200, r.text
    users = r.json()["users"]
    emails = [u["email"] for u in users]
    assert "admin-x@example.com" in emails
    assert "joe@example.com" in emails


def test_admin_adjust_credits_round_trip(client, register, auth_header, set_role):
    """管理员加 / 减额度,目标用户余额真的应该变"""
    a_token, admin_user = register(client, "admin-y@example.com")
    set_role(admin_user["id"], "admin")

    _, target = register(client, "target@example.com")
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

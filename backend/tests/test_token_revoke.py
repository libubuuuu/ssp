"""Token 吊销测试

覆盖:
- decode_jwt_token 在 tokens_invalid_before > token.iat 时返回 None
- POST /api/auth/logout-all-devices 让旧 token 失效
- POST /api/auth/change-password 自动吊销旧 token
- POST /api/auth/reset-password-by-code 自动吊销旧 token
- POST /api/admin/users/{id}/force-logout 让目标用户旧 token 失效 + 写审计
"""
import time

from app.services.auth import (
    create_user,
    create_jwt_token,
    decode_jwt_token,
    invalidate_user_tokens,
)
from app.services import audit


def _make_user(email: str, role: str = "user") -> dict:
    user = create_user(email=email, password="secret123", name=email.split("@")[0])
    if role != "user":
        from app.database import get_db
        with get_db() as conn:
            c = conn.cursor()
            c.execute("UPDATE users SET role = ? WHERE id = ?", (role, user["id"]))
            conn.commit()
        user["role"] = role
    return user


def test_decode_returns_none_after_invalidate():
    """invalidate 后,之前签的 token decode 返回 None"""
    user = _make_user("revoke-a@example.com")
    token = create_jwt_token(user["id"], user["email"], "user")
    # 此刻 token 应该有效
    assert decode_jwt_token(token) is not None

    # invalidate 返回时间戳(int 秒);该 ts 严格 > 旧 token iat 即生效
    ts = invalidate_user_tokens(user["id"])
    assert isinstance(ts, int) and ts > 0

    # 同一 token 现在应该被拒绝
    assert decode_jwt_token(token) is None


def test_new_token_after_invalidate_is_valid():
    """invalidate 后重新签的 token 应仍有效"""
    user = _make_user("revoke-b@example.com")
    invalidate_user_tokens(user["id"])
    time.sleep(1)
    new_token = create_jwt_token(user["id"], user["email"], "user")
    assert decode_jwt_token(new_token) is not None


def test_logout_all_devices_endpoint_invalidates(client):
    """POST /api/auth/logout-all-devices 让当前 token + 其他设备 token 全失效"""
    user = _make_user("revoke-c@example.com")
    old_token = create_jwt_token(user["id"], user["email"], "user")
    headers = {"Authorization": f"Bearer {old_token}"}

    # 应该能调通(用旧 token)
    time.sleep(1)
    r = client.post("/api/auth/logout-all-devices", headers=headers)
    assert r.status_code == 200, r.text

    # 现在旧 token 应失效
    assert decode_jwt_token(old_token) is None


def test_change_password_invalidates_old_token(client):
    """改密码后,旧 token 不再有效(防泄漏密码后旧 token 仍可用)"""
    user = _make_user("revoke-d@example.com")
    old_token = create_jwt_token(user["id"], user["email"], "user")
    headers = {"Authorization": f"Bearer {old_token}"}

    time.sleep(1)
    r = client.post(
        "/api/auth/change-password",
        json={"current_password": "secret123", "new_password": "new-pwd-123"},
        headers=headers,
    )
    assert r.status_code == 200, r.text

    assert decode_jwt_token(old_token) is None


def test_reset_password_by_code_invalidates_old_token(client):
    """凭验证码重置密码后,旧 token 失效"""
    user = _make_user("revoke-e@example.com")
    old_token = create_jwt_token(user["id"], user["email"], "user")

    # 注入验证码绕过邮件
    from app.api import auth as auth_module
    auth_module._EMAIL_CODES[user["email"]] = {
        "code": "123456",
        "expires_at": time.time() + 600,
    }

    time.sleep(1)
    r = client.post(
        "/api/auth/reset-password-by-code",
        json={"email": user["email"], "code": "123456", "new_password": "another-new"},
    )
    assert r.status_code == 200, r.text

    assert decode_jwt_token(old_token) is None


def test_admin_force_logout_invalidates_target_and_writes_audit(client):
    """admin force-logout 把目标用户 token 失效 + 审计有记录"""
    admin = _make_user("revoke-admin@example.com", role="admin")
    target = _make_user("revoke-target@example.com")

    target_token = create_jwt_token(target["id"], target["email"], "user")
    assert decode_jwt_token(target_token) is not None

    # admin 调 force-logout
    admin_token = create_jwt_token(admin["id"], admin["email"], "admin")
    time.sleep(1)
    r = client.post(
        f"/api/admin/users/{target['id']}/force-logout",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r.status_code == 200, r.text

    # target 旧 token 应失效
    assert decode_jwt_token(target_token) is None

    # admin 自己的 token 不受影响
    assert decode_jwt_token(admin_token) is not None

    # 审计有记录
    rows = audit.list_audit_log(actor_user_id=admin["id"], action="force_logout")
    assert len(rows) == 1
    assert rows[0]["target_id"] == target["id"]
    assert rows[0]["details"]["target_email"] == target["email"]


def test_non_admin_cannot_force_logout(client):
    """普通用户调 force-logout 应返 403"""
    user_a = _make_user("revoke-na-a@example.com")
    user_b = _make_user("revoke-na-b@example.com")
    token = create_jwt_token(user_a["id"], user_a["email"], "user")
    r = client.post(
        f"/api/admin/users/{user_b['id']}/force-logout",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 403


def test_force_logout_unknown_user_returns_404(client):
    admin = _make_user("revoke-admin-404@example.com", role="admin")
    token = create_jwt_token(admin["id"], admin["email"], "admin")
    r = client.post(
        "/api/admin/users/nonexistent-id/force-logout",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 404

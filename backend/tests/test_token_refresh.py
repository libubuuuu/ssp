"""JWT refresh token 测试

覆盖:
- create_access/refresh_token 的 type 字段
- decode_jwt_token 拒绝 refresh token(防混用调业务接口)
- decode_refresh_token 拒绝 access token
- 旧 token(无 type 字段)依然能 decode_jwt_token 通过(向后兼容)
- /api/auth/login 同时返回 access_token + refresh_token
- /api/auth/refresh 用 refresh 换新 access
- /api/auth/refresh 拒绝 access token
- 用户被 invalidate 后,refresh 也失效
"""
import time
import jwt as pyjwt

from app.services.auth import (
    create_access_token,
    create_refresh_token,
    create_jwt_token,
    decode_jwt_token,
    decode_refresh_token,
    invalidate_user_tokens,
    JWT_SECRET,
    JWT_ALGORITHM,
    create_user,
)


def _make_user(email: str) -> dict:
    return create_user(email=email, password="secret123", name=email.split("@")[0])


def test_access_token_has_type_access():
    user = _make_user("refresh-a@example.com")
    token = create_access_token(user["id"], user["email"], "user")
    payload = pyjwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    assert payload["type"] == "access"


def test_refresh_token_has_type_refresh():
    user = _make_user("refresh-b@example.com")
    token = create_refresh_token(user["id"], user["email"], "user")
    payload = pyjwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    assert payload["type"] == "refresh"


def test_decode_jwt_rejects_refresh_token():
    """业务接口的 decode_jwt_token 必须拒绝 refresh,防止 refresh 滥用"""
    user = _make_user("refresh-c@example.com")
    refresh = create_refresh_token(user["id"], user["email"], "user")
    assert decode_jwt_token(refresh) is None


def test_decode_refresh_rejects_access_token():
    """decode_refresh_token 必须拒绝 access(只接受真 refresh)"""
    user = _make_user("refresh-d@example.com")
    access = create_access_token(user["id"], user["email"], "user")
    assert decode_refresh_token(access) is None


def test_legacy_token_without_type_still_works_as_access():
    """老 token 没 type 字段,向后兼容 — decode_jwt_token 当 access 通过"""
    user = _make_user("refresh-legacy@example.com")
    # 手工签一个无 type 字段的 token(模拟老格式)
    from datetime import datetime, timedelta
    payload = {
        "user_id": user["id"],
        "email": user["email"],
        "role": "user",
        "exp": datetime.utcnow() + timedelta(hours=1),
        "iat": datetime.utcnow(),
    }
    legacy = pyjwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)
    decoded = decode_jwt_token(legacy)
    assert decoded is not None
    assert decoded["user_id"] == user["id"]


def test_login_returns_both_tokens(client):
    """/api/auth/login 同时返回 access_token + refresh_token + 兼容 token 字段"""
    _make_user("refresh-login@example.com")
    r = client.post(
        "/api/auth/login",
        json={"email": "refresh-login@example.com", "password": "secret123"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "access_token" in body
    assert "refresh_token" in body
    assert body["token"] == body["access_token"]  # 向后兼容字段


def test_refresh_endpoint_swaps_refresh_for_new_access(client):
    """POST /api/auth/refresh:用 refresh_token 换新 access"""
    user = _make_user("refresh-swap@example.com")
    refresh = create_refresh_token(user["id"], user["email"], "user")

    r = client.post("/api/auth/refresh", json={"refresh_token": refresh})
    assert r.status_code == 200, r.text
    new_access = r.json()["access_token"]
    # 新 access 能通过 decode_jwt_token
    payload = decode_jwt_token(new_access)
    assert payload is not None
    assert payload["user_id"] == user["id"]


def test_refresh_endpoint_rejects_access_token(client):
    """用 access token 调 /refresh 应失败 — refresh 接口只接 refresh"""
    user = _make_user("refresh-reject-access@example.com")
    access = create_access_token(user["id"], user["email"], "user")
    r = client.post("/api/auth/refresh", json={"refresh_token": access})
    assert r.status_code == 401


def test_refresh_endpoint_rejects_garbage(client):
    r = client.post("/api/auth/refresh", json={"refresh_token": "not-a-real-jwt"})
    assert r.status_code == 401


def test_invalidate_user_also_kills_refresh_token():
    """改密码 / force-logout 后 refresh 也应失效(用户级吊销覆盖 refresh)"""
    user = _make_user("refresh-invalidated@example.com")
    refresh = create_refresh_token(user["id"], user["email"], "user")
    assert decode_refresh_token(refresh) is not None

    time.sleep(1)
    invalidate_user_tokens(user["id"])
    assert decode_refresh_token(refresh) is None


def test_create_jwt_token_is_alias_for_access(client):
    """老代码用 create_jwt_token,等价于 create_access_token"""
    user = _make_user("refresh-alias@example.com")
    legacy_call = create_jwt_token(user["id"], user["email"], "user")
    payload = pyjwt.decode(legacy_call, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    assert payload["type"] == "access"

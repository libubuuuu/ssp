"""2FA / TOTP 测试 — 之前是测试黑洞(发现于 2026-04-27 服务降权,
pyotp 漏装暴露此处零覆盖)。

覆盖:
- /2fa/status:默认关、启用后开
- /2fa/setup:返回 secret + qr_code base64 + manual_entry
- /2fa/enable:错码 400 / 正码 success / 验完真启用
- /2fa/disable:未启用 400 / 错码 400 / 正码关闭
- /login:启用 2FA 的用户必须 totp_code,错码拒,正码过
"""
import pyotp
import pytest

from tests.conftest import _register, _auth


def _now_code(secret: str) -> str:
    return pyotp.TOTP(secret).now()


def _setup_2fa(client, token: str) -> str:
    """走 setup → enable 完整流程,返回 secret"""
    r = client.post("/api/auth/2fa/setup", headers=_auth(token))
    assert r.status_code == 200, r.text
    secret = r.json()["secret"]
    code = _now_code(secret)
    r = client.post(
        "/api/auth/2fa/enable",
        json={"secret": secret, "code": code},
        headers=_auth(token),
    )
    assert r.status_code == 200, r.text
    return secret


def test_2fa_status_default_off(client):
    token, _ = _register(client, "2fa-default@example.com")
    r = client.get("/api/auth/2fa/status", headers=_auth(token))
    assert r.status_code == 200
    assert r.json() == {"enabled": False}


def test_2fa_setup_returns_secret_and_qr(client):
    token, _ = _register(client, "2fa-setup@example.com")
    r = client.post("/api/auth/2fa/setup", headers=_auth(token))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["secret"]  # 非空
    assert body["qr_code"].startswith("data:image/png;base64,")
    assert body["manual_entry"] == body["secret"]


def test_2fa_enable_rejects_wrong_code(client):
    token, _ = _register(client, "2fa-wrong@example.com")
    r = client.post("/api/auth/2fa/setup", headers=_auth(token))
    secret = r.json()["secret"]
    r = client.post(
        "/api/auth/2fa/enable",
        json={"secret": secret, "code": "000000"},  # 几乎不可能正确
        headers=_auth(token),
    )
    assert r.status_code == 400


def test_2fa_enable_then_status_on(client):
    token, _ = _register(client, "2fa-enable@example.com")
    _setup_2fa(client, token)

    r = client.get("/api/auth/2fa/status", headers=_auth(token))
    assert r.json() == {"enabled": True}


def test_2fa_disable_rejects_when_not_enabled(client):
    """没启用 2FA 直接 disable → 400(防止误操作)"""
    token, _ = _register(client, "2fa-not-enabled@example.com")
    r = client.post(
        "/api/auth/2fa/disable",
        json={"code": "123456"},
        headers=_auth(token),
    )
    assert r.status_code == 400


def test_2fa_disable_with_wrong_code_rejected(client):
    token, _ = _register(client, "2fa-disable-wrong@example.com")
    _setup_2fa(client, token)

    r = client.post(
        "/api/auth/2fa/disable",
        json={"code": "000000"},
        headers=_auth(token),
    )
    assert r.status_code == 400


def test_2fa_disable_with_correct_code_works(client):
    token, _ = _register(client, "2fa-disable-ok@example.com")
    secret = _setup_2fa(client, token)

    r = client.post(
        "/api/auth/2fa/disable",
        json={"code": _now_code(secret)},
        headers=_auth(token),
    )
    assert r.status_code == 200

    r = client.get("/api/auth/2fa/status", headers=_auth(token))
    assert r.json() == {"enabled": False}


def test_login_requires_totp_when_2fa_enabled(client):
    """启用 2FA 的用户登录必须带 totp_code"""
    email = "2fa-login-needs@example.com"
    token, _ = _register(client, email)
    _setup_2fa(client, token)

    r = client.post(
        "/api/auth/login",
        json={"email": email, "password": "secret123"},
    )
    assert r.status_code == 401
    detail = r.json()["detail"]
    # detail 是个 dict {"need_2fa": True, "message": ...}
    assert isinstance(detail, dict) and detail.get("need_2fa") is True


def test_login_rejects_wrong_totp(client):
    email = "2fa-login-wrong@example.com"
    token, _ = _register(client, email)
    _setup_2fa(client, token)

    r = client.post(
        "/api/auth/login",
        json={"email": email, "password": "secret123", "totp_code": "000000"},
    )
    assert r.status_code == 401


def test_login_accepts_correct_totp(client):
    email = "2fa-login-ok@example.com"
    token, _ = _register(client, email)
    secret = _setup_2fa(client, token)

    r = client.post(
        "/api/auth/login",
        json={"email": email, "password": "secret123", "totp_code": _now_code(secret)},
    )
    assert r.status_code == 200, r.text
    assert "access_token" in r.json()

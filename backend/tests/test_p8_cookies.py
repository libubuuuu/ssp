"""P8 阶段 1:Cookie 双轨测试

覆盖:
- login / register 成功后 set 两个 cookie(access + refresh)
- get_current_user:cookie 优先 / Authorization header fallback / 都没 → 401
- /api/auth/refresh 从 cookie 读 refresh,也从 body 兜底
- /api/auth/logout 清 cookie
- 双轨期间老前端继续工作(只用 header)
"""
import time as _time

from app.api.auth import ACCESS_COOKIE_NAME, REFRESH_COOKIE_NAME


def _put_code(email: str, code: str = "999999"):
    from app.api import auth as auth_module
    auth_module._EMAIL_CODES[email] = {
        "code": code,
        "expires_at": _time.time() + 300,
        "sent_at": _time.time(),
        "purpose": "register",
    }


def _register_keep_cookies(client, email: str):
    """注册不清 cookie(给本文件用,因为要测 cookie 持久行为)"""
    _put_code(email)
    r = client.post("/api/auth/register", json={
        "email": email, "password": "secret123", "code": "999999",
    })
    assert r.status_code == 200, r.text
    return r.json()["access_token"], r.json()["user"]


# === register 成功 set cookie ===

def test_register_sets_both_cookies(client):
    _put_code("cookie-reg@example.com")
    r = client.post("/api/auth/register", json={
        "email": "cookie-reg@example.com",
        "password": "secret123",
        "code": "999999",
    })
    assert r.status_code == 200
    cookies = r.cookies
    assert ACCESS_COOKIE_NAME in cookies
    assert REFRESH_COOKIE_NAME in cookies
    assert len(cookies[ACCESS_COOKIE_NAME]) > 20  # JWT 至少这么长
    assert cookies[ACCESS_COOKIE_NAME] == r.json()["access_token"]
    # body 也仍含 token(双轨)
    assert "access_token" in r.json()
    assert "refresh_token" in r.json()


# === login 成功 set cookie ===

def test_login_sets_both_cookies(client, register):
    register(client, "cookie-login@example.com", password="my-pw-1")
    r = client.post("/api/auth/login", json={
        "email": "cookie-login@example.com", "password": "my-pw-1"
    })
    assert r.status_code == 200
    assert ACCESS_COOKIE_NAME in r.cookies
    assert REFRESH_COOKIE_NAME in r.cookies


# === get_current_user 双轨 ===

def test_me_reads_from_cookie(client):
    """注册后,client.cookies 里有 access_token,/me 不传 Authorization 也能拿到"""
    _register_keep_cookies(client, "cookie-me@example.com")
    # client.cookies 由 register 的响应自动 set,后续请求自动带
    r = client.get("/api/auth/me")  # 注意:没带 Authorization
    assert r.status_code == 200
    assert r.json()["email"] == "cookie-me@example.com"


def test_me_reads_from_header_when_no_cookie(client, register, auth_header):
    """老前端用 Authorization,没 cookie → 仍能用"""
    token, user = register(client, "header-me@example.com")
    # _register 已经清过 cookies(P8 行为),这里再保险一次
    client.cookies.clear()
    r = client.get("/api/auth/me", headers=auth_header(token))
    assert r.status_code == 200
    assert r.json()["email"] == "header-me@example.com"


def test_me_cookie_takes_priority_over_header(client):
    """cookie + header 都给 → cookie 优先(避免老 header 残留导致用错身份)"""
    # 注册第一个用户拿 token
    token_a, _ = _register_keep_cookies(client, "cookie-prio-a@example.com")
    client.cookies.clear()
    # 注册第二个用户,这次 cookie 留下
    token_b, _ = _register_keep_cookies(client, "cookie-prio-b@example.com")
    # 此时 client.cookies 是 user_b 的;header 用 user_a 的 token
    r = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token_a}"})
    # cookie 优先 → 应当是 user_b
    assert r.json()["email"] == "cookie-prio-b@example.com"


def test_me_unauthenticated(client):
    """都没 → 401"""
    r = client.get("/api/auth/me")
    assert r.status_code == 401


def test_me_bad_header_format(client):
    r = client.get("/api/auth/me", headers={"Authorization": "Token xyz"})  # 不是 Bearer
    assert r.status_code == 401


# === refresh ===

def test_refresh_from_cookie(client):
    """没传 body refresh_token,从 cookie 读"""
    _register_keep_cookies(client, "refresh-cookie@example.com")
    # client.cookies 里有 refresh_token,直接调 refresh
    r = client.post("/api/auth/refresh", json={})  # 空 body
    assert r.status_code == 200, r.text
    assert "access_token" in r.json()
    # 新 access 也写到 cookie
    assert ACCESS_COOKIE_NAME in r.cookies


def test_refresh_from_body(client):
    """老前端传 body refresh_token → 仍工作"""
    _register_keep_cookies(client, "refresh-body@example.com")
    refresh = client.cookies[REFRESH_COOKIE_NAME]  # 拿出来用 body 形式传
    client.cookies.clear()
    r = client.post("/api/auth/refresh", json={"refresh_token": refresh})
    assert r.status_code == 200, r.text


def test_refresh_no_token_fails(client):
    client.cookies.clear()
    r = client.post("/api/auth/refresh", json={})
    assert r.status_code == 401


# === logout ===

def test_logout_clears_cookies(client):
    _register_keep_cookies(client, "logout@example.com")
    assert ACCESS_COOKIE_NAME in client.cookies

    r = client.post("/api/auth/logout")
    assert r.status_code == 200, r.text
    # 直接调 /me 应该 401(cookie 已清)
    r2 = client.get("/api/auth/me")
    assert r2.status_code == 401


def test_logout_requires_auth(client):
    """没登录直接调 logout → 401"""
    client.cookies.clear()
    r = client.post("/api/auth/logout")
    assert r.status_code == 401


# === 红色洞修复:/login-by-code 也要 set cookie ===

def test_login_by_code_sets_cookies_for_existing_user(client, register):
    """已存在用户用邮箱码登录 → 也 set cookie"""
    # 先注册创建用户
    register(client, "lbc-existing@example.com")
    client.cookies.clear()

    # 注入 login code(login-by-code 是登录,不是注册)
    from app.api import auth as auth_module
    auth_module._EMAIL_CODES["lbc-existing@example.com"] = {
        "code": "888888",
        "expires_at": _time.time() + 300,
        "sent_at": _time.time(),
        "purpose": "login",
    }

    r = client.post("/api/auth/login-by-code", json={
        "email": "lbc-existing@example.com",
        "code": "888888",
    })
    assert r.status_code == 200, r.text
    assert ACCESS_COOKIE_NAME in r.cookies
    assert REFRESH_COOKIE_NAME in r.cookies


def test_change_password_set_new_cookies_seamless_login(client, register, auth_header):
    """改密后本设备无缝续登:新 cookie 已 set,/me 立即可用"""
    token, user = register(client, "cp-seamless@example.com", password="old-pass")
    h = auth_header(token)

    r = client.post(
        "/api/auth/change-password",
        json={"current_password": "old-pass", "new_password": "new-pass-1"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    # 新 cookie 已 set
    assert ACCESS_COOKIE_NAME in r.cookies
    assert REFRESH_COOKIE_NAME in r.cookies

    # 旧 token 已失效(invalidate_user_tokens)— 用旧 header 应当 401
    client.cookies.clear()
    r_old = client.get("/api/auth/me", headers=h)
    assert r_old.status_code == 401, "旧 token 必须失效"


def test_change_password_invalidates_old_tokens(client, register, auth_header):
    """关键:改密后旧 access token 不能再用(防密码泄漏后老 token 残留)"""
    token, user = register(client, "cp-inv@example.com", password="old-pass-2")
    h = auth_header(token)

    # 改密成功
    r = client.post(
        "/api/auth/change-password",
        json={"current_password": "old-pass-2", "new_password": "new-pass-2"},
        headers=h,
    )
    assert r.status_code == 200

    # 旧 token 立刻失效
    client.cookies.clear()
    r_me = client.get("/api/auth/me", headers=h)
    assert r_me.status_code == 401


def test_login_by_code_auto_registers_with_INITIAL_CREDITS(client):
    """新邮箱用 login-by-code 自动注册 → credits = INITIAL_CREDITS(10),不是硬编码 10"""
    from app.api import auth as auth_module
    from app.services.auth import INITIAL_CREDITS

    auth_module._EMAIL_CODES["lbc-newbie@example.com"] = {
        "code": "777777",
        "expires_at": _time.time() + 300,
        "sent_at": _time.time(),
        "purpose": "login",
    }
    r = client.post("/api/auth/login-by-code", json={
        "email": "lbc-newbie@example.com",
        "code": "777777",
    })
    assert r.status_code == 200, r.text
    assert r.json()["user"]["credits"] == INITIAL_CREDITS
    # cookie 也要 set
    assert ACCESS_COOKIE_NAME in r.cookies

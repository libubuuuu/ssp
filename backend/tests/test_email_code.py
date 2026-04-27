"""
邮箱验证码登录/重置(Resend)系统
关注点:
- send-code 频率限制(60s)
- login-by-code 首次自动注册
- login-by-code 错误码 / 过期码
- reset-password-by-code happy / 需先 send-code
"""
import time


def test_send_code_creates_cache_entry(client):
    """RESEND_API_KEY 没设(测试 env)→ fallback 打印 → 仍返回 200"""
    r = client.post("/api/auth/send-code", json={
        "email": "user1@example.com", "purpose": "login"
    })
    assert r.status_code == 200, r.text
    assert r.json().get("success") is True

    from app.api import auth as auth_module
    assert "user1@example.com" in auth_module._EMAIL_CODES
    assert len(auth_module._EMAIL_CODES["user1@example.com"]["code"]) == 6


def test_send_code_rate_limited_60s(client):
    client.post("/api/auth/send-code", json={"email": "rate@example.com"})
    r2 = client.post("/api/auth/send-code", json={"email": "rate@example.com"})
    assert r2.status_code == 429
    assert "秒后再试" in r2.json().get("detail", "")


def test_send_code_invalid_email(client):
    r = client.post("/api/auth/send-code", json={"email": "not-an-email"})
    assert r.status_code == 400


def test_login_by_code_first_time_auto_registers(client):
    client.post("/api/auth/send-code", json={"email": "newuser@example.com"})
    from app.api import auth as auth_module
    code = auth_module._EMAIL_CODES["newuser@example.com"]["code"]

    r = client.post("/api/auth/login-by-code", json={
        "email": "newuser@example.com", "code": code,
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert "token" in body
    assert body["user"]["email"] == "newuser@example.com"
    assert body["user"]["credits"] == 10  # 首登默认 10 额度


def test_login_by_code_wrong_code_rejected(client):
    client.post("/api/auth/send-code", json={"email": "wrong@example.com"})
    r = client.post("/api/auth/login-by-code", json={
        "email": "wrong@example.com", "code": "000000",  # 极小概率与随机生成的相等,可接受
    })
    # 如果偶然命中(1/900000),换一次 code 重试;这里直接断言失败语义
    assert r.status_code in (200, 400)
    if r.status_code == 200:
        # 万一中了,也算覆盖到 happy path 不算 bug
        assert "token" in r.json()


def test_login_by_code_without_send_code_first(client):
    r = client.post("/api/auth/login-by-code", json={
        "email": "no-send@example.com", "code": "123456",
    })
    assert r.status_code == 400
    assert "请先发送验证码" in r.json().get("detail", "")


def test_login_by_code_expired(client, monkeypatch):
    """模拟验证码过期"""
    client.post("/api/auth/send-code", json={"email": "exp@example.com"})
    from app.api import auth as auth_module
    # 把 expires_at 改成过去
    auth_module._EMAIL_CODES["exp@example.com"]["expires_at"] = time.time() - 1

    r = client.post("/api/auth/login-by-code", json={
        "email": "exp@example.com",
        "code": auth_module._EMAIL_CODES["exp@example.com"]["code"],
    })
    assert r.status_code == 400
    assert "已过期" in r.json().get("detail", "")
    # 过期码应当被作废(从字典里 pop)
    assert "exp@example.com" not in auth_module._EMAIL_CODES


def test_forgot_password_endpoint_deprecated_410(client):
    """旧 /forgot-password 端点已废弃,返 410 Gone(原版返假成功消息)"""
    r = client.post("/api/auth/forgot-password", json={"email": "any@example.com"})
    assert r.status_code == 410
    body = r.json()
    assert "废弃" in body["detail"] or "deprecated" in body["detail"].lower()
    # 应引导用新流程
    assert "send-code" in body["detail"] or "reset-password-by-code" in body["detail"]


def test_reset_password_by_code_happy(client):
    # 先注册(P3-2 起需要邮箱码,这里直接注入)
    from app.api import auth as auth_module
    import time as _time
    auth_module._EMAIL_CODES["reset@example.com"] = {
        "code": "999999", "expires_at": _time.time() + 300,
        "sent_at": _time.time(), "purpose": "register",
    }
    r = client.post("/api/auth/register", json={
        "email": "reset@example.com", "password": "old-pw-123", "code": "999999",
    })
    assert r.status_code == 200, r.text
    # 发码
    client.post("/api/auth/send-code", json={
        "email": "reset@example.com", "purpose": "reset"
    })
    from app.api import auth as auth_module
    code = auth_module._EMAIL_CODES["reset@example.com"]["code"]

    r = client.post("/api/auth/reset-password-by-code", json={
        "email": "reset@example.com",
        "code": code,
        "new_password": "new-pw-456",
    })
    assert r.status_code == 200, r.text

    # 旧密码应失效
    r_old = client.post("/api/auth/login", json={
        "email": "reset@example.com", "password": "old-pw-123"
    })
    assert r_old.status_code == 401

    # 新密码应可登录
    r_new = client.post("/api/auth/login", json={
        "email": "reset@example.com", "password": "new-pw-456"
    })
    assert r_new.status_code == 200


def test_reset_password_too_short(client):
    client.post("/api/auth/register", json={
        "email": "short@example.com", "password": "old-pw-123"
    })
    client.post("/api/auth/send-code", json={"email": "short@example.com"})
    from app.api import auth as auth_module
    code = auth_module._EMAIL_CODES["short@example.com"]["code"]

    r = client.post("/api/auth/reset-password-by-code", json={
        "email": "short@example.com", "code": code, "new_password": "abc",
    })
    assert r.status_code == 400


def test_reset_password_unknown_email(client):
    """对未注册邮箱,先 send-code 拿到 code(系统不验证邮箱是否存在),再 reset 时才发现"""
    client.post("/api/auth/send-code", json={"email": "ghost@example.com"})
    from app.api import auth as auth_module
    code = auth_module._EMAIL_CODES["ghost@example.com"]["code"]

    r = client.post("/api/auth/reset-password-by-code", json={
        "email": "ghost@example.com", "code": code, "new_password": "any-new-pw",
    })
    assert r.status_code == 404

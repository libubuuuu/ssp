"""
经典邮箱密码登录路径
- 注册 happy path / 重复邮箱 / 弱密码 / P3-2 邮箱码缺失或错误
- 登录 happy path / 错密码 / 不存在的用户
- /me 携带 token / 不带 token
"""
import time as _time


def _put_code(email: str, code: str = "999999"):
    """注入一个有效邮箱码到 _EMAIL_CODES 内存表"""
    from app.api import auth as auth_module
    auth_module._EMAIL_CODES[email] = {
        "code": code,
        "expires_at": _time.time() + 300,
        "sent_at": _time.time(),
        "purpose": "register",
    }


def test_register_happy_path(client):
    _put_code("alice@example.com")
    r = client.post("/api/auth/register", json={
        "email": "alice@example.com",
        "password": "secret123",
        "name": "Alice",
        "code": "999999",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert "token" in body
    assert body["user"]["email"] == "alice@example.com"
    assert body["user"]["credits"] == 10  # 默认初始额度(P3-1 反羊毛党降到 10)


def test_register_duplicate_email_rejected(client):
    _put_code("dup@example.com")
    payload = {"email": "dup@example.com", "password": "secret123", "code": "999999"}
    r1 = client.post("/api/auth/register", json=payload)
    assert r1.status_code == 200, r1.text
    # 第二次:重新放码,但邮箱已存在
    _put_code("dup@example.com")
    payload["code"] = "999999"
    r2 = client.post("/api/auth/register", json=payload)
    assert r2.status_code == 400


def test_register_weak_password_rejected(client):
    _put_code("weak@example.com")
    r = client.post("/api/auth/register", json={
        "email": "weak@example.com",
        "password": "abc",  # < 6 位被 pydantic Field min_length 拒
        "code": "999999",
    })
    assert r.status_code == 422


def test_register_missing_code_rejected(client):
    """P3-2:注册不带 code → 422 (Pydantic field required)"""
    r = client.post("/api/auth/register", json={
        "email": "nocode@example.com", "password": "secret123",
    })
    assert r.status_code == 422


def test_register_wrong_code_rejected(client):
    """P3-2:错 code → 400 + 不创建用户"""
    _put_code("wrong@example.com", code="111111")
    r = client.post("/api/auth/register", json={
        "email": "wrong@example.com", "password": "secret123", "code": "222222",
    })
    assert r.status_code == 400
    # 用户不应被创建
    from app.services.auth import get_user_by_email
    assert get_user_by_email("wrong@example.com") is None


def test_register_no_code_sent_rejected(client):
    """P3-2:_EMAIL_CODES 没记录 → 400 "请先发送验证码" """
    r = client.post("/api/auth/register", json={
        "email": "ghost@example.com", "password": "secret123", "code": "999999",
    })
    assert r.status_code == 400
    body = r.json()
    assert "发送" in body["detail"] or "验证码" in body["detail"]


def test_register_expired_code_rejected(client):
    """P3-2:已过期的 code → 400"""
    from app.api import auth as auth_module
    auth_module._EMAIL_CODES["expired@example.com"] = {
        "code": "999999",
        "expires_at": _time.time() - 1,  # 已过期
        "sent_at": _time.time() - 600,
        "purpose": "register",
    }
    r = client.post("/api/auth/register", json={
        "email": "expired@example.com", "password": "secret123", "code": "999999",
    })
    assert r.status_code == 400


def test_register_consumes_code_one_shot(client):
    """P3-2:成功注册后 code 立即作废,同一邮箱二次用同样 code 不行"""
    _put_code("oneshot@example.com")
    r1 = client.post("/api/auth/register", json={
        "email": "oneshot@example.com", "password": "secret123", "code": "999999",
    })
    assert r1.status_code == 200
    # 假设这次注册被某种方式失败回退,二次用同 code
    from app.services.auth import get_user_by_email
    # 删掉用户模拟"如果还能再注册"
    from app.database import get_db
    with get_db() as conn:
        conn.execute("DELETE FROM users WHERE email = ?", ("oneshot@example.com",))
        conn.commit()
    # 再调,code 已被作废
    r2 = client.post("/api/auth/register", json={
        "email": "oneshot@example.com", "password": "secret123", "code": "999999",
    })
    assert r2.status_code == 400


def test_login_happy_path(client):
    _put_code("bob@example.com")
    client.post("/api/auth/register", json={
        "email": "bob@example.com", "password": "correct-pw", "code": "999999",
    })
    r = client.post("/api/auth/login", json={
        "email": "bob@example.com", "password": "correct-pw"
    })
    assert r.status_code == 200
    assert "token" in r.json()


def test_login_wrong_password(client):
    _put_code("carol@example.com")
    client.post("/api/auth/register", json={
        "email": "carol@example.com", "password": "correct-pw", "code": "999999",
    })
    r = client.post("/api/auth/login", json={
        "email": "carol@example.com", "password": "WRONG"
    })
    assert r.status_code == 401


def test_login_unknown_user(client):
    r = client.post("/api/auth/login", json={
        "email": "ghost-login@example.com", "password": "whatever"
    })
    assert r.status_code == 401


def test_me_requires_token(client):
    r = client.get("/api/auth/me")
    # 没带 Authorization,应当被拒;用 401 或 403 都接受
    assert r.status_code in (401, 403)


def test_me_with_token(client):
    _put_code("dave@example.com")
    reg = client.post("/api/auth/register", json={
        "email": "dave@example.com", "password": "secret123", "code": "999999",
    }).json()
    token = reg["token"]
    r = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["email"] == "dave@example.com"

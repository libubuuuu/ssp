"""
经典邮箱密码登录路径
- 注册 happy path / 重复邮箱 / 弱密码
- 登录 happy path / 错密码 / 不存在的用户
- /me 携带 token / 不带 token
"""


def test_register_happy_path(client):
    r = client.post("/api/auth/register", json={
        "email": "alice@example.com",
        "password": "secret123",
        "name": "Alice",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert "token" in body
    assert body["user"]["email"] == "alice@example.com"
    assert body["user"]["credits"] == 10  # 默认初始额度(P3-1 反羊毛党降到 10)


def test_register_duplicate_email_rejected(client):
    payload = {"email": "dup@example.com", "password": "secret123"}
    r1 = client.post("/api/auth/register", json=payload)
    assert r1.status_code == 200
    r2 = client.post("/api/auth/register", json=payload)
    assert r2.status_code == 400


def test_register_weak_password_rejected(client):
    r = client.post("/api/auth/register", json={
        "email": "weak@example.com",
        "password": "abc",  # < 6 位被 pydantic Field min_length 拒
    })
    assert r.status_code == 422


def test_login_happy_path(client):
    client.post("/api/auth/register", json={
        "email": "bob@example.com", "password": "correct-pw"
    })
    r = client.post("/api/auth/login", json={
        "email": "bob@example.com", "password": "correct-pw"
    })
    assert r.status_code == 200
    assert "token" in r.json()


def test_login_wrong_password(client):
    client.post("/api/auth/register", json={
        "email": "carol@example.com", "password": "correct-pw"
    })
    r = client.post("/api/auth/login", json={
        "email": "carol@example.com", "password": "WRONG"
    })
    assert r.status_code == 401


def test_login_unknown_user(client):
    r = client.post("/api/auth/login", json={
        "email": "ghost@example.com", "password": "whatever"
    })
    assert r.status_code == 401


def test_me_requires_token(client):
    r = client.get("/api/auth/me")
    # 没带 Authorization,应当被拒;用 401 或 403 都接受
    assert r.status_code in (401, 403)


def test_me_with_token(client):
    reg = client.post("/api/auth/register", json={
        "email": "dave@example.com", "password": "secret123"
    }).json()
    token = reg["token"]
    r = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["email"] == "dave@example.com"

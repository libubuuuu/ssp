"""微信支付 V3 stub 单测 + 端点测试

不真打微信沙箱(用户主导启用前不应该打外部 API),只测:
- 未启用时所有路径 503 / WeChatPayDisabled
- 端点鉴权(create / query 鉴权,notify 无鉴权)
- 端点参数校验(订单不存在 / 非本人 / 状态错)
- AESGCM 解密回调正确(用 stub 数据)
"""
import os
from unittest.mock import patch, AsyncMock
import pytest


@pytest.fixture()
def app_with_wechat(app):
    from app.api import wechat_pay as wp
    if not any(str(r.path).startswith("/api/wechat-pay") for r in app.routes):
        app.include_router(wp.router, prefix="/api/wechat-pay")
    return app


@pytest.fixture()
def client_w(app_with_wechat):
    from fastapi.testclient import TestClient
    return TestClient(app_with_wechat)


def _create_order(client, token: str, amount: int = 100, price: float = 9.9) -> str:
    """直接 INSERT credit_orders,返回 order_id"""
    import uuid
    from app.database import get_db
    order_id = str(uuid.uuid4())
    # 从 token 解 user_id(用 /api/auth/me)
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"}).json()
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO credit_orders (id, user_id, amount, price, status) "
            "VALUES (?, ?, ?, ?, 'pending')",
            (order_id, me["id"], amount, price),
        )
        conn.commit()
    return order_id


# === 未启用时 503 ===

def test_create_returns_503_when_disabled(client_w, register, auth_header):
    """默认 WECHAT_PAY_ENABLED=false → 503"""
    token, _ = register(client_w, "wp-disabled@example.com")
    order_id = _create_order(client_w, token)
    r = client_w.post(f"/api/wechat-pay/create/{order_id}", headers=auth_header(token))
    assert r.status_code == 503


def test_query_returns_503_when_disabled(client_w, register, auth_header):
    token, _ = register(client_w, "wp-q-disabled@example.com")
    order_id = _create_order(client_w, token)
    r = client_w.get(f"/api/wechat-pay/query/{order_id}", headers=auth_header(token))
    assert r.status_code == 503


# === 鉴权 ===

def test_create_unauthenticated_401(client_w):
    r = client_w.post("/api/wechat-pay/create/anyid")
    assert r.status_code == 401


def test_query_unauthenticated_401(client_w):
    r = client_w.get("/api/wechat-pay/query/anyid")
    assert r.status_code == 401


def test_notify_no_auth_required(client_w):
    """notify 无鉴权(微信回调,通过签名校验身份)— 但默认未启用 503/解密失败"""
    r = client_w.post("/api/wechat-pay/notify", json={})
    # 空 body 或非 SUCCESS event → 200 ignore;有 event 但 disabled → 503
    assert r.status_code in (200, 400, 503)


# === 订单归属 / 状态 ===

def test_create_other_user_order_403(client_w, register, auth_header):
    a_token, _ = register(client_w, "wp-a@example.com")
    b_token, _ = register(client_w, "wp-b@example.com")
    a_order = _create_order(client_w, a_token)
    r = client_w.post(f"/api/wechat-pay/create/{a_order}", headers=auth_header(b_token))
    assert r.status_code == 403


def test_create_nonexistent_order_404(client_w, register, auth_header):
    token, _ = register(client_w, "wp-404@example.com")
    r = client_w.post("/api/wechat-pay/create/no-such-id", headers=auth_header(token))
    assert r.status_code == 404


def test_create_already_paid_400(client_w, register, auth_header):
    token, _ = register(client_w, "wp-paid@example.com")
    order_id = _create_order(client_w, token)
    # 直接 SQL 标 paid
    from app.database import get_db
    with get_db() as conn:
        conn.execute("UPDATE credit_orders SET status = 'paid' WHERE id = ?", (order_id,))
        conn.commit()
    r = client_w.post(f"/api/wechat-pay/create/{order_id}", headers=auth_header(token))
    assert r.status_code == 400


# === query 本地 paid 短路 ===

def test_query_local_paid_returns_success_without_wechat(client_w, register, auth_header):
    """本地已 paid 直接返,不打微信 API → 不需要启用"""
    token, _ = register(client_w, "wp-q-paid@example.com")
    order_id = _create_order(client_w, token)
    from app.database import get_db
    with get_db() as conn:
        conn.execute("UPDATE credit_orders SET status = 'paid' WHERE id = ?", (order_id,))
        conn.commit()
    r = client_w.get(f"/api/wechat-pay/query/{order_id}", headers=auth_header(token))
    assert r.status_code == 200
    body = r.json()
    assert body["trade_state"] == "SUCCESS"
    assert body["local_status"] == "paid"


# === AESGCM 解密回调(纯单元) ===

def test_decrypt_resource_with_stub_key():
    """AESGCM 解密用真 32 字节密钥 + 真加密数据,验证 round-trip"""
    import base64
    import json
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    key = b"x" * 32
    aesgcm = AESGCM(key)
    nonce = b"123456789012"  # 12 字节
    associated = b"transaction"
    plaintext = json.dumps({
        "trade_state": "SUCCESS",
        "out_trade_no": "test-order-123",
        "amount": {"total": 100},
    }).encode()
    ciphertext = aesgcm.encrypt(nonce, plaintext, associated)
    ciphertext_b64 = base64.b64encode(ciphertext).decode()

    # 临时启用 + 设密钥
    from app.config import get_settings
    s = get_settings()
    orig_enabled = s.WECHAT_PAY_ENABLED
    orig_key = s.WECHAT_PAY_API_V3_KEY
    s.WECHAT_PAY_ENABLED = True
    s.WECHAT_PAY_API_V3_KEY = key.decode()
    # mch_id 等其他字段也得有,_check_enabled 校验
    s.WECHAT_PAY_MCH_ID = "1900000000"
    s.WECHAT_PAY_APP_ID = "wxtestappid"
    s.WECHAT_PAY_CERT_SERIAL = "FAKE_SERIAL"
    s.WECHAT_PAY_PRIVATE_KEY_PATH = "/tmp/fake.pem"
    s.WECHAT_PAY_NOTIFY_URL = "https://test.example.com/notify"

    try:
        from app.services import wechat_pay
        result = wechat_pay.decrypt_notify_resource(
            ciphertext=ciphertext_b64,
            nonce=nonce.decode(),
            associated_data=associated.decode(),
        )
        assert result["trade_state"] == "SUCCESS"
        assert result["out_trade_no"] == "test-order-123"
        assert result["amount"]["total"] == 100
    finally:
        s.WECHAT_PAY_ENABLED = orig_enabled
        s.WECHAT_PAY_API_V3_KEY = orig_key

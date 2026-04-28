"""products CUD 鉴权 + 商家归属测试

五十七续:之前 POST/PUT/DELETE 完全无鉴权 → 任何人匿名增删改任何商家产品
(OWASP Broken Access Control)。

修后:
- 401:未登录
- 403:登录但非 merchant owner / admin
- 404:merchant 不存在 / product 不存在
- 200:owner 自己的 merchant / admin 跨商家
"""
import pytest
from unittest.mock import patch


@pytest.fixture()
def app_with_products(app):
    from app.api import products as products_module
    if not any(str(r.path).startswith("/api/products") for r in app.routes):
        app.include_router(products_module.router, prefix="/api/products")
    return app


@pytest.fixture()
def client_p(app_with_products):
    from fastapi.testclient import TestClient
    return TestClient(app_with_products)


def _create_merchant(user_id: str, name: str = "Test Merchant") -> str:
    """直接 INSERT 一个 merchants 行(没 API),返回 merchant_id"""
    import uuid
    from app.database import get_db
    merchant_id = str(uuid.uuid4())
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO merchants (id, user_id, shop_name) VALUES (?, ?, ?)",
            (merchant_id, user_id, name),
        )
        conn.commit()
    return merchant_id


def _create_product_in_db(merchant_id: str) -> str:
    import uuid
    from datetime import datetime
    from app.database import get_db
    pid = str(uuid.uuid4())
    now = datetime.now().isoformat()
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """INSERT INTO products (id, merchant_id, name, category, gender, price, stock,
                                     is_published, created_at, updated_at)
               VALUES (?, ?, 'Test', 'shirt', 'unisex', 99.0, 10, 0, ?, ?)""",
            (pid, merchant_id, now, now),
        )
        conn.commit()
    return pid


_VALID_PRODUCT = {
    "name": "Test",
    "description": "x",
    "category": "shirt",
    "gender": "unisex",
    "price": 99.0,
    "stock": 10,
}


# === POST /api/products ===

def test_create_product_unauthenticated_401(client_p):
    payload = {**_VALID_PRODUCT, "merchant_id": "any"}
    r = client_p.post("/api/products", json=payload)
    assert r.status_code == 401


def test_create_product_non_owner_returns_403(client_p, register, auth_header):
    """A 是某 merchant owner,B 登录但试图给 A 的 merchant 加产品 → 403"""
    a_token, a_user = register(client_p, "p-owner@example.com")
    b_token, b_user = register(client_p, "p-other@example.com")
    a_merchant = _create_merchant(a_user["id"], "A's Shop")

    payload = {**_VALID_PRODUCT, "merchant_id": a_merchant}
    r = client_p.post("/api/products", json=payload, headers=auth_header(b_token))
    assert r.status_code == 403


def test_create_product_nonexistent_merchant_404(client_p, register, auth_header):
    token, _ = register(client_p, "p-no-mch@example.com")
    payload = {**_VALID_PRODUCT, "merchant_id": "nonexistent_merchant_id"}
    r = client_p.post("/api/products", json=payload, headers=auth_header(token))
    assert r.status_code == 404


def test_create_product_owner_passes(client_p, register, auth_header):
    """owner 给自己的 merchant 加产品 → 200"""
    token, user = register(client_p, "p-self@example.com")
    merchant_id = _create_merchant(user["id"])
    payload = {**_VALID_PRODUCT, "merchant_id": merchant_id}
    r = client_p.post("/api/products", json=payload, headers=auth_header(token))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["merchant_id"] == merchant_id
    assert body["name"] == "Test"


def test_create_product_admin_passes_cross_merchant(client_p, register, auth_header, set_role):
    """admin 给别人的 merchant 加产品 → 200(运营场景)"""
    a_token, a_user = register(client_p, "p-mch-owner@example.com")
    admin_token, admin_user = register(client_p, "p-admin@example.com")
    set_role(admin_user["id"], "admin")
    a_merchant = _create_merchant(a_user["id"])

    # admin 用刚改的 role,需要重新登录拿新 token... 但 register 已返回 token,
    # role 改在 DB 里,get_current_user 每次重查 DB 拿到的 role 是新的。验证下:
    payload = {**_VALID_PRODUCT, "merchant_id": a_merchant}
    r = client_p.post("/api/products", json=payload, headers=auth_header(admin_token))
    assert r.status_code == 200, r.text


# === PUT /api/products/{id} ===

def test_update_product_unauthenticated_401(client_p):
    r = client_p.put("/api/products/any_id", json={"name": "x"})
    assert r.status_code == 401


def test_update_product_nonexistent_404(client_p, register, auth_header):
    token, _ = register(client_p, "p-up-404@example.com")
    r = client_p.put("/api/products/no_such_id", json={"name": "x"}, headers=auth_header(token))
    assert r.status_code == 404


def test_update_product_non_owner_403(client_p, register, auth_header):
    a_token, a_user = register(client_p, "p-up-owner@example.com")
    b_token, _ = register(client_p, "p-up-other@example.com")
    a_merchant = _create_merchant(a_user["id"])
    pid = _create_product_in_db(a_merchant)

    r = client_p.put(f"/api/products/{pid}", json={"name": "hacked"}, headers=auth_header(b_token))
    assert r.status_code == 403


def test_update_product_owner_passes(client_p, register, auth_header):
    token, user = register(client_p, "p-up-self@example.com")
    merchant_id = _create_merchant(user["id"])
    pid = _create_product_in_db(merchant_id)

    r = client_p.put(f"/api/products/{pid}", json={"name": "renamed", "price": 199.0},
                     headers=auth_header(token))
    assert r.status_code == 200
    assert r.json()["name"] == "renamed"
    assert r.json()["price"] == 199.0


# === DELETE /api/products/{id} ===

def test_delete_product_unauthenticated_401(client_p):
    r = client_p.delete("/api/products/any_id")
    assert r.status_code == 401


def test_delete_product_non_owner_403(client_p, register, auth_header):
    a_token, a_user = register(client_p, "p-del-owner@example.com")
    b_token, _ = register(client_p, "p-del-other@example.com")
    a_merchant = _create_merchant(a_user["id"])
    pid = _create_product_in_db(a_merchant)

    r = client_p.delete(f"/api/products/{pid}", headers=auth_header(b_token))
    assert r.status_code == 403

    # product 应仍存在
    r2 = client_p.get(f"/api/products/{pid}")
    assert r2.status_code == 200


def test_delete_product_owner_passes(client_p, register, auth_header):
    token, user = register(client_p, "p-del-self@example.com")
    merchant_id = _create_merchant(user["id"])
    pid = _create_product_in_db(merchant_id)

    r = client_p.delete(f"/api/products/{pid}", headers=auth_header(token))
    assert r.status_code == 200
    # 后续 GET 应 404
    r2 = client_p.get(f"/api/products/{pid}")
    assert r2.status_code == 404


# === 公开 GET 端点不受影响 ===

def test_list_products_still_public(client_p):
    """GET /api/products 仍匿名可调(电商展示场景)"""
    r = client_p.get("/api/products")
    assert r.status_code == 200

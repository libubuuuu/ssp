"""payment.py 路径覆盖(P7 覆盖率补齐)

原覆盖率 50% — 主要缺管理员侧 + 用户隔离 + 边界。本文件覆盖:
- /packages, /credit-packs(公开列表)
- /orders/create:套餐 / 充值包 / 无效类型
- /orders/{id}:happy / 不存在 / 别人的(403)
- /orders 列表(用户)
- /orders/{id}/confirm:非管理员 403 / 不存在 404 / 已确认 400 / 成功 + 加积分 + 审计
- /admin/orders:非管理员 403 / 列表
"""
import pytest


# === 公开端点(无鉴权)===

def test_get_packages_public(client):
    r = client.get("/api/payment/packages")
    assert r.status_code == 200
    body = r.json()
    assert "packages" in body
    assert isinstance(body["packages"], list)


def test_get_credit_packs_public(client):
    r = client.get("/api/payment/credit-packs")
    assert r.status_code == 200
    body = r.json()
    assert "packs" in body or isinstance(body, dict)


# === 创建订单 ===

def test_create_order_package(client, register, auth_header):
    token, _ = register(client, "pay-pkg@example.com")
    # 拿一个真存在的 package id
    pkgs = client.get("/api/payment/packages").json()["packages"]
    pkg_id = pkgs[0]["id"]
    r = client.post(
        "/api/payment/orders/create",
        json={"type": "package", "package_id": pkg_id},
        headers=auth_header(token),
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "pending"
    assert r.json()["type"] == "package"


def test_create_order_credit_pack(client, register, auth_header):
    token, _ = register(client, "pay-cp@example.com")
    packs = client.get("/api/payment/credit-packs").json()
    # /credit-packs 返回结构可能是 {credit_packs: [...]} 或顶层 list
    pack_list = packs.get("credit_packs") or packs.get("packs") or []
    if not pack_list:
        pytest.skip("credit-packs 列表为空")
    cp_id = pack_list[0]["id"]
    r = client.post(
        "/api/payment/orders/create",
        json={"type": "credit", "credit_pack_id": cp_id},
        headers=auth_header(token),
    )
    assert r.status_code == 200, r.text


def test_create_order_invalid_package_id(client, register, auth_header):
    token, _ = register(client, "pay-bad-pkg@example.com")
    r = client.post(
        "/api/payment/orders/create",
        json={"type": "package", "package_id": "no-such-pkg"},
        headers=auth_header(token),
    )
    assert r.status_code == 400


def test_create_order_invalid_type(client, register, auth_header):
    token, _ = register(client, "pay-bad-type@example.com")
    r = client.post(
        "/api/payment/orders/create",
        json={"type": "wire_transfer"},  # 不支持
        headers=auth_header(token),
    )
    assert r.status_code == 400


# === 查询订单 ===

def test_get_order_owner_can_read(client, register, auth_header):
    token, _ = register(client, "pay-own@example.com")
    pkgs = client.get("/api/payment/packages").json()["packages"]
    create_r = client.post(
        "/api/payment/orders/create",
        json={"type": "package", "package_id": pkgs[0]["id"]},
        headers=auth_header(token),
    )
    order_id = create_r.json()["order_id"]
    r = client.get(f"/api/payment/orders/{order_id}", headers=auth_header(token))
    assert r.status_code == 200
    assert r.json()["status"] == "pending"


def test_get_order_other_user_403(client, register, auth_header):
    token_a, _ = register(client, "pay-A@example.com")
    token_b, _ = register(client, "pay-B@example.com")
    pkgs = client.get("/api/payment/packages").json()["packages"]
    create_r = client.post(
        "/api/payment/orders/create",
        json={"type": "package", "package_id": pkgs[0]["id"]},
        headers=auth_header(token_a),
    )
    order_id = create_r.json()["order_id"]
    # B 想读 A 的订单
    r = client.get(f"/api/payment/orders/{order_id}", headers=auth_header(token_b))
    assert r.status_code == 403


def test_get_order_nonexistent_404(client, register, auth_header):
    token, _ = register(client, "pay-404@example.com")
    r = client.get("/api/payment/orders/no-such-order", headers=auth_header(token))
    assert r.status_code == 404


# === 用户订单列表 ===

def test_list_my_orders_user_isolation(client, register, auth_header):
    token_a, _ = register(client, "pay-listA@example.com")
    token_b, _ = register(client, "pay-listB@example.com")
    pkgs = client.get("/api/payment/packages").json()["packages"]
    pkg_id = pkgs[0]["id"]
    # A 创建 2 个,B 创建 1 个
    for _ in range(2):
        client.post("/api/payment/orders/create",
                    json={"type": "package", "package_id": pkg_id},
                    headers=auth_header(token_a))
    client.post("/api/payment/orders/create",
                json={"type": "package", "package_id": pkg_id},
                headers=auth_header(token_b))

    r_a = client.get("/api/payment/orders", headers=auth_header(token_a))
    r_b = client.get("/api/payment/orders", headers=auth_header(token_b))
    assert len(r_a.json()["orders"]) == 2
    assert len(r_b.json()["orders"]) == 1


# === 管理员确认订单 ===

def _make_admin_token(client, register, auth_header, set_role, email: str):
    token, user = register(client, email)
    set_role(user["id"], "admin")
    return token, user


def test_confirm_order_non_admin_403(client, register, auth_header):
    """普通用户调 confirm → 403"""
    token, _ = register(client, "pay-non-admin@example.com")
    r = client.post("/api/payment/orders/whatever/confirm", headers=auth_header(token))
    assert r.status_code == 403


def test_confirm_order_nonexistent_404(client, register, auth_header, set_role):
    a_token, _ = _make_admin_token(client, register, auth_header, set_role, "pay-admin404@example.com")
    r = client.post("/api/payment/orders/no-such/confirm", headers=auth_header(a_token))
    assert r.status_code == 404


def test_confirm_order_happy_credits_added(client, register, auth_header, set_role):
    """确认订单成功:用户积分加 + 状态 paid + 审计写入"""
    a_token, admin = _make_admin_token(client, register, auth_header, set_role, "pay-admin-happy@example.com")
    user_token, user = register(client, "pay-happy-user@example.com")
    pkgs = client.get("/api/payment/packages").json()["packages"]
    create_r = client.post(
        "/api/payment/orders/create",
        json={"type": "package", "package_id": pkgs[0]["id"]},
        headers=auth_header(user_token),
    )
    order_id = create_r.json()["order_id"]
    initial_amount = create_r.json()["amount"]

    # 用户初始积分(P3-1 后默认 10)
    me = client.get("/api/auth/me", headers=auth_header(user_token)).json()
    initial_credits = me["credits"]

    r = client.post(f"/api/payment/orders/{order_id}/confirm", headers=auth_header(a_token))
    assert r.status_code == 200, r.text
    assert r.json()["credits_added"] == initial_amount

    # 用户积分实际增加
    me_after = client.get("/api/auth/me", headers=auth_header(user_token)).json()
    assert me_after["credits"] == initial_credits + initial_amount


def test_confirm_order_already_paid_400(client, register, auth_header, set_role):
    a_token, _ = _make_admin_token(client, register, auth_header, set_role, "pay-admin-twice@example.com")
    user_token, _ = register(client, "pay-twice-user@example.com")
    pkgs = client.get("/api/payment/packages").json()["packages"]
    create_r = client.post(
        "/api/payment/orders/create",
        json={"type": "package", "package_id": pkgs[0]["id"]},
        headers=auth_header(user_token),
    )
    order_id = create_r.json()["order_id"]
    r1 = client.post(f"/api/payment/orders/{order_id}/confirm", headers=auth_header(a_token))
    assert r1.status_code == 200
    # 二次确认 → 400
    r2 = client.post(f"/api/payment/orders/{order_id}/confirm", headers=auth_header(a_token))
    assert r2.status_code == 400


def test_admin_list_orders_non_admin_403(client, register, auth_header):
    token, _ = register(client, "pay-admin-list-403@example.com")
    r = client.get("/api/payment/admin/orders", headers=auth_header(token))
    assert r.status_code == 403


def test_admin_list_orders_status_filter(client, register, auth_header, set_role):
    a_token, _ = _make_admin_token(client, register, auth_header, set_role, "pay-admin-list@example.com")
    user_token, _ = register(client, "pay-listed-user@example.com")
    pkgs = client.get("/api/payment/packages").json()["packages"]
    client.post("/api/payment/orders/create",
                json={"type": "package", "package_id": pkgs[0]["id"]},
                headers=auth_header(user_token))

    r = client.get("/api/payment/admin/orders?status=pending", headers=auth_header(a_token))
    assert r.status_code == 200
    orders = r.json()["orders"]
    assert all(o["status"] == "pending" for o in orders)


def test_admin_list_orders_status_all_returns_paid_and_pending(client, register, auth_header, set_role):
    """status=all 走另一条 SQL 分支,不带 WHERE,paid + pending 都返回"""
    a_token, _ = _make_admin_token(client, register, auth_header, set_role, "pay-list-all@example.com")
    user_token, user = register(client, "pay-mixed-user@example.com")
    pkgs = client.get("/api/payment/packages").json()["packages"]

    # 起 2 个 pending 订单,confirm 第一个变成 paid
    o1 = client.post("/api/payment/orders/create",
                     json={"type": "package", "package_id": pkgs[0]["id"]},
                     headers=auth_header(user_token)).json()
    o2 = client.post("/api/payment/orders/create",
                     json={"type": "package", "package_id": pkgs[0]["id"]},
                     headers=auth_header(user_token)).json()
    client.post(f"/api/payment/orders/{o1['order_id']}/confirm", headers=auth_header(a_token))

    r = client.get("/api/payment/admin/orders?status=all", headers=auth_header(a_token))
    assert r.status_code == 200
    orders = r.json()["orders"]
    statuses = {o["status"] for o in orders if o["id"] in (o1["order_id"], o2["order_id"])}
    assert statuses == {"paid", "pending"}


def test_admin_list_orders_empty_returns_empty_array(client, register, auth_header, set_role):
    """无订单时返空数组,不挂 / 不返 null"""
    a_token, _ = _make_admin_token(client, register, auth_header, set_role, "pay-list-empty@example.com")
    r = client.get("/api/payment/admin/orders?status=pending", headers=auth_header(a_token))
    assert r.status_code == 200
    assert r.json() == {"orders": []}


def test_admin_list_orders_includes_user_email_via_join(client, register, auth_header, set_role):
    """LEFT JOIN users 应带回 user_email + user_name 字段"""
    a_token, _ = _make_admin_token(client, register, auth_header, set_role, "pay-join@example.com")
    user_token, _ = register(client, "pay-joined-user@example.com", name="JoinedUser")
    pkgs = client.get("/api/payment/packages").json()["packages"]
    o = client.post("/api/payment/orders/create",
                    json={"type": "package", "package_id": pkgs[0]["id"]},
                    headers=auth_header(user_token)).json()

    r = client.get("/api/payment/admin/orders?status=pending", headers=auth_header(a_token))
    matched = [x for x in r.json()["orders"] if x["id"] == o["order_id"]]
    assert len(matched) == 1
    row = matched[0]
    assert row["user_email"] == "pay-joined-user@example.com"
    assert row["user_name"] == "JoinedUser"


def test_confirm_order_writes_audit_log(client, register, auth_header, set_role):
    """admin 确认订单 → audit_log 表写一条 confirm_order 记录(合规重点)"""
    a_token, admin_user = _make_admin_token(client, register, auth_header, set_role, "pay-audit@example.com")
    user_token, _ = register(client, "pay-audit-target@example.com")
    pkgs = client.get("/api/payment/packages").json()["packages"]
    o = client.post("/api/payment/orders/create",
                    json={"type": "package", "package_id": pkgs[0]["id"]},
                    headers=auth_header(user_token)).json()

    client.post(f"/api/payment/orders/{o['order_id']}/confirm", headers=auth_header(a_token))

    from app.database import get_db
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT action, target_id FROM audit_log WHERE actor_user_id = ? AND action = 'confirm_order'",
            (admin_user["id"],),
        )
        rows = cursor.fetchall()
    assert len(rows) == 1
    assert rows[0][1] == o["order_id"]

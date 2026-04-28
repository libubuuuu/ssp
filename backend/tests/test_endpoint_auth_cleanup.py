"""零散 endpoint 鉴权 cleanup — 五十八续

把 sweep 剩下的低优先级 endpoint 一次性加鉴权:
- /api/content/enhance:纯模板返回,但仍是 attack surface
- /api/image/inpaint:501 stub,加鉴权防扫

公开 endpoint 仍保持 public:
- /api/payment/packages、/credit-packs(price list)
- /api/products(GET 列表 / 详情)
- /api/image/models、/api/avatar/voice/presets(静态展示数据)
"""
import pytest


@pytest.fixture()
def app_with_image_content(app):
    from app.api import image as image_module, content as content_module
    if not any(str(r.path).startswith("/api/image") for r in app.routes):
        app.include_router(image_module.router, prefix="/api/image")
    if not any(str(r.path).startswith("/api/content") for r in app.routes):
        app.include_router(content_module.router, prefix="/api/content")
    return app


@pytest.fixture()
def client_x(app_with_image_content):
    from fastapi.testclient import TestClient
    return TestClient(app_with_image_content)


# === /api/content/enhance ===

def test_enhance_unauthenticated_returns_401(client_x):
    r = client_x.post("/api/content/enhance", json={"prompt": "x"})
    assert r.status_code == 401


def test_enhance_authenticated_returns_template(client_x, register, auth_header):
    """登录用户调返回正常模板 dict"""
    token, _ = register(client_x, "ce-ok@example.com")
    r = client_x.post("/api/content/enhance",
                      json={"prompt": "test product", "style": "advertising"},
                      headers=auth_header(token))
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert "title" in body
    assert "scenes" in body and len(body["scenes"]) > 0


# === /api/image/inpaint(501 stub)===

def test_inpaint_unauthenticated_returns_401(client_x):
    r = client_x.post("/api/image/inpaint", json={"image_url": "x", "mask": "x", "prompt": "x"})
    assert r.status_code == 401


def test_inpaint_authenticated_returns_501_stub(client_x, register, auth_header):
    """登录用户调拿 501(功能未实现)— 鉴权放行后才到 stub 逻辑"""
    token, _ = register(client_x, "ip-ok@example.com")
    r = client_x.post("/api/image/inpaint",
                      json={"image_url": "x", "mask_url": "y", "prompt": "z"},
                      headers=auth_header(token))
    assert r.status_code == 501


# /api/image/models / /api/avatar/voice/presets 等 public 端点不在本文件验证 —
# 它们实现耦合 fal service init,本次改动也没动它们。本文件只验改动的两条新加鉴权。

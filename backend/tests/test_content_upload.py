"""/api/content/upload 鉴权 + size/MIME 守卫测试

五十六续:之前有 Depends(get_current_user) 但无 size guard,
await file.read() 直接读到内存,nginx 上限 500MB → 后端 OOM 攻击面。
upload_guard.read_bounded 接通后:超 10MB → 413,错 MIME → 415。
"""
from unittest.mock import patch, AsyncMock
import pytest


@pytest.fixture()
def app_with_content(app):
    """共用 app 上注册 content 路由"""
    from app.api import content as content_module
    if not any(str(r.path).startswith("/api/content") for r in app.routes):
        app.include_router(content_module.router, prefix="/api/content")
    return app


@pytest.fixture()
def client_c(app_with_content):
    from fastapi.testclient import TestClient
    return TestClient(app_with_content)


def test_upload_unauthenticated_returns_401(client_c):
    files = {"file": ("a.jpg", b"x", "image/jpeg")}
    r = client_c.post("/api/content/upload", files=files)
    assert r.status_code == 401


def test_upload_oversize_returns_413(client_c, register, auth_header):
    """>10MB 拒收 413"""
    token, _ = register(client_c, "ct-big@example.com")
    huge = b"x" * (11 * 1024 * 1024)
    files = {"file": ("big.jpg", huge, "image/jpeg")}
    r = client_c.post("/api/content/upload", files=files, headers=auth_header(token))
    assert r.status_code == 413


def test_upload_wrong_mime_returns_415(client_c, register, auth_header):
    """非白名单 MIME 拒收 415"""
    token, _ = register(client_c, "ct-mime@example.com")
    files = {"file": ("a.bmp", b"BM_fake", "image/bmp")}
    r = client_c.post("/api/content/upload", files=files, headers=auth_header(token))
    assert r.status_code == 415


def test_upload_valid_passes_to_fal(client_c, register, auth_header):
    """合规图片 → 通过 guard,fal_client mock 后返 URL"""
    token, _ = register(client_c, "ct-ok@example.com")
    valid = b"\xff\xd8\xff\xe0" + b"x" * 1024  # JPEG magic + 数据
    files = {"file": ("ok.jpg", valid, "image/jpeg")}
    with patch("fal_client.upload_file_async", new=AsyncMock(return_value="https://fal.media/xxx.jpg")):
        r = client_c.post("/api/content/upload", files=files, headers=auth_header(token))
    assert r.status_code == 200
    body = r.json()
    assert body["url"] == "https://fal.media/xxx.jpg"
    assert body["image_url"] == "https://fal.media/xxx.jpg"

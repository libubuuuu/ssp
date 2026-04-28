"""STS 签发端点测试 — 不真打腾讯云 STS API,mock SDK 调用验证逻辑"""
import pytest
from unittest.mock import patch, MagicMock


@pytest.fixture()
def app_with_storage(app):
    from app.api import storage as storage_module
    if not any(str(r.path).startswith("/api/storage") for r in app.routes):
        app.include_router(storage_module.router, prefix="/api/storage")
    return app


@pytest.fixture()
def client_s(app_with_storage):
    from fastapi.testclient import TestClient
    return TestClient(app_with_storage)


# === 默认未启用 ===

def test_sts_returns_503_when_disabled(client_s, register, auth_header):
    token, _ = register(client_s, "sts-disabled@example.com")
    r = client_s.post("/api/storage/sts", json={"filename": "test.mp4"}, headers=auth_header(token))
    assert r.status_code == 503


def test_sts_unauthenticated_401(client_s):
    r = client_s.post("/api/storage/sts", json={"filename": "test.mp4"})
    assert r.status_code == 401


# === 启用后 ===

def _enable_storage(monkeypatch):
    """临时打开 STORAGE_DIRECT_UPLOAD_ENABLED 给测试用"""
    from app.config import get_settings
    s = get_settings()
    monkeypatch.setattr(s, "STORAGE_DIRECT_UPLOAD_ENABLED", True)
    monkeypatch.setattr(s, "STORAGE_PROVIDER", "tencent_cos")
    monkeypatch.setattr(s, "STORAGE_BUCKET", "ssp-test-1300000000")
    monkeypatch.setattr(s, "STORAGE_REGION", "ap-guangzhou")
    monkeypatch.setattr(s, "STORAGE_SECRET_ID", "AKID-fake")
    monkeypatch.setattr(s, "STORAGE_SECRET_KEY", "secret-fake")


def test_sts_returns_credentials_on_success(client_s, register, auth_header, monkeypatch):
    """启用 + mock STS API → 返凭证 + object_key + public_url"""
    _enable_storage(monkeypatch)
    token, user = register(client_s, "sts-ok@example.com")

    # mock 腾讯云 STS SDK 返一个假的凭证
    fake_resp = MagicMock()
    fake_resp.Credentials.TmpSecretId = "tmp-secret-id"
    fake_resp.Credentials.TmpSecretKey = "tmp-secret-key"
    fake_resp.Credentials.Token = "session-token"
    fake_resp.ExpiredTime = 1730000000

    with patch("tencentcloud.sts.v20180813.sts_client.StsClient") as MockClient:
        MockClient.return_value.GetFederationToken.return_value = fake_resp
        r = client_s.post(
            "/api/storage/sts",
            json={"filename": "video.mp4"},
            headers=auth_header(token),
        )

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["credentials"]["tmpSecretId"] == "tmp-secret-id"
    assert body["credentials"]["tmpSecretKey"] == "tmp-secret-key"
    assert body["credentials"]["sessionToken"] == "session-token"
    assert body["bucket"] == "ssp-test-1300000000"
    assert body["region"] == "ap-guangzhou"
    # object_key 应含 user_id 路径 + filename(清洗后)
    assert user["id"] in body["object_key"]
    assert "video.mp4" in body["object_key"]
    # public_url 域名格式正确
    assert body["public_url"].startswith("https://ssp-test-1300000000.cos.ap-guangzhou.myqcloud.com/")


def test_sts_filename_with_path_traversal_is_sanitized(client_s, register, auth_header, monkeypatch):
    """文件名含 ../ 等危险字符应被清洗"""
    _enable_storage(monkeypatch)
    token, _ = register(client_s, "sts-path@example.com")

    fake_resp = MagicMock()
    fake_resp.Credentials.TmpSecretId = "x"
    fake_resp.Credentials.TmpSecretKey = "x"
    fake_resp.Credentials.Token = "x"
    fake_resp.ExpiredTime = 1730000000

    with patch("tencentcloud.sts.v20180813.sts_client.StsClient") as MockClient:
        MockClient.return_value.GetFederationToken.return_value = fake_resp
        r = client_s.post(
            "/api/storage/sts",
            json={"filename": "../../etc/passwd"},
            headers=auth_header(token),
        )

    assert r.status_code == 200
    body = r.json()
    # 清洗后不应含 ../
    assert "../" not in body["object_key"]


def test_sts_invalid_bucket_format_returns_503(client_s, register, auth_header, monkeypatch):
    """STORAGE_BUCKET 缺 -appid 后缀 → 配置错 503"""
    _enable_storage(monkeypatch)
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "STORAGE_BUCKET", "no-appid-bucket")
    token, _ = register(client_s, "sts-badbucket@example.com")

    r = client_s.post("/api/storage/sts", json={"filename": "x.mp4"}, headers=auth_header(token))
    assert r.status_code == 503


def test_sts_filename_required_validation(client_s, register, auth_header, monkeypatch):
    """空 filename → 422"""
    _enable_storage(monkeypatch)
    token, _ = register(client_s, "sts-empty@example.com")
    r = client_s.post("/api/storage/sts", json={"filename": ""}, headers=auth_header(token))
    assert r.status_code == 422

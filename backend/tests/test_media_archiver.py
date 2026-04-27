"""media_archiver 测试

策略:用 monkeypatch 替换 httpx.AsyncClient,模拟成功 / 404 / 超大文件 / 异常,
不真上 fal.media 网络。
"""
import asyncio
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services import media_archiver as ma


@pytest.fixture
def tmp_uploads(tmp_path, monkeypatch):
    """每个测试一份独立 uploads 目录,不污染 /opt/ssp/uploads"""
    monkeypatch.setattr(ma, "UPLOADS_ROOT", tmp_path / "uploads")
    monkeypatch.setattr(ma, "PUBLIC_BASE_URL", "https://example.com/uploads")
    return tmp_path / "uploads"


def _mock_stream_response(status_code: int, content_chunks: list[bytes], content_type: str = "image/png"):
    """构造一个支持 async with 的 mock httpx response"""
    ctx = MagicMock()
    resp = MagicMock()
    resp.status_code = status_code
    resp.headers = {"content-type": content_type}

    async def _aiter(chunk_size=64 * 1024):
        for c in content_chunks:
            yield c

    resp.aiter_bytes = _aiter
    ctx.__aenter__ = AsyncMock(return_value=resp)
    ctx.__aexit__ = AsyncMock(return_value=None)
    return ctx


def _mock_client(stream_ctx):
    client_ctx = MagicMock()
    client = MagicMock()
    client.stream = MagicMock(return_value=stream_ctx)
    client_ctx.__aenter__ = AsyncMock(return_value=client)
    client_ctx.__aexit__ = AsyncMock(return_value=None)
    return client_ctx


def test_empty_url_returns_unchanged(tmp_uploads):
    assert asyncio.run(ma.archive_url("", "uid-1")) == ""
    assert asyncio.run(ma.archive_url(None, "uid-1")) is None


def test_non_http_url_unchanged(tmp_uploads):
    assert asyncio.run(ma.archive_url("data:image/png;base64,xxx", "uid-1")).startswith("data:")
    assert asyncio.run(ma.archive_url("/local/path.png", "uid-1")) == "/local/path.png"


def test_already_archived_url_unchanged(tmp_uploads):
    public = "https://example.com/uploads/uid-1/2026-04/abc.png"
    assert asyncio.run(ma.archive_url(public, "uid-1")) == public


def test_successful_download(tmp_uploads, monkeypatch):
    """200 + 100KB 内容 → 写到本地 + 返回新 URL"""
    chunks = [b"\x89PNG" + b"x" * 100000]
    stream = _mock_stream_response(200, chunks, "image/png")
    monkeypatch.setattr(ma.httpx, "AsyncClient", MagicMock(return_value=_mock_client(stream)))

    new_url = asyncio.run(ma.archive_url("https://fal.media/files/abc.png", "user-aaa", "image"))

    assert new_url.startswith("https://example.com/uploads/user-aaa/")
    assert new_url.endswith(".png")
    # 文件真的写到了本地
    rel_path = new_url.replace("https://example.com/uploads/", "")
    assert (tmp_uploads / rel_path).exists()
    assert (tmp_uploads / rel_path).stat().st_size > 100000


def test_404_falls_back_to_original(tmp_uploads, monkeypatch):
    stream = _mock_stream_response(404, [b""])
    monkeypatch.setattr(ma.httpx, "AsyncClient", MagicMock(return_value=_mock_client(stream)))

    original = "https://fal.media/files/missing.png"
    new = asyncio.run(ma.archive_url(original, "uid", "image"))
    assert new == original  # fallback 原 URL


def test_oversized_file_falls_back(tmp_uploads, monkeypatch):
    """超 100MB 上限 → 删半量文件 + fallback"""
    monkeypatch.setattr(ma, "MAX_BYTES", 1024)  # 临时降到 1KB 触发
    chunks = [b"x" * 800, b"x" * 800]  # 总 1600B > 1024B
    stream = _mock_stream_response(200, chunks, "video/mp4")
    monkeypatch.setattr(ma.httpx, "AsyncClient", MagicMock(return_value=_mock_client(stream)))

    original = "https://fal.media/big.mp4"
    new = asyncio.run(ma.archive_url(original, "uid", "video"))
    assert new == original  # fallback


def test_http_exception_falls_back(tmp_uploads, monkeypatch):
    """httpx 抛异常 → fallback 不爆"""
    bad_client_ctx = MagicMock()
    bad_client_ctx.__aenter__ = AsyncMock(side_effect=ma.httpx.ConnectError("boom"))
    bad_client_ctx.__aexit__ = AsyncMock(return_value=None)
    monkeypatch.setattr(ma.httpx, "AsyncClient", MagicMock(return_value=bad_client_ctx))

    original = "https://fal.media/x.png"
    new = asyncio.run(ma.archive_url(original, "uid", "image"))
    assert new == original


def test_path_traversal_safe_user_id(tmp_uploads, monkeypatch):
    """user_id 含路径穿越字符 → 被洗掉,不会写到 ../something"""
    chunks = [b"x" * 100]
    stream = _mock_stream_response(200, chunks, "image/jpeg")
    monkeypatch.setattr(ma.httpx, "AsyncClient", MagicMock(return_value=_mock_client(stream)))

    new = asyncio.run(ma.archive_url("https://fal.media/x.jpg", "../../../etc", "image"))
    assert new.startswith("https://example.com/uploads/")
    # 不能出现 ".." 字面
    assert "../" not in new
    assert "etc" in new or "_etc" in new  # 被洗成下划线


def test_ext_picked_from_content_type(tmp_uploads, monkeypatch):
    """URL 没扩展名时,从 Content-Type 推"""
    stream = _mock_stream_response(200, [b"x" * 100], "video/mp4")
    monkeypatch.setattr(ma.httpx, "AsyncClient", MagicMock(return_value=_mock_client(stream)))

    new = asyncio.run(ma.archive_url("https://fal.media/no-ext-here", "uid", "video"))
    assert new.endswith(".mp4")


def test_pick_ext_unit():
    assert ma._pick_ext("https://x.com/a.png", None) == ".png"
    assert ma._pick_ext("https://x.com/a.JPG", None) == ".jpg"
    assert ma._pick_ext("https://x.com/no-ext", "image/webp") == ".webp"
    assert ma._pick_ext("https://x.com/no-ext", None) == ".bin"
    # 不接受奇怪扩展名
    assert ma._pick_ext("https://x.com/file.exe?download=1", "video/mp4") == ".mp4"

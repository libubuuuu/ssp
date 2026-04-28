"""upload_guard service 单元测试

关注点:
- read_bounded:小文件读、超限 413、MIME 拒绝 415、空文件 OK
- stream_bounded_to_path:大文件流式落盘、超限清理、MIME 拒绝
- 集成:接到 video_studio 的 /upload 后真接住 size 攻击
"""
import asyncio
import io
import pytest
from pathlib import Path
from fastapi import HTTPException, UploadFile
from starlette.datastructures import Headers

from app.services.upload_guard import (
    read_bounded,
    stream_bounded_to_path,
    IMAGE_MIMES,
    SHORT_VIDEO_MIMES,
    LONG_VIDEO_MIMES,
)


def _fake_upload(content: bytes, mime: str, filename: str = "test.bin") -> UploadFile:
    """构造 UploadFile 测试用,bypass FastAPI 的依赖注入"""
    file_like = io.BytesIO(content)
    headers = Headers({"content-type": mime})
    return UploadFile(file=file_like, filename=filename, headers=headers)


# ==================== read_bounded ====================


def test_read_bounded_small_ok():
    """1KB 图片 < 10MB 限,通过"""
    f = _fake_upload(b"x" * 1024, "image/jpeg")
    out = asyncio.run(read_bounded(f, max_bytes=10 * 1024 * 1024, allowed_mimes=IMAGE_MIMES, label="图片"))
    assert len(out) == 1024


def test_read_bounded_over_limit_413():
    """5MB 文件,限 1MB → 413"""
    f = _fake_upload(b"x" * (5 * 1024 * 1024), "image/jpeg")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(read_bounded(f, max_bytes=1 * 1024 * 1024, allowed_mimes=IMAGE_MIMES, label="图片"))
    assert exc.value.status_code == 413
    assert "1MB" in exc.value.detail


def test_read_bounded_wrong_mime_415():
    """exe 伪装成 jpeg → 415"""
    f = _fake_upload(b"MZ\x00\x00", "application/x-msdownload", filename="evil.exe")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(read_bounded(f, max_bytes=10 * 1024 * 1024, allowed_mimes=IMAGE_MIMES, label="图片"))
    assert exc.value.status_code == 415


def test_read_bounded_empty_ok():
    """空文件 OK(由调用方 Pillow 等处理无效内容)"""
    f = _fake_upload(b"", "image/jpeg")
    out = asyncio.run(read_bounded(f, max_bytes=10 * 1024 * 1024, allowed_mimes=IMAGE_MIMES, label="图片"))
    assert out == b""


def test_read_bounded_at_exact_limit():
    """正好等于上限不应触发(只有 > 才挡)"""
    f = _fake_upload(b"x" * 1024, "image/jpeg")
    out = asyncio.run(read_bounded(f, max_bytes=1024, allowed_mimes=IMAGE_MIMES, label="图片"))
    assert len(out) == 1024


# ==================== stream_bounded_to_path ====================


def test_stream_bounded_small_ok(tmp_path: Path):
    f = _fake_upload(b"x" * (2 * 1024 * 1024), "video/mp4", filename="a.mp4")
    target = tmp_path / "out.mp4"
    n = asyncio.run(stream_bounded_to_path(
        f, target_path=target,
        max_bytes=10 * 1024 * 1024,
        allowed_mimes=SHORT_VIDEO_MIMES,
        label="视频",
    ))
    assert n == 2 * 1024 * 1024
    assert target.exists()
    assert target.stat().st_size == 2 * 1024 * 1024


def test_stream_bounded_over_limit_cleans_up(tmp_path: Path):
    """超限后部分文件应被清掉,不留磁盘垃圾"""
    f = _fake_upload(b"x" * (5 * 1024 * 1024), "video/mp4", filename="big.mp4")
    target = tmp_path / "big.mp4"
    with pytest.raises(HTTPException) as exc:
        asyncio.run(stream_bounded_to_path(
            f, target_path=target,
            max_bytes=1 * 1024 * 1024,
            allowed_mimes=SHORT_VIDEO_MIMES,
            label="视频",
        ))
    assert exc.value.status_code == 413
    # 关键:超限后 target 不应存在(已清理)
    assert not target.exists()


def test_stream_bounded_wrong_mime_415(tmp_path: Path):
    f = _fake_upload(b"x" * 1024, "application/zip", filename="hidden.zip")
    target = tmp_path / "x.zip"
    with pytest.raises(HTTPException) as exc:
        asyncio.run(stream_bounded_to_path(
            f, target_path=target,
            max_bytes=10 * 1024 * 1024,
            allowed_mimes=SHORT_VIDEO_MIMES,
            label="视频",
        ))
    assert exc.value.status_code == 415


def test_stream_bounded_long_video_octet_stream_allowed(tmp_path: Path):
    """LONG_VIDEO_MIMES 包含 octet-stream(iOS Safari 兼容)"""
    f = _fake_upload(b"x" * 1024, "application/octet-stream", filename="ios.mov")
    target = tmp_path / "ios.mov"
    n = asyncio.run(stream_bounded_to_path(
        f, target_path=target,
        max_bytes=10 * 1024 * 1024,
        allowed_mimes=LONG_VIDEO_MIMES,
        label="长视频",
    ))
    assert n == 1024

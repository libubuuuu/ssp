"""文件上传守卫 — size + MIME + 流式不入内存

为什么:
    nginx client_max_body_size 给 500MB 上限,后端不二次校验 → 用户上传 500MB
    文件后,Pillow / fal_client 一次 read 到内存 = 后端 OOM。
    本模块提供两套读法:
      1. read_bounded() — 小文件全读到内存(image / 短视频),超限 raise 413
      2. stream_bounded_to_path() — 大文件流式落盘(长视频工作台),超限边读边清

设计:
    - MIME 用 file.content_type 校验(浏览器声明,不绝对可信但拦掉 99% 误传)
    - 真伪检测留 Pillow / ffprobe 在调用方判断(无效图片 Pillow.open 自然抛错)
    - 限额超出 → HTTPException 413(规范,不是 400)
"""
from pathlib import Path
from typing import Iterable
from fastapi import HTTPException, UploadFile


# 通用 MIME 集合
IMAGE_MIMES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
SHORT_VIDEO_MIMES = {
    "video/mp4",
    "video/quicktime",
    "video/x-matroska",
    "video/webm",
}
LONG_VIDEO_MIMES = SHORT_VIDEO_MIMES | {"video/x-msvideo", "application/octet-stream"}
# studio 长视频允许 octet-stream:有些 iOS Safari / 旧浏览器不发 video/* 而是 octet-stream

DEFAULT_CHUNK = 1024 * 1024  # 1 MB


def _check_mime(file: UploadFile, allowed: Iterable[str], label: str) -> None:
    """校验 Content-Type;不在白名单 → 415"""
    mime = (file.content_type or "").lower()
    allowed_set = set(allowed)
    if mime not in allowed_set:
        raise HTTPException(
            status_code=415,
            detail=f"{label} 不支持的文件类型 {mime!r},允许:{sorted(allowed_set)}",
        )


async def read_bounded(
    file: UploadFile,
    max_bytes: int,
    allowed_mimes: Iterable[str],
    label: str = "上传文件",
) -> bytes:
    """读小文件到内存,超限 raise 413。

    使用场景:image / 短视频 (<= 100MB),需要一次性 Pillow / ffprobe 处理
    """
    _check_mime(file, allowed_mimes, label)

    chunks = []
    total = 0
    while True:
        chunk = await file.read(DEFAULT_CHUNK)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"{label} 超过最大限制 {max_bytes // (1024 * 1024)}MB",
            )
        chunks.append(chunk)
    return b"".join(chunks)


async def stream_bounded_to_path(
    file: UploadFile,
    target_path: Path,
    max_bytes: int,
    allowed_mimes: Iterable[str],
    label: str = "上传文件",
    chunk_size: int = DEFAULT_CHUNK,
) -> int:
    """流式落盘,超限即终止 + 清理。

    使用场景:大视频(GB 级),不能加载到内存。
    返回总写入字节数。
    """
    _check_mime(file, allowed_mimes, label)

    total = 0
    target_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(target_path, "wb") as f:
            while True:
                chunk = await file.read(chunk_size)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    # 立刻清掉部分写入,防磁盘垃圾累积
                    f.close()
                    try:
                        target_path.unlink(missing_ok=True)
                    except OSError:
                        pass
                    raise HTTPException(
                        status_code=413,
                        detail=f"{label} 超过最大限制 {max_bytes // (1024 * 1024)}MB",
                    )
                f.write(chunk)
    except HTTPException:
        raise
    except Exception:
        # 任何 I/O 错误也要清掉部分文件
        try:
            target_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise

    return total

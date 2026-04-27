"""
媒体归档(BUG-2 阶段 A:fal.media URL → 本地 /uploads)

背景:fal.media 短期保留(7-30 天后 404),用户回看历史 = 投诉 = 退款。
本模块下载 FAL 返回的 URL 到 /opt/ssp/uploads/{user_id}/{YYYY-MM}/{uuid}.{ext},
返回指向自己服务器 nginx /uploads/ 的 https URL。

阶段 A(此实现):本地磁盘
阶段 B(下次):接腾讯云 COS / 阿里云 OSS;接入点不变,只换 archive_url 内部实现

使用:
    from app.services.media_archiver import archive_url
    new_url = await archive_url(fal_url, user_id="...", kind="image")

设计:
- 失败 fallback 返回原 fal_url + 写 logger warning,不抛异常(主流程不能因归档失败爆掉)
- 文件大小硬上限 100MB(fal 视频也撑不到这个)
- ext 从 URL 推 + Content-Type 兜底
- uploads 目录 mode 755 / 文件 mode 644(nginx 静态 serve 需要 r)
"""
from __future__ import annotations

import logging
import mimetypes
import os
import re
import uuid as _uuid
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

# 物理路径:本地磁盘
UPLOADS_ROOT = Path(os.environ.get("SSP_UPLOADS_ROOT", "/opt/ssp/uploads"))
# 公网 URL 前缀(nginx alias /uploads/ → UPLOADS_ROOT)
PUBLIC_BASE_URL = os.environ.get("SSP_UPLOADS_PUBLIC_BASE", "https://ailixiao.com/uploads")

MAX_BYTES = 100 * 1024 * 1024  # 100MB 硬上限
DOWNLOAD_TIMEOUT_SEC = 60.0     # 视频可能 30s+,留充足

# Content-Type → 扩展名兜底 + URL 扩展名白名单
# 跟 nginx /uploads/ location ~* \.(...) 必须保持一致
_CT_TO_EXT = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "video/mp4": ".mp4",
    "video/webm": ".webm",
    "video/quicktime": ".mov",
    "audio/mpeg": ".mp3",
    "audio/wav": ".wav",
    "audio/mp4": ".m4a",
}
_ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif",
                ".mp4", ".webm", ".mov",
                ".mp3", ".wav", ".m4a"}

_SAFE_FILENAME_PART = re.compile(r"[^A-Za-z0-9._-]")


def _pick_ext(url: str, content_type: Optional[str]) -> str:
    """白名单选扩展名:URL 末段 ∩ 白名单 优先,否则 Content-Type 推,默认 .bin

    安全:不接受任意 URL 扩展名(防 .exe / .sh 之类),即使 nginx 也不会 serve,
    但能阻止落盘占用磁盘空间。
    """
    parsed = urlparse(url)
    path_ext = Path(parsed.path).suffix.lower()
    if path_ext in _ALLOWED_EXT:
        # JPG → .jpg(规范化)
        return ".jpg" if path_ext == ".jpeg" else path_ext

    if content_type:
        ct = content_type.split(";")[0].strip().lower()
        if ct in _CT_TO_EXT:
            return _CT_TO_EXT[ct]
        guessed = mimetypes.guess_extension(ct)
        if guessed and guessed in _ALLOWED_EXT:
            return guessed

    return ".bin"


def _safe_user_dir(user_id: str) -> str:
    """确保 user_id 不含路径穿越字符;UUID / 邮箱前缀都安全,但保险再洗一遍"""
    return _SAFE_FILENAME_PART.sub("_", user_id)[:64] or "anon"


async def archive_url(url: str, user_id: str, kind: str = "media") -> str:
    """下载 url 到本地 /opt/ssp/uploads,返回新 URL。失败 fallback 返回原 URL。

    user_id: 用于目录隔离(同时也是审计语义)
    kind:   image / video / audio / media — 用于日志 + 文件名前缀

    返回值永远是字符串(新 URL 或原 URL),绝不抛异常 — 调用方安全。
    """
    if not url:
        return url

    # 不归档非 http(s) URL(可能已是本地路径或 data:base64)
    if not url.startswith(("http://", "https://")):
        return url

    # 已经是我们自己的 /uploads/ URL 不重复归档
    if url.startswith(PUBLIC_BASE_URL.rstrip("/") + "/"):
        return url

    safe_user = _safe_user_dir(user_id or "anon")
    yyyymm = datetime.utcnow().strftime("%Y-%m")
    file_uuid = _uuid.uuid4().hex

    try:
        async with httpx.AsyncClient(timeout=DOWNLOAD_TIMEOUT_SEC, follow_redirects=True) as client:
            async with client.stream("GET", url) as resp:
                if resp.status_code != 200:
                    logger.warning(
                        "archive_url failed: status=%d url=%s",
                        resp.status_code, url[:120],
                    )
                    return url

                ext = _pick_ext(url, resp.headers.get("content-type"))
                filename = f"{kind[:16]}_{file_uuid}{ext}"
                target_dir = UPLOADS_ROOT / safe_user / yyyymm
                target_dir.mkdir(parents=True, exist_ok=True)
                target_path = target_dir / filename

                total = 0
                with target_path.open("wb") as f:
                    async for chunk in resp.aiter_bytes(chunk_size=64 * 1024):
                        total += len(chunk)
                        if total > MAX_BYTES:
                            f.close()
                            target_path.unlink(missing_ok=True)
                            logger.warning(
                                "archive_url skipped: file > %dMB url=%s",
                                MAX_BYTES // 1024 // 1024, url[:120],
                            )
                            return url
                        f.write(chunk)

                # 设权限 644 让 nginx 能读(目录 755)
                os.chmod(target_path, 0o644)

                public = f"{PUBLIC_BASE_URL.rstrip('/')}/{safe_user}/{yyyymm}/{filename}"
                logger.info("archive_url ok: %s -> %s (%d bytes)", url[:80], public, total)
                return public

    except (httpx.HTTPError, OSError) as e:
        logger.warning("archive_url exception: url=%s err=%s", url[:120], e)
        return url


# 隐藏雷 #1:用户主动删 generation_history 时调,清掉本地文件
# 真实现在 uploads_gc.delete_archived,这里只是按用户原话"media_archiver.py 加 delete_archived"
# 提供一个对外稳定入口
from app.services.uploads_gc import delete_archived  # noqa: E402  re-export
__all__ = ["archive_url", "delete_archived"]

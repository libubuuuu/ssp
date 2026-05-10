"""P221 V2 — upload/video → check-duration 之间的本地视频缓存(Path B)。

为什么需要:
- check-duration 算 motion_score 要 ffmpeg 真读视频
- 跨境读 fal CDN 实测 6.7-9.4s/2 段(三审实测),用户体感卡死
- 用户上传时本来就在后端走了一遭,顺手存本地,后续 ffmpeg 直读 → 200ms 量级

设计:
- 路径:`/tmp/v2_cache/{sha256}.mp4`(SHA256 做 key,跨用户自然去重)
- 文件持有方:upload/video 写入,check-duration 读取
- 生存期:30 分钟(典型用户 upload→选档→create 走完 5 分钟内,30 分钟留余量)
- GC:cron 每 30 分钟扫一次 deploy/v2-cache-gc.sh;另上传时 opportunistic sweep
- 安全:
  - SHA256 做 key,用户 A 算不出用户 B 的 hash,无法窃取(motion_score 也没敏感数据)
  - 路径穿越守卫:resolve 后必须在 CACHE_ROOT 子树下
- 失败降级:cache miss / 读不到 → check-duration 走 fal CDN URL,体验慢但功能不挂

不放在 /opt/ssp/uploads 因为:
- uploads 是用户长期媒体归档(30 天 fal.media 防过期),挂 chown ssp-app:ssp-app
- v2_cache 是分钟级临时文件,放 uploads 干扰 uploads_gc 90 天扫描
- /tmp 重启自清,刚好契合生存期
"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

CACHE_ROOT = Path(os.environ.get("VC2_CACHE_ROOT", "/tmp/v2_cache"))
DEFAULT_MAX_AGE_SECONDS = int(os.environ.get("VC2_CACHE_MAX_AGE_SECONDS", "1800"))  # 30 min


def _ensure_root() -> None:
    CACHE_ROOT.mkdir(parents=True, exist_ok=True)


def _is_within_cache(p: Path) -> bool:
    try:
        p.resolve().relative_to(CACHE_ROOT.resolve())
        return True
    except (ValueError, OSError):
        return False


def cache_path(sha256: str) -> Path:
    """SHA256 → 缓存路径。仅返路径,不保证文件存在。

    sha256 必须是 64 hex 字符,不接受其他形式以防路径注入。
    """
    if not sha256 or len(sha256) != 64 or not all(c in "0123456789abcdef" for c in sha256.lower()):
        raise ValueError(f"非法 sha256: {sha256!r}")
    return CACHE_ROOT / f"{sha256.lower()}.mp4"


def try_get(sha256: str) -> Optional[str]:
    """查缓存。命中返本地路径字符串;未命中或非法 sha256 返 None。"""
    if not sha256:
        return None
    try:
        p = cache_path(sha256)
    except ValueError:
        return None
    if not _is_within_cache(p):
        return None
    if not p.exists():
        return None
    return str(p)


def store(sha256: str, src_path: str) -> Optional[str]:
    """把上传时的 NamedTemporaryFile 移进缓存。成功返缓存路径,失败返 None。

    若目标已存在(同 hash 重复上传 → dedupe),src 直接 unlink 不覆盖。
    用 os.replace 做原子 move(同分区)/ shutil.move(跨分区)兜底。
    """
    import shutil
    try:
        target = cache_path(sha256)
    except ValueError as e:
        logger.warning("vc2_cache.store 拒绝非法 sha256: %s", e)
        return None
    _ensure_root()
    try:
        if target.exists():
            # dedupe:删源文件留缓存
            try: os.unlink(src_path)
            except OSError: pass
            return str(target)
        # 同分区原子 rename;跨分区 fallback
        try:
            os.replace(src_path, target)
        except OSError:
            shutil.move(src_path, str(target))
        return str(target)
    except OSError as e:
        logger.warning("vc2_cache.store 失败 sha=%s: %s", sha256[:8], e)
        return None


def clean_old(max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS) -> dict:
    """扫 CACHE_ROOT,删 mtime 早于 cutoff 的文件。

    返 {scanned, deleted, freed_bytes, errors}
    """
    if not CACHE_ROOT.exists():
        return {"scanned": 0, "deleted": 0, "freed_bytes": 0, "errors": []}
    cutoff = time.time() - int(max_age_seconds)
    scanned = deleted = freed_bytes = 0
    errors: list = []
    for entry in CACHE_ROOT.iterdir():
        if not entry.is_file():
            continue
        scanned += 1
        try:
            st = entry.stat()
            if st.st_mtime > cutoff:
                continue
            size = st.st_size
            entry.unlink()
            deleted += 1
            freed_bytes += size
        except OSError as e:
            errors.append(f"{entry}: {e}")
            logger.warning("vc2_cache clean_old skip %s: %s", entry, e)
    return {
        "scanned": scanned,
        "deleted": deleted,
        "freed_bytes": freed_bytes,
        "errors": errors,
    }

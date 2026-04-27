"""
uploads 磁盘清理(隐藏雷 #1)

媒体归档(BUG-2)把 fal.media URL 落到 /opt/ssp/uploads/{user}/{YYYY-MM}/...,
但没自动清理 — 一年下来磁盘必满。

策略:
- 90 天 mtime 超期就删(用户极少回看 90 天前的内容)
- 用户主动删 generation_history 时同步删本地文件(delete_archived)
- watchdog 加磁盘水位告警,>= 80% 推微信

接入:
- cron 模板 /opt/ssp/deploy/uploads-gc.cron.example,每天 04:00 跑
- generation_history 删除接口里调 delete_archived(留给前端 admin 实现)

设计:
- 只删 _SAFE_BASE 子树下的文件,**绝不**接受相对路径或 .. 上溯
- 干跑模式 dry_run=True 默认,执行写日志方便审计
- 软删:先 rename .deleted-{ts},再过 7 天才真删(防误操作)— 太重,本期先简单 unlink + 写日志
"""
from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

UPLOADS_ROOT = Path(os.environ.get("SSP_UPLOADS_ROOT", "/opt/ssp/uploads"))
DEFAULT_RETENTION_DAYS = int(os.environ.get("SSP_UPLOADS_RETENTION_DAYS", "90"))


def _is_within_uploads(p: Path) -> bool:
    """安全检查:p 必须真在 UPLOADS_ROOT 子树下"""
    try:
        p.resolve().relative_to(UPLOADS_ROOT.resolve())
        return True
    except (ValueError, OSError):
        return False


def clean_old_uploads(days: int = DEFAULT_RETENTION_DAYS, dry_run: bool = False) -> dict:
    """删 mtime > N 天前的所有上传文件

    返回 dict:{scanned, deleted, freed_bytes, errors}
    """
    import time
    if not UPLOADS_ROOT.exists():
        return {"scanned": 0, "deleted": 0, "freed_bytes": 0, "errors": []}

    cutoff = time.time() - days * 86400
    scanned = deleted = freed_bytes = 0
    errors = []

    for root, dirs, files in os.walk(UPLOADS_ROOT):
        for name in files:
            path = Path(root) / name
            scanned += 1
            try:
                if path.stat().st_mtime > cutoff:
                    continue
                size = path.stat().st_size
                if dry_run:
                    logger.info("uploads_gc DRY: would delete %s (%d bytes)", path, size)
                else:
                    path.unlink()
                    logger.info("uploads_gc deleted: %s (%d bytes)", path, size)
                deleted += 1
                freed_bytes += size
            except OSError as e:
                errors.append(f"{path}: {e}")
                logger.warning("uploads_gc skip %s: %s", path, e)

    # 顺手删空目录(避免目录树膨胀)
    if not dry_run:
        for root, dirs, files in os.walk(UPLOADS_ROOT, topdown=False):
            for d in dirs:
                p = Path(root) / d
                try:
                    p.rmdir()  # 只删空目录,非空抛 OSError
                except OSError:
                    pass

    return {
        "scanned": scanned,
        "deleted": deleted,
        "freed_bytes": freed_bytes,
        "errors": errors,
    }


def delete_archived(url: str) -> bool:
    """根据归档 URL 删本地文件(用户主动删 generation_history 时调用)

    URL 形如 https://ailixiao.com/uploads/{user}/{YYYY-MM}/{file}
    安全:仅接受指向 UPLOADS_ROOT 子树的路径;路径穿越拒绝。
    """
    if not url:
        return False
    parsed = urlparse(url)
    # 只接受 /uploads/... 路径(忽略 host,可能带 CDN 不同域)
    if "/uploads/" not in parsed.path:
        return False
    rel = parsed.path.split("/uploads/", 1)[1]
    target = UPLOADS_ROOT / rel
    if not _is_within_uploads(target):
        logger.warning("delete_archived rejected suspicious path: %s", url)
        return False
    if not target.exists():
        return False
    try:
        target.unlink()
        logger.info("delete_archived ok: %s", target)
        return True
    except OSError as e:
        logger.warning("delete_archived failed %s: %s", target, e)
        return False


def disk_usage_pct() -> Optional[int]:
    """返回 UPLOADS_ROOT 所在分区占用百分比;不可读返 None

    watchdog 调用,>= 80 推微信。"""
    if not UPLOADS_ROOT.exists():
        return None
    try:
        usage = shutil.disk_usage(UPLOADS_ROOT)
        return int(usage.used * 100 / usage.total)
    except OSError:
        return None

"""口播带货 session 60 天 GC(七十七续 P12)。

uploads_gc 是按 mtime 90 天扫整 /opt/ssp/uploads/,oral session 政策更紧
(60 天)且要 DB 同步标记,所以单独跑。

策略:
- 选择条件:created_at < cutoff AND (status='completed' OR status='cancelled'
  OR status LIKE 'failed_%') AND archived_at IS NULL
- in-flight session(uploaded / asr_running / tts_running / inpainting_running /
  lipsync_running / edit_submitted)绝不动 — 用户可能还在排队
- 每个 session 整目录 rmtree(orig.mp4 / mask / swap1 / swap2 / final.mp4 等)
- DB row 保留(账单/审计/admin drill-down 看历史)只 UPDATE archived_at = now
- 路径穿越守卫:rmtree 前确认 target 真在 ORAL_UPLOAD_ROOT 子树下

cron: deploy/oral-gc.sh + oral-gc.cron.example,每天 05:00 跑(避开 04:00 uploads-gc)
"""
from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

ORAL_UPLOAD_ROOT = Path(os.environ.get("UPLOADS_ROOT", "/opt/ssp/uploads")) / "oral"
DEFAULT_RETENTION_DAYS = int(os.environ.get("SSP_ORAL_RETENTION_DAYS", "60"))


def _is_within_oral_root(p: Path) -> bool:
    try:
        p.resolve().relative_to(ORAL_UPLOAD_ROOT.resolve())
        return True
    except (ValueError, OSError):
        return False


def _terminal_clause() -> str:
    """选 DB 终态:完成 / 取消 / 失败_*。"""
    return "(status = 'completed' OR status = 'cancelled' OR status LIKE 'failed_%')"


def clean_old_oral_sessions(days: int = DEFAULT_RETENTION_DAYS, dry_run: bool = False) -> dict:
    """删超期 oral session 目录 + 标记 archived_at。

    返回 dict:{scanned, archived, freed_bytes, errors}
    - scanned:符合条件 row 数
    - archived:成功删 + 标记
    - freed_bytes:估算释放空间(rmtree 前 du)
    """
    from app.database import get_db

    if not ORAL_UPLOAD_ROOT.exists():
        return {"scanned": 0, "archived": 0, "freed_bytes": 0, "errors": []}

    scanned = archived = freed_bytes = 0
    errors: list = []

    with get_db() as conn:
        cursor = conn.cursor()
        sql = f"""
            SELECT id, user_id
              FROM oral_sessions
             WHERE created_at < datetime('now', ? )
               AND {_terminal_clause()}
               AND archived_at IS NULL
        """
        cursor.execute(sql, (f"-{int(days)} days",))
        rows = cursor.fetchall()

    for row in rows:
        scanned += 1
        sid = row["id"] if hasattr(row, "keys") else row[0]
        user_id = row["user_id"] if hasattr(row, "keys") else row[1]
        target = ORAL_UPLOAD_ROOT / str(user_id) / sid

        if not _is_within_oral_root(target):
            logger.warning("oral_gc skip suspicious path: sid=%s target=%s", sid, target)
            errors.append(f"{sid}: path outside oral root")
            continue

        size = 0
        if target.exists():
            try:
                for root, dirs, files in os.walk(target):
                    for name in files:
                        try:
                            size += (Path(root) / name).stat().st_size
                        except OSError:
                            pass
            except OSError:
                pass

        if dry_run:
            logger.info("oral_gc DRY: would archive sid=%s dir=%s size=%d", sid, target, size)
            archived += 1
            freed_bytes += size
            continue

        try:
            if target.exists():
                shutil.rmtree(target)
            with get_db() as conn2:
                cursor2 = conn2.cursor()
                cursor2.execute(
                    "UPDATE oral_sessions SET archived_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (sid,),
                )
                conn2.commit()
            archived += 1
            freed_bytes += size
            logger.info("oral_gc archived: sid=%s freed=%d", sid, size)
        except OSError as e:
            errors.append(f"{sid}: {e}")
            logger.warning("oral_gc failed sid=%s: %s", sid, e)

    return {
        "scanned": scanned,
        "archived": archived,
        "freed_bytes": freed_bytes,
        "errors": errors,
    }

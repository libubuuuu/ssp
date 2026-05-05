"""P160 #7(2026-05-06)refund_sweep:扫漏退的 pending_refunds(refunded=0 + TTL 过期)

设计:
- 每 5 分钟跑(crontab */5 * * * *)
- 只看 pending_refunds.refunded=0 + age > 30min(TTL 内由 polling 处理)
- 对每条根据 jobs.json 状态决定:
  · jobs.status='failed' → 真漏退 → add_credits + UPDATE refunded=1
  · jobs.status='completed' → 任务成功,只是没标记 → UPDATE refunded=1(不退钱)
  · jobs 不在 jobs.json(已被 TTL 清理 90d+)→ 信任 pending_refunds 退掉
- 不调 fal(用户严格"no fal API call"约定)
- audit_log 记 reason='refund_sweep_漏退'

跑法:
  /opt/ssp/backend/venv/bin/python /opt/ssp/scripts/refund_sweep.py
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
import uuid

DB_PATH = os.environ.get("DATABASE_PATH", "/opt/ssp/backend/dev.db")
JOBS_JSON = "/opt/ssp/jobs_data/jobs.json"
TTL_SECONDS = 30 * 60  # 30min,跟 refund_tracker._TTL_SECONDS 一致
LOG_PREFIX = "[refund_sweep]"


def _log(msg: str) -> None:
    print(f"{LOG_PREFIX} {time.strftime('%Y-%m-%d %H:%M:%S')} {msg}")


def _load_jobs() -> dict:
    try:
        with open(JOBS_JSON) as f:
            return json.load(f)
    except Exception as e:
        _log(f"jobs.json 读失败: {e}")
        return {}


def main() -> int:
    if not os.path.exists(DB_PATH):
        _log(f"DB 不存在: {DB_PATH}")
        return 1

    jobs = _load_jobs()
    cutoff = time.time() - TTL_SECONDS

    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    c = db.cursor()

    # 取所有 refunded=0 + 已过 TTL 的
    c.execute(
        """SELECT task_id, user_id, cost, registered_at
           FROM pending_refunds
           WHERE refunded = 0 AND registered_at < ?
           ORDER BY registered_at ASC""",
        (cutoff,),
    )
    candidates = c.fetchall()
    _log(f"找到 {len(candidates)} 条 TTL 过期未退款")

    refunded_count = 0
    skipped_completed = 0
    skipped_unknown = 0
    refunded_amount = 0

    for r in candidates:
        task_id = r["task_id"]
        user_id = r["user_id"]
        cost = int(r["cost"])
        age_min = int((time.time() - r["registered_at"]) / 60)

        # 1. 查 jobs.json 状态
        job_status = None
        for j in jobs.values():
            if j.get("id") == task_id or task_id in (j.get("fal_task_ids") or []):
                job_status = j.get("status")
                break

        if job_status == "completed":
            # 任务真成功,只是没标记 refunded=1 → 标记 + 不退
            c.execute(
                "UPDATE pending_refunds SET refunded = 1 WHERE task_id = ? AND refunded = 0",
                (task_id,),
            )
            db.commit()
            skipped_completed += 1
            _log(f"  task={task_id[:12]} user={user_id[:8]} 任务已成功不退,只标记 refunded=1")
            continue

        # 2. 失败 / 不知道 → 退款
        try:
            # 原子加 + 拿新余额
            c.execute(
                "UPDATE users SET credits = credits + ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (cost, user_id),
            )
            if c.rowcount == 0:
                _log(f"  task={task_id[:12]} user={user_id[:8]} 用户不存在,跳过")
                continue
            c.execute("SELECT credits FROM users WHERE id = ?", (user_id,))
            new_credits = c.fetchone()[0]
            # 标记 refunded=1
            c.execute(
                "UPDATE pending_refunds SET refunded = 1 WHERE task_id = ? AND refunded = 0",
                (task_id,),
            )
            # 写 ledger(P157)
            c.execute(
                """INSERT INTO credits_ledger (id, user_id, delta, balance_after, reason, ref_id, module, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    uuid.uuid4().hex,
                    user_id,
                    cost,
                    new_credits,
                    "refund_sweep",
                    task_id,
                    "refund_sweep_cron",
                    time.time(),
                ),
            )
            db.commit()
            refunded_count += 1
            refunded_amount += cost
            tag = "failed" if job_status == "failed" else "unknown(jobs.json 已清理)"
            _log(f"  ✅ task={task_id[:12]} user={user_id[:8]} cost={cost} age={age_min}min ({tag}) → balance={new_credits}")
            if job_status != "failed":
                skipped_unknown += 1
        except Exception as e:
            _log(f"  ❌ task={task_id[:12]} 退款失败: {e}")
            db.rollback()

    db.close()

    _log("=" * 50)
    _log(f"扫描完毕:")
    _log(f"  退款 {refunded_count} 条 共 {refunded_amount} 积分")
    _log(f"  完成任务标记 {skipped_completed} 条")
    _log(f"  未知状态(jobs.json 没记录)退 {skipped_unknown} 条")
    return 0


if __name__ == "__main__":
    sys.exit(main())

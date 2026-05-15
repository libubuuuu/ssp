"""数据库定时备份 + 积分对账

备份策略：
- SQLite backup API（WAL 安全，在线热备）
- 备份目录：/root/ssp/backups/
- 文件名：db_backup_YYYY-MM-DD.sqlite
- 保留最近 30 天，超期自动删除
- 后端启动跑一次，之后每 24 小时循环

对账：
- 每次备份后跑 SUM(credits_ledger.delta) vs users.credits
- 不一致写 log_error 报警
- 只读查询，不修改任何数据
"""
import asyncio
import os
import sqlite3
from datetime import datetime, timedelta

BACKUP_DIR = "/opt/ssp/backups"
RETAIN_DAYS = 30
INTERVAL_SECONDS = 86400  # 24 小时


def _get_db_path() -> str:
    """返回当前进程实际使用的数据库绝对路径。"""
    from ..database import DATABASE_PATH
    return os.path.abspath(DATABASE_PATH)


def backup_database() -> str:
    """用 SQLite backup API 热备数据库，WAL 模式下安全。

    Returns:
        备份文件路径，失败时抛异常。
    """
    from .logger import log_info, log_error

    os.makedirs(BACKUP_DIR, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    dest = os.path.join(BACKUP_DIR, f"db_backup_{today}.sqlite")

    db_path = _get_db_path()
    try:
        src_conn = sqlite3.connect(db_path)
        dst_conn = sqlite3.connect(dest)
        with dst_conn:
            src_conn.backup(dst_conn)
        src_conn.close()
        dst_conn.close()
        size_kb = os.path.getsize(dest) // 1024
        log_info(f"db_backup: 备份完成 → {dest} ({size_kb} KB)")
    except Exception as e:
        log_error(f"db_backup: 备份失败 db={db_path} dest={dest}: {e}")
        raise

    # 删除 RETAIN_DAYS 天前的备份文件
    cutoff = datetime.now() - timedelta(days=RETAIN_DAYS)
    deleted = 0
    try:
        for fname in os.listdir(BACKUP_DIR):
            if not (fname.startswith("db_backup_") and fname.endswith(".sqlite")):
                continue
            fpath = os.path.join(BACKUP_DIR, fname)
            if datetime.fromtimestamp(os.path.getmtime(fpath)) < cutoff:
                os.remove(fpath)
                deleted += 1
        if deleted:
            log_info(f"db_backup: 清理 {deleted} 个超过 {RETAIN_DAYS} 天的旧备份")
    except Exception as e:
        log_error(f"db_backup: 清理旧备份异常(不影响本次备份): {e}")

    return dest


def reconcile_credits() -> int:
    """对账：SUM(credits_ledger.delta) 应等于 users.credits。

    发现不一致的用户写 log_error 报警，不修改任何数据。
    Returns:
        不一致用户数量。
    """
    from .logger import log_info, log_error

    db_path = _get_db_path()
    mismatches = 0
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # 所有有 ledger 记录的用户的 SUM(delta)
        cursor.execute("""
            SELECT l.user_id,
                   COALESCE(SUM(l.delta), 0) AS ledger_sum,
                   u.credits                  AS actual_credits,
                   u.email
              FROM credits_ledger l
              JOIN users u ON l.user_id = u.id
             GROUP BY l.user_id
        """)
        rows = cursor.fetchall()
        conn.close()

        for row in rows:
            diff = row["actual_credits"] - row["ledger_sum"]
            if diff != 0:
                mismatches += 1
                log_error(
                    f"credits_reconcile 不一致: user={row['email']} "
                    f"ledger_sum={row['ledger_sum']} actual={row['actual_credits']} "
                    f"diff={diff:+d}"
                )

        if mismatches == 0:
            log_info(f"credits_reconcile OK: 检查 {len(rows)} 个用户，全部一致")
        else:
            log_error(f"credits_reconcile 发现 {mismatches} 个用户余额不一致，请人工核查")

    except Exception as e:
        log_error(f"credits_reconcile 运行异常: {e}")

    return mismatches


def run_backup_and_reconcile() -> None:
    """备份 + 对账，一次性同步调用入口。"""
    from .logger import log_info, log_error
    log_info("db_backup: 开始每日备份 + 对账")
    try:
        backup_database()
    except Exception as e:
        log_error(f"db_backup: 备份异常，跳过但继续对账: {e}")
    try:
        reconcile_credits()
    except Exception as e:
        log_error(f"db_backup: 对账异常: {e}")


async def db_backup_loop() -> None:
    """后台无限循环：每 24 小时执行一次备份 + 对账。"""
    while True:
        await asyncio.sleep(INTERVAL_SECONDS)
        try:
            run_backup_and_reconcile()
        except Exception as e:
            from .logger import log_error
            log_error(f"db_backup_loop 未捕获异常: {e}")

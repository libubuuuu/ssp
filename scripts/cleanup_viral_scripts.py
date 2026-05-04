"""viral_scripts 库 LRU 保鲜:删 90 天前老内容,保底最近 500 条。

跑法:/opt/ssp/backend/venv/bin/python /opt/ssp/scripts/cleanup_viral_scripts.py

cron 挂在 03:35(scrape 03:30 跑完后再清)。

逻辑:
- 删 scraped_at < now - 90 天 的老条目
- 但永远保留最新 500 条(防 P110 抽样池子被清空)
- 即:删除 = (老 90 天) AND NOT (在最新 500 条之内)
"""
import os
import sqlite3
import sys

DB_PATH = os.environ.get("DATABASE_PATH", "/opt/ssp/backend/dev.db")
KEEP_DAYS = int(os.environ.get("VIRAL_KEEP_DAYS", "90"))
KEEP_MIN = int(os.environ.get("VIRAL_KEEP_MIN", "500"))


def main() -> int:
    if not os.path.exists(DB_PATH):
        print(f"[skip] db not found: {DB_PATH}")
        return 0
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    before = cur.execute("SELECT COUNT(*) FROM viral_scripts").fetchone()[0]

    # 删:超过 KEEP_DAYS 天 AND 不在最新 KEEP_MIN 条之内
    cur.execute(
        f"""
        DELETE FROM viral_scripts
        WHERE scraped_at < datetime('now', '-{KEEP_DAYS} days')
          AND id NOT IN (
              SELECT id FROM viral_scripts
              ORDER BY scraped_at DESC
              LIMIT {KEEP_MIN}
          )
        """
    )
    deleted = cur.rowcount
    conn.commit()
    after = cur.execute("SELECT COUNT(*) FROM viral_scripts").fetchone()[0]

    print(f"[cleanup] keep_days={KEEP_DAYS} keep_min={KEEP_MIN}")
    print(f"[cleanup] before={before}  deleted={deleted}  after={after}")
    print("[cleanup] 当前分布:")
    for region, kind, cnt in cur.execute(
        "SELECT region, kind, COUNT(*) FROM viral_scripts GROUP BY region, kind ORDER BY region, kind"
    ).fetchall():
        print(f"  {region:8s} {kind:8s} : {cnt}")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

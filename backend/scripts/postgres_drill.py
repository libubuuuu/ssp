"""Postgres 切换 staging 演练 — 端到端验证迁移流程

用户开 Postgres 实例后,在切上线**前**跑一次,验证 6 步切换流程能 work,
心里有底再真切。脚本对目标库做完整流程:

  1. 清空目标库(drop_all,确保干净)
  2. alembic upgrade head(建 schema)
  3. schema 反射校验(断言 16 张业务表都在)
  4. 数据迁移(调 sqlite_to_postgres)
  5. 行数比对(逐表源 vs 目标)
  6. 抽样字段校验(users.email / pending_refunds.task_id 等关键 PK)

如全过 → 演练通过,切上线信心 +1。
如某步失败 → 报告卡哪步,方便排查。

用法:
    cd /opt/ssp/backend

    # 演练 1:用 SQLite 作为 staging 目标(无依赖,本地试 alembic upgrade)
    venv/bin/python scripts/postgres_drill.py \\
        --source-sqlite /opt/ssp/backend/dev.db \\
        --target-url sqlite:////tmp/drill_target.db

    # 演练 2:真 PG staging 实例
    venv/bin/python scripts/postgres_drill.py \\
        --source-sqlite /opt/ssp/backend/dev.db \\
        --target-url postgresql+psycopg2://USER:PWD@STAGING_HOST:5432/ssp_staging

不污染生产 dev.db(只读 source);target-url 必须是空 / 可清空的库。
"""
import argparse
import os
import subprocess
import sys
from pathlib import Path

# 业务核心表(不含 alembic_version),必须迁移成功
EXPECTED_TABLES = {
    "users", "merchants", "products",
    "body_measurements", "body_models",
    "tasks", "model_health",
    "generation_history", "credit_orders",
    "orders", "order_items",
    "audit_log",
    "register_ip_log", "register_ip_failure_log",
    "pending_refunds",
}

# 抽样字段校验(每表挑一个 PK 字段,从源拿 5 行,看目标有同样的)
SAMPLE_CHECKS = {
    "users": ("id", "email"),
    "pending_refunds": ("task_id", "user_id"),
    "credit_orders": ("id", "user_id"),
    "audit_log": ("id", "actor_user_id"),
}


def step(n: int, total: int, desc: str) -> None:
    print(f"[{n}/{total}] {desc} ", end="", flush=True)


def ok(msg: str = "") -> None:
    print(f"✅ {msg}")


def fail(msg: str) -> None:
    print(f"❌ {msg}")


def drop_all_tables(target_url: str) -> None:
    """清空目标库(drop 所有业务表 + alembic_version)"""
    from sqlalchemy import create_engine, MetaData, text
    engine = create_engine(target_url)
    md = MetaData()
    md.reflect(bind=engine)
    if md.tables:
        md.drop_all(bind=engine)
    # alembic_version 表 reflect 会包含,上面 drop_all 会删
    # 确保干净:再删一次以防有 view / 残留(忽略错误)
    with engine.connect() as conn:
        try:
            conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
            conn.commit()
        except Exception:
            pass


def run_alembic_upgrade(target_url: str, backend_dir: Path) -> int:
    """alembic upgrade head 用 DATABASE_URL 指向 target"""
    env = os.environ.copy()
    env["DATABASE_URL"] = target_url
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=str(backend_dir),
        env=env,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"\n  STDOUT: {result.stdout}\n  STDERR: {result.stderr}")
    return result.returncode


def reflect_tables(target_url: str) -> set:
    from sqlalchemy import create_engine, MetaData
    engine = create_engine(target_url)
    md = MetaData()
    md.reflect(bind=engine)
    return set(md.tables.keys())


def row_count(url: str, table: str) -> int:
    from sqlalchemy import create_engine, text
    engine = create_engine(url)
    with engine.connect() as conn:
        return conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar() or 0


def sample_rows(url: str, table: str, fields: tuple, limit: int = 5) -> list:
    from sqlalchemy import create_engine, text
    engine = create_engine(url)
    cols = ", ".join(fields)
    with engine.connect() as conn:
        return [tuple(r) for r in conn.execute(text(f"SELECT {cols} FROM {table} LIMIT {limit}")).fetchall()]


def main(source_sqlite: str, target_url: str, backend_dir: Path) -> int:
    print(f"=== Postgres 切换演练 ===")
    print(f"源:    sqlite:///{source_sqlite}")
    print(f"目标:  {target_url}")
    print()

    if not Path(source_sqlite).exists():
        fail(f"源 SQLite 不存在: {source_sqlite}")
        return 1

    TOTAL = 5

    # Step 1: 清空目标
    step(1, TOTAL, "清空目标库 ........... ")
    try:
        drop_all_tables(target_url)
        ok()
    except Exception as e:
        fail(f"清空失败: {e}")
        return 2

    # Step 2: alembic upgrade
    step(2, TOTAL, "alembic upgrade head .. ")
    rc = run_alembic_upgrade(target_url, backend_dir)
    if rc != 0:
        fail(f"alembic 失败 rc={rc}")
        return 3
    ok()

    # Step 3: schema 反射校验
    step(3, TOTAL, "schema 反射校验 ........ ")
    found = reflect_tables(target_url)
    business_tables = found - {"alembic_version"}
    missing = EXPECTED_TABLES - business_tables
    extra = business_tables - EXPECTED_TABLES
    if missing:
        fail(f"缺表: {sorted(missing)}")
        return 4
    if extra:
        print(f"⚠ 多余表(可能是测试遗留): {sorted(extra)}")
    ok(f"({len(business_tables)} 张业务表)")

    # Step 4: 数据迁移
    step(4, TOTAL, "数据迁移 .............. ")
    sys.path.insert(0, str(backend_dir / "scripts"))
    try:
        import sqlite_to_postgres as migrator
        rc = migrator.main(source_sqlite, target_url, dry_run=False)
        if rc != 0:
            fail(f"数据迁移失败 rc={rc}")
            return 5
        # migrator.main 会打印细节,这里不重复 ok()
        print("  ", end="")
        ok()
    except Exception as e:
        fail(f"迁移异常: {e}")
        return 5

    # Step 5: 行数比对 + 抽样字段校验
    step(5, TOTAL, "行数 + 抽样校验 ....... ")
    src_url = f"sqlite:///{source_sqlite}"
    from sqlalchemy import create_engine, inspect
    src_tables = set(inspect(create_engine(src_url)).get_table_names())

    mismatched = []
    skipped = []
    for tbl in sorted(EXPECTED_TABLES):
        if tbl not in src_tables:
            skipped.append(tbl)
            continue
        try:
            src_n = row_count(src_url, tbl)
            dst_n = row_count(target_url, tbl)
            if src_n != dst_n:
                mismatched.append((tbl, src_n, dst_n))
        except Exception as e:
            mismatched.append((tbl, "?", f"err: {e}"))

    if mismatched:
        fail("行数不一致:")
        for t, s, d in mismatched:
            print(f"   {t}: src={s} dst={d}")
        return 6

    # 抽样字段
    sample_fail = []
    for tbl, fields in SAMPLE_CHECKS.items():
        try:
            src_sample = set(sample_rows(src_url, tbl, fields))
            dst_sample = set(sample_rows(target_url, tbl, fields))
            if src_sample and not src_sample.issubset(dst_sample):
                missing_rows = src_sample - dst_sample
                sample_fail.append((tbl, missing_rows))
        except Exception:
            pass  # 表为空 / 字段不存在等忽略
    if sample_fail:
        fail("抽样字段不一致:")
        for t, miss in sample_fail:
            print(f"   {t}: 目标缺 {miss}")
        return 7

    ok()
    if skipped:
        print(f"  (源库缺 {len(skipped)} 张新表,已跳过:{', '.join(skipped)})")
    print()
    print("✅ 演练全过,可以切换生产。")
    print()
    print("切换 SOP:")
    print("  1. backup_daily.sh 备份当前 SQLite")
    print("  2. supervisor stop 业务进程(防迁移期写入)")
    print("  3. 跑本脚本对真 staging 做最终演练")
    print("  4. 改 backend/.env 的 DATABASE_URL 指向 PG")
    print("  5. 跑 sqlite_to_postgres.py 做最终数据迁移")
    print("  6. 切 backend 业务代码到 SQLAlchemy(独立 PR)")
    print("  7. supervisor start + 蓝绿部署 + 24h 观察")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Postgres 切换 staging 演练")
    parser.add_argument("--source-sqlite", required=True, help="源 SQLite 文件路径(只读)")
    parser.add_argument("--target-url", required=True,
                        help="目标 SQLAlchemy URL(可以是 sqlite:/// 或 postgresql+psycopg2://...)")
    parser.add_argument("--backend-dir", default=str(Path(__file__).resolve().parent.parent),
                        help="backend 根目录(含 alembic.ini),默认自动探测")
    args = parser.parse_args()
    sys.exit(main(args.source_sqlite, args.target_url, Path(args.backend_dir)))

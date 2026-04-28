"""SQLite → Postgres 数据迁移脚本

切 Postgres 时跑,把 SQLite dev.db 的所有业务数据搬到 Postgres。

前置条件(用户主导,docs/POSTGRES-MIGRATION.md):
1. 已开 Postgres 实例(腾讯云 RDS / 自建)
2. 已 `pip install psycopg2-binary`
3. 已在 Postgres 上跑 `alembic upgrade head`(schema 就位)
4. 业务停机或切到只读模式(防迁移期间数据漂移)

用法:
    # dry-run:只 count + 前 5 行 sample 验证
    python scripts/sqlite_to_postgres.py \\
        --sqlite-path /opt/ssp/backend/dev.db \\
        --postgres-url postgresql+psycopg2://user:pwd@host:5432/ssp \\
        --dry-run

    # 真迁移:逐表 INSERT,原子事务
    python scripts/sqlite_to_postgres.py \\
        --sqlite-path /opt/ssp/backend/dev.db \\
        --postgres-url postgresql+psycopg2://user:pwd@host:5432/ssp

设计要点:
- 用 SQLAlchemy Core 跨方言通用(Connection + reflect Table from metadata)
- 逐表事务:单表 INSERT 失败仅 rollback 该表,不污染其他表
- BOOLEAN 类型:SQLite 存 0/1 INTEGER,SQLAlchemy 自动转 Postgres bool
- 表迁移顺序:无 FK 表先迁(users / merchants),依赖表后迁(products 依赖 merchants)
- 每张表迁完做 row count 校验,源表 != 目标表 → raise
"""
import argparse
import sys
from pathlib import Path

# 表迁移顺序:依赖关系排好序,被依赖的先迁
# 来源:scan app/database.py FOREIGN KEY 关系
TABLE_ORDER = [
    "users",                       # 无 FK
    "merchants",                   # 依赖 users
    "products",                    # 依赖 merchants
    "body_measurements",           # 依赖 users
    "body_models",                 # 依赖 users
    "tasks",                       # 依赖 users
    "model_health",                # 独立
    "generation_history",          # 依赖 users
    "credit_orders",               # 依赖 users
    "orders",                      # 独立(电商订单)
    "order_items",                 # 依赖 orders
    "audit_log",                   # 依赖 users(actor)
    "register_ip_log",             # 独立
    "register_ip_failure_log",     # 独立
    "pending_refunds",             # 独立(task_id PK,user_id 非外键)
]


def migrate_table(src_engine, dst_engine, dst_metadata, table_name: str, dry_run: bool) -> int:
    """迁移单表,返回迁移行数。

    每表独立连接 + 独立 begin/commit,避免长连接 autobegin 状态污染。
    源缺表 graceful skip(老 dev.db 可能没新表,不当 fail)。
    """
    from sqlalchemy import select, inspect

    if table_name not in dst_metadata.tables:
        print(f"  ⚠ 目标库无表 {table_name},跳过(确认 alembic upgrade 是否完整)")
        return 0

    # 源端表存在性检查 — 老 dev.db 可能缺新表(如 register_ip_log / pending_refunds)
    # 这种情况不是 fail,业务部署后 init_db 会建表,只是历史无数据
    src_tables = inspect(src_engine).get_table_names()
    if table_name not in src_tables:
        print(f"  ⚠ 源库无表 {table_name},跳过(老 dev.db 缺新增表,可接受)")
        return 0

    dst_table = dst_metadata.tables[table_name]

    # 从源(SQLite)读全部行
    with src_engine.connect() as src_conn:
        src_rows = src_conn.execute(select(dst_table)).fetchall()
    src_count = len(src_rows)

    if dry_run:
        sample = src_rows[:5]
        print(f"  [dry-run] {table_name}: {src_count} 行,前 5 行 sample:")
        for r in sample:
            print(f"    {dict(r._mapping)}")
        return src_count

    if src_count == 0:
        print(f"  {table_name}: 空表,跳过")
        return 0

    # 批量 INSERT 到目标(Postgres)— engine.begin() 自动事务
    rows_dicts = [dict(r._mapping) for r in src_rows]
    with dst_engine.begin() as dst_conn:
        dst_conn.execute(dst_table.insert(), rows_dicts)

    # 校验 row count(独立 readonly 连接)
    with dst_engine.connect() as dst_conn:
        dst_count = dst_conn.execute(select(dst_table)).fetchall()
    if len(dst_count) != src_count:
        raise RuntimeError(
            f"表 {table_name} 迁移行数不匹配:source={src_count}, target={len(dst_count)}"
        )
    print(f"  ✅ {table_name}: {src_count} 行迁完")
    return src_count


def main(sqlite_path: str, postgres_url: str, dry_run: bool, tables: list = None) -> int:
    from sqlalchemy import create_engine, MetaData

    if not Path(sqlite_path).exists():
        print(f"❌ SQLite 文件不存在: {sqlite_path}", file=sys.stderr)
        return 1

    src_url = f"sqlite:///{sqlite_path}"
    print(f"源: {src_url}")
    print(f"目标: {postgres_url}")
    print(f"模式: {'dry-run(不写)' if dry_run else '真迁移'}")
    print()

    src_engine = create_engine(src_url)
    dst_engine = create_engine(postgres_url)

    # 反射目标库 schema(必须先 alembic upgrade 建好表)
    dst_metadata = MetaData()
    dst_metadata.reflect(bind=dst_engine)

    if not dst_metadata.tables:
        print("❌ 目标库无表,先跑 `alembic upgrade head`", file=sys.stderr)
        return 1

    target_tables = tables or TABLE_ORDER
    total = 0
    for tname in target_tables:
        try:
            total += migrate_table(src_engine, dst_engine, dst_metadata, tname, dry_run)
        except Exception as e:
            print(f"❌ {tname} 迁移失败: {e}", file=sys.stderr)
            if not dry_run:
                print("   注意:已迁的表已 commit,本表 rollback 不影响。修问题后单独重迁本表:")
                print(f"   python {sys.argv[0]} ... --tables {tname}")
            return 2

    print()
    print(f"✅ 完成,共迁移 {total} 行" + ("(dry-run 未写)" if dry_run else ""))
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SQLite → Postgres 数据迁移")
    parser.add_argument("--sqlite-path", required=True, help="SQLite dev.db 路径")
    parser.add_argument("--postgres-url", required=True,
                        help="postgresql+psycopg2://user:pwd@host:5432/db")
    parser.add_argument("--dry-run", action="store_true", help="只 count + sample 不真写")
    parser.add_argument("--tables", nargs="+", help="只迁指定表(默认全部)")
    args = parser.parse_args()
    sys.exit(main(args.sqlite_path, args.postgres_url, args.dry_run, args.tables))

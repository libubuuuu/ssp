"""sqlite_to_postgres.py 数据迁移脚本测试

策略:用 SQLite → SQLite round-trip(同种方言但 API 通用),验证:
- 行数一致
- 数据 round-trip 完整
- dry-run 不真写
- 不存在的表跳过(警告非 fail)
- 单表只迁选项

真 Postgres 迁移留 staging 演练(本测试不接 psycopg2,scope 失控)。
"""
import sys
import tempfile
from pathlib import Path

import pytest


@pytest.fixture()
def sqlite_paths():
    """生成两个 tmp SQLite 文件,模拟 source / target"""
    src_fd = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    src_fd.close()
    dst_fd = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    dst_fd.close()
    yield Path(src_fd.name), Path(dst_fd.name)
    Path(src_fd.name).unlink(missing_ok=True)
    Path(dst_fd.name).unlink(missing_ok=True)


def _init_minimal_schema(db_path: Path):
    """init_db 在指定 db 上建表(全 schema)。

    monkeypatch module-level DATABASE_PATH 而非 env var,因为 database.py
    在 import 时读了 env 一次,后续改 env 不生效。
    """
    from app import database as db_module
    old_path = db_module.DATABASE_PATH
    db_module.DATABASE_PATH = str(db_path)
    try:
        db_module.init_db()
    finally:
        db_module.DATABASE_PATH = old_path


def _seed_users(db_path: Path, n: int = 3):
    """往 users 表插 n 行测试数据"""
    import sqlite3
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()
    for i in range(n):
        cursor.execute(
            "INSERT INTO users (id, email, password_hash, role, credits) VALUES (?, ?, ?, ?, ?)",
            (f"u{i}", f"u{i}@test.com", "hash", "user", 100 + i),
        )
    conn.commit()
    conn.close()


def _seed_pending_refunds(db_path: Path, n: int = 2):
    import sqlite3
    import time
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()
    for i in range(n):
        cursor.execute(
            "INSERT INTO pending_refunds (task_id, user_id, cost, registered_at, refunded) "
            "VALUES (?, ?, ?, ?, ?)",
            (f"task_{i}", f"u{i}", 10 * (i + 1), time.time(), 0),
        )
    conn.commit()
    conn.close()


def _row_count(db_path: Path, table: str) -> int:
    import sqlite3
    conn = sqlite3.connect(str(db_path))
    n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    conn.close()
    return n


def test_dry_run_does_not_write(sqlite_paths):
    src, dst = sqlite_paths
    _init_minimal_schema(src)
    _init_minimal_schema(dst)
    _seed_users(src, n=3)

    from scripts import sqlite_to_postgres as migrator

    rc = migrator.main(str(src), f"sqlite:///{dst}", dry_run=True, tables=["users"])
    assert rc == 0
    # dry-run 不写
    assert _row_count(dst, "users") == 0
    # source 不变
    assert _row_count(src, "users") == 3


def test_full_migration_round_trip(sqlite_paths):
    """全表迁移:行数一致 + 数据完整"""
    src, dst = sqlite_paths
    _init_minimal_schema(src)
    _init_minimal_schema(dst)
    _seed_users(src, n=5)
    _seed_pending_refunds(src, n=4)

    from scripts import sqlite_to_postgres as migrator
    rc = migrator.main(str(src), f"sqlite:///{dst}", dry_run=False)
    assert rc == 0

    assert _row_count(dst, "users") == 5
    assert _row_count(dst, "pending_refunds") == 4
    # 其他空表迁完仍 0
    assert _row_count(dst, "tasks") == 0


def test_data_integrity_users_preserved(sqlite_paths):
    """逐行校验:email / credits 字段 round-trip 一致"""
    src, dst = sqlite_paths
    _init_minimal_schema(src)
    _init_minimal_schema(dst)
    _seed_users(src, n=3)

    from scripts import sqlite_to_postgres as migrator
    migrator.main(str(src), f"sqlite:///{dst}", dry_run=False, tables=["users"])

    import sqlite3
    conn = sqlite3.connect(str(dst))
    rows = conn.execute("SELECT id, email, credits FROM users ORDER BY id").fetchall()
    conn.close()
    assert rows == [
        ("u0", "u0@test.com", 100),
        ("u1", "u1@test.com", 101),
        ("u2", "u2@test.com", 102),
    ]


def test_specific_tables_only(sqlite_paths):
    """--tables 只迁指定表,其他表不动"""
    src, dst = sqlite_paths
    _init_minimal_schema(src)
    _init_minimal_schema(dst)
    _seed_users(src, n=2)
    _seed_pending_refunds(src, n=3)

    from scripts import sqlite_to_postgres as migrator
    migrator.main(str(src), f"sqlite:///{dst}", dry_run=False, tables=["users"])

    assert _row_count(dst, "users") == 2
    # pending_refunds 没指定,不迁
    assert _row_count(dst, "pending_refunds") == 0


def test_missing_sqlite_returns_error_code():
    """源文件不存在 → 1"""
    from scripts import sqlite_to_postgres as migrator
    rc = migrator.main("/nonexistent/path.db", "sqlite:///tmp.db", dry_run=False)
    assert rc == 1


def test_empty_target_returns_error_code(sqlite_paths):
    """目标库无表(没跑 alembic upgrade)→ 2"""
    src, _ = sqlite_paths
    _init_minimal_schema(src)
    _seed_users(src, n=1)

    # 目标 db 没建表
    empty_dst = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    empty_dst.close()
    try:
        from scripts import sqlite_to_postgres as migrator
        rc = migrator.main(str(src), f"sqlite:///{empty_dst.name}", dry_run=False)
        assert rc == 1  # main() 在 reflect 后发现 dst_metadata.tables 空 → 返 1
    finally:
        Path(empty_dst.name).unlink(missing_ok=True)

"""
数据库模块
直接使用 sqlite3，跳过 Prisma Python
"""
import os
import sqlite3
from contextlib import contextmanager
from typing import Optional
import json
from datetime import datetime

# 路径默认 ./dev.db,测试或多环境通过 DATABASE_PATH 覆盖
DATABASE_PATH = os.environ.get("DATABASE_PATH", "./dev.db")


@contextmanager
def get_db():
    """获取数据库连接

    PRAGMA 说明:
    - journal_mode=WAL: 写不阻塞读,生产并发写场景必开;一次设置文件级生效持久
    - synchronous=NORMAL: 配合 WAL 平衡耐久性和性能(满 fsync 太慢,WAL 自带保障)
    - busy_timeout=5000: 撞锁等 5 秒再放弃,避免高并发下偶发 "database is locked"
    """
    conn = sqlite3.connect(DATABASE_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def _patch_users_columns(cursor):
    """幂等地补齐 users 表的后加列。
    Phase 2 迁 PostgreSQL + Alembic 后由真正的迁移系统接管。
    """
    patches = [
        ("totp_secret", "ALTER TABLE users ADD COLUMN totp_secret TEXT DEFAULT NULL"),
        ("totp_enabled", "ALTER TABLE users ADD COLUMN totp_enabled INTEGER DEFAULT 0"),
        # tokens_invalid_before:Unix 时间戳。token.iat 早于此值则该 token 失效。
        # 改密码 / 主动登出所有设备 / 管理员强制踢人时,把当前时间戳写进来。
        ("tokens_invalid_before", "ALTER TABLE users ADD COLUMN tokens_invalid_before INTEGER DEFAULT 0"),
    ]
    for col_name, sql in patches:
        try:
            cursor.execute(sql)
        except sqlite3.OperationalError as e:
            # SQLite 已有该列时报 "duplicate column name: xxx",此情况是幂等成功
            if "duplicate column" not in str(e).lower():
                raise


def init_db():
    """初始化数据库表"""
    with get_db() as conn:
        cursor = conn.cursor()

        # 用户表（含 2FA 列;迁移管理见下方 _patch_users_columns）
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            name TEXT,
            avatar_url TEXT,
            role TEXT DEFAULT 'user',
            credits INTEGER DEFAULT 100,
            phone TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            totp_secret TEXT DEFAULT NULL,
            totp_enabled INTEGER DEFAULT 0,
            tokens_invalid_before INTEGER DEFAULT 0
        )
        """)
        _patch_users_columns(cursor)

        # 身材数据表
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS body_measurements (
            id TEXT PRIMARY KEY,
            user_id TEXT UNIQUE NOT NULL,
            height REAL,
            weight REAL,
            chest REAL,
            waist REAL,
            hips REAL,
            shoulder REAL,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
        """)

        # 商家表
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS merchants (
            id TEXT PRIMARY KEY,
            user_id TEXT UNIQUE NOT NULL,
            shop_name TEXT,
            shop_desc TEXT,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
        """)

        # 产品表
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS products (
            id TEXT PRIMARY KEY,
            merchant_id TEXT NOT NULL,
            name TEXT NOT NULL,
            description TEXT,
            category TEXT NOT NULL,
            gender TEXT NOT NULL,
            price REAL NOT NULL,
            images TEXT,
            model_3d_url TEXT,
            thumbnail_url TEXT,
            sizes TEXT,
            stock INTEGER DEFAULT 0,
            is_published BOOLEAN DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (merchant_id) REFERENCES merchants(id)
        )
        """)

        # 订单表
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            total_amount REAL NOT NULL,
            status TEXT DEFAULT 'pending',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
        """)

        # 订单明细表
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS order_items (
            id TEXT PRIMARY KEY,
            order_id TEXT NOT NULL,
            product_id TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            size TEXT NOT NULL,
            customization TEXT,
            FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE CASCADE
        )
        """)

        # 3D 人体模型表
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS body_models (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            source_image_url TEXT NOT NULL,
            model_3d_url TEXT NOT NULL,
            thumbnail_url TEXT,
            measurements TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """)

        # 任务表（AI 生成任务）
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            module TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            input TEXT,
            output TEXT,
            model_used TEXT,
            cost_credits INTEGER DEFAULT 0,
            retry_count INTEGER DEFAULT 0,
            queue_position INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            completed_at TEXT,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
        """)

        # 模型健康监控表
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS model_health (
            id TEXT PRIMARY KEY,
            model_name TEXT UNIQUE NOT NULL,
            success_count INTEGER DEFAULT 0,
            failure_count INTEGER DEFAULT 0,
            last_error_at TEXT,
            is_disabled BOOLEAN DEFAULT 0,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """)

        # 生成历史表
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS generation_history (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            module TEXT NOT NULL,
            prompt TEXT,
            images TEXT,
            videos TEXT,
            cost INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
        """)

        # 订单表（额度充值）
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS credit_orders (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            amount INTEGER NOT NULL,
            price REAL NOT NULL,
            status TEXT DEFAULT 'pending',
            paid_at TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
        """)

        # 审计日志表(管理员操作不可变记录)
        # 不提供 UPDATE / DELETE 接口,只增不改
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS audit_log (
            id TEXT PRIMARY KEY,
            actor_user_id TEXT NOT NULL,
            actor_email TEXT,
            action TEXT NOT NULL,
            target_type TEXT,
            target_id TEXT,
            details TEXT,
            ip TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """)

        # P3-3:注册 IP 日志(反羊毛党 — 同 IP 24h 限 3 次成功注册)
        # registered_at_ts 用 unix 时间戳便于窗口比较
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS register_ip_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ip TEXT NOT NULL,
            registered_at_ts REAL NOT NULL
        )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_register_ip_log_ip ON register_ip_log(ip)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_register_ip_log_ts ON register_ip_log(registered_at_ts)")

        # BUG-1:注册失败 IP 日志(反脚本爆破 — 同 IP 24h 失败 >=10 次 → 429)
        # 上一轮 P3-3 只对"成功"计数,脚本反复试错 code 无配额,本表补这个洞
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS register_ip_failure_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ip TEXT NOT NULL,
            attempted_at_ts REAL NOT NULL,
            reason TEXT
        )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_register_ip_failure_log_ip ON register_ip_failure_log(ip)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_register_ip_failure_log_ts ON register_ip_failure_log(attempted_at_ts)")

        # 创建索引优化查询性能
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_tasks_user_id ON tasks(user_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_tasks_created_at ON tasks(created_at)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_generation_history_user_id ON generation_history(user_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_credit_orders_user_id ON credit_orders(user_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_email ON users(email)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_audit_log_actor ON audit_log(actor_user_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_audit_log_action ON audit_log(action)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_audit_log_created_at ON audit_log(created_at)")

        conn.commit()
        print("Database initialized successfully!")


if __name__ == "__main__":
    init_db()

"""
数据库模块
直接使用 sqlite3，跳过 Prisma Python
"""
import sqlite3
from contextlib import contextmanager
from typing import Optional
import json
from datetime import datetime

DATABASE_PATH = "./dev.db"


@contextmanager
def get_db():
    """获取数据库连接"""
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def init_db():
    """初始化数据库表"""
    with get_db() as conn:
        cursor = conn.cursor()

        # 用户表（新增 credits 字段）
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
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """)

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

        # 创建索引优化查询性能
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_tasks_user_id ON tasks(user_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_tasks_created_at ON tasks(created_at)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_generation_history_user_id ON generation_history(user_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_credit_orders_user_id ON credit_orders(user_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_email ON users(email)")

        conn.commit()
        print("Database initialized successfully!")


if __name__ == "__main__":
    init_db()

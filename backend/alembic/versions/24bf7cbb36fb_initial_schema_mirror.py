"""initial schema mirror — 镜像 app/database.py init_db()

Revision ID: 24bf7cbb36fb
Revises:
Create Date: 2026-04-28 15:36:38.367445

设计:
- 这是 Phase 2 Postgres 迁移的脚手架第一份 migration
- 完全镜像当前 app/database.py init_db() 创建的 schema(14 表 + 索引)
- 现有 dev.db 直接 `alembic stamp head` 标已迁,不真跑 upgrade
- fresh DB(测试 / 新部署 / Postgres) `alembic upgrade head` 生成等价 schema
- 用 op.create_table + sa.Column 而非 raw SQL,跨 SQLite/Postgres 兼容
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "24bf7cbb36fb"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # users — 主表(含 2FA + token 失效时间戳)
    op.create_table(
        "users",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("email", sa.Text(), nullable=False, unique=True),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("name", sa.Text()),
        sa.Column("avatar_url", sa.Text()),
        sa.Column("role", sa.Text(), server_default="user"),
        sa.Column("credits", sa.Integer(), server_default=sa.text("100")),
        sa.Column("phone", sa.Text()),
        sa.Column("created_at", sa.Text(), server_default=sa.func.current_timestamp()),
        sa.Column("updated_at", sa.Text(), server_default=sa.func.current_timestamp()),
        sa.Column("totp_secret", sa.Text()),
        sa.Column("totp_enabled", sa.Integer(), server_default=sa.text("0")),
        sa.Column("tokens_invalid_before", sa.Integer(), server_default=sa.text("0")),
    )
    op.create_index("idx_users_email", "users", ["email"])

    # body_measurements — 用户身材数据(1:1 user)
    op.create_table(
        "body_measurements",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("user_id", sa.Text(), nullable=False, unique=True),
        sa.Column("height", sa.Float()),
        sa.Column("weight", sa.Float()),
        sa.Column("chest", sa.Float()),
        sa.Column("waist", sa.Float()),
        sa.Column("hips", sa.Float()),
        sa.Column("shoulder", sa.Float()),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )

    # merchants — 商家(1:1 user)
    op.create_table(
        "merchants",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("user_id", sa.Text(), nullable=False, unique=True),
        sa.Column("shop_name", sa.Text()),
        sa.Column("shop_desc", sa.Text()),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )

    # products — 商品
    op.create_table(
        "products",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("merchant_id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("gender", sa.Text(), nullable=False),
        sa.Column("price", sa.Float(), nullable=False),
        sa.Column("images", sa.Text()),
        sa.Column("model_3d_url", sa.Text()),
        sa.Column("thumbnail_url", sa.Text()),
        sa.Column("sizes", sa.Text()),
        sa.Column("stock", sa.Integer(), server_default=sa.text("0")),
        sa.Column("is_published", sa.Boolean(), server_default=sa.text("0")),
        sa.Column("created_at", sa.Text(), server_default=sa.func.current_timestamp()),
        sa.Column("updated_at", sa.Text(), server_default=sa.func.current_timestamp()),
        sa.ForeignKeyConstraint(["merchant_id"], ["merchants.id"]),
    )

    # orders — 订单
    op.create_table(
        "orders",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("total_amount", sa.Float(), nullable=False),
        sa.Column("status", sa.Text(), server_default="pending"),
        sa.Column("created_at", sa.Text(), server_default=sa.func.current_timestamp()),
        sa.Column("updated_at", sa.Text(), server_default=sa.func.current_timestamp()),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
    )

    # order_items — 订单明细
    op.create_table(
        "order_items",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("order_id", sa.Text(), nullable=False),
        sa.Column("product_id", sa.Text(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("size", sa.Text(), nullable=False),
        sa.Column("customization", sa.Text()),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="CASCADE"),
    )

    # body_models — 3D 人体模型
    op.create_table(
        "body_models",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("source_image_url", sa.Text(), nullable=False),
        sa.Column("model_3d_url", sa.Text(), nullable=False),
        sa.Column("thumbnail_url", sa.Text()),
        sa.Column("measurements", sa.Text()),
        sa.Column("created_at", sa.Text(), server_default=sa.func.current_timestamp()),
    )

    # tasks — AI 生成任务
    op.create_table(
        "tasks",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("module", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default="pending"),
        sa.Column("input", sa.Text()),
        sa.Column("output", sa.Text()),
        sa.Column("model_used", sa.Text()),
        sa.Column("cost_credits", sa.Integer(), server_default=sa.text("0")),
        sa.Column("retry_count", sa.Integer(), server_default=sa.text("0")),
        sa.Column("queue_position", sa.Integer()),
        sa.Column("created_at", sa.Text(), server_default=sa.func.current_timestamp()),
        sa.Column("completed_at", sa.Text()),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
    )
    op.create_index("idx_tasks_user_id", "tasks", ["user_id"])
    op.create_index("idx_tasks_status", "tasks", ["status"])
    op.create_index("idx_tasks_created_at", "tasks", ["created_at"])

    # model_health — FAL 模型健康监控
    op.create_table(
        "model_health",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("model_name", sa.Text(), nullable=False, unique=True),
        sa.Column("success_count", sa.Integer(), server_default=sa.text("0")),
        sa.Column("failure_count", sa.Integer(), server_default=sa.text("0")),
        sa.Column("last_error_at", sa.Text()),
        sa.Column("is_disabled", sa.Boolean(), server_default=sa.text("0")),
        sa.Column("updated_at", sa.Text(), server_default=sa.func.current_timestamp()),
    )

    # generation_history — 生成历史(消费记录)
    op.create_table(
        "generation_history",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("module", sa.Text(), nullable=False),
        sa.Column("prompt", sa.Text()),
        sa.Column("images", sa.Text()),
        sa.Column("videos", sa.Text()),
        sa.Column("cost", sa.Integer(), server_default=sa.text("0")),
        sa.Column("created_at", sa.Text(), server_default=sa.func.current_timestamp()),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
    )
    op.create_index("idx_generation_history_user_id", "generation_history", ["user_id"])

    # credit_orders — 额度充值订单
    op.create_table(
        "credit_orders",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("price", sa.Float(), nullable=False),
        sa.Column("status", sa.Text(), server_default="pending"),
        sa.Column("paid_at", sa.Text()),
        sa.Column("created_at", sa.Text(), server_default=sa.func.current_timestamp()),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
    )
    op.create_index("idx_credit_orders_user_id", "credit_orders", ["user_id"])

    # audit_log — 管理员审计日志(只增不改)
    op.create_table(
        "audit_log",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("actor_user_id", sa.Text(), nullable=False),
        sa.Column("actor_email", sa.Text()),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("target_type", sa.Text()),
        sa.Column("target_id", sa.Text()),
        sa.Column("details", sa.Text()),
        sa.Column("ip", sa.Text()),
        sa.Column("created_at", sa.Text(), server_default=sa.func.current_timestamp()),
    )
    op.create_index("idx_audit_log_actor", "audit_log", ["actor_user_id"])
    op.create_index("idx_audit_log_action", "audit_log", ["action"])
    op.create_index("idx_audit_log_created_at", "audit_log", ["created_at"])

    # register_ip_log — P3-3 反羊毛党(成功注册 IP 24h 限 3 次)
    op.create_table(
        "register_ip_log",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("ip", sa.Text(), nullable=False),
        sa.Column("registered_at_ts", sa.Float(), nullable=False),
    )
    op.create_index("idx_register_ip_log_ip", "register_ip_log", ["ip"])
    op.create_index("idx_register_ip_log_ts", "register_ip_log", ["registered_at_ts"])

    # register_ip_failure_log — BUG-1 反脚本爆破(失败 IP 24h 限 10 次)
    op.create_table(
        "register_ip_failure_log",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("ip", sa.Text(), nullable=False),
        sa.Column("attempted_at_ts", sa.Float(), nullable=False),
        sa.Column("reason", sa.Text()),
    )
    op.create_index("idx_register_ip_failure_log_ip", "register_ip_failure_log", ["ip"])
    op.create_index("idx_register_ip_failure_log_ts", "register_ip_failure_log", ["attempted_at_ts"])


def downgrade() -> None:
    """Downgrade schema — 反向 drop 全部表。生产慎用,主要给测试 / 灾备演练"""
    # drop 顺序与 upgrade 相反,先 drop 引用表再 drop 被引用表
    op.drop_index("idx_register_ip_failure_log_ts", table_name="register_ip_failure_log")
    op.drop_index("idx_register_ip_failure_log_ip", table_name="register_ip_failure_log")
    op.drop_table("register_ip_failure_log")

    op.drop_index("idx_register_ip_log_ts", table_name="register_ip_log")
    op.drop_index("idx_register_ip_log_ip", table_name="register_ip_log")
    op.drop_table("register_ip_log")

    op.drop_index("idx_audit_log_created_at", table_name="audit_log")
    op.drop_index("idx_audit_log_action", table_name="audit_log")
    op.drop_index("idx_audit_log_actor", table_name="audit_log")
    op.drop_table("audit_log")

    op.drop_index("idx_credit_orders_user_id", table_name="credit_orders")
    op.drop_table("credit_orders")

    op.drop_index("idx_generation_history_user_id", table_name="generation_history")
    op.drop_table("generation_history")

    op.drop_table("model_health")

    op.drop_index("idx_tasks_created_at", table_name="tasks")
    op.drop_index("idx_tasks_status", table_name="tasks")
    op.drop_index("idx_tasks_user_id", table_name="tasks")
    op.drop_table("tasks")

    op.drop_table("body_models")
    op.drop_table("order_items")
    op.drop_table("orders")
    op.drop_table("products")
    op.drop_table("merchants")
    op.drop_table("body_measurements")

    op.drop_index("idx_users_email", table_name="users")
    op.drop_table("users")

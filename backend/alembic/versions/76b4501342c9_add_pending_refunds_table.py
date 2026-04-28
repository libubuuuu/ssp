"""add pending_refunds table

Revision ID: 76b4501342c9
Revises: 24bf7cbb36fb
Create Date: 2026-04-28 17:08:22.051455

异步任务失败退款追踪持久化:
- 之前 refund_tracker 用进程内存 dict,backend 重启 → 丢退款记录 → 失败任务永远不退
- 表 PRIMARY KEY = task_id 防同 task_id 重复 register
- 退款用原子 SQL UPDATE WHERE refunded=0,rowcount==1 才真退 → 多 tab/HTTP+WS 并发幂等
- registered_at 索引便于 GC(惰性触发,删 30 分钟前的 entries)
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '76b4501342c9'
down_revision: Union[str, Sequence[str], None] = '24bf7cbb36fb'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "pending_refunds",
        sa.Column("task_id", sa.Text(), primary_key=True),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("cost", sa.Integer(), nullable=False),
        sa.Column("registered_at", sa.Float(), nullable=False),
        sa.Column("refunded", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    op.create_index(
        "idx_pending_refunds_registered_at",
        "pending_refunds",
        ["registered_at"],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("idx_pending_refunds_registered_at", table_name="pending_refunds")
    op.drop_table("pending_refunds")

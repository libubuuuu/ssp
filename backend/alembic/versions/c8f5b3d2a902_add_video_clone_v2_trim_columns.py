"""add video_clone_v2 trim columns (smart slice hint)

Revision ID: c8f5b3d2a902
Revises: b7e3a2c5d901
Create Date: 2026-05-09 22:00:00.000000

P221 A2(2026-05-09)智能切片提示:9-15s / 17-23s 等非 8 倍数时长的视频,
让用户在 check-duration 端点选择丢弃哪段(末尾/开头/中间)。

新加 3 列(全 nullable + DEFAULT,跟 _patch_video_clone_v2_columns 等价):
- trimmed_seconds REAL DEFAULT 0:用户主动丢弃的秒数
- trim_start REAL:丢弃段开始(NULL = 没丢)
- trim_end REAL:丢弃段结束(NULL = 没丢)
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c8f5b3d2a902'
down_revision: Union[str, Sequence[str], None] = 'b7e3a2c5d901'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("video_clone_v2_jobs", sa.Column("trimmed_seconds", sa.Float(), nullable=True, server_default="0"))
    op.add_column("video_clone_v2_jobs", sa.Column("trim_start", sa.Float(), nullable=True))
    op.add_column("video_clone_v2_jobs", sa.Column("trim_end", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("video_clone_v2_jobs", "trim_end")
    op.drop_column("video_clone_v2_jobs", "trim_start")
    op.drop_column("video_clone_v2_jobs", "trimmed_seconds")

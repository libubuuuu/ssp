"""add oral dual-mask columns

Revision ID: 8a3f1c2d9b04
Revises: 76b4501342c9
Create Date: 2026-04-29 16:00:00.000000

七十七续 P9b:口播带货工作台双 mask + 双轮 inpaint。

变化:
- oral_sessions 加 4 列(全 nullable):
    person_mask_image_path / product_mask_image_path / swap1_video_url / swap1_fal_request_id
- legacy mask_image_path 保留(读老数据)
- swapped_video_url 语义重映射:第二轮 inpaint 输出(若无 product mask 则 = swap1_video_url),
  下游 lipsync 不变
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '8a3f1c2d9b04'
down_revision: Union[str, Sequence[str], None] = '76b4501342c9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("oral_sessions", sa.Column("person_mask_image_path", sa.Text(), nullable=True))
    op.add_column("oral_sessions", sa.Column("product_mask_image_path", sa.Text(), nullable=True))
    op.add_column("oral_sessions", sa.Column("swap1_video_url", sa.Text(), nullable=True))
    op.add_column("oral_sessions", sa.Column("swap1_fal_request_id", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("oral_sessions", "swap1_fal_request_id")
    op.drop_column("oral_sessions", "swap1_video_url")
    op.drop_column("oral_sessions", "product_mask_image_path")
    op.drop_column("oral_sessions", "person_mask_image_path")

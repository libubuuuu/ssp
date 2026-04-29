"""add oral_sessions.archived_at

Revision ID: c5d2e8f1a703
Revises: 8a3f1c2d9b04
Create Date: 2026-04-29 17:30:00.000000

七十七续 P12:oral_sessions 60 天 GC。

- 加 archived_at 列(nullable),非 NULL 表示目录已清
- DB row 保留(账单/审计/admin 后台),只清磁盘上的产物目录
- /opt/ssp/uploads/oral/<uid>/<sid>/ 整树 rmtree
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c5d2e8f1a703'
down_revision: Union[str, Sequence[str], None] = '8a3f1c2d9b04'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("oral_sessions", sa.Column("archived_at", sa.TIMESTAMP(), nullable=True))


def downgrade() -> None:
    op.drop_column("oral_sessions", "archived_at")

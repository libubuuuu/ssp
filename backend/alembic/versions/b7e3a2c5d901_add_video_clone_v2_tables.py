"""add video_clone_v2 tables

Revision ID: b7e3a2c5d901
Revises: c5d2e8f1a703
Create Date: 2026-05-09 00:00:00.000000

P221(2026-05-09) 视频复刻 V2 三张表 — 双版本下载架构。

详见:
- docs/P221-API-SCHEMA.md(v4 设计文档,9 端点 + 4 个产品功能)
- docs/P221-MIGRATION.sql(SQL 镜像)
- docs/legal/{terms-of-service.md(§6.3+§4.4.6),video-clone-v2-upload-disclaimer.md(C7),P221-LEGAL-DELTA.md}

变化:
- video_clone_v2_jobs(主任务表):type/replacement_mode/双版本 final_video_url_*/segments_plan JSON 等
- video_clone_v2_daily_budget(保险 3 看板,跨重启持久化)
- video_clone_v2_disclaimer_log(法务 §4.4.4/§4.4.6 事后举证)

跟 database.py init_db() 等价(项目双轨制)。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b7e3a2c5d901'
down_revision: Union[str, Sequence[str], None] = 'c5d2e8f1a703'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "video_clone_v2_jobs",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("replacement_mode", sa.Text(), nullable=False),
        sa.Column("tier", sa.Text(), nullable=True),
        sa.Column("segment_tiers", sa.Text(), nullable=True),
        sa.Column("input_video_url", sa.Text(), nullable=False),
        sa.Column("input_video_local_path", sa.Text(), nullable=True),
        sa.Column("input_video_duration_sec", sa.Float(), nullable=False),
        sa.Column("input_video_sha256", sa.Text(), nullable=True),
        sa.Column("image_urls", sa.Text(), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("prompt_compiled", sa.Text(), nullable=True),
        sa.Column("segments_plan", sa.Text(), nullable=False),
        sa.Column("segments_count", sa.Integer(), nullable=False),
        sa.Column("segments_results", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("final_video_url", sa.Text(), nullable=True),
        sa.Column("final_video_url_watermarked", sa.Text(), nullable=True),
        sa.Column("final_video_url_raw", sa.Text(), nullable=True),
        sa.Column("final_video_local_path", sa.Text(), nullable=True),
        sa.Column("total_credits_charged", sa.Integer(), nullable=False),
        sa.Column("total_credits_refunded", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("fal_cost_total_usd", sa.Float(), nullable=False, server_default="0"),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("error_step", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.TIMESTAMP(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("completed_at", sa.TIMESTAMP(), nullable=True),
        sa.Column("archived_at", sa.TIMESTAMP(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
    )
    op.create_index("idx_vc2_user_time", "video_clone_v2_jobs", ["user_id", "created_at"])
    op.create_index("idx_vc2_status", "video_clone_v2_jobs", ["status"])
    op.create_index("idx_vc2_archive", "video_clone_v2_jobs", ["archived_at", "completed_at"])
    op.create_index("idx_vc2_type", "video_clone_v2_jobs", ["type"])

    op.create_table(
        "video_clone_v2_daily_budget",
        sa.Column("date", sa.Text(), primary_key=True),
        sa.Column("spent_usd", sa.Float(), nullable=False, server_default="0"),
        sa.Column("locked", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.TIMESTAMP(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
    )

    op.create_table(
        "video_clone_v2_disclaimer_log",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("job_id", sa.Text(), nullable=False),
        sa.Column("ip", sa.Text(), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("video_sha256", sa.Text(), nullable=True),
        sa.Column("disclaimer_version", sa.Text(), nullable=False, server_default="v1"),
        sa.Column("acknowledged_at", sa.TIMESTAMP(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("watermark_choice", sa.Text(), nullable=True),
        sa.Column("watermark_chosen_at", sa.TIMESTAMP(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["job_id"], ["video_clone_v2_jobs.id"]),
    )
    op.create_index("idx_vc2_disclaimer_user", "video_clone_v2_disclaimer_log", ["user_id", "acknowledged_at"])
    op.create_index("idx_vc2_disclaimer_job", "video_clone_v2_disclaimer_log", ["job_id"])


def downgrade() -> None:
    op.drop_index("idx_vc2_disclaimer_job", table_name="video_clone_v2_disclaimer_log")
    op.drop_index("idx_vc2_disclaimer_user", table_name="video_clone_v2_disclaimer_log")
    op.drop_table("video_clone_v2_disclaimer_log")
    op.drop_table("video_clone_v2_daily_budget")
    op.drop_index("idx_vc2_type", table_name="video_clone_v2_jobs")
    op.drop_index("idx_vc2_archive", table_name="video_clone_v2_jobs")
    op.drop_index("idx_vc2_status", table_name="video_clone_v2_jobs")
    op.drop_index("idx_vc2_user_time", table_name="video_clone_v2_jobs")
    op.drop_table("video_clone_v2_jobs")

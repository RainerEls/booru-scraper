"""Add saved searches that run on a schedule

Revision ID: 0005
Revises: 0004
"""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "schedules",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("tags", sa.String(), nullable=False),
        sa.Column("blacklist_tags", sa.String(), nullable=False),
        sa.Column("rating", sa.String(), nullable=True),
        sa.Column("post_limit", sa.Integer(), nullable=False),
        sa.Column("interval_hours", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("last_run_at", sa.DateTime(), nullable=True),
    )
    op.add_column("runs", sa.Column("schedule_id", sa.Integer(), nullable=True))


def downgrade():
    op.drop_column("runs", "schedule_id")
    op.drop_table("schedules")

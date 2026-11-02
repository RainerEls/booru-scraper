"""Keep each run's log and the posts it couldn't upload

Revision ID: 0004
Revises: 0003
"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("runs", sa.Column("log", sa.JSON(), nullable=True))
    op.add_column("runs", sa.Column("failed_posts", sa.JSON(), nullable=True))


def downgrade():
    op.drop_column("runs", "failed_posts")
    op.drop_column("runs", "log")

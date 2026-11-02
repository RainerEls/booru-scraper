"""Record when a run finished

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("runs", sa.Column("finished_at", sa.DateTime(), nullable=True))


def downgrade():
    op.drop_column("runs", "finished_at")

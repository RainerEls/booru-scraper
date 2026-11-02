"""Record which run a retry came from

Revision ID: 0006
Revises: 0005
"""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("runs", sa.Column("retry_of", sa.Integer(), nullable=True))
    op.execute(
        r"UPDATE runs SET retry_of = substring(tags from '^Retry of run (\d+)$')::integer "
        r"WHERE tags ~ '^Retry of run \d+$'"
    )


def downgrade():
    op.drop_column("runs", "retry_of")

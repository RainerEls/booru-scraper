"""Record each post's parent, and which posts still need linking

Revision ID: 0008
Revises: 0007
"""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("posts", sa.Column("parent_post_id", sa.Integer(), nullable=True))
    op.add_column("posts", sa.Column("links_pending", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_index("ix_posts_source_parent_post_id", "posts", ["source", "parent_post_id"])


def downgrade():
    op.drop_index("ix_posts_source_parent_post_id", table_name="posts")
    op.drop_column("posts", "links_pending")
    op.drop_column("posts", "parent_post_id")

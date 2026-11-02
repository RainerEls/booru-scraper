"""Create any tables missing from a database that predates migrations

Revision ID: 0001
Revises:
"""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

metadata = sa.MetaData()


def timestamp(name):
    return sa.Column(name, sa.DateTime(), nullable=False)


sa.Table(
    "posts",
    metadata,
    sa.Column("id", sa.Integer(), primary_key=True),
    timestamp("created_at"),
    timestamp("updated_at"),
    sa.Column("status", sa.String(), nullable=False),
    sa.Column("source", sa.String(), nullable=False),
    sa.Column("source_post_id", sa.Integer(), nullable=False),
    sa.Column("szurubooru_post_id", sa.Integer(), nullable=True),
    sa.Column("md5", sa.String(), nullable=False),
    sa.Column("image_url", sa.String(), nullable=False),
    sa.UniqueConstraint("source", "source_post_id"),
    sa.UniqueConstraint("szurubooru_post_id"),
)
sa.Table(
    "tags",
    metadata,
    sa.Column("id", sa.Integer(), primary_key=True),
    timestamp("created_at"),
    timestamp("updated_at"),
    sa.Column("tag", sa.String(), nullable=False),
    sa.Column("type", sa.Integer(), nullable=False),
    sa.UniqueConstraint("tag"),
)
sa.Table(
    "runs",
    metadata,
    sa.Column("id", sa.Integer(), primary_key=True),
    sa.Column("uuid", sa.String(), nullable=False),
    timestamp("created_at"),
    sa.Column("source", sa.String(), nullable=False),
    sa.Column("tags", sa.String(), nullable=True),
    sa.Column("blacklist_tags", sa.String(), nullable=True),
    sa.Column("rating", sa.String(), nullable=True),
    sa.Column("post_limit", sa.Integer(), nullable=True),
    sa.Column("total", sa.Integer(), nullable=True),
    sa.Column("processed", sa.Integer(), nullable=True),
    sa.Column("skipped", sa.Integer(), nullable=True),
    sa.Column("failed", sa.Integer(), nullable=True),
    sa.Column("status", sa.String(), nullable=False),
    sa.UniqueConstraint("uuid"),
)
sa.Table(
    "integrity_checks",
    metadata,
    sa.Column("id", sa.Integer(), primary_key=True),
    timestamp("created_at"),
    sa.Column("status", sa.String(), nullable=False),
    sa.Column("report", sa.JSON(), nullable=True),
    sa.Column("error", sa.String(), nullable=True),
)


def upgrade():
    metadata.create_all(op.get_bind())


def downgrade():
    pass

"""Drop the unread tags.type column and index posts.md5

Revision ID: 0002
Revises: 0001
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE tags DROP COLUMN IF EXISTS type")
    op.execute("CREATE INDEX IF NOT EXISTS ix_posts_md5 ON posts (md5)")


def downgrade():
    op.execute("DROP INDEX IF EXISTS ix_posts_md5")
    op.execute("ALTER TABLE tags ADD COLUMN type INTEGER NOT NULL DEFAULT 0")

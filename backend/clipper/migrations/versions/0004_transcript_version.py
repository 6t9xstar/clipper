"""transcript version for optimistic concurrency

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-16 10:30:00
"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("transcripts") as b:
        b.add_column(sa.Column("version", sa.Integer(), nullable=False, server_default="1"))


def downgrade() -> None:
    with op.batch_alter_table("transcripts") as b:
        b.drop_column("version")

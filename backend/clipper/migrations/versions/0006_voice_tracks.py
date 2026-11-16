"""voice tracks (voiceover takes per clip)

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-16 22:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "voice_tracks",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("project_id", sa.String(length=32), nullable=False),
        sa.Column("asset_id", sa.String(length=32), nullable=False),
        sa.Column("clip_id", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("loudness_lufs", sa.Float(), nullable=True),
        sa.Column("segments", sa.JSON(), nullable=False),
        sa.Column("peaks", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["clip_id"], ["clips.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("voice_tracks") as b:
        b.create_index(b.f("ix_voice_tracks_asset_id"), ["asset_id"], unique=False)
        b.create_index(b.f("ix_voice_tracks_clip_id"), ["clip_id"], unique=False)
        b.create_index(b.f("ix_voice_tracks_project_id"), ["project_id"], unique=False)


def downgrade() -> None:
    op.drop_table("voice_tracks")

"""add generic and child audios

Revision ID: 20260531_0021
Revises: 20260531_0020
Create Date: 2026-05-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260531_0021"
down_revision: str | None = "20260531_0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "generic_audios",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("audio_url", sa.String(1024), nullable=False),
        sa.Column("image_url", sa.String(1024), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_generic_audios_name"),
    )
    op.create_index("ix_generic_audios_status", "generic_audios", ["status"])
    op.create_index("ix_generic_audios_created_at", "generic_audios", ["created_at"])

    op.create_table(
        "child_audios",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("child_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("audio_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["audio_id"], ["generic_audios.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["child_id"], ["child_profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("child_id", "audio_id", name="uq_child_audios_child_audio"),
    )
    op.create_index("ix_child_audios_child_id", "child_audios", ["child_id"])
    op.create_index("ix_child_audios_audio_id", "child_audios", ["audio_id"])
    op.create_index("ix_child_audios_created_at", "child_audios", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_child_audios_created_at", table_name="child_audios")
    op.drop_index("ix_child_audios_audio_id", table_name="child_audios")
    op.drop_index("ix_child_audios_child_id", table_name="child_audios")
    op.drop_table("child_audios")

    op.drop_index("ix_generic_audios_created_at", table_name="generic_audios")
    op.drop_index("ix_generic_audios_status", table_name="generic_audios")
    op.drop_table("generic_audios")

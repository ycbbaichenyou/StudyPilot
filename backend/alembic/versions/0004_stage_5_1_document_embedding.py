"""Add the Stage 5-1 document embedding status fields.

Revision ID: 0004_stage_5_1_document_embedding
Revises: 0003_stage_4_2_chunk
Create Date: 2026-09-13
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0004_stage_5_1_document_embedding"
down_revision: str | Sequence[str] | None = "0003_stage_4_2_chunk"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add embedding progress and active-generation metadata to documents."""
    op.add_column(
        "documents",
        sa.Column(
            "embedding_status",
            sa.String(length=50),
            nullable=False,
            server_default="pending",
        ),
    )
    op.add_column(
        "documents",
        sa.Column("embedding_error", sa.Text(), nullable=True),
    )
    op.add_column(
        "documents",
        sa.Column("embedded_at", sa.DateTime(timezone=False), nullable=True),
    )
    op.add_column(
        "documents",
        sa.Column("embedding_generation_id", sa.String(length=32), nullable=True),
    )


def downgrade() -> None:
    """Remove the Stage 5-1 document embedding status fields."""
    # The project's supported SQLite can drop these independent columns in place.
    # Avoiding batch table recreation is important: replacing `documents` would
    # activate its children's ON DELETE CASCADE actions and lose parsed content.
    op.drop_column("documents", "embedding_generation_id")
    op.drop_column("documents", "embedded_at")
    op.drop_column("documents", "embedding_error")
    op.drop_column("documents", "embedding_status")

"""Add the Stage 4-2 document chunk data model.

Revision ID: 0003_stage_4_2_chunk
Revises: 0002_stage_3_2_document_content
Create Date: 2026-09-12
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0003_stage_4_2_chunk"
down_revision: str | Sequence[str] | None = "0002_stage_3_2_document_content"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add ordered chunks derived from parsed document content."""
    op.create_table(
        "chunks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("document_content_id", sa.Integer(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("start_offset", sa.Integer(), nullable=False),
        sa.Column("end_offset", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=False), nullable=False),
        sa.CheckConstraint(
            "end_offset > start_offset",
            name="ck_chunks_end_offset_after_start_offset",
        ),
        sa.CheckConstraint(
            "sequence >= 0",
            name="ck_chunks_sequence_nonnegative",
        ),
        sa.CheckConstraint(
            "start_offset >= 0",
            name="ck_chunks_start_offset_nonnegative",
        ),
        sa.ForeignKeyConstraint(
            ["document_content_id"],
            ["document_contents.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "document_content_id",
            "sequence",
            name="uq_chunks_document_content_id_sequence",
        ),
    )
    op.create_index(
        op.f("ix_chunks_document_content_id"),
        "chunks",
        ["document_content_id"],
        unique=False,
    )


def downgrade() -> None:
    """Remove the Stage 4-2 document chunk data model."""
    op.drop_index(
        op.f("ix_chunks_document_content_id"),
        table_name="chunks",
    )
    op.drop_table("chunks")

"""Add the Stage 3-2 document parsing data model.

Revision ID: 0002_stage_3_2_document_content
Revises: 0001_stage_2_baseline
Create Date: 2026-09-11
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0002_stage_3_2_document_content"
down_revision: str | Sequence[str] | None = "0001_stage_2_baseline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add parsing metadata and ordered parsed document content."""
    op.add_column(
        "documents",
        sa.Column("parse_error", sa.Text(), nullable=True),
    )
    op.add_column(
        "documents",
        sa.Column("parsed_at", sa.DateTime(timezone=False), nullable=True),
    )
    op.create_table(
        "document_contents",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("source_type", sa.String(length=50), nullable=False),
        sa.Column("source_start", sa.Integer(), nullable=False),
        sa.Column("source_end", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=False), nullable=False),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "document_id",
            "sequence",
            name="uq_document_contents_document_id_sequence",
        ),
    )
    op.create_index(
        op.f("ix_document_contents_document_id"),
        "document_contents",
        ["document_id"],
        unique=False,
    )


def downgrade() -> None:
    """Remove the Stage 3-2 document parsing data model."""
    op.drop_index(
        op.f("ix_document_contents_document_id"),
        table_name="document_contents",
    )
    op.drop_table("document_contents")
    with op.batch_alter_table("documents") as batch_op:
        batch_op.drop_column("parsed_at")
        batch_op.drop_column("parse_error")

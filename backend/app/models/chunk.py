from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.document_content import DocumentContent


def naive_utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Chunk(Base):
    __tablename__ = "chunks"
    __table_args__ = (
        UniqueConstraint(
            "document_content_id",
            "sequence",
            name="uq_chunks_document_content_id_sequence",
        ),
        CheckConstraint("sequence >= 0", name="ck_chunks_sequence_nonnegative"),
        CheckConstraint(
            "start_offset >= 0",
            name="ck_chunks_start_offset_nonnegative",
        ),
        CheckConstraint(
            "end_offset > start_offset",
            name="ck_chunks_end_offset_after_start_offset",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    document_content_id: Mapped[int] = mapped_column(
        ForeignKey("document_contents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    start_offset: Mapped[int] = mapped_column(Integer, nullable=False)
    end_offset: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False),
        default=naive_utc_now,
        nullable=False,
    )

    document_content: Mapped[DocumentContent] = relationship(back_populates="chunks")

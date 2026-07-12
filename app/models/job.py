from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func

from app.queue.contracts import QUEUE_PAYLOAD_SCHEMA_VERSION
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class PendingJob(Base):
    __tablename__ = "pending_jobs"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_pending_jobs_idempotency_key"),
        Index("ix_pending_jobs_claim", "status", "lease_expires_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    paper_id: Mapped[int] = mapped_column(ForeignKey("papers.id", ondelete="RESTRICT"), index=True, nullable=False)
    task_type: Mapped[str] = mapped_column(String(40), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(
        String(255), default=lambda: f"legacy:{uuid4()}", nullable=False
    )
    attempt: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    claim_generation: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    payload_schema_version: Mapped[int] = mapped_column(
        Integer, default=QUEUE_PAYLOAD_SCHEMA_VERSION, nullable=False
    )
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    lease_owner: Mapped[str | None] = mapped_column(String(255))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    run = relationship("ExtractionRun", back_populates="task", uselist=False)

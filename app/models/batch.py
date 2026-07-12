from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

if TYPE_CHECKING:
    from app.models.job import PendingJob
    from app.models.persistence import ExtractionRun, Project
    from app.models.paper import Paper


def _uuid() -> str:
    return str(uuid4())


_BATCH_RUN_STATUSES = "'pending', 'running', 'succeeded', 'partial_failed', 'failed', 'cancelling', 'cancelled'"
_BATCH_ITEM_STATUSES = "'pending', 'queued', 'processing', 'succeeded', 'failed', 'reused', 'cancelled'"


class BatchRun(Base):
    __tablename__ = "batch_runs"
    __table_args__ = (
        UniqueConstraint("project_id", "submission_key", name="uq_batch_runs_project_submission_key"),
        CheckConstraint(f"status IN ({_BATCH_RUN_STATUSES})", name="ck_batch_runs_status"),
        CheckConstraint("batch_concurrency > 0", name="ck_batch_runs_concurrency_positive"),
        Index("ix_batch_runs_project_status_updated", "project_id", "status", "updated_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False)
    submission_key: Mapped[str] = mapped_column(String(255), nullable=False)
    source_root: Mapped[str] = mapped_column(String(2048), nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="pending")
    batch_concurrency: Mapped[int] = mapped_column(Integer, nullable=False)
    config_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    result_config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    project: Mapped[Project] = relationship()
    items: Mapped[list[BatchItem]] = relationship(back_populates="batch_run", order_by="BatchItem.ordinal")
    events: Mapped[list[BatchEvent]] = relationship(back_populates="batch_run", order_by="BatchEvent.id")


class BatchItem(Base):
    __tablename__ = "batch_items"
    __table_args__ = (
        UniqueConstraint("batch_run_id", "ordinal", name="uq_batch_items_run_ordinal"),
        UniqueConstraint("batch_run_id", "source_relative_path", name="uq_batch_items_run_relative_path"),
        CheckConstraint(f"status IN ({_BATCH_ITEM_STATUSES})", name="ck_batch_items_status"),
        Index("ix_batch_items_run_status", "batch_run_id", "status"),
        Index("ix_batch_items_source_sha256", "source_sha256"),
        Index("ix_batch_items_paper_id", "paper_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    batch_run_id: Mapped[str] = mapped_column(ForeignKey("batch_runs.id", ondelete="RESTRICT"), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    source_relative_path: Mapped[str] = mapped_column(String(2048), nullable=False)
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    source_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="pending")
    current_stage: Mapped[str | None] = mapped_column(String(80))
    paper_id: Mapped[int | None] = mapped_column(ForeignKey("papers.id", ondelete="RESTRICT"))
    resolved_extraction_run_id: Mapped[str | None] = mapped_column(ForeignKey("extraction_runs.id", ondelete="RESTRICT"))
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    batch_run: Mapped[BatchRun] = relationship(back_populates="items")
    paper: Mapped[Paper | None] = relationship()
    resolved_extraction_run: Mapped[ExtractionRun | None] = relationship(foreign_keys=[resolved_extraction_run_id])
    jobs: Mapped[list[PendingJob]] = relationship(back_populates="batch_item")
    events: Mapped[list[BatchEvent]] = relationship(back_populates="batch_item", order_by="BatchEvent.id")


class BatchEvent(Base):
    __tablename__ = "batch_events"
    __table_args__ = (
        Index("ix_batch_events_run_id", "batch_run_id", "id"),
        Index("ix_batch_events_item_id", "batch_item_id", "id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    batch_run_id: Mapped[str] = mapped_column(ForeignKey("batch_runs.id", ondelete="RESTRICT"), nullable=False)
    batch_item_id: Mapped[str | None] = mapped_column(ForeignKey("batch_items.id", ondelete="RESTRICT"))
    event_type: Mapped[str] = mapped_column(String(120), nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    batch_run: Mapped[BatchRun] = relationship(back_populates="events")
    batch_item: Mapped[BatchItem | None] = relationship(back_populates="events")

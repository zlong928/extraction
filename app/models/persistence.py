from __future__ import annotations

from datetime import datetime
from typing import Any, TYPE_CHECKING
from uuid import uuid4

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    event,
    func,
    inspect,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

if TYPE_CHECKING:
    from app.models.job import PendingJob
    from app.models.paper import Paper, PaperAsset


def _uuid() -> str:
    return str(uuid4())


class ImmutableRecordError(RuntimeError):
    pass


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    papers: Mapped[list[Paper]] = relationship(back_populates="project")
    deliveries: Mapped[list[DeliveryVersion]] = relationship(back_populates="project")


class StorageObject(Base):
    __tablename__ = "storage_objects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    object_key: Mapped[str] = mapped_column(String(1024), unique=True, nullable=False)
    uri: Mapped[str] = mapped_column(String(2048), unique=True, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    media_type: Mapped[str] = mapped_column(String(255), nullable=False)
    etag: Mapped[str | None] = mapped_column(String(255))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class ExtractionRun(Base):
    __tablename__ = "extraction_runs"
    __table_args__ = (
        UniqueConstraint("task_id", name="uq_extraction_runs_task_id"),
        Index("ix_extraction_runs_paper_status_created", "paper_id", "status", "created_at"),
        Index("ix_extraction_runs_input_pipeline", "input_object_id", "pipeline_version"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    task_id: Mapped[int] = mapped_column(ForeignKey("pending_jobs.id", ondelete="RESTRICT"), nullable=False)
    paper_id: Mapped[int] = mapped_column(ForeignKey("papers.id", ondelete="RESTRICT"), nullable=False)
    input_object_id: Mapped[str] = mapped_column(ForeignKey("storage_objects.id", ondelete="RESTRICT"), nullable=False)
    source_asset_id: Mapped[int | None] = mapped_column(ForeignKey("paper_assets.id", ondelete="RESTRICT"))
    parent_run_id: Mapped[str | None] = mapped_column(ForeignKey("extraction_runs.id", ondelete="RESTRICT"))
    attempt: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    model_provider: Mapped[str] = mapped_column(String(120), nullable=False)
    model_name: Mapped[str] = mapped_column(String(255), nullable=False)
    model_version: Mapped[str] = mapped_column(String(255), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(255), nullable=False)
    pipeline_version: Mapped[str] = mapped_column(String(255), nullable=False)
    config_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(40), index=True, nullable=False, default="running")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_type: Mapped[str | None] = mapped_column(String(255))
    error_message: Mapped[str | None] = mapped_column(Text)
    raw_output_object_id: Mapped[str | None] = mapped_column(ForeignKey("storage_objects.id", ondelete="RESTRICT"))
    normalized_schema_version: Mapped[str | None] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    task: Mapped[PendingJob] = relationship(back_populates="run")
    paper: Mapped[Paper] = relationship(back_populates="extraction_runs", foreign_keys=[paper_id])
    source_asset: Mapped[PaperAsset | None] = relationship(foreign_keys=[source_asset_id])
    input_object: Mapped[StorageObject] = relationship(foreign_keys=[input_object_id])
    raw_output_object: Mapped[StorageObject | None] = relationship(foreign_keys=[raw_output_object_id])
    results: Mapped[list[StructuredResult]] = relationship(
        back_populates="run", cascade="all, delete-orphan", order_by="StructuredResult.natural_key"
    )
    artifacts: Mapped[list[RunArtifact]] = relationship(
        back_populates="run", cascade="all, delete-orphan", order_by="RunArtifact.filename"
    )


class RunArtifact(Base):
    __tablename__ = "run_artifacts"
    __table_args__ = (
        UniqueConstraint("run_id", "role", "filename", name="uq_run_artifact_role_filename"),
        Index("ix_run_artifacts_run_role", "run_id", "role"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(ForeignKey("extraction_runs.id", ondelete="CASCADE"), nullable=False)
    object_id: Mapped[str] = mapped_column(ForeignKey("storage_objects.id", ondelete="RESTRICT"), nullable=False)
    role: Mapped[str] = mapped_column(String(120), nullable=False)
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    run: Mapped[ExtractionRun] = relationship(back_populates="artifacts")
    object: Mapped[StorageObject] = relationship()


class StructuredResult(Base):
    __tablename__ = "structured_results"
    __table_args__ = (
        UniqueConstraint("run_id", "result_type", "natural_key", name="uq_structured_result_natural_key"),
        Index("ix_structured_results_run_type", "run_id", "result_type"),
        Index("ix_structured_results_paper_panel", "paper_id", "panel_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(ForeignKey("extraction_runs.id", ondelete="CASCADE"), nullable=False)
    paper_id: Mapped[int] = mapped_column(ForeignKey("papers.id", ondelete="RESTRICT"), nullable=False)
    source_asset_id: Mapped[int | None] = mapped_column(ForeignKey("paper_assets.id", ondelete="RESTRICT"))
    result_type: Mapped[str] = mapped_column(String(120), nullable=False)
    natural_key: Mapped[str] = mapped_column(String(500), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(120), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    page_number: Mapped[int | None] = mapped_column(Integer)
    figure_id: Mapped[str | None] = mapped_column(String(300), index=True)
    panel_id: Mapped[str | None] = mapped_column(String(300), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    run: Mapped[ExtractionRun] = relationship(back_populates="results")


class DeliveryVersion(Base):
    __tablename__ = "delivery_versions"
    __table_args__ = (Index("ix_delivery_versions_project_status", "project_id", "status"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False)
    version: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(40), index=True, nullable=False, default="building")
    data_scope: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    snapshot_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    database_schema_version: Mapped[str] = mapped_column(String(120), nullable=False)
    pipeline_version: Mapped[str] = mapped_column(String(255), nullable=False)
    model_prompt_versions: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    record_counts: Mapped[dict[str, int]] = mapped_column(JSON, nullable=False, default=dict)
    manifest_object_id: Mapped[str | None] = mapped_column(ForeignKey("storage_objects.id", ondelete="RESTRICT"))
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    project: Mapped[Project] = relationship(back_populates="deliveries")
    artifacts: Mapped[list[DeliveryArtifact]] = relationship(
        back_populates="delivery_version", cascade="all, delete-orphan", order_by="DeliveryArtifact.filename"
    )


class DeliveryArtifact(Base):
    __tablename__ = "delivery_artifacts"
    __table_args__ = (
        UniqueConstraint("delivery_version_id", "filename", name="uq_delivery_artifact_filename"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    delivery_version_id: Mapped[str] = mapped_column(
        ForeignKey("delivery_versions.id", ondelete="CASCADE"), nullable=False
    )
    object_id: Mapped[str] = mapped_column(ForeignKey("storage_objects.id", ondelete="RESTRICT"), nullable=False)
    format: Mapped[str] = mapped_column(String(40), nullable=False)
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    media_type: Mapped[str] = mapped_column(String(255), nullable=False)
    row_count: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    delivery_version: Mapped[DeliveryVersion] = relationship(back_populates="artifacts")
    object: Mapped[StorageObject] = relationship()


_TERMINAL_RUN_STATUSES = {"succeeded", "partial_failure", "failed", "cancelled"}
_PUBLISHED_DELIVERY_STATUSES = {"published", "failed"}


@event.listens_for(ExtractionRun, "before_update")
def _prevent_terminal_run_updates(_mapper, _connection, target: ExtractionRun) -> None:
    state = inspect(target)
    history = state.attrs.status.history
    previous = history.deleted
    finalizing = bool(previous and previous[0] not in _TERMINAL_RUN_STATUSES and target.status in _TERMINAL_RUN_STATUSES)
    if target.status in _TERMINAL_RUN_STATUSES and not finalizing:
        raise ImmutableRecordError(f"ExtractionRun {target.id} is terminal and cannot be modified")


@event.listens_for(ExtractionRun, "before_delete")
def _prevent_run_deletion(_mapper, _connection, target: ExtractionRun) -> None:
    raise ImmutableRecordError(f"ExtractionRun {target.id} is an audit fact and cannot be deleted")


@event.listens_for(StructuredResult, "before_update")
@event.listens_for(StructuredResult, "before_delete")
def _prevent_structured_result_mutation(_mapper, _connection, target: StructuredResult) -> None:
    raise ImmutableRecordError(f"StructuredResult {target.id} is an immutable run fact")


@event.listens_for(StructuredResult, "before_insert")
def _prevent_result_append_to_terminal_run(_mapper, connection, target: StructuredResult) -> None:
    status = connection.execute(
        ExtractionRun.__table__.select()
        .with_only_columns(ExtractionRun.__table__.c.status)
        .where(ExtractionRun.__table__.c.id == target.run_id)
    ).scalar_one()
    if status in _TERMINAL_RUN_STATUSES:
        raise ImmutableRecordError(f"ExtractionRun {target.run_id} is terminal; results cannot be appended")


@event.listens_for(RunArtifact, "before_update")
@event.listens_for(RunArtifact, "before_delete")
def _prevent_run_artifact_mutation(_mapper, _connection, target: RunArtifact) -> None:
    raise ImmutableRecordError(f"RunArtifact {target.id} is immutable")


@event.listens_for(RunArtifact, "before_insert")
def _prevent_artifact_append_to_terminal_run(_mapper, connection, target: RunArtifact) -> None:
    status = connection.execute(
        ExtractionRun.__table__.select()
        .with_only_columns(ExtractionRun.__table__.c.status)
        .where(ExtractionRun.__table__.c.id == target.run_id)
    ).scalar_one()
    if status in _TERMINAL_RUN_STATUSES:
        raise ImmutableRecordError(f"ExtractionRun {target.run_id} is terminal; artifacts cannot be appended")


@event.listens_for(StorageObject, "before_update")
@event.listens_for(StorageObject, "before_delete")
def _prevent_storage_object_mutation(_mapper, _connection, target: StorageObject) -> None:
    raise ImmutableRecordError(f"StorageObject {target.id} is a content-addressed fact")


@event.listens_for(DeliveryVersion, "before_update")
def _prevent_published_delivery_updates(_mapper, _connection, target: DeliveryVersion) -> None:
    state = inspect(target)
    history = state.attrs.status.history
    previous = history.deleted
    publishing = bool(
        previous and previous[0] not in _PUBLISHED_DELIVERY_STATUSES and target.status in _PUBLISHED_DELIVERY_STATUSES
    )
    if target.status in _PUBLISHED_DELIVERY_STATUSES and not publishing:
        raise ImmutableRecordError(f"DeliveryVersion {target.version} is immutable")


@event.listens_for(DeliveryVersion, "before_delete")
def _prevent_delivery_deletion(_mapper, _connection, target: DeliveryVersion) -> None:
    raise ImmutableRecordError(f"DeliveryVersion {target.version} cannot be deleted")


@event.listens_for(DeliveryArtifact, "before_update")
@event.listens_for(DeliveryArtifact, "before_delete")
def _prevent_delivery_artifact_mutation(_mapper, _connection, target: DeliveryArtifact) -> None:
    raise ImmutableRecordError(f"DeliveryArtifact {target.id} is immutable")


@event.listens_for(DeliveryArtifact, "before_insert")
def _prevent_artifact_append_to_published_delivery(_mapper, connection, target: DeliveryArtifact) -> None:
    status = connection.execute(
        DeliveryVersion.__table__.select()
        .with_only_columns(DeliveryVersion.__table__.c.status)
        .where(DeliveryVersion.__table__.c.id == target.delivery_version_id)
    ).scalar_one()
    if status in _PUBLISHED_DELIVERY_STATUSES:
        raise ImmutableRecordError(
            f"DeliveryVersion {target.delivery_version_id} is published; artifacts cannot be appended"
        )

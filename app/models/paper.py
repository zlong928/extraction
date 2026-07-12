from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.enums import ExtractionStatus, PaperStatus

if TYPE_CHECKING:
    from app.models.figure import Figure, Panel
    from app.models.persistence import ExtractionRun, Project, StorageObject


class Paper(Base):
    __tablename__ = "papers"
    __table_args__ = (
        Index(
            "uq_papers_project_active_hash",
            "project_id",
            "file_hash",
            unique=True,
            postgresql_where=text("status <> 'deleted'"),
            sqlite_where=text("status <> 'deleted'"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="RESTRICT"), default=1, index=True
    )
    pdf_object_id: Mapped[str | None] = mapped_column(
        ForeignKey("storage_objects.id", ondelete="RESTRICT"), unique=True
    )
    mineru_content_object_id: Mapped[str | None] = mapped_column(ForeignKey("storage_objects.id", ondelete="RESTRICT"))
    mineru_layout_object_id: Mapped[str | None] = mapped_column(ForeignKey("storage_objects.id", ondelete="RESTRICT"))
    mineru_markdown_object_id: Mapped[str | None] = mapped_column(ForeignKey("storage_objects.id", ondelete="RESTRICT"))
    latest_audit_object_id: Mapped[str | None] = mapped_column(ForeignKey("storage_objects.id", ondelete="RESTRICT"))
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(500), nullable=False)
    file_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
    file_hash: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    mime_type: Mapped[str] = mapped_column(String(120), default="application/pdf", nullable=False)
    status: Mapped[str] = mapped_column(String(40), default=PaperStatus.PENDING.value, index=True, nullable=False)
    text_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    mineru_markdown: Mapped[str | None] = mapped_column(Text, nullable=True)
    mineru_artifact_dir: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    mineru_extract_dir: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    mineru_content_list_path: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    layout_data: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    assets: Mapped[list[PaperAsset]] = relationship(
        back_populates="paper",
        cascade="all, delete-orphan",
        order_by="PaperAsset.id",
    )
    figures: Mapped[list[Figure]] = relationship(
        back_populates="paper",
        cascade="all, delete-orphan",
        order_by="Figure.id",
    )
    project: Mapped[Project | None] = relationship(back_populates="papers")
    pdf_object: Mapped[StorageObject | None] = relationship(foreign_keys=[pdf_object_id])
    mineru_content_object: Mapped[StorageObject | None] = relationship(foreign_keys=[mineru_content_object_id])
    mineru_layout_object: Mapped[StorageObject | None] = relationship(foreign_keys=[mineru_layout_object_id])
    mineru_markdown_object: Mapped[StorageObject | None] = relationship(foreign_keys=[mineru_markdown_object_id])
    latest_audit_object: Mapped[StorageObject | None] = relationship(foreign_keys=[latest_audit_object_id])
    extraction_runs: Mapped[list[ExtractionRun]] = relationship(
        back_populates="paper", foreign_keys="ExtractionRun.paper_id", order_by="ExtractionRun.created_at"
    )


class PaperAsset(Base):
    __tablename__ = "paper_assets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    paper_id: Mapped[int] = mapped_column(ForeignKey("papers.id"), index=True, nullable=False)
    figure_id: Mapped[int | None] = mapped_column(ForeignKey("figures.id"), nullable=True, index=True)
    object_id: Mapped[str | None] = mapped_column(ForeignKey("storage_objects.id", ondelete="RESTRICT"), index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True, nullable=False)
    asset_type: Mapped[str] = mapped_column(String(40), default="image", index=True, nullable=False)
    asset_index: Mapped[int] = mapped_column(Integer, nullable=False)
    label: Mapped[str | None] = mapped_column(String(300), nullable=True)
    page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    file_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(120), default="image/png", nullable=False)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    caption: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    paper: Mapped[Paper] = relationship(back_populates="assets")
    figure: Mapped[Figure | None] = relationship(back_populates="assets")
    panel: Mapped[Panel | None] = relationship(back_populates="asset", uselist=False)
    extractions: Mapped[list[ImageExtraction]] = relationship(
        back_populates="asset",
        cascade="all, delete-orphan",
        order_by="ImageExtraction.id",
    )
    object: Mapped[StorageObject | None] = relationship(foreign_keys=[object_id])


class ImageExtraction(Base):
    __tablename__ = "image_extractions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("paper_assets.id"), index=True, nullable=False)
    figure_id: Mapped[int | None] = mapped_column(ForeignKey("figures.id"), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(40), default=ExtractionStatus.PENDING.value, index=True, nullable=False)
    query: Mapped[str | None] = mapped_column(Text, nullable=True)
    csv_path: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    asset: Mapped[PaperAsset] = relationship(back_populates="extractions")
    figure: Mapped[Figure | None] = relationship(back_populates="extractions")

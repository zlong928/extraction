from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

if TYPE_CHECKING:
    from app.models.paper import ImageExtraction, Paper, PaperAsset


class Figure(Base):
    __tablename__ = "figures"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    paper_id: Mapped[int] = mapped_column(ForeignKey("papers.id"), index=True, nullable=False)
    figure_id: Mapped[str] = mapped_column(String(300), nullable=False)
    caption_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_multi_panel: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    panel_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    paper: Mapped[Paper] = relationship(back_populates="figures")
    assets: Mapped[list[PaperAsset]] = relationship(back_populates="figure")
    extractions: Mapped[list[ImageExtraction]] = relationship(back_populates="figure")
    panels: Mapped[list[Panel]] = relationship(back_populates="figure", cascade="all, delete-orphan", order_by="Panel.panel_index")


class Panel(Base):
    __tablename__ = "panels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    figure_id: Mapped[int] = mapped_column(ForeignKey("figures.id"), index=True, nullable=False)
    asset_id: Mapped[int | None] = mapped_column(ForeignKey("paper_assets.id"), nullable=True, index=True)
    panel_id: Mapped[str] = mapped_column(String(300), nullable=False)
    evidence_shape: Mapped[str] = mapped_column(String(80), default="unknown", nullable=False)
    domain_task: Mapped[str] = mapped_column(String(80), default="unknown", nullable=False)
    extractor: Mapped[str] = mapped_column(String(120), default="overview_schematic_extractor", nullable=False)
    extraction_priority: Mapped[str] = mapped_column(String(40), default="panel_level", nullable=False)
    panel_index: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    figure: Mapped[Figure] = relationship(back_populates="panels")
    asset: Mapped[PaperAsset | None] = relationship(back_populates="panel")

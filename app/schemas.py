from __future__ import annotations

import json
from datetime import datetime

from pydantic import BaseModel, Field

from app.models import ExtractionStatus, Figure, ImageExtraction, Panel, Paper, PaperAsset, PaperStatus
from app.services.pdf import audit_summary_for_paper


class PanelRead(BaseModel):
    id: int
    figure_id: int
    asset_id: int | None = None
    panel_id: str
    panel_type: str = "unusable"
    domain_task: str
    extractor: str
    extraction_priority: str
    panel_index: int
    metadata: dict = Field(default_factory=dict)
    created_at: datetime

    @classmethod
    def from_model(cls, panel: Panel) -> PanelRead:
        metadata = _safe_json(panel.metadata_json)
        return cls(
            id=panel.id,
            figure_id=panel.figure_id,
            asset_id=panel.asset_id,
            panel_id=panel.panel_id,
            panel_type=str(metadata.get("panel_type") or "unusable"),
            domain_task=panel.domain_task,
            extractor=panel.extractor,
            extraction_priority=panel.extraction_priority,
            panel_index=panel.panel_index,
            metadata=metadata,
            created_at=panel.created_at,
        )


class FigureRead(BaseModel):
    id: int
    paper_id: int
    figure_id: str
    caption_text: str | None = None
    page_number: int | None = None
    is_multi_panel: bool
    panel_count: int
    metadata: dict = Field(default_factory=dict)
    created_at: datetime
    panels: list[PanelRead] = Field(default_factory=list)
    assets: list[int] = Field(default_factory=list)

    @classmethod
    def from_model(cls, figure: Figure) -> FigureRead:
        metadata = _safe_json(figure.metadata_json)
        return cls(
            id=figure.id,
            paper_id=figure.paper_id,
            figure_id=figure.figure_id,
            caption_text=figure.caption_text,
            page_number=figure.page_number,
            is_multi_panel=figure.is_multi_panel,
            panel_count=figure.panel_count,
            metadata=metadata,
            created_at=figure.created_at,
            panels=[PanelRead.from_model(p) for p in figure.panels],
            assets=[a.id for a in figure.assets],
        )


class AssetRead(BaseModel):
    id: int
    paper_id: int
    figure_id: int | None = None
    asset_type: str
    label: str | None
    page_number: int | None
    image_url: str
    mime_type: str
    width: int | None
    height: int | None
    metadata: dict = Field(default_factory=dict)
    latest_extraction: ExtractionRead | None = None
    created_at: datetime

    @classmethod
    def from_model(cls, asset: PaperAsset) -> AssetRead:
        metadata = _safe_json(asset.metadata_json)
        latest_extraction = asset.extractions[-1] if asset.extractions else None
        return cls(
            id=asset.id,
            paper_id=asset.paper_id,
            figure_id=asset.figure_id,
            asset_type=asset.asset_type,
            label=asset.label,
            page_number=asset.page_number,
            image_url=f"/papers/assets/{asset.id}",
            mime_type=asset.mime_type,
            width=asset.width,
            height=asset.height,
            metadata=metadata,
            latest_extraction=ExtractionRead.from_model(latest_extraction) if latest_extraction else None,
            created_at=asset.created_at,
        )


class PaperRead(BaseModel):
    id: int
    title: str
    original_filename: str
    status: str
    page_count: int | None
    asset_count: int
    figure_count: int
    text_preview: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime
    audit_summary: dict | None = None
    assets: list[AssetRead] = Field(default_factory=list)
    figures: list[FigureRead] = Field(default_factory=list)

    @classmethod
    def from_model(cls, paper: Paper, *, include_assets: bool = True, include_figures: bool = False) -> PaperRead:
        text = (paper.text_content or "").strip()
        active_assets = [asset for asset in paper.assets if asset.is_active]
        return cls(
            id=paper.id,
            title=paper.title,
            original_filename=paper.original_filename,
            status=_status_value(paper.status, PaperStatus),
            page_count=paper.page_count,
            asset_count=len(active_assets),
            figure_count=len(paper.figures),
            text_preview=text[:500] if text else None,
            error_message=paper.error_message,
            created_at=paper.created_at,
            updated_at=paper.updated_at,
            audit_summary=audit_summary_for_paper(paper),
            assets=[AssetRead.from_model(asset) for asset in active_assets] if include_assets else [],
            figures=[FigureRead.from_model(fig) for fig in paper.figures] if include_figures else [],
        )


class ExtractionRead(BaseModel):
    id: int
    asset_id: int
    figure_id: int | None = None
    status: str
    query: str | None
    csv_url: str | None
    result: dict | None
    error_message: str | None
    created_at: datetime
    completed_at: datetime | None

    @classmethod
    def from_model(cls, extraction: ImageExtraction) -> ExtractionRead:
        result = _safe_json(extraction.result_json)
        return cls(
            id=extraction.id,
            asset_id=extraction.asset_id,
            figure_id=extraction.figure_id,
            status=_status_value(extraction.status, ExtractionStatus),
            query=extraction.query,
            csv_url=f"/extractions/{extraction.id}/csv" if extraction.csv_path else None,
            result=result,
            error_message=extraction.error_message,
            created_at=extraction.created_at,
            completed_at=extraction.completed_at,
        )


def _safe_json(value: str | None) -> dict | None:
    if not value:
        return None
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        return None


def _status_value(value, enum_cls) -> str:
    if isinstance(value, enum_cls):
        return value.value
    text = str(value)
    if text in enum_cls.__members__:
        return enum_cls.__members__[text].value
    values = {item.value for item in enum_cls}
    return text if text in values else text.lower()

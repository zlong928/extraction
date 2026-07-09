from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from content_pipeline.contracts.blocks import ContentBlock


@dataclass(slots=True)
class SpatialRelation:
    source_block_id: str
    target_block_id: str
    relation: str
    score: float


@dataclass(slots=True)
class PageGraph:
    page_idx: int
    blocks_by_reading_order: list[ContentBlock] = field(default_factory=list)
    blocks_by_spatial_order: list[ContentBlock] = field(default_factory=list)
    headings: list[ContentBlock] = field(default_factory=list)
    figures: list[ContentBlock] = field(default_factory=list)
    tables: list[ContentBlock] = field(default_factory=list)
    formulas: list[ContentBlock] = field(default_factory=list)
    spatial_relations: list[SpatialRelation] = field(default_factory=list)


@dataclass(slots=True)
class DocumentGraph:
    blocks: list[ContentBlock]
    pages: dict[int, PageGraph]
    global_index: dict[str, ContentBlock]
    image_blocks: list[ContentBlock]
    chart_blocks: list[ContentBlock]
    table_blocks: list[ContentBlock]
    formula_blocks: list[ContentBlock]
    heading_blocks: list[ContentBlock]
    reference_blocks: list[ContentBlock]
    filtered_blocks: list[ContentBlock] = field(default_factory=list)
    filtered_type_counts: dict[str, int] = field(default_factory=dict)

    def summary(self) -> dict[str, Any]:
        return {
            "block_count": len(self.blocks),
            "page_count": len(self.pages),
            "image_count": len(self.image_blocks),
            "chart_count": len(self.chart_blocks),
            "table_count": len(self.table_blocks),
            "formula_count": len(self.formula_blocks),
            "reference_count": len(self.reference_blocks),
            "filtered_block_count": len(self.filtered_blocks),
            "filtered_type_counts": dict(self.filtered_type_counts),
        }


@dataclass(slots=True)
class FigureNode:
    figure_id: str
    label: str | None
    page_idx: int
    image_blocks: list[str] = field(default_factory=list)
    chart_blocks: list[str] = field(default_factory=list)
    caption_blocks: list[str] = field(default_factory=list)
    parent_section: dict[str, Any] | None = None
    bbox_union: list[float] | None = None
    panels: list["PanelNode"] = field(default_factory=list)
    related_tables: list[str] = field(default_factory=list)
    related_formulas: list[str] = field(default_factory=list)
    related_references: list[str] = field(default_factory=list)
    provenance: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class PanelNode:
    panel_id: str
    panel_label: str | None
    parent_figure_id: str
    page_idx: int
    image_block_id: str | None = None
    chart_block_id: str | None = None
    caption_block_ids: list[str] = field(default_factory=list)
    bbox: list[float] | None = None
    spatial_position: str | None = None
    local_context_block_ids: list[str] = field(default_factory=list)
    sibling_panel_ids: list[str] = field(default_factory=list)
    related_table_ids: list[str] = field(default_factory=list)
    related_formula_ids: list[str] = field(default_factory=list)
    related_reference_ids: list[str] = field(default_factory=list)
    provenance: dict[str, Any] = field(default_factory=dict)
    caption_segment_text: str = ""
    caption_segment_status: str = "missing"
    caption_segment_confidence: float = 0.0
    caption_segment_grouped_panel_labels: list[str] = field(default_factory=list)
    caption_segment_provenance: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class FigurePanelGraph:
    figures: list[FigureNode]

    def panel_nodes(self) -> list[PanelNode]:
        return [panel for figure in self.figures for panel in figure.panels]

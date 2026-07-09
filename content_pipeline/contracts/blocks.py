from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ContentBlock:
    block_id: str
    page_idx: int
    reading_order: int
    global_order: int
    raw_type: str
    normalized_type: str
    text: str = ""
    text_hash: str = ""
    structured_content: dict[str, Any] = field(default_factory=dict)
    bbox: list[float] | None = None
    text_level: int | None = None
    image_path: str | None = None
    table_html: str | None = None
    formula_latex: str | None = None
    formula_mathml: str | None = None
    reference_markers: list[str] = field(default_factory=list)
    caption_fields: dict[str, list[str]] = field(default_factory=dict)
    caption_body_fields: dict[str, list[str]] = field(default_factory=dict)
    footnote_fields: dict[str, list[str]] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    raw_block: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ResolvedImagePath:
    original_value: str | None
    normalized_value: str | None
    resolved_path: str | None
    resolution_method: str
    warnings: list[str] = field(default_factory=list)


@dataclass(slots=True)
class PanelMarkerCandidate:
    marker: str
    start: int
    end: int
    confidence: float
    evidence_type: str
    rejection_reason: str | None = None
    surrounding_text: str = ""


@dataclass(slots=True)
class LayoutMatch:
    matched_layout_block_id: str
    matched_layout_type: str
    iou: float
    layout_matched_panel: bool = False

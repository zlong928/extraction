from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


USE_FOR_EXTRACTION = "use_for_extraction"
USE_FOR_DISAMBIGUATION = "use_for_disambiguation"
BACKGROUND_ONLY = "background_only"
DO_NOT_USE_FOR_CURRENT_PANEL = "do_not_use_for_current_panel"

EVIDENCE_PRIMARY = "primary"
EVIDENCE_SUPPORTING = "supporting"
EVIDENCE_BACKGROUND = "background"
EVIDENCE_NON_CURRENT_PANEL = "non_current_panel"


@dataclass(slots=True)
class SelectedContext:
    primary_caption_blocks: list[str] = field(default_factory=list)
    local_text_blocks: list[str] = field(default_factory=list)
    section_heading_blocks: list[str] = field(default_factory=list)
    nearby_table_blocks: list[str] = field(default_factory=list)
    nearby_formula_blocks: list[str] = field(default_factory=list)
    reference_blocks: list[str] = field(default_factory=list)
    sibling_context_blocks: list[str] = field(default_factory=list)
    background_blocks: list[str] = field(default_factory=list)
    excluded_blocks: list[str] = field(default_factory=list)
    spatial_context: list[dict[str, Any]] = field(default_factory=list)
    reading_context: list[dict[str, Any]] = field(default_factory=list)
    section_hierarchy: list[dict[str, Any]] = field(default_factory=list)
    citation_context: list[dict[str, Any]] = field(default_factory=list)
    context_quality: dict[str, Any] = field(default_factory=dict)
    dedupe_report: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class EvidenceItem:
    evidence_id: str
    block_id: str
    source_type: str
    relation: str
    scope: str
    text: str
    text_hash: str
    structured_content: dict[str, Any] | None
    page_idx: int
    reading_order: int
    bbox: list[float] | None
    confidence: float
    use_policy: str
    text_level: str = ""
    text_format: str = "plain_text"
    evidence_role: str = ""
    segment_status: str = ""
    segment_confidence: float = 0.0
    visual_grounding: dict[str, Any] | None = None


@dataclass(slots=True)
class EvidencePacket:
    evidence_packet_id: str
    paper_id: str
    figure_id: str
    panel_id: str | None
    image_ref: str | None
    visual_block_ids: list[str]
    primary_caption: EvidenceItem | None = None
    allowed_context: list[EvidenceItem] = field(default_factory=list)
    background_context: list[EvidenceItem] = field(default_factory=list)
    sibling_context: list[EvidenceItem] = field(default_factory=list)
    excluded_context: list[EvidenceItem] = field(default_factory=list)
    tables: list[EvidenceItem] = field(default_factory=list)
    formulas: list[EvidenceItem] = field(default_factory=list)
    references: list[EvidenceItem] = field(default_factory=list)
    spatial_context: list[dict[str, Any]] = field(default_factory=list)
    reading_context: list[dict[str, Any]] = field(default_factory=list)
    section_hierarchy: list[dict[str, Any]] = field(default_factory=list)
    provenance: dict[str, Any] = field(default_factory=dict)
    dedupe_report: list[dict[str, Any]] = field(default_factory=list)
    audit_trace: list[dict[str, Any]] = field(default_factory=list)


def panel_evidence_contract(packet: EvidencePacket, *, extraction_task: dict[str, Any] | None = None) -> dict[str, Any]:
    caption_segment = dict(packet.provenance.get("caption_segment") or {})
    caption_text = str(caption_segment.get("text") or "")
    caption_structured = _caption_structured(packet.primary_caption, caption_text=caption_text)
    caption_rich_text = _caption_rich_text(packet.primary_caption, caption_text=caption_text)
    caption_status = str(caption_segment.get("status") or "missing")
    primary, supporting, background, non_current = [], [], [], []
    for item in _all_evidence_items(packet):
        mapped = _contract_evidence_item(item)
        role = item.evidence_role or _evidence_role_for_policy(item)
        if role == EVIDENCE_PRIMARY:
            primary.append(mapped)
        elif role == EVIDENCE_SUPPORTING:
            supporting.append(mapped)
        elif role == EVIDENCE_BACKGROUND:
            background.append(mapped)
        else:
            non_current.append(mapped)
    return {
        "contract_version": "panel_evidence_contract/v1",
        "current_panel": {
            "paper_id": packet.paper_id,
            "figure_id": packet.figure_id,
            "panel_id": packet.panel_id or "",
            "panel_label": str(packet.provenance.get("panel_label") or ""),
            "visual_block_id": str(packet.provenance.get("visual_block_id") or ""),
            "image_ref": packet.image_ref or "",
        },
        "caption": {
            "caption_segment": {
                "text": str(caption_segment.get("text") or ""),
                "status": caption_status,
                "confidence": float(caption_segment.get("confidence") or 0.0),
                "grouped_panel_labels": list(caption_segment.get("grouped_panel_labels") or []),
                "provenance": dict(caption_segment.get("provenance") or {}),
                "structured_tokens": caption_structured,
                "formatted_tokens": caption_rich_text,
            },
            "figure_caption_summary": str(packet.provenance.get("figure_caption_summary") or ""),
            "figure_footnotes": [
                _contract_evidence_item(item)
                for item in _all_evidence_items(packet)
                if item.text_level == "footnote"
            ],
        },
        "evidence": {
            "primary": _dedupe_contract_items(primary),
            "supporting": _dedupe_contract_items(supporting),
            "background": _dedupe_contract_items(background),
            "non_current_panel": _dedupe_contract_items(non_current),
        },
        "extraction_task": extraction_task or {
            "decision": "",
            "matched_target_group_ids": [],
            "allowed_metrics": [],
            "needs_digitization": False,
            "paper_task_gap_candidate": False,
        },
    }


def _all_evidence_items(packet: EvidencePacket) -> list[EvidenceItem]:
    items: list[EvidenceItem] = []
    if packet.primary_caption:
        items.append(packet.primary_caption)
    items.extend(packet.allowed_context)
    items.extend(packet.tables)
    items.extend(packet.formulas)
    items.extend(packet.references)
    items.extend(packet.background_context)
    items.extend(packet.sibling_context)
    items.extend(packet.excluded_context)
    return items


def _contract_evidence_item(item: EvidenceItem) -> dict[str, Any]:
    caption_segment_ref = item.source_type == "caption" and item.relation == "primary_caption" and item.text_level == "caption_segment"
    return {
        "evidence_id": item.evidence_id,
        "block_id": item.block_id,
        "source_type": item.source_type,
        "relation": item.relation,
        "use_policy": item.use_policy,
        "text_level": item.text_level,
        "text_format": item.text_format,
        "evidence_role": item.evidence_role or _evidence_role_for_policy(item),
        "segment_status": item.segment_status,
        "segment_confidence": item.segment_confidence,
        "page_idx": item.page_idx,
        "reading_order": item.reading_order,
        "text_excerpt": "" if caption_segment_ref else (item.text or "")[:360],
        "caption_segment_ref": caption_segment_ref,
        "visual_grounding": item.visual_grounding or {},
    }


def _evidence_role_for_policy(item: EvidenceItem) -> str:
    if item.use_policy == USE_FOR_EXTRACTION:
        return EVIDENCE_PRIMARY if item.relation in {"primary_caption", "same_panel_visual"} else EVIDENCE_SUPPORTING
    if item.use_policy == BACKGROUND_ONLY:
        return EVIDENCE_BACKGROUND
    if item.use_policy == DO_NOT_USE_FOR_CURRENT_PANEL:
        return EVIDENCE_NON_CURRENT_PANEL
    return EVIDENCE_SUPPORTING


def _dedupe_contract_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        key = str(item.get("evidence_id") or "")
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _caption_structured(item: EvidenceItem | None, *, caption_text: str) -> dict[str, Any]:
    structured = getattr(item, "structured_content", None)
    if not isinstance(structured, dict):
        return {}
    value = structured.get("caption_structured")
    if not isinstance(value, dict):
        return {}
    if not caption_text:
        return value
    segment_norm = _normalize_caption_token_text(caption_text)
    result: dict[str, Any] = {}
    for key, items in value.items():
        if not isinstance(items, list):
            continue
        item_text = _normalize_caption_token_text(" ".join(
            str(item.get("content") or "")
            for item in items
            if isinstance(item, dict)
        ))
        # Keep structured tokens only when they are already scoped to the current
        # segment. Full multi-panel structured captions would reintroduce the old
        # duplicate/raw caption prompt surface.
        if item_text and item_text in segment_norm:
            result[key] = items
    return result


def _caption_rich_text(item: EvidenceItem | None, *, caption_text: str) -> str:
    structured = getattr(item, "structured_content", None)
    if not isinstance(structured, dict):
        return ""
    value = structured.get("caption_rich_text")
    rich = str(value) if value else ""
    if not rich or not caption_text:
        return rich
    rich_norm = _normalize_caption_token_text(rich)
    rich_plain_norm = _normalize_caption_token_text(_strip_caption_tags(rich))
    segment_norm = _normalize_caption_token_text(caption_text)
    return rich if rich_norm and (rich_norm in segment_norm or rich_plain_norm in segment_norm) else ""


def _normalize_caption_token_text(value: str) -> str:
    return " ".join(str(value or "").replace("<", " <").replace(">", "> ").split()).lower()


def _strip_caption_tags(value: str) -> str:
    text = str(value or "")
    result: list[str] = []
    in_tag = False
    for char in text:
        if char == "<":
            in_tag = True
            result.append(" ")
            continue
        if char == ">":
            in_tag = False
            result.append(" ")
            continue
        if not in_tag:
            result.append(char)
    return "".join(result)

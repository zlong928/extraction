from __future__ import annotations

from typing import Any

from content_pipeline.contracts.evidence import EvidencePacket, panel_evidence_contract
from content_pipeline.contracts.semantic import PanelSemanticResult
from content_pipeline.contracts.visual import VisualExtractionContext


def build_visual_extraction_context(
    *,
    packet: EvidencePacket,
    panel_semantic: PanelSemanticResult | None,
    include_benchmark_semantics: bool = True,
) -> VisualExtractionContext:
    contract = panel_evidence_contract(packet)
    caption_segment = contract.get("caption", {}).get("caption_segment", {})
    caption_text = str(caption_segment.get("text") or (packet.primary_caption.text if packet.primary_caption else ""))
    panel_context_warnings = []
    if isinstance(caption_segment, dict) and not str(caption_segment.get("text") or "").strip():
        panel_context_warnings.append("missing_caption_segment")
    tables = "\n".join(item.text for item in packet.tables if item.text)[:2000]
    formulas = "\n".join(item.text for item in packet.formulas if item.text)[:1000]
    profile = _public_dict(panel_semantic) if include_benchmark_semantics else {}
    if not include_benchmark_semantics and panel_semantic is not None:
        profile["evidence_role"] = getattr(panel_semantic, "evidence_role", "")
        profile["extraction_decision"] = getattr(panel_semantic, "extraction_decision", "")
        profile["panel_type"] = getattr(panel_semantic, "panel_type", "")
    visual_type = str(packet.provenance.get("visual_normalized_type") or "").lower()
    units = _caption_table_unit_hints(caption_text, tables)
    return VisualExtractionContext(
        paper_id=packet.paper_id,
        figure_id=packet.figure_id,
        panel_id=str(packet.panel_id or ""),
        image_ref=packet.image_ref or "",
        visual_type=visual_type,
        tables=tables,
        formulas=formulas,
        evidence_map=_evidence_map(packet),
        section_hierarchy=packet.section_hierarchy,
        panel_semantic_profile=profile,
        chart_type_hint=str(profile.get("panel_type") or visual_type or ""),
        image_kind_hint=str(profile.get("panel_type") or visual_type or ""),
        axis_unit_hints=units,
        panel_evidence_contract=contract,
        panel_context_warnings=panel_context_warnings,
    )


def _public_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if hasattr(value, "__dataclass_fields__"):
        return {k: getattr(value, k) for k in value.__dataclass_fields__ if k != "raw_output"}
    return dict(value) if isinstance(value, dict) else {}


def _caption_table_unit_hints(caption_text: str, tables: str) -> list[str]:
    hints: list[str] = []
    text = f"{caption_text}\n{tables}".lower()
    for token in ("day", "h", "min", "s", "%", "a.u.", "mg", "g", "µm", "nm", "mm", "Pa", "MPa", "mg/L"):
        if token.lower() in text:
            hints.append(token)
    return list(dict.fromkeys(item for item in hints if item))[:24]


def _evidence_map(packet: EvidencePacket) -> list[dict[str, Any]]:
    ordered = []
    if packet.primary_caption:
        ordered.append(packet.primary_caption)
    ordered.extend(packet.allowed_context)
    ordered.extend(packet.tables)
    ordered.extend(packet.formulas)
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in ordered:
        if item.evidence_id in seen:
            continue
        seen.add(item.evidence_id)
        caption_segment_ref = (
            item.source_type == "caption"
            and item.relation == "primary_caption"
            and getattr(item, "text_level", "") == "caption_segment"
        )
        result.append({
            "evidence_id": item.evidence_id,
            "block_id": item.block_id,
            "source_type": item.source_type,
            "relation": item.relation,
            "use_policy": item.use_policy,
            "text_level": getattr(item, "text_level", ""),
            "evidence_role": getattr(item, "evidence_role", ""),
            "segment_status": getattr(item, "segment_status", ""),
            "caption_segment_ref": caption_segment_ref,
            "page_idx": item.page_idx,
            "reading_order": item.reading_order,
            "text_excerpt": "" if caption_segment_ref else (item.text or "")[:240],
        })
    return result[:20]

from __future__ import annotations

import hashlib

from content_pipeline.contracts.evidence import (
    BACKGROUND_ONLY,
    DO_NOT_USE_FOR_CURRENT_PANEL,
    EVIDENCE_BACKGROUND,
    EVIDENCE_NON_CURRENT_PANEL,
    EVIDENCE_PRIMARY,
    EVIDENCE_SUPPORTING,
    USE_FOR_DISAMBIGUATION,
    USE_FOR_EXTRACTION,
    EvidenceItem,
    EvidencePacket,
    SelectedContext,
)
from content_pipeline.contracts.graph import DocumentGraph, FigureNode, PanelNode
from content_pipeline.evidence.evidence_deduplicator import EvidenceDeduplicator


class EvidencePacketBuilder:
    def __init__(self, deduplicator: EvidenceDeduplicator | None = None) -> None:
        self.deduplicator = deduplicator or EvidenceDeduplicator()

    def build(self, *, paper_id: str, document_graph: DocumentGraph, figure: FigureNode, panel: PanelNode, selected: SelectedContext) -> EvidencePacket:
        visual_id = panel.image_block_id or panel.chart_block_id
        visual_block = document_graph.global_index.get(visual_id) if visual_id else None
        items: list[EvidenceItem] = []
        if visual_block is not None:
            items.append(self._visual_item(visual_block))
        for idx, block_id in enumerate(selected.primary_caption_blocks):
            relation = "primary_caption" if idx == 0 else "caption_continuation"
            items.append(self._caption_item(document_graph, block_id, panel, relation, "panel", USE_FOR_EXTRACTION, 0.95))
            for footnote in self._footnote_items(document_graph, block_id):
                items.append(footnote)
        for block_id in selected.local_text_blocks:
            items.append(self._item(document_graph, block_id, "same_panel", "panel", USE_FOR_EXTRACTION, 0.8))
        for block_id in selected.section_heading_blocks:
            items.append(self._item(document_graph, block_id, "same_section", "section", USE_FOR_DISAMBIGUATION, 0.65))
        for block_id in selected.background_blocks:
            items.append(self._item(document_graph, block_id, "background", "figure", BACKGROUND_ONLY, 0.45))
        for block_id in selected.sibling_context_blocks:
            items.append(self._item(document_graph, block_id, "sibling_panel", "panel", DO_NOT_USE_FOR_CURRENT_PANEL, 0.25))
        for block_id in selected.excluded_blocks:
            items.append(self._item(document_graph, block_id, "excluded", "panel", DO_NOT_USE_FOR_CURRENT_PANEL, 0.1))
        for block_id in selected.nearby_table_blocks:
            items.append(self._item(document_graph, block_id, "adjacent_table", "panel", USE_FOR_EXTRACTION, 0.78))
        for block_id in selected.nearby_formula_blocks:
            items.append(self._item(document_graph, block_id, "adjacent_formula", "figure", USE_FOR_DISAMBIGUATION, 0.65))
        for block_id in selected.reference_blocks:
            items.append(self._item(document_graph, block_id, "citation", "figure", USE_FOR_DISAMBIGUATION, 0.55))
        deduped, report = self.deduplicator.dedupe(items)
        primary = next((item for item in deduped if item.relation == "primary_caption"), None)
        return EvidencePacket(
            evidence_packet_id=f"ep-{panel.panel_id}",
            paper_id=paper_id,
            figure_id=figure.figure_id,
            panel_id=panel.panel_id,
            image_ref=visual_block.image_path if visual_block else None,
            visual_block_ids=[bid for bid in [visual_id] if bid],
            primary_caption=primary,
            allowed_context=[i for i in deduped if i.use_policy == USE_FOR_EXTRACTION and i is not primary],
            background_context=[i for i in deduped if i.use_policy == BACKGROUND_ONLY],
            sibling_context=[i for i in deduped if i.relation == "sibling_panel"],
            excluded_context=[i for i in deduped if i.use_policy == DO_NOT_USE_FOR_CURRENT_PANEL],
            tables=[i for i in deduped if i.relation == "adjacent_table"],
            formulas=[i for i in deduped if i.relation == "adjacent_formula"],
            references=[i for i in deduped if i.relation in {"citation", "caption_footnote"}],
            spatial_context=selected.spatial_context,
            reading_context=selected.reading_context,
            section_hierarchy=selected.section_hierarchy,
            provenance={
                "source": "content_graph",
                "visual_raw_type": visual_block.raw_type if visual_block else "",
                "visual_normalized_type": visual_block.normalized_type if visual_block else "",
                "visual_block_id": visual_id or "",
                "panel_label": panel.panel_label or "",
                "caption_segment": {
                    "text": panel.caption_segment_text,
                    "status": panel.caption_segment_status,
                    "confidence": panel.caption_segment_confidence,
                    "grouped_panel_labels": panel.caption_segment_grouped_panel_labels,
                    "provenance": panel.caption_segment_provenance,
                },
            },
            dedupe_report=report,
            audit_trace=[{"event": "evidence_packet_built", "panel_id": panel.panel_id, "dedupe_removed": len(report)}],
        )

    def _visual_item(self, block) -> EvidenceItem:
        visual_ref = block.image_path or block.block_id
        text = block.text or f"{block.normalized_type} visual block {visual_ref}"
        return EvidenceItem(
            evidence_id=f"ev-{block.block_id}-visual",
            block_id=block.block_id,
            source_type=block.normalized_type,
            relation="same_panel_visual",
            scope="visual",
            text=text,
            text_hash=f"{block.text_hash}:visual" if block.text_hash else f"{block.block_id}:visual",
            structured_content=_structured_content_for_evidence(block),
            page_idx=block.page_idx,
            reading_order=block.reading_order,
            bbox=block.bbox,
            confidence=0.9,
            use_policy=USE_FOR_EXTRACTION,
            text_level="visual",
            text_format="image_ref",
            evidence_role=EVIDENCE_PRIMARY,
            visual_grounding={"image_ref": visual_ref, "region": None},
        )

    def _item(self, document_graph: DocumentGraph, block_id: str, relation: str, scope: str, use_policy: str, confidence: float) -> EvidenceItem:
        block = document_graph.global_index[block_id]
        source_type = "caption" if relation in {"primary_caption", "caption_continuation"} else block.normalized_type
        text = block.table_html or block.formula_latex or block.text
        return EvidenceItem(
            evidence_id=f"ev-{block.block_id}-{relation}",
            block_id=block.block_id,
            source_type=source_type,
            relation=relation,
            scope=scope,
            text=text,
            text_hash=block.text_hash or _hash_text(text),
            structured_content=_structured_content_for_evidence(block),
            page_idx=block.page_idx,
            reading_order=block.reading_order,
            bbox=block.bbox,
            confidence=confidence,
            use_policy=use_policy,
            text_level=_text_level(block, relation),
            text_format=_text_format(block),
            evidence_role=_evidence_role(relation, use_policy),
        )

    def _caption_item(self, document_graph: DocumentGraph, block_id: str, panel: PanelNode, relation: str, scope: str, use_policy: str, confidence: float) -> EvidenceItem:
        block = document_graph.global_index[block_id]
        segment_status = panel.caption_segment_status
        segment_text = panel.caption_segment_text if segment_status in {"exact", "grouped_shared"} else ""
        text = segment_text or block.text
        segment_confidence = panel.caption_segment_confidence if segment_text else 0.0
        item_confidence = segment_confidence or confidence
        return EvidenceItem(
            evidence_id=f"ev-{block.block_id}-{relation}",
            block_id=block.block_id,
            source_type="caption",
            relation=relation,
            scope=scope,
            text=text,
            text_hash=_hash_text(text) if segment_text else block.text_hash or _hash_text(text),
            structured_content=_structured_content_for_evidence(block),
            page_idx=block.page_idx,
            reading_order=block.reading_order,
            bbox=block.bbox,
            confidence=item_confidence,
            use_policy=use_policy,
            text_level="caption_segment" if segment_text else "caption_body",
            text_format="rich_text" if block.metadata.get("caption_rich_text") else "plain_text",
            evidence_role=EVIDENCE_PRIMARY if relation == "primary_caption" else EVIDENCE_SUPPORTING,
            segment_status=segment_status if segment_text else "fallback_regex",
            segment_confidence=segment_confidence,
        )

    def _footnote_items(self, document_graph: DocumentGraph, block_id: str) -> list[EvidenceItem]:
        block = document_graph.global_index[block_id]
        items: list[EvidenceItem] = []
        for key, values in (block.footnote_fields or {}).items():
            for index, text in enumerate(values):
                if not text:
                    continue
                items.append(EvidenceItem(
                    evidence_id=f"ev-{block.block_id}-{key}-{index}",
                    block_id=block.block_id,
                    source_type="footnote",
                    relation="caption_footnote",
                    scope="figure",
                    text=text,
                    text_hash=_hash_text(text),
                    structured_content={"caption_footnote_structured": block.metadata.get("caption_footnote_structured", {})},
                    page_idx=block.page_idx,
                    reading_order=block.reading_order,
                    bbox=block.bbox,
                    confidence=0.65,
                    use_policy=USE_FOR_DISAMBIGUATION,
                    text_level="footnote",
                    text_format="plain_text",
                    evidence_role=EVIDENCE_SUPPORTING,
                ))
        return items


def _structured_content_for_evidence(block) -> dict:
    structured = dict(block.structured_content or {})
    caption_structured = block.metadata.get("caption_structured")
    caption_rich_text = block.metadata.get("caption_rich_text")
    footnote_structured = block.metadata.get("caption_footnote_structured")
    if caption_structured:
        structured["caption_structured"] = caption_structured
    if footnote_structured:
        structured["caption_footnote_structured"] = footnote_structured
    if caption_rich_text:
        structured["caption_rich_text"] = caption_rich_text
    if block.caption_body_fields:
        structured["caption_body_fields"] = block.caption_body_fields
    if block.footnote_fields:
        structured["footnote_fields"] = block.footnote_fields
    if block.caption_fields:
        structured["caption_fields"] = block.caption_fields
    return structured


def _hash_text(text: str) -> str:
    return hashlib.sha1(" ".join(str(text or "").split()).encode("utf-8")).hexdigest()


def _text_level(block, relation: str) -> str:
    if relation in {"primary_caption", "caption_continuation"}:
        return "caption_body"
    if relation == "adjacent_table":
        return "table"
    if relation == "adjacent_formula":
        return "formula"
    if relation == "same_section":
        return "section_heading"
    if relation == "background":
        return "background_text"
    if relation == "sibling_panel":
        return "sibling_panel"
    return str(getattr(block, "normalized_type", "") or "text")


def _text_format(block) -> str:
    if getattr(block, "table_html", None):
        return "html"
    if getattr(block, "formula_latex", None):
        return "latex"
    return "plain_text"


def _evidence_role(relation: str, use_policy: str) -> str:
    if relation in {"primary_caption", "same_panel_visual"}:
        return EVIDENCE_PRIMARY
    if use_policy == BACKGROUND_ONLY:
        return EVIDENCE_BACKGROUND
    if use_policy == DO_NOT_USE_FOR_CURRENT_PANEL:
        return EVIDENCE_NON_CURRENT_PANEL
    return EVIDENCE_SUPPORTING

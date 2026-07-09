from __future__ import annotations

import re

from content_pipeline.contracts.evidence import SelectedContext
from content_pipeline.contracts.graph import DocumentGraph, FigureNode, PanelNode


_MAX_REFERENCE_BLOCKS = 3


class EvidenceContextSelector:
    """Select evidence by graph relation rather than fixed text windows."""

    def select_for_panel(self, document_graph: DocumentGraph, figure: FigureNode, panel: PanelNode) -> SelectedContext:
        visual_id = panel.image_block_id or panel.chart_block_id
        full_caption_blocks = _full_caption_blocks(document_graph, figure, panel)
        sibling_visual_ids = {
            sibling.image_block_id or sibling.chart_block_id
            for sibling in figure.panels
            if sibling.panel_id in set(panel.sibling_panel_ids)
        }
        sibling_visual_ids.discard(None)
        marker_caption_blocks = [bid for bid in panel.caption_block_ids if bid not in full_caption_blocks and bid not in sibling_visual_ids]
        primary_caption = list(dict.fromkeys(full_caption_blocks or panel.caption_block_ids))
        page = document_graph.pages.get(panel.page_idx)
        local_text: list[str] = []
        headings: list[str] = []
        tables = list(panel.related_table_ids)
        formulas = list(panel.related_formula_ids)
        refs = list(panel.related_reference_ids)
        background: list[str] = []
        excluded: list[str] = list(marker_caption_blocks)
        if page and visual_id and visual_id in document_graph.global_index:
            visual = document_graph.global_index[visual_id]
            for block in page.blocks_by_reading_order:
                if block.block_id == visual_id or block.block_id in primary_caption or block.block_id in excluded:
                    continue
                if block.normalized_type == "heading" or block.text_level:
                    if block.reading_order <= visual.reading_order:
                        headings.append(block.block_id)
                elif block.normalized_type in {"text", "list"}:
                    if not str(block.text or "").strip():
                        continue
                    if abs(block.reading_order - visual.reading_order) <= 3:
                        local_text.append(block.block_id)
                    elif block.reading_order < visual.reading_order:
                        background.append(block.block_id)
                elif block.normalized_type == "table" and block.block_id not in tables:
                    if abs(block.reading_order - visual.reading_order) <= 5:
                        tables.append(block.block_id)
                elif block.normalized_type == "formula" and block.block_id not in formulas:
                    if abs(block.reading_order - visual.reading_order) <= 5:
                        formulas.append(block.block_id)
                elif block.normalized_type == "reference" and block.block_id not in refs:
                    if block.reading_order >= visual.reading_order:
                        refs.append(block.block_id)
        sibling_context: list[str] = []
        current_ids = set(primary_caption + excluded + ([visual_id] if visual_id else []))
        for sibling_id in panel.sibling_panel_ids:
            for sibling in figure.panels:
                if sibling.panel_id == sibling_id:
                    sibling_context.extend(bid for bid in sibling.caption_block_ids if bid not in current_ids)
                    sibling_visual = sibling.image_block_id or sibling.chart_block_id
                    if sibling_visual and sibling_visual not in current_ids:
                        sibling_context.append(sibling_visual)
        excluded.extend(sibling_context)
        section_hierarchy = _section_hierarchy(document_graph, panel)
        headings.extend(item["block_id"] for item in section_hierarchy if item.get("block_id"))
        citation_context = _citation_context(document_graph, figure, panel)
        reading_context = _reading_context(document_graph, panel)
        spatial_context = _spatial_context(document_graph, panel)
        return SelectedContext(
            primary_caption_blocks=primary_caption,
            local_text_blocks=list(dict.fromkeys(local_text)),
            section_heading_blocks=list(dict.fromkeys(headings)),
            nearby_table_blocks=list(dict.fromkeys(tables)),
            nearby_formula_blocks=list(dict.fromkeys(formulas)),
            reference_blocks=list(dict.fromkeys(refs + [c["block_id"] for c in citation_context]))[:_MAX_REFERENCE_BLOCKS],
            sibling_context_blocks=list(dict.fromkeys(sibling_context)),
            background_blocks=list(dict.fromkeys(background)),
            excluded_blocks=list(dict.fromkeys(excluded)),
            spatial_context=spatial_context,
            reading_context=reading_context,
            section_hierarchy=section_hierarchy,
            citation_context=citation_context,
            context_quality={
                "selection_method": "graph_relation",
                "full_caption_preferred": bool(full_caption_blocks),
                "reference_limit": _MAX_REFERENCE_BLOCKS,
            },
        )


def _full_caption_blocks(document_graph: DocumentGraph, figure: FigureNode, panel: PanelNode) -> list[str]:
    full_from_provenance = list(figure.provenance.get("full_caption_block_ids") or [])
    candidates = list(dict.fromkeys(full_from_provenance + figure.caption_blocks + panel.caption_block_ids))
    panel_visual_id = panel.image_block_id or panel.chart_block_id
    panel_visual = document_graph.global_index.get(panel_visual_id or "")
    panel_visual_has_full_caption = bool(panel_visual and _is_full_figure_caption(panel_visual.text))
    sibling_visual_ids = {
        sibling.image_block_id or sibling.chart_block_id
        for sibling in figure.panels
        if sibling.panel_id in set(panel.sibling_panel_ids)
    }
    sibling_visual_ids.discard(None)
    result = []
    for block_id in candidates:
        if (
            block_id in sibling_visual_ids
            and block_id != panel_visual_id
            and (block_id not in full_from_provenance or panel_visual_has_full_caption)
        ):
            continue
        block = document_graph.global_index.get(block_id)
        if block and _is_full_figure_caption(block.text):
            result.append(block_id)
    return list(dict.fromkeys(result))


def _is_full_figure_caption(text: str) -> bool:
    clean = re.sub(r"\s+", " ", str(text or "").strip())
    if not clean:
        return False
    if re.fullmatch(r"(?:text\s*)?\(?[A-Za-z]\)?\s*", clean):
        return False
    if re.search(r"\b(?:fig(?:ure)?\.?)\s*\d+", clean, re.IGNORECASE):
        return True
    return False


def _section_hierarchy(document_graph: DocumentGraph, panel: PanelNode) -> list[dict]:
    visual_id = panel.image_block_id or panel.chart_block_id
    visual = document_graph.global_index.get(visual_id or "")
    if not visual:
        return []
    heading_stack: list[tuple[int, object]] = []
    for block in sorted(document_graph.heading_blocks, key=lambda b: b.global_order):
        if block.global_order > visual.global_order:
            break
        level = block.text_level or 1
        while heading_stack and heading_stack[-1][0] >= level:
            heading_stack.pop()
        heading_stack.append((level, block))
    result: list[dict] = []
    for level, block in heading_stack:
        distance_in_global_order = visual.global_order - block.global_order
        if block.page_idx == visual.page_idx:
            distance_in_blocks = visual.reading_order - block.reading_order
        else:
            distance_in_blocks = distance_in_global_order
        result.append({
            "level": level,
            "title": block.text,
            "block_id": block.block_id,
            "distance_in_blocks": distance_in_blocks,
            "distance_in_global_order": distance_in_global_order,
            "page_idx": block.page_idx,
        })
    return result


def _citation_context(document_graph: DocumentGraph, figure: FigureNode, panel: PanelNode) -> list[dict]:
    visual_id = panel.image_block_id or panel.chart_block_id
    visual = document_graph.global_index.get(visual_id or "")
    if not visual:
        return []
    result: list[dict] = []
    label_tokens = [token.lower() for token in [figure.label, figure.figure_id] if token]
    for page_idx in (visual.page_idx, visual.page_idx + 1):
        page = document_graph.pages.get(page_idx)
        if not page:
            continue
        for block in page.blocks_by_reading_order:
            if page_idx == visual.page_idx and block.reading_order < visual.reading_order:
                continue
            if page_idx == visual.page_idx + 1 and block.reading_order > 10:
                continue
            haystack = block.text.lower()
            if block.normalized_type == "reference" or any(token and token in haystack for token in label_tokens):
                result.append({"block_id": block.block_id, "page_idx": block.page_idx, "type": block.normalized_type, "text": block.text})
    return result


def _reading_context(document_graph: DocumentGraph, panel: PanelNode) -> list[dict]:
    visual_id = panel.image_block_id or panel.chart_block_id
    visual = document_graph.global_index.get(visual_id or "")
    page = document_graph.pages.get(panel.page_idx)
    if not visual or not page:
        return []
    return [
        {"block_id": block.block_id, "type": block.normalized_type, "reading_order": block.reading_order}
        for block in page.blocks_by_reading_order
        if abs(block.reading_order - visual.reading_order) <= 5
    ]


def _spatial_context(document_graph: DocumentGraph, panel: PanelNode) -> list[dict]:
    visual_id = panel.image_block_id or panel.chart_block_id
    page = document_graph.pages.get(panel.page_idx)
    if not visual_id or not page:
        return []
    return [
        {"source_block_id": rel.source_block_id, "target_block_id": rel.target_block_id, "relation": rel.relation}
        for rel in page.spatial_relations
        if rel.source_block_id == visual_id or rel.target_block_id == visual_id
    ]

from __future__ import annotations

from collections import defaultdict
from dataclasses import replace

from content_pipeline.contracts.blocks import ContentBlock
from content_pipeline.contracts.graph import DocumentGraph, PageGraph, SpatialRelation


_DEFAULT_FILTERED_TYPES = {
    "page_header",
    "page_footer",
    "page_number",
    "page_footnote",
    "aside",
}


class DocumentGraphBuilder:
    """Build page, type, reading-order, and spatial indexes over content blocks."""

    def __init__(self, filtered_types: set[str] | None = None) -> None:
        self.filtered_types = set(filtered_types or _DEFAULT_FILTERED_TYPES)
        self.last_report: dict[str, object] = {}

    def build(self, blocks: list[ContentBlock]) -> DocumentGraph:
        blocks, filtered_blocks, filtered_type_counts = self._filter_and_compress(blocks)
        pages: dict[int, PageGraph] = {}
        by_page: dict[int, list[ContentBlock]] = defaultdict(list)
        for block in blocks:
            by_page[block.page_idx].append(block)
        for page_idx, page_blocks in by_page.items():
            reading = sorted(page_blocks, key=lambda b: b.reading_order)
            spatial = sorted(page_blocks, key=lambda b: ((b.bbox or [0, 0, 0, 0])[1], (b.bbox or [0, 0, 0, 0])[0]))
            pages[page_idx] = PageGraph(
                page_idx=page_idx,
                blocks_by_reading_order=reading,
                blocks_by_spatial_order=spatial,
                headings=[b for b in reading if b.normalized_type == "heading" or b.text_level],
                figures=[b for b in reading if b.normalized_type in {"image", "chart"}],
                tables=[b for b in reading if b.normalized_type == "table"],
                formulas=[b for b in reading if b.normalized_type == "formula"],
                spatial_relations=_spatial_relations(reading),
            )
        return DocumentGraph(
            blocks=sorted(blocks, key=lambda b: b.global_order),
            pages=pages,
            global_index={b.block_id: b for b in blocks},
            image_blocks=[b for b in blocks if b.normalized_type == "image"],
            chart_blocks=[b for b in blocks if b.normalized_type == "chart"],
            table_blocks=[b for b in blocks if b.normalized_type == "table"],
            formula_blocks=[b for b in blocks if b.normalized_type == "formula"],
            heading_blocks=[b for b in blocks if b.normalized_type == "heading" or b.text_level],
            reference_blocks=[b for b in blocks if b.normalized_type == "reference"],
            filtered_blocks=filtered_blocks,
            filtered_type_counts=filtered_type_counts,
        )

    def _filter_and_compress(self, blocks: list[ContentBlock]) -> tuple[list[ContentBlock], list[ContentBlock], dict[str, int]]:
        kept_originals: list[ContentBlock] = []
        filtered_blocks: list[ContentBlock] = []
        filtered_type_counts: dict[str, int] = {}
        for block in sorted(blocks, key=lambda b: (b.global_order, b.page_idx, b.reading_order)):
            if block.normalized_type in self.filtered_types:
                reason = f"filtered_page_noise:{block.normalized_type}"
                metadata = dict(block.metadata)
                metadata.setdefault("mineru_reading_order", block.reading_order)
                metadata.setdefault("mineru_global_order", block.global_order)
                metadata["filtered_reason"] = reason
                filtered = replace(block, metadata=metadata)
                filtered_blocks.append(filtered)
                filtered_type_counts[block.normalized_type] = filtered_type_counts.get(block.normalized_type, 0) + 1
            else:
                kept_originals.append(block)

        per_page_counts: dict[int, int] = defaultdict(int)
        kept: list[ContentBlock] = []
        for new_global_order, block in enumerate(kept_originals):
            new_reading_order = per_page_counts[block.page_idx]
            per_page_counts[block.page_idx] += 1
            metadata = dict(block.metadata)
            metadata.setdefault("mineru_reading_order", block.reading_order)
            metadata.setdefault("mineru_global_order", block.global_order)
            kept.append(replace(
                block,
                reading_order=new_reading_order,
                global_order=new_global_order,
                metadata=metadata,
            ))

        self.last_report = {
            "event": "content_blocks_filtered",
            "filtered_count": len(filtered_blocks),
            "filtered_type_counts": dict(filtered_type_counts),
            "filtered_blocks": [
                {
                    "block_id": block.block_id,
                    "page_idx": block.page_idx,
                    "mineru_reading_order": block.metadata.get("mineru_reading_order", block.reading_order),
                    "mineru_global_order": block.metadata.get("mineru_global_order", block.global_order),
                    "raw_type": block.raw_type,
                    "normalized_type": block.normalized_type,
                    "filtered_reason": block.metadata.get("filtered_reason"),
                }
                for block in filtered_blocks
            ],
        }
        return kept, filtered_blocks, filtered_type_counts


def _spatial_relations(blocks: list[ContentBlock]) -> list[SpatialRelation]:
    relations: list[SpatialRelation] = []
    with_bbox = [b for b in blocks if b.bbox]
    for left in with_bbox:
        for right in with_bbox:
            if left.block_id == right.block_id:
                continue
            relation = _relation(left.bbox or [], right.bbox or [])
            if relation:
                relations.append(SpatialRelation(left.block_id, right.block_id, relation, 1.0))
    return relations


def _relation(a: list[float], b: list[float]) -> str | None:
    if len(a) != 4 or len(b) != 4:
        return None
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    x_overlap = max(0.0, min(ax2, bx2) - max(ax1, bx1))
    y_overlap = max(0.0, min(ay2, by2) - max(ay1, by1))
    aw = max(ax2 - ax1, 1.0)
    ah = max(ay2 - ay1, 1.0)
    bw = max(bx2 - bx1, 1.0)
    bh = max(by2 - by1, 1.0)
    if ax1 <= bx1 and ay1 <= by1 and ax2 >= bx2 and ay2 >= by2:
        return "contains"
    if x_overlap / min(aw, bw) > 0.5 and ay2 <= by1:
        return "above"
    if x_overlap / min(aw, bw) > 0.5 and ay1 >= by2:
        return "below"
    if y_overlap / min(ah, bh) > 0.5 and ax2 <= bx1:
        return "left_of"
    if y_overlap / min(ah, bh) > 0.5 and ax1 >= bx2:
        return "right_of"
    if x_overlap > 0 and y_overlap > 0:
        return "overlaps"
    vertical_gap = min(abs(ay2 - by1), abs(by2 - ay1))
    horizontal_gap = min(abs(ax2 - bx1), abs(bx2 - ax1))
    if vertical_gap < 60 or horizontal_gap < 60:
        return "near"
    return None

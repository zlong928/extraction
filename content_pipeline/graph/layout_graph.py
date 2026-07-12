from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from content_pipeline.contracts.blocks import LayoutMatch
from content_pipeline.contracts.graph import DocumentGraph


@dataclass(slots=True)
class LayoutBlock:
    layout_block_id: str
    page_idx: int
    block_type: str
    bbox: list[float]
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class LayoutGraph:
    blocks: list[LayoutBlock]
    matches: dict[str, LayoutMatch]


class LayoutGraphBuilder:
    def build(self, layout_path: str | Path | None, document_graph: DocumentGraph, *, iou_threshold: float = 0.5) -> LayoutGraph:
        layout_blocks = self._load_layout_blocks(layout_path)
        matches: dict[str, LayoutMatch] = {}
        for content_block in document_graph.blocks:
            if not content_block.bbox:
                continue
            same_page = [block for block in layout_blocks if block.page_idx == content_block.page_idx]
            best: tuple[float, LayoutBlock] | None = None
            for layout_block in same_page:
                score = _iou(content_block.bbox, layout_block.bbox)
                if score > 0 and (best is None or score > best[0]):
                    best = (score, layout_block)
            if best and best[0] >= iou_threshold:
                score, layout_block = best
                match = LayoutMatch(
                    matched_layout_block_id=layout_block.layout_block_id,
                    matched_layout_type=layout_block.block_type,
                    iou=score,
                    layout_matched_panel=layout_block.block_type in {"panel", "figure", "image"},
                )
                matches[content_block.block_id] = match
                content_block.metadata["layout_match"] = {
                    "matched_layout_block_id": match.matched_layout_block_id,
                    "matched_layout_type": match.matched_layout_type,
                    "iou": match.iou,
                    "layout_matched_panel": match.layout_matched_panel,
                }
        return LayoutGraph(layout_blocks, matches)

    def _load_layout_blocks(self, layout_path: str | Path | None) -> list[LayoutBlock]:
        if not layout_path:
            return []
        path = Path(layout_path)
        if not path.is_file():
            return []
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return []
        pages = raw if isinstance(raw, list) else raw.get("pages", []) if isinstance(raw, dict) else []
        blocks: list[LayoutBlock] = []
        for page_idx, page in enumerate(pages):
            if not isinstance(page, dict):
                continue
            actual_page_idx = page.get("page_idx", page.get("page_id", page_idx))
            preproc = page.get("preproc_blocks") or page.get("blocks") or []
            if not isinstance(preproc, list):
                continue
            for idx, block in enumerate(preproc):
                if not isinstance(block, dict):
                    continue
                bbox = _coerce_bbox(block.get("bbox"))
                if bbox is None:
                    continue
                blocks.append(LayoutBlock(
                    layout_block_id=f"layout-p{actual_page_idx}-b{idx}",
                    page_idx=int(actual_page_idx),
                    block_type=str(block.get("type") or block.get("block_type") or "unknown").lower(),
                    bbox=bbox,
                    raw=block,
                ))
        return blocks


def _coerce_bbox(value: Any) -> list[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        return [float(value[0]), float(value[1]), float(value[2]), float(value[3])]
    except (TypeError, ValueError):
        return None


def _iou(a: list[float], b: list[float]) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    intersection = iw * ih
    if intersection <= 0:
        return 0.0
    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    return intersection / max(area_a + area_b - intersection, 1.0)

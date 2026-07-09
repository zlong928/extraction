from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from content_pipeline.contracts.blocks import ContentBlock
from content_pipeline.contracts.graph import DocumentGraph, FigureNode, FigurePanelGraph, PanelNode
from content_pipeline.graph.layout_graph import LayoutGraph
from content_pipeline.mineru.panel_marker_detector import PanelMarkerDetector


class FigurePanelGraphBuilder:
    def __init__(self, marker_detector: PanelMarkerDetector | None = None) -> None:
        self.marker_detector = marker_detector or PanelMarkerDetector()

    def build(self, document_graph: DocumentGraph, layout_graph: LayoutGraph | None = None) -> FigurePanelGraph:
        visuals = sorted(document_graph.image_blocks + document_graph.chart_blocks, key=lambda b: (b.page_idx, b.reading_order))
        by_page: dict[int, list[ContentBlock]] = defaultdict(list)
        for block in visuals:
            by_page[block.page_idx].append(block)
        figures: list[FigureNode] = []
        seen_figure_ids: dict[str, int] = {}
        for page_idx, page_visuals in sorted(by_page.items()):
            groups = self._group_visuals(page_visuals, layout_graph, document_graph)
            for group in groups:
                figure = self._build_figure(group, document_graph)
                self._ensure_unique_figure_identity(figure, seen_figure_ids)
                figures.append(figure)
        return FigurePanelGraph(figures)

    def _ensure_unique_figure_identity(self, figure: FigureNode, seen_figure_ids: dict[str, int]) -> None:
        base_figure_id = figure.figure_id
        seen_figure_ids[base_figure_id] = seen_figure_ids.get(base_figure_id, 0) + 1
        occurrence = seen_figure_ids[base_figure_id]
        if occurrence == 1:
            return

        old_figure_id = figure.figure_id
        new_figure_id = f"{base_figure_id}-{occurrence}"
        panel_id_map: dict[str, str] = {}
        figure.figure_id = new_figure_id
        figure.provenance["base_figure_id"] = base_figure_id
        figure.provenance["figure_id_occurrence"] = occurrence

        for panel in figure.panels:
            old_panel_id = panel.panel_id
            suffix = old_panel_id[len(old_figure_id):].lstrip("-") if old_panel_id.startswith(old_figure_id) else old_panel_id
            panel.panel_id = f"{new_figure_id}-{suffix}" if suffix else new_figure_id
            panel.parent_figure_id = new_figure_id
            panel.provenance["base_panel_id"] = old_panel_id
            panel_id_map[old_panel_id] = panel.panel_id

        for panel in figure.panels:
            panel.sibling_panel_ids = [panel_id_map.get(pid, pid) for pid in panel.sibling_panel_ids]

    def _caption_text_for_visual(self, block: ContentBlock) -> str:
        parts = []
        caption_fields = block.caption_body_fields or {
            key: values
            for key, values in block.caption_fields.items()
            if "footnote" not in key
        }
        for values in caption_fields.values():
            parts.extend(values)
        if not parts and block.structured_content:
            for key in ("image_caption", "chart_caption", "table_caption", "caption"):
                val = block.structured_content.get(key)
                if isinstance(val, str):
                    parts.append(val)
                elif isinstance(val, dict):
                    parts.append(str(val.get("text", "")))
        return " ".join(parts)

    def _figure_number_from_text(self, text: str) -> str | None:
        match = re.search(r"(?i)\b(fig(?:ure)?\.?\s*\d+[a-z]?(?:\s*\([a-z]\))?)\b", text)
        if match:
            raw = match.group(1)
            num_match = re.search(r"\d+", raw)
            if num_match:
                return f"Figure {num_match.group()}"
        return None

    def _has_composite_panel_markers(self, text: str) -> bool:
        markers = self.marker_detector.detect(text)
        return len(markers) >= 2 if markers else False

    def _group_visuals(self, visuals: list[ContentBlock], layout_graph: LayoutGraph | None, document_graph: DocumentGraph) -> list[list[ContentBlock]]:
        if len(visuals) <= 1:
            return [visuals] if visuals else []

        captioned = []
        for v in visuals:
            caption_text = self._caption_text_for_visual(v)
            fig_num = self._figure_number_from_text(caption_text)
            has_caption = bool(caption_text.strip())
            captioned.append((v, caption_text, fig_num, has_caption))

        all_fig_numbers = {fn for _, _, fn, _ in captioned if fn}
        if len(all_fig_numbers) >= 2:
            groups: dict[str, list[ContentBlock]] = {}
            current_key: str | None = None
            for v, ct, fn, hc in captioned:
                if fn:
                    current_key = fn
                key = current_key or f"unlabeled_{v.block_id}"
                groups.setdefault(key, []).append(v)
            result = list(groups.values())
            for g in result:
                for item in g:
                    item.metadata["group_provenance"] = "separate_caption"
            return result

        combined_text_for_single_figure = " ".join(dict.fromkeys(ct for _, ct, _, _ in captioned if ct))
        non_empty_captions = [ct.strip() for _, ct, _, _ in captioned if ct.strip()]
        if len(all_fig_numbers) == 1 and self._has_composite_panel_markers(combined_text_for_single_figure):
            provenance = "same_caption_composite" if len(set(non_empty_captions)) == 1 else "mineru_nested_composite_caption"
            for item in visuals:
                item.metadata["group_provenance"] = provenance
            return [visuals]

        if layout_graph:
            matched = [v for v in visuals if v.block_id in layout_graph.matches]
            if matched and len(matched) == len(visuals):
                combined_text = " ".join(dict.fromkeys(self._caption_text_for_visual(v) for v in visuals))
                if self._has_composite_panel_markers(combined_text):
                    for item in visuals:
                        item.metadata["group_provenance"] = "layout_group"
                    return [visuals]

        bbox_groups = self._bbox_spatial_groups(captioned, document_graph)
        if bbox_groups:
            for group in bbox_groups:
                provenance = "bbox_spatial_cluster" if len(group) > 1 else "uncertain_split_conservative"
                for item in group:
                    item.metadata["group_provenance"] = provenance
            return bbox_groups

        for v, ct, fn, hc in captioned:
            if hc and not fn:
                for item in visuals:
                    item.metadata.setdefault("group_provenance", "fallback_single_visual")
                return [[v] for v in visuals]

        reading_orders = [v.reading_order for v in visuals]
        if max(reading_orders) - min(reading_orders) > 3:
            for item in visuals:
                item.metadata["group_provenance"] = "spatially_separated"
            return [[v] for v in visuals]

        combined_text_global = " ".join(dict.fromkeys(self._caption_text_for_visual(v) for v in visuals))
        if self._has_composite_panel_markers(combined_text_global):
            for item in visuals:
                item.metadata["group_provenance"] = "same_caption_composite"
            return [visuals]

        for item in visuals:
            item.metadata["group_provenance"] = "uncertain_split_conservative"
        return [[v] for v in visuals]

    def _bbox_spatial_groups(self, captioned: list[tuple[ContentBlock, str, str | None, bool]], document_graph: DocumentGraph) -> list[list[ContentBlock]] | None:
        if not 2 <= len(captioned) <= 6:
            return None
        visuals = [item[0] for item in captioned]
        if any(not v.bbox for v in visuals):
            return None
        fig_numbers = {fn for _, _, fn, _ in captioned if fn}
        if len(fig_numbers) >= 2:
            return None
        captions = [ct.strip().lower() for _, ct, _, _ in captioned if ct.strip()]
        repeated_caption = bool(captions) and len(set(captions)) == 1
        insufficient_caption = not captions or repeated_caption or len(fig_numbers) == 1
        if not insufficient_caption:
            return None

        ordered = sorted(visuals, key=lambda b: ((b.bbox or [0, 0, 0, 0])[1], (b.bbox or [0, 0, 0, 0])[0]))
        groups: list[list[ContentBlock]] = []
        current = [ordered[0]]
        current_reasons: list[dict[str, Any]] = []
        group_reasons: dict[str, list[dict[str, Any]]] = {}
        for block in ordered[1:]:
            decision = _bbox_cluster_decision(current, block)
            if decision["cluster"]:
                current.append(block)
                current_reasons.append(decision)
            else:
                groups.append(current)
                group_reasons[current[0].block_id] = current_reasons
                current = [block]
                current_reasons = []
        groups.append(current)
        group_reasons[current[0].block_id] = current_reasons
        if all(len(group) == 1 for group in groups):
            return None
        for group in groups:
            nearby_caption_ids = _nearby_caption_ids(document_graph, group)
            reason_payload = {
                "cluster_decisions": group_reasons.get(group[0].block_id, []),
                "nearby_caption_block_ids": nearby_caption_ids,
                "caption_state": "none" if not captions else ("repeated" if repeated_caption else "shared_figure_number" if fig_numbers else "insufficient"),
            }
            for block in group:
                block.metadata["bbox_cluster_reason"] = reason_payload
        return groups

    def _build_figure(self, group: list[ContentBlock], document_graph: DocumentGraph) -> FigureNode:
        first = group[0]
        caption_texts = [self._caption_text_for_visual(b) for b in group]
        combined_caption = " ".join(dict.fromkeys(ct for ct in caption_texts if ct))
        figure_label = _figure_label(combined_caption) or f"Figure {len(document_graph.image_blocks + document_graph.chart_blocks)}"
        figure_id = _slug(figure_label)
        provenance_methods = [b.metadata.get("group_provenance", "layout_or_spatial_visual_grouping") for b in group]
        unique_provs = list(dict.fromkeys(provenance_methods))
        caption_blocks = self._figure_caption_blocks(document_graph, group)
        full_caption_blocks = self._full_figure_caption_blocks(group)
        figure = FigureNode(
            figure_id=figure_id,
            label=figure_label,
            page_idx=first.page_idx,
            image_blocks=[b.block_id for b in group if b.normalized_type == "image"],
            chart_blocks=[b.block_id for b in group if b.normalized_type == "chart"],
            caption_blocks=caption_blocks,
            bbox_union=_bbox_union([b.bbox for b in group if b.bbox]),
            provenance={
                "method": unique_provs[0] if len(unique_provs) == 1 else "mixed_grouping",
                "all_provenances": unique_provs,
                "full_caption_block_ids": full_caption_blocks,
                "caption_figure_number": self._figure_number_from_text(combined_caption),
                "bbox_cluster_reason": group[0].metadata.get("bbox_cluster_reason", {}),
            },
        )

        markers = self.marker_detector.detect(combined_caption, figure_label=figure_label)
        caption_segments = _panel_caption_segments(combined_caption, markers)
        single_visual_segment = _single_visual_caption_segment(combined_caption) if len(group) == 1 else None
        used_labels: set[str] = set()
        for index, visual in enumerate(group, 1):
            label = self._panel_label_for_visual(visual, markers, index, used_labels)
            used_labels.add(label)
            segment = caption_segments.get(label.lower()) or single_visual_segment or _missing_caption_segment()
            panel_id = f"{figure_id}-{label}"
            own_caps = self._own_caption_blocks(visual, figure)
            panel = PanelNode(
                panel_id=panel_id,
                panel_label=label,
                parent_figure_id=figure.figure_id,
                page_idx=visual.page_idx,
                image_block_id=visual.block_id if visual.normalized_type == "image" else None,
                chart_block_id=visual.block_id if visual.normalized_type == "chart" else None,
                caption_block_ids=own_caps,
                bbox=visual.bbox,
                spatial_position=_spatial_position(visual, group),
                local_context_block_ids=self._local_context(document_graph, visual, own_caps),
                related_table_ids=_related_ids(document_graph.table_blocks, group),
                related_formula_ids=_related_ids(document_graph.formula_blocks, group),
                related_reference_ids=_related_ids(document_graph.reference_blocks, group),
                provenance={"panel_marker_source": "detector" if index - 1 < len(markers) else "spatial_index", "visual_index": index},
                caption_segment_text=segment["text"],
                caption_segment_status=segment["status"],
                caption_segment_confidence=segment["confidence"],
                caption_segment_grouped_panel_labels=segment["grouped_panel_labels"],
                caption_segment_provenance=segment["provenance"],
            )
            panel.provenance["grouping_method"] = figure.provenance["method"]
            figure.panels.append(panel)

        for panel in figure.panels:
            panel.sibling_panel_ids = [p.panel_id for p in figure.panels if p.panel_id != panel.panel_id]
            for block in group:
                if block.block_id == panel.image_block_id or block.block_id == panel.chart_block_id:
                    panel.related_table_ids = _related_ids(document_graph.table_blocks, [block])
                    panel.related_formula_ids = _related_ids(document_graph.formula_blocks, [block])
                    panel.related_reference_ids = _related_ids(document_graph.reference_blocks, [block])
                    break

        return figure

    def _figure_caption_blocks(self, document_graph: DocumentGraph, group: list[ContentBlock]) -> list[str]:
        ids: list[str] = []
        page = document_graph.pages.get(group[0].page_idx)
        if not page:
            return ids
        group_figure_number = next(
            (
                self._figure_number_from_text(self._caption_text_for_visual(block))
                for block in group
                if self._figure_number_from_text(self._caption_text_for_visual(block))
            ),
            None,
        )
        visual_ids = {b.block_id for b in group}
        visual_orders = {b.reading_order for b in group}
        for block in page.blocks_by_reading_order:
            if block.block_id in visual_ids and block.caption_fields:
                ids.append(block.block_id)
                continue
            if block.normalized_type in {"text", "heading"} and any(abs(block.reading_order - order) <= 2 for order in visual_orders):
                block_figure_number = self._figure_number_from_text(block.text)
                if block_figure_number and (group_figure_number is None or block_figure_number == group_figure_number):
                    ids.append(block.block_id)
        return list(dict.fromkeys(ids))

    def _full_figure_caption_blocks(self, group: list[ContentBlock]) -> list[str]:
        ids = []
        for block in group:
            caption = self._caption_text_for_visual(block)
            if self._figure_number_from_text(caption):
                ids.append(block.block_id)
        return list(dict.fromkeys(ids))

    def _own_caption_blocks(self, visual: ContentBlock, figure: FigureNode) -> list[str]:
        full_caption_ids = list(figure.provenance.get("full_caption_block_ids") or [])
        if visual.caption_fields:
            return list(dict.fromkeys([visual.block_id, *full_caption_ids]))
        return list(dict.fromkeys(full_caption_ids or figure.caption_blocks))

    def _panel_label_for_visual(self, visual: ContentBlock, markers: list[Any], index: int, used_labels: set[str]) -> str:
        own_markers = self.marker_detector.detect(self._caption_text_for_visual(visual))
        for own_marker in own_markers:
            candidate = own_marker.marker
            if candidate not in used_labels:
                return candidate
        if index - 1 < len(markers):
            candidate = markers[index - 1].marker
            if candidate not in used_labels:
                return candidate
        for marker in markers:
            candidate = marker.marker
            if candidate not in used_labels:
                return candidate
        candidate = _spatial_label(index)
        if candidate not in used_labels:
            return candidate
        return f"{candidate}{index}"

    def _local_context(self, document_graph: DocumentGraph, visual: ContentBlock, own_caps: list[str]) -> list[str]:
        ids = []
        page = document_graph.pages.get(visual.page_idx)
        if page:
            for block in page.blocks_by_reading_order:
                if block.block_id == visual.block_id or block.block_id in own_caps:
                    continue
                if block.normalized_type in {"text", "list", "heading"} and abs(block.reading_order - visual.reading_order) <= 3:
                    ids.append(block.block_id)
        return list(dict.fromkeys([visual.block_id] + own_caps + ids))


def _related_ids(blocks: list[ContentBlock], group: list[ContentBlock]) -> list[str]:
    if not group:
        return []
    page_idx = group[0].page_idx
    min_order = min(b.reading_order for b in group)
    max_order = max(b.reading_order for b in group)
    return [b.block_id for b in blocks if b.page_idx == page_idx and min_order - 3 <= b.reading_order <= max_order + 5]


def _figure_label(text: str) -> str | None:
    match = re.search(r"(?i)\b(fig(?:ure)?\.?\s*\d+)\b", text or "")
    return match.group(1) if match else None


def _slug(label: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "-", label.strip()).strip("-").lower()
    return cleaned or "figure"


def _bbox_union(boxes: list[list[float] | None]) -> list[float] | None:
    valid = [box for box in boxes if box and len(box) == 4]
    if not valid:
        return None
    return [min(b[0] for b in valid), min(b[1] for b in valid), max(b[2] for b in valid), max(b[3] for b in valid)]


def _bbox_cluster_decision(current: list[ContentBlock], candidate: ContentBlock) -> dict[str, Any]:
    current_union = _bbox_union([b.bbox for b in current])
    cand = candidate.bbox
    if not current_union or not cand:
        return {"cluster": False, "reason": "missing_bbox", "candidate_block_id": candidate.block_id}
    x_overlap = _x_overlap_ratio(current_union, cand)
    y_overlap = _y_overlap_ratio(current_union, cand)
    horizontal_gap = _horizontal_gap(current_union, cand)
    vertical_gap = _vertical_gap(current_union, cand)
    x_gap_limit = _gap_limit(current_union, cand, axis="x")
    y_gap_limit = _gap_limit(current_union, cand, axis="y")
    same_row = y_overlap >= 0.35 and horizontal_gap <= x_gap_limit
    same_column = x_overlap >= 0.35 and vertical_gap <= y_gap_limit
    union = _bbox_union([current_union, cand])
    union_area = _area(union or [])
    component_area = sum(_area(b.bbox or []) for b in current) + _area(cand)
    compactness = union_area / component_area if component_area > 0 else 999.0
    cluster = bool((same_row or same_column) and compactness <= 2.8)
    return {
        "cluster": cluster,
        "reason": "same_row_or_column_compact" if cluster else "bbox_gap_or_compactness_failed",
        "candidate_block_id": candidate.block_id,
        "current_block_ids": [block.block_id for block in current],
        "same_row": same_row,
        "same_column": same_column,
        "x_overlap_ratio": round(x_overlap, 3),
        "y_overlap_ratio": round(y_overlap, 3),
        "horizontal_gap": round(horizontal_gap, 2),
        "vertical_gap": round(vertical_gap, 2),
        "x_gap_limit": round(x_gap_limit, 2),
        "y_gap_limit": round(y_gap_limit, 2),
        "union_compactness": round(compactness, 3),
    }


def _nearby_caption_ids(document_graph: DocumentGraph, group: list[ContentBlock]) -> list[str]:
    if not group:
        return []
    page = document_graph.pages.get(group[0].page_idx)
    union = _bbox_union([block.bbox for block in group])
    if not page or not union:
        return []
    visual_ids = {block.block_id for block in group}
    min_order = min(block.reading_order for block in group)
    max_order = max(block.reading_order for block in group)
    ids: list[str] = []
    for block in page.blocks_by_reading_order:
        if block.block_id in visual_ids:
            continue
        has_caption_signal = bool(block.caption_fields) or bool(re.search(r"(?i)\bfig(?:ure)?\.?\s*\d+", block.text or ""))
        if not has_caption_signal:
            continue
        reading_near = min_order - 2 <= block.reading_order <= max_order + 3
        spatial_near = False
        if block.bbox:
            x_overlap = _x_overlap_ratio(union, block.bbox)
            vertical_gap = _vertical_gap(union, block.bbox)
            spatial_near = x_overlap >= 0.2 and vertical_gap <= 90
        if reading_near or spatial_near:
            ids.append(block.block_id)
    return list(dict.fromkeys(ids))[:5]


def _x_overlap_ratio(a: list[float], b: list[float]) -> float:
    overlap = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    return overlap / max(min(a[2] - a[0], b[2] - b[0]), 1.0)


def _y_overlap_ratio(a: list[float], b: list[float]) -> float:
    overlap = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    return overlap / max(min(a[3] - a[1], b[3] - b[1]), 1.0)


def _horizontal_gap(a: list[float], b: list[float]) -> float:
    if a[2] < b[0]:
        return b[0] - a[2]
    if b[2] < a[0]:
        return a[0] - b[2]
    return 0.0


def _vertical_gap(a: list[float], b: list[float]) -> float:
    if a[3] < b[1]:
        return b[1] - a[3]
    if b[3] < a[1]:
        return a[1] - b[3]
    return 0.0


def _gap_limit(a: list[float], b: list[float], *, axis: str) -> float:
    if axis == "x":
        avg_width = ((a[2] - a[0]) + (b[2] - b[0])) / 2
        return max(60.0, avg_width * 0.35)
    avg_height = ((a[3] - a[1]) + (b[3] - b[1])) / 2
    return max(70.0, avg_height * 0.45)


def _area(box: list[float]) -> float:
    if len(box) != 4:
        return 0.0
    return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])


def _spatial_label(index: int) -> str:
    return chr(ord("a") + index - 1) if 1 <= index <= 26 else f"p{index}"


def _spatial_position(block: ContentBlock, group: list[ContentBlock]) -> str | None:
    if not block.bbox or len(group) == 1:
        return "single"
    ordered = sorted([b for b in group if b.bbox], key=lambda b: ((b.bbox or [0, 0, 0, 0])[1], (b.bbox or [0, 0, 0, 0])[0]))
    try:
        return f"visual_order_{ordered.index(block) + 1}"
    except ValueError:
        return None


def _panel_caption_segments(caption_text: str, markers: list[Any]) -> dict[str, dict[str, Any]]:
    text = " ".join(str(caption_text or "").split())
    if not text:
        return {}
    groups = _caption_marker_groups(text, markers)
    if not groups:
        return {}
    result: dict[str, dict[str, Any]] = {}
    for index, group in enumerate(groups):
        next_start = groups[index + 1]["boundary_start"] if index + 1 < len(groups) else len(text)
        segment_text = text[group["start"]:next_start].strip(" ;,")
        if not segment_text:
            continue
        labels = sorted(group["labels"])
        status = "grouped_shared" if len(labels) > 1 else "exact"
        confidence = float(group["confidence"])
        if status == "grouped_shared":
            confidence = min(confidence, 0.68)
        payload = {
            "text": segment_text,
            "status": status,
            "confidence": round(confidence, 3),
            "grouped_panel_labels": labels if status == "grouped_shared" else [],
            "provenance": {
                "source": group["source"],
                "marker_text": group["marker_text"],
                "marker_start": group["start"],
                "segment_start": group["start"],
                "segment_end": next_start,
            },
        }
        for label in labels:
            result.setdefault(label, payload)
    return result


def _single_visual_caption_segment(caption_text: str) -> dict[str, Any] | None:
    text = " ".join(str(caption_text or "").split())
    if not text:
        return None
    return {
        "text": text,
        "status": "exact",
        "confidence": 0.86,
        "grouped_panel_labels": [],
        "provenance": {
            "source": "single_visual_full_caption",
            "segment_start": 0,
            "segment_end": len(text),
        },
    }


def _caption_marker_groups(text: str, markers: list[Any]) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    grouped_re = re.compile(
        r"(?P<prefix>^|[\s;,.])\(?\s*(?P<marker>[A-Za-z](?:\s*(?:,|/|&|and|–|-|to)\s*[A-Za-z])*)\s*[\).:]",
        re.IGNORECASE,
    )
    for match in grouped_re.finditer(text):
        labels = _labels_from_caption_marker(match.group("marker"))
        if not labels:
            continue
        groups.append({
            "labels": labels,
            "boundary_start": match.start(),
            "start": match.start("marker"),
            "end": match.end("marker"),
            "confidence": 0.9,
            "source": "group_marker_regex" if len(labels) > 1 else "marker_regex",
            "marker_text": match.group("marker"),
        })
    claimed = {(group["start"], group["end"]) for group in groups}
    for marker in markers:
        start = int(getattr(marker, "start", 0) or 0)
        end = int(getattr(marker, "end", 0) or 0)
        if (start, end) in claimed:
            continue
        label = str(getattr(marker, "marker", "") or "").lower()
        if not label:
            continue
        groups.append({
            "labels": {label},
            "boundary_start": start,
            "start": start,
            "end": end,
            "confidence": float(getattr(marker, "confidence", 0.0) or 0.0),
            "source": str(getattr(marker, "evidence_type", "") or "panel_marker_detector"),
            "marker_text": label,
        })
    groups.sort(key=lambda item: (item["start"], -len(item["labels"])))
    result: list[dict[str, Any]] = []
    seen_starts: set[int] = set()
    for group in groups:
        if group["start"] in seen_starts:
            continue
        if result and len(result[-1]["labels"]) > 1 and group["labels"].issubset(result[-1]["labels"]):
            continue
        seen_starts.add(group["start"])
        result.append(group)
    return result


def _labels_from_caption_marker(marker: str) -> set[str]:
    normalized = str(marker or "").lower().replace("and", ",").replace("&", ",").replace("/", ",")
    labels: set[str] = set()
    for part in re.split(r"\s*,\s*", normalized):
        part = part.strip()
        if not part:
            continue
        range_match = re.fullmatch(r"([a-z])\s*(?:–|-|to)\s*([a-z])", part)
        if range_match:
            start, end = range_match.groups()
            if ord(start) <= ord(end):
                labels.update(chr(code) for code in range(ord(start), ord(end) + 1))
        elif re.fullmatch(r"[a-z]", part):
            labels.add(part)
    return labels


def _missing_caption_segment() -> dict[str, Any]:
    return {
        "text": "",
        "status": "missing",
        "confidence": 0.0,
        "grouped_panel_labels": [],
        "provenance": {},
    }

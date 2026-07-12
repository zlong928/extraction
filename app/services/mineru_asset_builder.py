from __future__ import annotations

import json
import re
import struct
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.core.constants import MARKDOWN_IMAGE_RE, compact_text
from app.models import Figure, Panel, Paper, PaperAsset
from app.services.local_image_profiler import LocalImageProfiler
from app.services.mineru_asset_builder_panel_context import build_panel_context_record
from app.services.storage import StorageService
from app.services.object_store import ObjectStore
from app.services.mineru_asset_builder_paths import (
    content_list_image_paths,
    resolve_image,
)
from content_pipeline.mineru.image_path_resolver import image_path_from_block


class MinerUAssetBuilder:
    _MARKDOWN_IMAGE_RE = MARKDOWN_IMAGE_RE

    def __init__(self, db: Session, storage: StorageService | None = None) -> None:
        self.db = db
        self.storage = storage or StorageService()

    def ingest(
        self,
        paper: Paper,
        markdown: str,
        extract_dir: str | None,
        *,
        content_list_path: str | None = None,
        layout_path: str | None = None,
    ) -> list[PaperAsset]:
        if not markdown or not extract_dir:
            return []
        root = Path(extract_dir)
        if not root.is_dir():
            return []

        md_refs = self._image_refs(markdown)
        content_list = self._load_content_list(content_list_path)
        layout_pages = self._load_layout_pages(layout_path)
        assets: list[PaperAsset] = []
        seen_filenames: set[str] = set()

        cl_image_blocks = [
            item
            for item in content_list
            if isinstance(item, dict) and item.get("type") in ("image", "chart")
        ]

        def _make_asset(
            image_path: Path,
            img_path_rel: str,
            alt: str,
            nearby_text: str,
            section: str,
            md_label: str,
            content_match: dict[str, Any],
            is_content_list_source: bool,
        ) -> PaperAsset | None:
            filename = image_path.name
            if filename in seen_filenames:
                return None
            seen_filenames.add(filename)

            stored = self._copy_asset(paper, image_path, len(assets))
            if stored is None:
                return None
            relative_path = stored.object_key
            width, height = self._image_dimensions(image_path)
            file_size = self._file_size(image_path)
            full_caption = self._merged_caption_from_parts(alt, content_match.get("content_list_caption", ""))
            panel_id = self._panel_id(alt) or self._panel_id(full_caption)
            mineru_type = str(content_match.get("mineru_type") or "markdown_image")
            page_idx = content_match.get("page_idx")
            layout_page = layout_pages.get(page_idx) if isinstance(page_idx, int) else None
            nearby_for_profile = " ".join(
                part for part in [nearby_text, str(content_match.get("nearby_content") or "")] if part
            )
            citation_context = content_match.get("citation_context", [])
            parent_figure_id = LocalImageProfiler.parent_figure_id(full_caption or nearby_text, md_label)
            markdown_context = self._markdown_context_for_image(
                markdown=markdown,
                image_name=image_path.name,
                ref_line=None,
            )
            if md_label:
                for ref in md_refs:
                    if ref.get("label") == md_label and ref.get("path"):
                        md_ref_name = Path(ref.get("path", "")).name
                        if md_ref_name and md_ref_name == image_path.name:
                            markdown_context = self._markdown_context_for_image(
                                markdown=markdown,
                                image_name=image_path.name,
                                ref_line=ref.get("line_index"),
                            )
                            break

            panel_context = self._build_panel_context_record(
                panel_id=panel_id,
                parent_figure_id=parent_figure_id,
                full_parent_caption=full_caption,
                nearby_candidates=[
                    nearby_text,
                    str(content_match.get("nearby_content") or ""),
                    str(content_match.get("mineru_nearby_text") or ""),
                    " ".join(str(item) for item in citation_context) if isinstance(citation_context, list) else "",
                ],
                markdown_candidates=markdown_context,
            )
            caption = (
                panel_context.get("panel_caption")
                or panel_context.get("panel_nearby_text")
                or MinerUAssetBuilder._compact_text(nearby_text)
            )
            local_profile = LocalImageProfiler.profile(
                caption=caption,
                nearby_text=nearby_for_profile,
                width=width,
                height=height,
                file_size=file_size,
                mineru_type=mineru_type,
                panel_id=panel_id,
                bbox=content_match.get("bbox") if isinstance(content_match.get("bbox"), list) else None,
                layout_page=layout_page,
            )
            page_number = int(page_idx) + 1 if isinstance(page_idx, int) else None
            label = self._label(caption) or self._label(nearby_text) or md_label or f"Figure {len(assets) + 1}"
            parent_figure_id = LocalImageProfiler.parent_figure_id(full_caption or nearby_text, label)
            section_hierarchy = content_match.get("section_hierarchy", [])
            layout_context_text = self._layout_context_str(layout_page, content_match.get("bbox"))

            metadata = {
                "source": "mineru_content_list" if is_content_list_source else "mineru_markdown",
                "mineru_img_path": img_path_rel,
                "mineru_alt_text": alt,
                "mineru_nearby_text": nearby_text,
                "mineru_section": section,
                "content_list_caption": content_match.get("content_list_caption", ""),
                "nearby_content": content_match.get("nearby_content", ""),
                "section_hierarchy": section_hierarchy,
                "citation_context": citation_context,
                "layout_context_text": layout_context_text,
                "page_idx": page_idx,
                "bbox": content_match.get("bbox"),
                "layout_page": layout_page or {},
                "parent_figure_id": parent_figure_id,
                "figure_group_key": f"paper-{paper.id}:{parent_figure_id}",
                "evidence_shape_hint": local_profile.evidence_shape,
                "recommended_extractor_hint": local_profile.recommended_extractor,
                "figure_role_hint": local_profile.figure_role,
                "visual_role": "chart_candidate" if mineru_type == "chart" else "image_candidate",
                "data_extraction_possible": local_profile.extraction_readiness != "skip",
                "image_width": width,
                "image_height": height,
                "file_size": file_size,
                "asset_scope": local_profile.asset_scope,
                "extraction_readiness": local_profile.extraction_readiness,
                "skip_reason": local_profile.skip_reason,
                "mineru_type": mineru_type,
                "full_caption": full_caption,
                "panel_caption": caption,
                "panel_context": panel_context,
                "panel_id": panel_id,
                "local_profile_confidence": local_profile.confidence,
                "local_profile_uncertainty": local_profile.uncertainty_reason,
            }
            return PaperAsset(
                paper_id=paper.id,
                object_id=stored.id,
                asset_type="figure",
                asset_index=len(assets),
                label=label,
                page_number=page_number,
                file_path=relative_path,
                mime_type=self._mime_type(relative_path),
                width=width,
                height=height,
                caption=caption,
                metadata_json=json.dumps(metadata, ensure_ascii=False),
            )

        # Phase 1: iterate content_list image/chart blocks (most authoritative)
        for cl_item in cl_image_blocks:
            img_path_rel = str(cl_item.get("img_path") or "")
            if not img_path_rel:
                continue
            source = self._resolve_image(root, img_path_rel)
            if source is None:
                continue
            cl_match = self._build_cl_match(cl_item, content_list)
            captions: list[str] = []
            for ckey in ("image_caption", "chart_caption"):
                raw = cl_item.get(ckey)
                if isinstance(raw, list):
                    captions.extend(self._compact_text(v) for v in raw if self._compact_text(v))
            alt = "; ".join(dict.fromkeys(captions)) if captions else ""
            asset = _make_asset(
                image_path=source,
                img_path_rel=img_path_rel,
                alt=alt,
                nearby_text="",
                section="",
                md_label=cl_match.get("label", ""),
                content_match=cl_match,
                is_content_list_source=True,
            )
            if asset is not None:
                assets.append(asset)

        # Phase 2: markdown-only images not yet covered by content_list
        for ref in md_refs:
            source = self._resolve_image(root, ref["path"])
            if source is None:
                continue
            filename = source.name
            if filename in seen_filenames:
                continue
            content_match = self._content_item_for_image(content_list, ref)
            md_alt = ref.get("caption") or ref.get("alt", "")
            asset = _make_asset(
                image_path=source,
                img_path_rel=ref["path"],
                alt=md_alt,
                nearby_text=ref.get("nearby_text", ""),
                section=ref.get("section", ""),
                md_label=ref.get("label", ""),
                content_match=content_match,
                is_content_list_source=bool(content_match),
            )
            if asset is not None:
                assets.append(asset)

        self._annotate_figure_groups(assets)
        for asset in assets:
            self.db.add(asset)
        self.db.flush()
        self._persist_figure_groups(paper, assets)
        return assets

    @staticmethod
    def _group_assets_by_figure(assets: list[PaperAsset]) -> dict[str, list[PaperAsset]]:
        groups: dict[str, list[PaperAsset]] = {}
        for asset in assets:
            metadata = MinerUAssetBuilder._asset_metadata(asset)
            group_key = str(metadata.get("figure_group_key") or asset.label or f"asset-{asset.asset_index}")
            groups.setdefault(group_key, []).append(asset)
        return groups

    def _annotate_figure_groups(self, assets: list[PaperAsset]) -> None:
        groups = self._group_assets_by_figure(assets)
        for group_assets in groups.values():
            sibling_indices = [asset.asset_index for asset in group_assets]
            for position, asset in enumerate(group_assets, 1):
                metadata = self._asset_metadata(asset)
                metadata["figure_group_size"] = len(group_assets)
                metadata["panel_index"] = position
                metadata["sibling_asset_indices"] = sibling_indices
                metadata["is_multi_panel_group"] = len(group_assets) > 1
                asset.metadata_json = json.dumps(metadata, ensure_ascii=False)

    def _persist_figure_groups(self, paper: Paper, assets: list[PaperAsset]) -> None:
        groups = self._group_assets_by_figure(assets)

        for group_key, group_assets in groups.items():
            primary = group_assets[0]
            meta = self._asset_metadata(primary)
            parent_figure_id = str(meta.get("parent_figure_id") or primary.label or f"Figure {primary.asset_index + 1}")
            figure = Figure(
                paper_id=paper.id,
                figure_id=parent_figure_id,
                caption_text=primary.caption or meta.get("full_caption") or "",
                page_number=primary.page_number,
                is_multi_panel=len(group_assets) > 1,
                panel_count=len(group_assets),
                metadata_json=json.dumps({
                    "figure_group_key": group_key,
                    "sibling_asset_indices": [a.asset_index for a in group_assets],
                }, ensure_ascii=False),
            )
            self.db.add(figure)
            self.db.flush()

            for position, asset in enumerate(group_assets, 1):
                asset.figure_id = figure.id
                asset_meta = self._asset_metadata(asset)
                evidence_shape = str(asset_meta.get("evidence_shape_hint") or "unknown")
                domain_task = str(asset_meta.get("domain_hint") or "unknown")
                extractor = str(asset_meta.get("recommended_extractor_hint") or "overview_schematic_extractor")
                panel = Panel(
                    figure_id=figure.id,
                    asset_id=asset.id,
                    panel_id=str(asset_meta.get("panel_id") or f"{parent_figure_id}-P{position}"),
                    evidence_shape=evidence_shape,
                    domain_task=domain_task,
                    extractor=extractor,
                    extraction_priority="panel_level" if len(group_assets) > 1 else "figure_level",
                    panel_index=position,
                    metadata_json=json.dumps({
                        "asset_index": asset.asset_index,
                        "sibling_asset_indices": [a.asset_index for a in group_assets],
                        "is_multi_panel_group": len(group_assets) > 1,
                    }, ensure_ascii=False),
                )
                self.db.add(panel)

        self.db.flush()

    @staticmethod
    def _asset_metadata(asset: PaperAsset) -> dict[str, Any]:
        try:
            parsed = json.loads(asset.metadata_json or "{}")
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}

    @staticmethod
    def _image_refs(markdown: str) -> list[dict[str, Any]]:
        lines = markdown.splitlines()
        current_section = ""
        refs: list[dict[str, Any]] = []
        for index, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("#"):
                current_section = stripped.lstrip("#").strip()
            for match in MinerUAssetBuilder._MARKDOWN_IMAGE_RE.finditer(line):
                alt = match.group(1).strip()
                nearby = " ".join(
                    lines[i].strip()
                    for i in range(max(0, index - 3), min(len(lines), index + 4))
                    if i != index and lines[i].strip()
                )
                label = MinerUAssetBuilder._label(alt)
                raw_path = match.group(2).strip().lstrip("./")
                path_clean = raw_path.split('"')[0].split("'")[0].strip()
                refs.append(
                    {
                        "alt": alt,
                        "path": path_clean,
                        "filename": Path(path_clean).name,
                        "label": label or "",
                        "caption": MinerUAssetBuilder._caption(alt, label),
                        "nearby_text": nearby[:1000],
                        "section": current_section,
                        "line_index": index,
                    }
                )
        return refs

    @staticmethod
    def _markdown_context_for_image(markdown: str, image_name: str, *, ref_line: int | None = None) -> list[str]:
        lines = markdown.splitlines()
        if not lines or not image_name:
            return []
        matches: list[int] = []
        for index, line in enumerate(lines):
            if image_name in line:
                matches.append(index)
        if ref_line is not None and all(index != ref_line for index in matches):
            matches.append(ref_line)

        contexts: list[str] = []
        for line_no in sorted(set(matches)):
            start = max(0, line_no - 40)
            end = min(len(lines), line_no + 160)
            contexts.append("\n".join(lines[start:end]))
        if not contexts and lines:
            if ref_line is not None:
                start = max(0, ref_line - 40)
                end = min(len(lines), ref_line + 160)
                contexts.append("\n".join(lines[start:end]))
        return contexts

    @staticmethod
    def _load_content_list(content_list_path: str | None) -> list[dict[str, Any]]:
        if not content_list_path:
            return []
        path = Path(content_list_path)
        if not path.is_file():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return []
        if isinstance(data, list) and all(isinstance(p, list) for p in data):
            return MinerUAssetBuilder._flatten_v2(data)
        return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []

    @staticmethod
    def _flatten_v2(pages: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
        flat: list[dict[str, Any]] = []
        for page_idx, page_items in enumerate(pages):
            if not isinstance(page_items, list):
                continue
            for item in page_items:
                if not isinstance(item, dict):
                    continue
                block = dict(item)
                block["page_idx"] = page_idx
                content = block.get("content")
                if isinstance(content, dict):
                    text = MinerUAssetBuilder._extract_nested_text(content)
                    if text and not block.get("text"):
                        block["text"] = text
                    for cap_key in ("image_caption", "chart_caption", "table_caption", "caption"):
                        cap = content.get(cap_key)
                        if cap:
                            cap_text = MinerUAssetBuilder._extract_nested_text(cap)
                            if cap_text:
                                block.setdefault(cap_key, []).append(cap_text)
                    level = content.get("level")
                    if level is not None and block.get("text_level") is None:
                        block["text_level"] = level
                if not block.get("img_path"):
                    block["img_path"] = image_path_from_block(block)
                if block.get("img_path") is None:
                    block["img_path"] = ""
                flat.append(block)
        return flat

    @staticmethod
    def _extract_nested_text(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        if isinstance(value, dict):
            parts: list[str] = []
            for v in value.values():
                t = MinerUAssetBuilder._extract_nested_text(v)
                if t:
                    parts.append(t)
            return " ".join(parts)
        if isinstance(value, list):
            parts = []
            for item in value:
                if isinstance(item, dict):
                    inner = item.get("content") or item.get("text") or ""
                    if isinstance(inner, str):
                        parts.append(inner)
                    else:
                        t = MinerUAssetBuilder._extract_nested_text(item)
                        if t:
                            parts.append(t)
                elif isinstance(item, str):
                    parts.append(item)
            return " ".join(parts)
        return str(value)

    @staticmethod
    def _load_layout_pages(layout_path: str | None) -> dict[int, dict[str, Any]]:
        if not layout_path:
            return {}
        path = Path(layout_path)
        if not path.is_file():
            return {}
        if path.suffix.lower() != ".json":
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        candidates: list[Any]
        if isinstance(data, list):
            candidates = data
        elif isinstance(data, dict):
            candidates = data.get("pages") if isinstance(data.get("pages"), list) else []
            if not candidates and any(key in data for key in ["page_idx", "page_id", "width", "height"]):
                candidates = [data]
        else:
            candidates = []
        pages: dict[int, dict[str, Any]] = {}
        for index, item in enumerate(candidates):
            if not isinstance(item, dict):
                continue
            raw_idx = item.get("page_idx", item.get("page_id", index))
            if isinstance(raw_idx, int):
                pages[raw_idx] = item
        return pages

    @staticmethod
    def _section_hierarchy(content_list: list[dict[str, Any]], hit_index: int) -> list[dict[str, Any]]:
        hierarchy: list[dict[str, Any]] = []
        seen_levels: set[int] = set()
        for item in reversed(content_list[:hit_index]):
            text_level = item.get("text_level")
            if text_level is not None and isinstance(text_level, int) and text_level >= 1:
                title = MinerUAssetBuilder._compact_text(item.get("text") or "")
                if title and text_level not in seen_levels:
                    hierarchy.append({"level": text_level, "title": title})
                    seen_levels.add(text_level)
        hierarchy.reverse()
        return hierarchy

    @staticmethod
    def _citation_context(content_list: list[dict[str, Any]], hit_index: int, figure_labels: list[str]) -> list[str]:
        if not figure_labels:
            return []
        citations: list[str] = []
        for item in content_list[hit_index + 1 : hit_index + 16]:
            text = MinerUAssetBuilder._compact_text(item.get("text") or "")
            if not text or item.get("type") not in ("text",):
                continue
            if any(label.lower() in text.lower() for label in figure_labels if label):
                citations.append(text[:400])
        return citations

    @staticmethod
    def _figure_labels_from_item(item: dict[str, Any], ref: dict[str, Any]) -> list[str]:
        labels: list[str] = []
        raw_label = str(ref.get("label") or "")
        if raw_label:
            labels.append(raw_label)
        for key in ("image_caption", "chart_caption"):
            raw = item.get(key)
            if isinstance(raw, list):
                for part in raw:
                    match = re.search(r"(?i)\b(fig(?:ure)?\.?\s*\d+[a-z]?)\b", str(part))
                    if match:
                        labels.append(match.group(1))
        return list(dict.fromkeys(labels))

    @staticmethod
    def _layout_context_str(layout_page: dict[str, Any] | None, bbox: list[Any] | None) -> str:
        if not layout_page:
            return ""
        parts: list[str] = []
        page_size = layout_page.get("page_size") or layout_page.get("size") or [0, 0]
        if isinstance(page_size, (list, tuple)) and len(page_size) == 2:
            parts.append(f"page_dimensions: {page_size[0]}x{page_size[1]}pt")
        if bbox and isinstance(bbox, list) and len(bbox) == 4:
            bbox_float = [float(v) for v in bbox]
            parts.append(
                f"image_bbox_on_page: [{bbox_float[0]:.0f}, {bbox_float[1]:.0f}, {bbox_float[2]:.0f}, "
                f"{bbox_float[3]:.0f}]"
            )
            if page_size and page_size[0] > 0:
                mid_x = (bbox_float[0] + bbox_float[2]) / 2
                parts.append("layout: single_column" if mid_x < page_size[0] * 0.6 else "layout: double_column_right")
        preproc = layout_page.get("preproc_blocks") or []
        block_types: dict[str, int] = {}
        for block in preproc[:50]:
            bt = str(block.get("type") or "unknown")
            block_types[bt] = block_types.get(bt, 0) + 1
        if block_types:
            summary = ", ".join(f"{k}: {v}" for k, v in sorted(block_types.items()))
            parts.append(f"page_blocks: {summary}")
        return " | ".join(parts)

    @staticmethod
    def _content_item_for_image(content_list: list[dict[str, Any]], image_ref: dict[str, Any]) -> dict[str, Any]:
        if not content_list:
            return {}
        target_name = str(image_ref.get("filename") or "").lower()
        target_key = str(image_ref.get("path") or "").lower()
        hit_index = -1
        for idx, item in enumerate(content_list):
            item_path = str(item.get("img_path") or "").lower()
            if not item_path:
                continue
            if target_name and target_name in item_path:
                hit_index = idx
                break
            if target_key and (item_path.endswith(target_key) or target_key.endswith(item_path)):
                hit_index = idx
                break
        if hit_index < 0:
            return {}

        item = content_list[hit_index]
        captions: list[str] = []
        for key in ("image_caption", "chart_caption"):
            raw = item.get(key)
            if isinstance(raw, list):
                captions.extend(
                    MinerUAssetBuilder._compact_text(value)
                    for value in raw
                    if MinerUAssetBuilder._compact_text(value)
                )
        inline_caption = MinerUAssetBuilder._compact_text(item.get("content"))
        if inline_caption:
            captions.append(inline_caption)
        for key in ("image_footnote", "chart_footnote"):
            raw = item.get(key)
            if isinstance(raw, list):
                captions.extend(
                    MinerUAssetBuilder._compact_text(value)
                    for value in raw
                    if MinerUAssetBuilder._compact_text(value)
                )

        nearby_parts: list[str] = []
        nearby_types = {"text", "image", "chart"}
        for sibling in content_list[max(0, hit_index - 10) : hit_index + 11]:
            sib_type = str(sibling.get("type") or "")
            if sib_type not in nearby_types:
                continue
            sibling_text = MinerUAssetBuilder._compact_text(sibling.get("content"))
            if sibling_text:
                nearby_parts.append(f"{sib_type}: {sibling_text}")

        figure_labels = MinerUAssetBuilder._figure_labels_from_item(item, image_ref)
        section_hierarchy = MinerUAssetBuilder._section_hierarchy(content_list, hit_index)
        citation_context = MinerUAssetBuilder._citation_context(content_list, hit_index, figure_labels)

        return {
            "mineru_type": str(item.get("type") or ""),
            "page_idx": item.get("page_idx"),
            "bbox": item.get("bbox"),
            "content_list_caption": "; ".join(dict.fromkeys(captions)),
            "nearby_content": "; ".join(nearby_parts),
            "section_hierarchy": section_hierarchy,
            "citation_context": citation_context,
        }

    @staticmethod
    def _merged_caption_from_parts(alt: str, content_list_caption: str) -> str:
        alt_clean = MinerUAssetBuilder._compact_text(alt)
        cl_clean = MinerUAssetBuilder._compact_text(content_list_caption)
        if not cl_clean:
            return alt_clean
        if not alt_clean:
            return cl_clean
        if cl_clean in alt_clean:
            return alt_clean
        if alt_clean in cl_clean:
            return cl_clean
        return f"{alt_clean} {cl_clean}".strip()

    @staticmethod
    def _figure_number(parent_figure_id: str) -> str:
        match = re.search(r"(?i)fig(?:ure)?\.?\s*(\d+)", parent_figure_id)
        return match.group(1) if match else ""

    @classmethod
    def _build_panel_context_record(
        cls,
        *,
        panel_id: str | None,
        parent_figure_id: str,
        full_parent_caption: str,
        nearby_candidates: list[str],
        markdown_candidates: list[str] | None = None,
    ) -> dict[str, Any]:
        return build_panel_context_record(
            panel_id=panel_id,
            parent_figure_id=parent_figure_id,
            full_parent_caption=full_parent_caption,
            nearby_candidates=nearby_candidates,
            markdown_candidates=markdown_candidates,
        )

    @staticmethod
    def _build_cl_match(
        cl_item: dict[str, Any],
        content_list: list[dict[str, Any]],
    ) -> dict[str, Any]:
        raw_type = str(cl_item.get("type") or "")
        page_idx = cl_item.get("page_idx")
        bbox = cl_item.get("bbox")
        captions: list[str] = []
        for key in ("image_caption", "chart_caption"):
            raw = cl_item.get(key)
            if isinstance(raw, list):
                captions.extend(
                    MinerUAssetBuilder._compact_text(value)
                    for value in raw
                    if MinerUAssetBuilder._compact_text(value)
                )
        inline_caption = MinerUAssetBuilder._compact_text(cl_item.get("content"))
        if inline_caption:
            captions.append(inline_caption)
        for key in ("image_footnote", "chart_footnote"):
            raw = cl_item.get(key)
            if isinstance(raw, list):
                captions.extend(
                    MinerUAssetBuilder._compact_text(value)
                    for value in raw
                    if MinerUAssetBuilder._compact_text(value)
                )
        caption_text = "; ".join(dict.fromkeys(captions))
        hit_index = MinerUAssetBuilder._cl_index(content_list, cl_item)
        nearby_content = ""
        if hit_index >= 0:
            nearby_parts: list[str] = []
            for sibling in content_list[max(0, hit_index - 10) : hit_index + 11]:
                sib_type = str(sibling.get("type") or "")
                if sib_type in {"text", "image", "chart"}:
                    sib_text = MinerUAssetBuilder._compact_text(sibling.get("content") or sibling.get("text") or "")
                    if sib_text:
                        nearby_parts.append(f"{sib_type}: {sib_text}")
            nearby_content = "; ".join(nearby_parts)
        figure_labels: list[str] = []
        label_match = re.search(r"(?i)\b(fig(?:ure)?\.?\s*\d+[a-z]?)\b", caption_text)
        if label_match:
            figure_labels.append(label_match.group(1))
        section_hierarchy = (
            MinerUAssetBuilder._section_hierarchy(content_list, hit_index)
            if hit_index >= 0
            else []
        )
        citation_context = (
            MinerUAssetBuilder._citation_context(content_list, hit_index, figure_labels)
            if hit_index >= 0
            else []
        )
        return {
            "mineru_type": raw_type,
            "page_idx": page_idx,
            "bbox": bbox,
            "content_list_caption": caption_text,
            "nearby_content": nearby_content,
            "section_hierarchy": section_hierarchy,
            "citation_context": citation_context,
            "label": figure_labels[0] if figure_labels else "",
        }

    @staticmethod
    def _cl_index(content_list: list[dict[str, Any]], target: dict[str, Any]) -> int:
        for idx, item in enumerate(content_list):
            if item is target:
                return idx
        return -1

    @staticmethod
    def _resolve_image(root: Path, image_path: str) -> Path | None:
        return resolve_image(root, image_path)

    @classmethod
    def content_list_image_paths(cls, content_list_path: str | Path, image_dir: str | Path) -> list[Path]:
        content_list = cls._load_content_list(str(content_list_path))
        if not content_list:
            return []
        return content_list_image_paths(content_list, image_dir)

    def _copy_asset(self, paper: Paper, source: Path, index: int):
        key = f"papers/{paper.id}/assets/mineru-{index + 1}{source.suffix or '.png'}"
        try:
            return ObjectStore(self.db, self.storage.adapter).put_file(
                key=key,
                source=source,
                media_type=self._mime_type(source.name),
                metadata={"role": "extracted_image", "paper_id": paper.id, "asset_index": index},
            )
        except Exception:
            return None

    @staticmethod
    def _file_size(path: Path) -> int:
        try:
            return path.stat().st_size
        except OSError:
            return 0

    @staticmethod
    def _image_dimensions(path: Path) -> tuple[int | None, int | None]:
        try:
            data = path.read_bytes()
        except OSError:
            return None, None
        if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
            try:
                width, height = struct.unpack(">II", data[16:24])
                return int(width), int(height)
            except struct.error:
                return None, None
        if data.startswith(b"\xff\xd8"):
            return MinerUAssetBuilder._jpeg_dimensions(data)
        return None, None

    @staticmethod
    def _jpeg_dimensions(data: bytes) -> tuple[int | None, int | None]:
        index = 2
        while index + 9 < len(data):
            if data[index] != 0xFF:
                index += 1
                continue
            marker = data[index + 1]
            index += 2
            while marker == 0xFF and index < len(data):
                marker = data[index]
                index += 1
            if marker in {0xD8, 0xD9}:
                continue
            if index + 2 > len(data):
                return None, None
            segment_length = int.from_bytes(data[index:index + 2], "big")
            if segment_length < 2 or index + segment_length > len(data):
                return None, None
            if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
                if segment_length >= 7:
                    height = int.from_bytes(data[index + 3:index + 5], "big")
                    width = int.from_bytes(data[index + 5:index + 7], "big")
                    return width, height
                return None, None
            index += segment_length
        return None, None

    @staticmethod
    def _panel_id(text: str) -> str | None:
        stripped = text.strip()
        prefix = re.match(r"(?i)^\s*([a-z])\s*[;:,.\-]\s+", stripped)
        if prefix:
            return prefix.group(1).lower()
        match = re.search(r"(?i)\bpanel\s*([a-z])\)|(?<![a-zA-Z])\(?([a-z])\)", stripped)
        if match:
            return (match.group(1) or match.group(2)).lower()
        if re.fullmatch(r"[a-z]\)?", stripped, re.IGNORECASE):
            return stripped[0].lower()
        return None

    @staticmethod
    def _label(text: str) -> str | None:
        match = re.search(r"(?i)\b(fig(?:ure)?\.?\s*\d+[a-z]?)\b", text)
        return match.group(1) if match else None

    @staticmethod
    def _caption(text: str, label: str | None) -> str:
        if not label:
            return text
        return re.sub(r"^" + re.escape(label) + r"[:\s.\-]+", "", text, flags=re.IGNORECASE).strip() or text

    @staticmethod
    def _mime_type(path: str) -> str:
        suffix = Path(path).suffix.lower()
        if suffix in {".jpg", ".jpeg"}:
            return "image/jpeg"
        if suffix == ".webp":
            return "image/webp"
        return "image/png"

    @staticmethod
    def _compact_text(value: object) -> str:
        return compact_text(value)

    @staticmethod
    def build_context_for_image(
        image_path: str | Path,
        *,
        markdown: str,
        extract_dir: str | Path,
        content_list_path: str | None = None,
        layout_path: str | None = None,
    ) -> dict[str, Any] | None:
        """为单张图片构建完整的 pipeline figure_input + asset_metadata。

        与 ingest() 共享全部核心逻辑（content_list 匹配、section_hierarchy、
        citation_context、layout_context、LocalImageProfiler），
        但不写 DB，不 copy 文件，不建 Figure/Panel 记录。

        Args:
            image_path: MinerU extracted/images/ 下的图片文件路径。
            markdown: MinerU 生成的完整 markdown 文本。
            extract_dir: MinerU extract 目录（含 images/ 子目录）。
            content_list_path: content_list_v2.json 或 content_list.json 路径。
            layout_path: layout.json 路径。

        Returns:
            dict: 与 content_pipeline EvidencePacketBuilder
                  兼容的 figure_input dict，含 asset_metadata 子字典。
                  匹配不到返回 None。
        """
        root = Path(extract_dir)
        if not root.is_dir():
            return None

        img = Path(image_path)
        image_name = img.name

        content_list = MinerUAssetBuilder._load_content_list(content_list_path)
        layout_pages = MinerUAssetBuilder._load_layout_pages(layout_path)
        md_refs = MinerUAssetBuilder._image_refs(markdown)

        cl_image_blocks = [
            item for item in content_list
            if isinstance(item, dict) and item.get("type") in ("image", "chart")
        ]

        # Phase 1: 从 content_list 按文件名匹配
        hit_item = None
        cl_match: dict[str, Any] = {}
        alt = ""
        section = ""
        md_label = ""
        nearby_text = ""
        page_idx: int | None = None

        for item in cl_image_blocks:
            item_path = str(item.get("img_path") or "")
            if image_name in item_path:
                hit_item = item
                page_idx = item.get("page_idx")
                cl_match = MinerUAssetBuilder._build_cl_match(item, content_list)
                captions: list[str] = []
                for key in ("image_caption", "chart_caption"):
                    raw = item.get(key)
                    if isinstance(raw, list):
                        captions.extend(
                            MinerUAssetBuilder._compact_text(v)
                            for v in raw if MinerUAssetBuilder._compact_text(v)
                        )
                alt = "; ".join(dict.fromkeys(captions)) if captions else ""
                break

        # Phase 2: 从 markdown 引用匹配（回退或补充）
        md_found_at: int | None = None
        for ref in md_refs:
            ref_name = Path(ref["path"]).name
            if ref_name == image_name:
                alt = alt or str(ref.get("alt") or "")
                section = str(ref.get("section") or "")
                md_label = str(ref.get("label") or "")
                nearby_text = str(ref.get("nearby_text") or "")
                md_found_at = ref.get("line_index")
                if not hit_item and cl_image_blocks:
                    fallback_match = MinerUAssetBuilder._content_item_for_image(content_list, ref)
                    if fallback_match:
                        hit_item = {"type": fallback_match.get("mineru_type", "")}
                        cl_match = fallback_match
                        page_idx = cl_match.get("page_idx")
                break

        if not hit_item and not md_found_at:
            return None

        mineru_type = str(cl_match.get("mineru_type") or (
            hit_item.get("type") if hit_item else "markdown_image"
        ))
        bbox = cl_match.get("bbox")
        layout_page = layout_pages.get(page_idx) if isinstance(page_idx, int) else None

        full_caption = MinerUAssetBuilder._merged_caption_from_parts(
            alt, cl_match.get("content_list_caption", "")
        )

        width, height = MinerUAssetBuilder._image_dimensions(img)
        file_size = MinerUAssetBuilder._file_size(img)

        nearby_for_profile = " ".join(
            part for part in [nearby_text, str(cl_match.get("nearby_content") or "")] if part
        )
        panel_id = MinerUAssetBuilder._panel_id(alt) or MinerUAssetBuilder._panel_id(full_caption)
        figure_number = MinerUAssetBuilder._figure_number(md_label) or MinerUAssetBuilder._figure_number(alt)
        caption_for_parent = MinerUAssetBuilder._label(full_caption) or f"Fig. {figure_number or ''}".strip()
        caption_for_parent = caption_for_parent or f"Figure {Path(img).stem}"
        parent_figure_id = LocalImageProfiler.parent_figure_id(full_caption or nearby_text, caption_for_parent)
        markdown_context = MinerUAssetBuilder._markdown_context_for_image(
            markdown=markdown,
            image_name=image_name,
            ref_line=md_found_at,
        )
        panel_context = MinerUAssetBuilder._build_panel_context_record(
            panel_id=panel_id,
            parent_figure_id=parent_figure_id,
            full_parent_caption=full_caption,
            nearby_candidates=[
                nearby_text,
                str(cl_match.get("nearby_content") or ""),
                str(cl_match.get("mineru_nearby_text") or ""),
                " ".join(str(item) for item in cl_match.get("citation_context", []) if item)
                if isinstance(cl_match.get("citation_context"), list)
                else "",
            ],
            markdown_candidates=markdown_context,
        )
        caption = (
            panel_context.get("panel_caption")
            or panel_context.get("panel_nearby_text")
            or MinerUAssetBuilder._compact_text(nearby_text)
        )
        local_profile = LocalImageProfiler.profile(
            caption=caption,
            nearby_text=nearby_for_profile,
            width=width,
            height=height,
            file_size=file_size,
            mineru_type=mineru_type,
            panel_id=panel_id,
            bbox=bbox if isinstance(bbox, list) else None,
            layout_page=layout_page,
        )

        label = (
            MinerUAssetBuilder._label(caption) or
            MinerUAssetBuilder._label(nearby_text) or
            md_label or
            "Figure"
        )
        parent_figure_id = LocalImageProfiler.parent_figure_id(caption_for_parent or full_caption or nearby_text, label)
        section_hierarchy = cl_match.get("section_hierarchy", [])
        citation_context = cl_match.get("citation_context", [])
        layout_context_text = MinerUAssetBuilder._layout_context_str(layout_page, bbox)

        context_parts: list[str] = []
        context_parts.append(f"Image filename: {image_name}")
        if section:
            context_parts.append(f"Section: {section}")
        if caption:
            context_parts.append(f"Panel-local caption: {caption[:800]}")
        if full_caption and full_caption != caption:
            context_parts.append(f"Full figure caption: {full_caption[:1200]}")
        if layout_context_text:
            context_parts.append(f"Layout: {layout_context_text}")
        if nearby_text:
            context_parts.append(f"Near markdown: {nearby_text[:800]}")
        if nearby_for_profile:
            context_parts.append(f"Nearby content: {nearby_for_profile}")
        if citation_context:
            context_parts.append(f"Citation: {citation_context[0][:300]}")

        metadata: dict[str, Any] = {
            "source": "mineru_content_list" if hit_item else "mineru_markdown",
            "mineru_img_path": str(hit_item.get("img_path", "")) if hit_item else "",
            "mineru_alt_text": alt,
            "mineru_nearby_text": nearby_text,
            "mineru_section": section,
            "content_list_caption": cl_match.get("content_list_caption", ""),
            "nearby_content": cl_match.get("nearby_content", ""),
            "section_hierarchy": section_hierarchy,
            "citation_context": citation_context,
            "layout_context_text": layout_context_text,
            "page_idx": page_idx,
            "bbox": bbox,
            "layout_page": layout_page or {},
            "parent_figure_id": parent_figure_id,
            "figure_group_key": parent_figure_id,
            "evidence_shape_hint": local_profile.evidence_shape,
            "recommended_extractor_hint": local_profile.recommended_extractor,
            "figure_role_hint": local_profile.figure_role,
            "visual_role": "chart_candidate" if mineru_type == "chart" else "image_candidate",
            "data_extraction_possible": local_profile.extraction_readiness != "skip",
            "image_width": width,
            "image_height": height,
            "file_size": file_size,
            "asset_scope": local_profile.asset_scope,
            "extraction_readiness": local_profile.extraction_readiness,
            "skip_reason": local_profile.skip_reason,
            "mineru_type": mineru_type,
            "full_caption": full_caption,
            "panel_caption": caption,
            "panel_context": panel_context,
            "panel_id": panel_id or "",
            "local_profile_confidence": local_profile.confidence,
            "local_profile_uncertainty": local_profile.uncertainty_reason,
        }

        return {
            "figure_image_ref": str(img.resolve()),
            "caption_text": caption or f"Image in {image_name}",
            "paper_context": " \n".join(part for part in context_parts if part),
            "figure_id": parent_figure_id,
            "source_pdf": "cli-local",
            "page_number": (int(page_idx) + 1) if isinstance(page_idx, int) else None,
            "asset_metadata": metadata,
        }

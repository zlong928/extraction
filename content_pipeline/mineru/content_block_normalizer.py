from __future__ import annotations

import hashlib
import json
import re
from html import escape
from pathlib import Path
from typing import Any

from content_pipeline.contracts.blocks import ContentBlock
from content_pipeline.contracts.errors import ExtractionInputError
from content_pipeline.mineru.image_path_resolver import ImagePathResolver


_BLOCK_TYPE_MAP = {
    "title": "heading",
    "doc_title": "heading",
    "paragraph_title": "heading",
    "paragraph": "text",
    "text": "text",
    "list": "list",
    "image": "image",
    "chart": "chart",
    "table": "table",
    "equation_interline": "formula",
    "equation": "formula",
    "interline_equation": "formula",
    "formula": "formula",
    "page_aside_text": "aside",
    "aside_text": "aside",
    "page_footnote": "page_footnote",
    "ref_text": "reference",
    "reference": "reference",
    "page_header": "page_header",
    "page_footer": "page_footer",
    "page_number": "page_number",
}

_CAPTION_KEYS = (
    "image_caption",
    "chart_caption",
    "table_caption",
    "caption",
)
_FOOTNOTE_KEYS = (
    "image_footnote",
    "chart_footnote",
    "table_footnote",
)
_CAPTION_AND_FOOTNOTE_KEYS = (*_CAPTION_KEYS, *_FOOTNOTE_KEYS)

_LATEX_KEYS = ("latex", "aslatex", "formula_latex", "math_content")
_MATHML_KEYS = ("mathml", "formula_mathml")


class ContentBlockNormalizer:
    """Normalize MinerU content_list_v2 blocks without flattening away structure."""

    def __init__(self, image_root: str | Path | None = None) -> None:
        self.image_resolver = ImagePathResolver(image_root)
        self.last_report: dict[str, Any] = {}

    def load(self, content_list_path: str | Path) -> list[ContentBlock]:
        path = Path(content_list_path)
        if not path.is_file():
            raise ExtractionInputError(f"content_list_v2.json not found: {path}")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ExtractionInputError(f"content_list_v2.json is invalid JSON: {path}") from exc
        if not isinstance(data, list) or not all(isinstance(page, list) for page in data):
            raise ExtractionInputError("content pipeline requires MinerU content_list_v2.json as list[list[block]]")
        return self.normalize_pages(data)

    def normalize_pages(self, pages: list[list[dict[str, Any]]]) -> list[ContentBlock]:
        blocks: list[ContentBlock] = []
        global_order = 0
        self.last_report = {"normalized_count": 0, "type_counts": {}}
        for page_idx, page_items in enumerate(pages):
            if not isinstance(page_items, list):
                continue
            for reading_order, raw in enumerate(page_items):
                if not isinstance(raw, dict):
                    continue
                block = self.normalize_block(raw, page_idx=page_idx, reading_order=reading_order, global_order=global_order)
                blocks.append(block)
                self.last_report["normalized_count"] += 1
                counts = self.last_report["type_counts"]
                counts[block.normalized_type] = counts.get(block.normalized_type, 0) + 1
                global_order += 1
        return blocks

    def normalize_block(self, raw: dict[str, Any], *, page_idx: int, reading_order: int, global_order: int) -> ContentBlock:
        raw_type = str(raw.get("type") or "unknown")
        normalized_type = _BLOCK_TYPE_MAP.get(raw_type.lower(), raw_type.lower() or "unknown")
        content = raw.get("content")
        structured_content = content if isinstance(content, dict) else ({"content": content} if content is not None else {})
        list_type = structured_content.get("list_type") if isinstance(structured_content, dict) else None
        if normalized_type == "list" and str(list_type or "").lower() == "reference_list":
            normalized_type = "reference"
        text = _compact_text(_extract_text(content if content is not None else raw.get("text")))
        caption_fields = _extract_caption_fields(raw, _CAPTION_AND_FOOTNOTE_KEYS)
        caption_body_fields = _extract_caption_fields(raw, _CAPTION_KEYS)
        footnote_fields = _extract_caption_fields(raw, _FOOTNOTE_KEYS)
        caption_structured = _extract_caption_structured(raw, _CAPTION_KEYS)
        footnote_structured = _extract_caption_structured(raw, _FOOTNOTE_KEYS)
        caption_rich_text = _caption_rich_text(caption_structured)
        if not text and caption_body_fields:
            text = _compact_text(" ".join(part for values in caption_body_fields.values() for part in values))
        bbox = _coerce_bbox(raw.get("bbox"))
        text_level = _coerce_int(raw.get("text_level"))
        if text_level is None and isinstance(content, dict):
            text_level = _coerce_int(content.get("level"))
        table_html = _extract_table_html(raw)
        formula_latex = _find_first_key(structured_content, _LATEX_KEYS)
        formula_mathml = _find_first_key(structured_content, _MATHML_KEYS)
        reference_markers = _extract_reference_markers(text) if normalized_type == "reference" else []
        resolved = self.image_resolver.resolve(raw) if normalized_type in {"image", "chart"} else None
        image_path = resolved.resolved_path or resolved.normalized_value if resolved else None
        metadata: dict[str, Any] = {
            "mineru_block_type": raw_type,
            "mineru_reading_order": reading_order,
            "mineru_global_order": global_order,
        }
        if list_type:
            metadata["list_type"] = list_type
        if caption_structured:
            metadata["caption_structured"] = caption_structured
        if footnote_structured:
            metadata["caption_footnote_structured"] = footnote_structured
        if caption_rich_text:
            metadata["caption_rich_text"] = caption_rich_text
        if resolved and resolved.warnings:
            metadata["image_path_warnings"] = resolved.warnings
        block_id = f"p{page_idx}-b{reading_order}-g{global_order}"
        return ContentBlock(
            block_id=block_id,
            page_idx=page_idx,
            reading_order=reading_order,
            global_order=global_order,
            raw_type=raw_type,
            normalized_type=normalized_type,
            text=text,
            text_hash=_hash_text(text),
            structured_content=structured_content,
            bbox=bbox,
            text_level=text_level,
            image_path=image_path,
            table_html=table_html,
            formula_latex=formula_latex,
            formula_mathml=formula_mathml,
            reference_markers=reference_markers,
            caption_fields=caption_fields,
            caption_body_fields=caption_body_fields,
            footnote_fields=footnote_fields,
            metadata=metadata,
            raw_block=dict(raw),
        )


def _extract_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        parts: list[str] = []
        for key, val in value.items():
            if key in {"level", "bbox", "img_path", "image_path", "path", "image_source", "html", "table_html", "type", "list_type"}:
                continue
            extracted = _extract_text(val)
            if extracted:
                parts.append(extracted)
        return " ".join(parts)
    if isinstance(value, list):
        parts = []
        for item in value:
            extracted = _extract_text(item)
            if extracted:
                parts.append(extracted)
        return " ".join(parts)
    return str(value)


def _extract_caption_fields(raw: dict[str, Any], keys: tuple[str, ...]) -> dict[str, list[str]]:
    content = raw.get("content")
    result: dict[str, list[str]] = {}
    sources = [raw]
    if isinstance(content, dict):
        sources.append(content)
    for source in sources:
        for key in keys:
            if key not in source:
                continue
            text = _compact_text(_extract_text(source.get(key)))
            if text:
                result.setdefault(key, [])
                if text not in result[key]:
                    result[key].append(text)
    return result


def _extract_caption_structured(raw: dict[str, Any], keys: tuple[str, ...]) -> dict[str, list[dict[str, Any]]]:
    content = raw.get("content")
    result: dict[str, list[dict[str, Any]]] = {}
    sources = [raw]
    if isinstance(content, dict):
        sources.append(content)
    for source in sources:
        for key in keys:
            if key not in source:
                continue
            items = _caption_items(source.get(key))
            if not items:
                continue
            bucket = result.setdefault(key, [])
            for item in items:
                if item not in bucket:
                    bucket.append(item)
    return result


def _caption_items(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if isinstance(value, str):
        text = _compact_text(value)
        return [{"type": "text", "content": text}] if text else []
    if isinstance(value, dict):
        item_type = str(value.get("type") or "text")
        if "content" in value:
            content = _compact_text(_extract_text(value.get("content")))
            return [{"type": item_type, "content": content}] if content else []
        if "text" in value:
            content = _compact_text(_extract_text(value.get("text")))
            return [{"type": item_type, "content": content}] if content else []
        nested = []
        for child in value.values():
            nested.extend(_caption_items(child))
        return nested
    if isinstance(value, list):
        items: list[dict[str, Any]] = []
        for child in value:
            items.extend(_caption_items(child))
        return items
    text = _compact_text(str(value))
    return [{"type": "text", "content": text}] if text else []


def _caption_rich_text(caption_structured: dict[str, list[dict[str, Any]]]) -> str:
    """Render typed caption tokens for LLM inputs without allowing malformed tags."""
    parts: list[str] = []
    for items in caption_structured.values():
        for item in items:
            item_type = _safe_caption_tag(item.get("type"))
            content = _compact_text(str(item.get("content") or ""))
            if content:
                parts.append(f"<{item_type}>{escape(content, quote=False)}</{item_type}>")
    return " ".join(parts)


def _safe_caption_tag(value: Any) -> str:
    tag = re.sub(r"[^a-zA-Z0-9_:-]+", "_", str(value or "text").strip()).strip("_:")
    return tag or "text"


def _extract_table_html(raw: dict[str, Any]) -> str | None:
    content = raw.get("content")
    for value in (raw.get("table_html"), raw.get("html")):
        if isinstance(value, str) and value.strip():
            return value.strip()
    if isinstance(content, dict):
        for key in ("table_html", "html"):
            value = content.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    if isinstance(content, str) and "<table" in content.lower():
        return content.strip()
    return None


def _find_first_key(value: Any, keys: tuple[str, ...]) -> str | None:
    if isinstance(value, dict):
        for key in keys:
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
        for candidate in value.values():
            found = _find_first_key(candidate, keys)
            if found:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _find_first_key(item, keys)
            if found:
                return found
    return None


def _extract_reference_markers(text: str) -> list[str]:
    return list(dict.fromkeys(re.findall(r"\[\s*\d+(?:\s*[-,]\s*\d+)*\s*\]", text)))


def _coerce_bbox(value: Any) -> list[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        return [float(value[0]), float(value[1]), float(value[2]), float(value[3])]
    except (TypeError, ValueError):
        return None


def _coerce_int(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return None


def _compact_text(text: str) -> str:
    return " ".join(str(text or "").replace("\n", " ").split())


def _hash_text(text: str) -> str:
    return hashlib.sha1(_compact_text(text).lower().encode("utf-8")).hexdigest() if text else ""

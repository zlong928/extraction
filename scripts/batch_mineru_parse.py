#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
from pathlib import Path
from typing import Iterable

import sys

# Ensure local project imports work when run as a standalone script.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import DATA_DIR
from app.core.constants import MARKDOWN_IMAGE_RE as MD_IMAGE_RE
from app.services.mineru_parser import MinerUParserError, MinerUParserService


def _safe_stem(name: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", name.strip()) or "unnamed"
    return stem.strip("._")[:140] or "unnamed"


def _iter_pdfs(input_dir: Path) -> Iterable[Path]:
    seen: set[Path] = set()
    for path in sorted(input_dir.rglob("*.pdf")):
        resolved = path.resolve()
        if path.is_file() and resolved not in seen:
            seen.add(resolved)
            yield path
    for path in sorted(input_dir.rglob("*.PDF")):
        resolved = path.resolve()
        if path.is_file() and resolved not in seen:
            seen.add(resolved)
            yield path


def _copy_or_skip(source: Path, target: Path, *, overwrite: bool) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and not overwrite:
        return
    if target.exists():
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
    shutil.copy2(source, target)


def _compact_text(value: object) -> str:
    text = str(value or "").replace("\n", " ").strip()
    text = re.sub(r"\s+", " ", text)
    return text


_MARKDOWN_IMAGE_RE_WITH_GROUP = MD_IMAGE_RE


def _extract_markdown_refs(markdown: str) -> list[dict[str, object]]:
    lines = markdown.splitlines()
    refs: list[dict[str, object]] = []
    current_section = ""
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("#"):
            current_section = stripped.lstrip("#").strip()
        for match in _MARKDOWN_IMAGE_RE_WITH_GROUP.finditer(line):
            alt = (match.group(1) or "").strip()
            raw = (match.group(2) or "").strip().lstrip("./")
            nearby = " ".join(
                lines[i].strip()
                for i in range(max(0, index - 3), min(len(lines), index + 4))
                if i != index and lines[i].strip()
            )
            refs.append(
                {
                    "alt": alt,
                    "path": raw,
                    "filename": Path(raw).name,
                    "section": current_section,
                    "nearby": nearby[:1000],
                    "line_index": index,
                }
            )
    return refs


def _load_content_list(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if isinstance(data, list) and all(isinstance(page, list) for page in data):
        return _flatten_content_list_v2(data)
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    return []


def _extract_nested_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        parts = [_extract_nested_text(v) for v in value.values()]
        return " ".join(part for part in parts if part)
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, dict):
                inner = item.get("content") or item.get("text") or ""
                parts.append(_extract_nested_text(inner))
            else:
                parts.append(_extract_nested_text(item))
        return " ".join(part for part in parts if part)
    return str(value)


def _image_path_from_block(item: dict[str, object]) -> str:
    content = item.get("content")
    candidates: list[object] = [
        item.get("img_path"),
        item.get("image_path"),
        item.get("path"),
    ]
    if isinstance(content, dict):
        candidates.extend([
            content.get("img_path"),
            content.get("image_path"),
            content.get("path"),
        ])
        image_source = content.get("image_source")
        if isinstance(image_source, dict):
            candidates.append(image_source.get("path"))
    for value in candidates:
        if isinstance(value, str) and value.strip():
            return value.strip().lstrip("./")
    return ""


def _flatten_content_list_v2(pages: list[object]) -> list[dict[str, object]]:
    flat: list[dict[str, object]] = []
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
                text = _extract_nested_text(content)
                if text and not block.get("text"):
                    block["text"] = text
                for cap_key in ("image_caption", "chart_caption", "table_caption", "caption"):
                    cap = content.get(cap_key)
                    cap_text = _extract_nested_text(cap)
                    if cap_text:
                        existing = block.get(cap_key)
                        values = existing if isinstance(existing, list) else []
                        block[cap_key] = [*values, cap_text]
                level = content.get("level")
                if level is not None and block.get("text_level") is None:
                    block["text_level"] = level
            if not block.get("img_path"):
                block["img_path"] = _image_path_from_block(block)
            if block.get("img_path") is None:
                block["img_path"] = ""
            flat.append(block)
    return flat


def _infer_section(markdown: str, line_index: int) -> str:
    if line_index < 0:
        return ""
    lines = markdown.splitlines()
    for idx in range(min(line_index, len(lines) - 1), -1, -1):
        text = lines[idx].strip()
        if text.startswith("#"):
            return text.lstrip("#").strip()
    return ""


def _content_item_for_image(
    content_list: list[dict[str, object]],
    image_item: dict[str, object],
    *,
    nearby_window: int = 1,
) -> tuple[str, int | None, str]:
    target_name = str(image_item.get("filename") or "").lower()
    target_key = str(image_item.get("path") or "").lower()

    hit_index = -1
    for idx, item in enumerate(content_list):
        if not isinstance(item, dict):
            continue
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
        return "", None, ""

    item = content_list[hit_index]

    captions: list[str] = []
    for key in ("image_caption", "chart_caption"):
        raw = item.get(key)
        if isinstance(raw, list):
            for value in raw:
                txt = _compact_text(value)
                if txt:
                    captions.append(txt)

    inline_caption = _compact_text(item.get("text") or item.get("content"))
    if inline_caption:
        captions.append(inline_caption)

    for key in ("image_footnote", "chart_footnote"):
        raw = item.get(key)
        if isinstance(raw, list):
            for value in raw:
                txt = _compact_text(value)
                if txt:
                    captions.append(txt)

    caption_text = "; ".join(dict.fromkeys(captions))

    page = None
    raw_page = item.get("page_idx")
    if isinstance(raw_page, int):
        page = raw_page + 1

    nearby_parts: list[str] = []
    for sibling in content_list[max(0, hit_index - nearby_window) : hit_index + nearby_window + 1]:
        if not isinstance(sibling, dict):
            continue
        sibling_type = str(sibling.get("type") or "")
        sibling_content = _compact_text(sibling.get("text") or sibling.get("content"))
        if sibling_content:
            nearby_parts.append(f"{sibling_type}: {sibling_content}")

    return caption_text, page, "; ".join(nearby_parts)


def _safe_jsonl_path(root: Path, name: str) -> Path:
    return root / name


def _build_pipeline_records(
    *,
    markdown_text: str,
    markdown_refs: list[dict[str, object]],
    content_list: list[dict[str, object]],
    structured_root: Path,
    source_pdf_name: str,
    overwrite: bool,
    extracted_images_dir: Path,
) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    seen_by_name: set[str] = set()
    for ref_index, image_ref in enumerate(markdown_refs, 1):
        filename = str(image_ref.get("filename") or "")
        if not filename:
            continue
        if filename in seen_by_name:
            # keep one image once if duplicated refs appear in markdown.
            continue
        seen_by_name.add(filename)

        content_caption, page_no, nearby_content = _content_item_for_image(content_list, image_ref)
        alt = _compact_text(image_ref.get("alt"))
        caption = alt
        if content_caption:
            merged = " ".join(part for part in (caption, content_caption) if part)
            caption = merged.strip()

        if not caption:
            caption = f"Image in {source_pdf_name}"

        section = _compact_text(
            image_ref.get("section") or _infer_section(markdown_text, int(image_ref.get("line_index", 0) or 0))
        )

        paper_context_parts = [
            f"Paper title: {source_pdf_name}",
            f"Source PDF: {source_pdf_name}",
            f"Image filename: {filename}",
        ]
        if section:
            paper_context_parts.append(f"Section: {section}")

        nearby = _compact_text(image_ref.get("nearby"))
        if nearby:
            paper_context_parts.append(f"Near markdown: {nearby}")
        if content_caption:
            paper_context_parts.append(f"Content list caption: {content_caption}")
        if nearby_content:
            paper_context_parts.append(f"Nearby content: {nearby_content}")

        # figure id fallback as Figure N if no explicit label found.
        match = re.search(r"(?i)\b(fig(?:ure)?\.?\s*\d+[a-z]?)\b", caption)
        figure_id = match.group(1) if match else f"Figure {ref_index}"

        source_path = structured_root / "images" / filename
        if not source_path.exists():
            candidate = extracted_images_dir / filename
            if candidate.exists():
                _copy_or_skip(candidate, source_path, overwrite=overwrite)

        if not source_path.exists():
            continue

        records.append(
            {
                "figure_image_ref": str(source_path),
                "caption_text": caption,
                "paper_context": " \n".join(part for part in paper_context_parts if part),
                "figure_id": figure_id,
                "source_pdf": str(structured_root / "source.pdf"),
                "page_number": page_no,
                "pipeline_ready": True,
            }
        )

    return records


def run_batch(input_dir: Path, *, output_root: Path, overwrite: bool = False) -> list[dict[str, object]]:
    parser = MinerUParserService()
    service_root = output_root / "pipeline_batch"
    reports: list[dict[str, object]] = []
    summary_items: list[dict[str, object]] = []
    pdf_files = list(_iter_pdfs(input_dir))

    if not pdf_files:
        return []

    for idx, pdf_path in enumerate(pdf_files, 1):
        slug = f"{idx:03d}-{_safe_stem(pdf_path.stem)}"
        run_root = service_root / slug
        run_root.mkdir(parents=True, exist_ok=True)

        try:
            result = parser.parse_pdf_file(
                pdf_path,
                data_id=f"batch-{slug}",
                output_root=run_root,
            )

            artifact_dir = Path(result.artifact_dir) if result.artifact_dir else run_root
            extract_dir = Path(result.extract_dir) if result.extract_dir else artifact_dir / "extracted"
            markdown_file = extract_dir / result.markdown_file

            structured_root = run_root / "structured"
            structured_root.mkdir(parents=True, exist_ok=True)

            _copy_or_skip(pdf_path, structured_root / "source.pdf", overwrite=overwrite)
            if markdown_file.exists():
                _copy_or_skip(markdown_file, structured_root / "full.md", overwrite=overwrite)

            content_list_path = result.content_list_path
            if content_list_path:
                content_list_file = Path(content_list_path)
                if content_list_file.exists():
                    _copy_or_skip(content_list_file, structured_root / content_list_file.name, overwrite=overwrite)

            if result.layout_path:
                layout_file = Path(result.layout_path)
                if layout_file.exists():
                    _copy_or_skip(layout_file, structured_root / layout_file.name, overwrite=overwrite)

            extracted_images = extract_dir / "images"
            if extracted_images.is_dir():
                target_images = structured_root / "images"
                if target_images.exists() and overwrite:
                    shutil.rmtree(target_images)
                target_images.mkdir(parents=True, exist_ok=True)
                for img in extracted_images.iterdir():
                    if img.is_file():
                        _copy_or_skip(img, target_images / img.name, overwrite=overwrite)

            markdown_text = markdown_file.read_text(encoding="utf-8", errors="replace") if markdown_file.exists() else ""
            markdown_refs = _extract_markdown_refs(markdown_text)
            content_list = _load_content_list(
                structured_root / "content_list.json"
                if (structured_root / "content_list.json").exists()
                else (Path(content_list_path) if content_list_path else (extract_dir / "content_list.json"))
            )

            pipeline_records = _build_pipeline_records(
                markdown_text=markdown_text,
                markdown_refs=markdown_refs,
                content_list=content_list,
                structured_root=structured_root,
                source_pdf_name=pdf_path.name,
                overwrite=overwrite,
                extracted_images_dir=extracted_images,
            )

            pipeline_input_path = _safe_jsonl_path(structured_root, "pipeline_inputs.jsonl")
            pipeline_input_path.write_text(
                "\n".join(json.dumps(item, ensure_ascii=False) for item in pipeline_records),
                encoding="utf-8",
            )

            structured_manifest = {
                "source_pdf": str(pdf_path),
                "batch_id": result.batch_id,
                "source_file": "source.pdf",
                "markdown": "full.md" if markdown_file.exists() else None,
                "paper_context_file": "pipeline_inputs.jsonl",
                "image_dir": "images",
                "pipeline_ready": bool(pipeline_records),
                "structured_root": str(structured_root),
                "artifact_dir": str(artifact_dir),
                "extracted_files_count": len(result.extracted_files),
            }
            manifest_path = run_root / "structured" / "manifest.json"
            manifest_path.write_text(
                json.dumps(structured_manifest, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            raw_manifest = {
                "source_pdf": str(pdf_path),
                "batch_id": result.batch_id,
                "file_name": result.file_name,
                "full_zip_url": result.full_zip_url,
                "artifact_dir": str(artifact_dir),
                "result_zip": str(Path(result.zip_path)) if result.zip_path else None,
                "extract_dir": str(extract_dir),
                "content_list_path": str(result.content_list_path) if result.content_list_path else None,
                "layout_path": str(result.layout_path) if result.layout_path else None,
                "extracted_files_count": len(result.extracted_files),
                "markdown_file": result.markdown_file,
            }
            (run_root / "mineru_raw_manifest.json").write_text(
                json.dumps(raw_manifest, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            # Per-run summary for batch indexing.
            summary_items.append(
                {
                    "batch_slug": slug,
                    "source_pdf": str(pdf_path),
                    "batch_id": result.batch_id,
                    "figure_count": len(pipeline_records),
                    "structured_root": str(structured_root),
                    "pipeline_inputs": str(pipeline_input_path),
                    "source_pdf_copy": str(structured_root / "source.pdf"),
                }
            )

            reports.append({"status": "success", "source_pdf": str(pdf_path), "batch_id": result.batch_id})
        except (MinerUParserError, Exception) as exc:
            reports.append({"status": "failed", "source_pdf": str(pdf_path), "error": str(exc)})

    summary_path = output_root / "mineru_batch_summary.jsonl"
    summary_path.write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in reports),
        encoding="utf-8",
    )
    pipeline_summary_path = output_root / "pipeline_batch_summary.jsonl"
    pipeline_summary_path.write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in summary_items),
        encoding="utf-8",
    )
    return reports


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Batch parse all PDFs in a folder with MinerU and output pipeline-ready assets."
    )
    parser.add_argument("input_dir", help="Directory containing source PDF files")
    parser.add_argument("--output-dir", default=str(DATA_DIR / "results"), help="Output root under data")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing outputs")
    return parser.parse_args(argv)


def main() -> int:
    args = parse_args()
    input_dir = Path(args.input_dir).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve()

    if not input_dir.is_dir():
        print(f"Input directory not found: {input_dir}")
        return 2

    if not os.getenv("MINERU_API_KEY"):
        print("MINERU_API_KEY is not set")
        return 3

    reports = run_batch(input_dir, output_root=output_dir, overwrite=args.overwrite)
    if not reports:
        print(f"No PDF files found under {input_dir}")
        return 0

    success_count = sum(item["status"] == "success" for item in reports)
    fail_count = len(reports) - success_count
    print(f"Done. success={success_count}, failed={fail_count}")

    pipeline_ready = sum(1 for item in reports if item.get("status") == "success")
    print(f"Pipeline input bundles ready: {pipeline_ready}")
    print(f"Output root: {output_dir / 'pipeline_batch'}")

    if fail_count:
        print("Failed files:")
        for item in reports:
            if item["status"] != "success":
                print(f"- {item['source_pdf']}: {item.get('error')}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

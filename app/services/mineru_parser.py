from __future__ import annotations

import io
import json
import threading
import time
import zipfile
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from app.core.constants import MARKDOWN_IMAGE_RE, compact_text
from app.config import (
    MINERU_API_BASE_URL,
    MINERU_API_KEY,
    MINERU_LANGUAGE,
    MINERU_MODEL_VERSION,
    MINERU_POLL_INTERVAL_SECONDS,
    MINERU_RESULT_RATE_LIMIT_PER_MINUTE,
    MINERU_SUBMIT_RATE_LIMIT_PER_MINUTE,
    MINERU_TIMEOUT_SECONDS,
)
from app.services.document_parser import ParsedDocument, ParsedElement, ParsedPage
from content_pipeline.mineru.image_path_resolver import image_path_from_block

_MAX_ZIP_UNCOMPRESSED_BYTES = 500 * 1024 * 1024


class MinerUParserError(RuntimeError):
    pass


class MinerUParserUnavailable(MinerUParserError):
    pass


class MinuteRateLimiter:
    def __init__(self, *, limit: int, window_seconds: float = 60.0) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self._events: deque[float] = deque()
        self._lock = threading.Lock()

    def acquire(self, amount: int = 1) -> None:
        if self.limit <= 0:
            return
        if amount > self.limit:
            raise MinerUParserError(f"Rate limit amount {amount} exceeds per-minute limit {self.limit}.")
        while True:
            with self._lock:
                now = time.monotonic()
                self._discard_expired(now)
                if len(self._events) + amount <= self.limit:
                    self._events.extend([now] * amount)
                    return
                sleep_for = max(0.0, self.window_seconds - (now - self._events[0]))
            time.sleep(sleep_for)

    def _discard_expired(self, now: float) -> None:
        while self._events and now - self._events[0] >= self.window_seconds:
            self._events.popleft()


_submit_rate_limiter = MinuteRateLimiter(limit=MINERU_SUBMIT_RATE_LIMIT_PER_MINUTE)
_result_rate_limiter = MinuteRateLimiter(limit=MINERU_RESULT_RATE_LIMIT_PER_MINUTE)


@dataclass(slots=True)
class MinerUParseResult:
    parsed_document: ParsedDocument
    batch_id: str
    file_name: str
    full_zip_url: str
    markdown_file: str
    original_markdown: str = ""
    artifact_dir: str | None = None
    zip_path: str | None = None
    extract_dir: str | None = None
    content_list_path: str | None = None
    layout_path: str | None = None
    extracted_files: list[str] = field(default_factory=list)


# ── v1 / v2 content list helpers ──────────────────────────────────

_BLOCK_TYPE_MAP: dict[str, str] = {
    "text": "paragraph",
    "paragraph": "paragraph",
    "title": "heading",
    "doc_title": "heading",
    "image": "image",
    "chart": "image",
    "table": "table",
    "equation": "formula",
    "interline_equation": "formula",
    "inline_formula": "formula",
    "list": "list",
    "page_header": "page_header",
    "page_footer": "page_footer",
    "page_footnote": "page_footnote",
    "page_number": "page_number",
    "header": "page_header",
    "footer": "page_footer",
    "footnote": "page_footnote",
    "image_caption": "caption",
    "chart_caption": "caption",
    "table_caption": "caption",
    "ref_text": "reference",
    "aside_text": "aside",
    "ocr_text": "ocr_text",
    "paragraph_title": "heading",
    "doc_title": "heading",
}

_MARKDOWN_IMAGE_RE = MARKDOWN_IMAGE_RE


def _normalize_element_type(raw_type: str) -> str:
    return _BLOCK_TYPE_MAP.get(raw_type, raw_type.lower())


def _extract_text_v2(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        parts: list[str] = []
        for val in content.values():
            extracted = _extract_text_v2(val)
            if extracted:
                parts.append(extracted)
        return " ".join(parts)
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                inner = item.get("content") or item.get("text") or ""
                if isinstance(inner, str):
                    parts.append(inner)
                else:
                    extracted = _extract_text_v2(item)
                    if extracted:
                        parts.append(extracted)
            elif isinstance(item, str):
                parts.append(item)
        return " ".join(parts)
    return str(content)


def _flatten_content_v2(item: dict[str, Any]) -> str:
    content = item.get("content")
    return _extract_text_v2(content)


def _caption_v2(item: dict[str, Any]) -> str:
    content = item.get("content")
    if isinstance(content, dict):
        for key in ("image_caption", "chart_caption", "table_caption", "caption"):
            cap = content.get(key)
            if cap:
                return _extract_text_v2(cap)
    return ""



def _load_markdown_image_map(archive: zipfile.ZipFile) -> dict[str, dict[str, str]]:
    """Build {filename: {"alt": ..., "path": ...}} from markdown in zip.
    Strips Markdown title attributes (e.g. ![](path \"title\")) from the path.
    """
    md_names = [
        n for n in archive.namelist()
        if n.lower().endswith(".md") and not n.endswith("/")
    ]
    if not md_names:
        return {}
    md_name = _preferred_md(md_names)
    md_text = archive.read(md_name).decode("utf-8", errors="replace")
    result: dict[str, dict[str, str]] = {}
    for match in _MARKDOWN_IMAGE_RE.finditer(md_text):
        alt = match.group(1).strip()
        raw = match.group(2).strip().lstrip("./")
        path_clean = raw.split('"')[0].split("'")[0].strip()
        filename = Path(path_clean).name
        result[filename] = {"alt": alt, "path": path_clean}
    return result



def _v2_to_elements(
    page_items: list[dict[str, Any]],
    page_idx: int,
    image_map: dict[str, dict[str, str]],
    image_counter: list[int],
) -> list[ParsedElement]:
    elements: list[ParsedElement] = []
    img_list = list(image_map.values()) if image_map else []
    for order, item in enumerate(page_items):
        if not isinstance(item, dict):
            continue
        raw_type = str(item.get("type") or "unknown")
        norm_type = _normalize_element_type(raw_type)
        bbox_raw = item.get("bbox")
        bbox: tuple[float, float, float, float] | None = None
        if isinstance(bbox_raw, (list, tuple)) and len(bbox_raw) == 4:
            try:
                bbox = (float(bbox_raw[0]), float(bbox_raw[1]), float(bbox_raw[2]), float(bbox_raw[3]))
            except (ValueError, TypeError):
                pass

        if norm_type == "image":
            caption = _caption_v2(item)
            resolved_path = image_path_from_block(item)
            unresolved = not bool(resolved_path)
            if not resolved_path:
                idx = image_counter[0]
                image_counter[0] += 1
                if img_list and idx < len(img_list):
                    candidate = img_list[idx]
                    resolved_path = candidate["path"]
                    caption = caption or candidate["alt"]
                    unresolved = False
            metadata: dict[str, Any] = {
                "mineru_block_type": raw_type,
                "mineru_reading_order": order,
                "caption": caption,
                "image_path": resolved_path,
                "image_path_unresolved": unresolved,
            }
            text = caption
            elements.append(ParsedElement(
                element_type="image",
                text=text,
                page_number=page_idx + 1,
                extractor="mineru",
                bbox=bbox,
                metadata=metadata,
            ))
        elif norm_type == "table":
            caption = _caption_v2(item)
            table_html = _extract_text_v2(item.get("content"))
            metadata = {
                "mineru_block_type": raw_type,
                "mineru_reading_order": order,
                "caption": caption,
                "table_html": table_html,
            }
            elements.append(ParsedElement(
                element_type="table",
                text=table_html or caption,
                page_number=page_idx + 1,
                extractor="mineru",
                bbox=bbox,
                metadata=metadata,
            ))
        elif norm_type in (
            "heading",
            "paragraph",
            "list",
            "page_header",
            "page_footer",
            "page_footnote",
            "reference",
            "aside",
            "page_number",
            "ocr_text",
            "caption",
        ):
            text = _flatten_content_v2(item)
            metadata = {
                "mineru_block_type": raw_type,
                "mineru_reading_order": order,
            }
            if norm_type in ("page_header", "page_footer", "page_footnote", "page_number"):
                text_level = None
            else:
                level_raw = None
                content = item.get("content")
                if isinstance(content, dict):
                    level_raw = content.get("level") or item.get("text_level")
                else:
                    level_raw = item.get("text_level")
                text_level = int(level_raw) if isinstance(level_raw, (int, float)) else None
                if text_level is not None:
                    metadata["text_level"] = text_level
            elements.append(ParsedElement(
                element_type=norm_type,
                text=text,
                page_number=page_idx + 1,
                extractor="mineru",
                bbox=bbox,
                metadata=metadata,
            ))
        else:
            text = _flatten_content_v2(item)
            metadata = {
                "mineru_block_type": raw_type,
                "mineru_reading_order": order,
            }
            elements.append(ParsedElement(
                element_type=norm_type,
                text=text,
                page_number=page_idx + 1,
                extractor="mineru",
                bbox=bbox,
                metadata=metadata,
            ))
    return elements


def _v1_to_elements(
    items: list[dict[str, Any]],
    image_map: dict[str, dict[str, str]],
) -> list[ParsedElement]:
    elements: list[ParsedElement] = []
    for order, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        raw_type = str(item.get("type") or "unknown")
        norm_type = _normalize_element_type(raw_type)
        text = compact_text(str(item.get("text") or ""))
        bbox_raw = item.get("bbox")
        bbox: tuple[float, float, float, float] | None = None
        if isinstance(bbox_raw, (list, tuple)) and len(bbox_raw) == 4:
            try:
                bbox = (float(bbox_raw[0]), float(bbox_raw[1]), float(bbox_raw[2]), float(bbox_raw[3]))
            except (ValueError, TypeError):
                pass
        raw_page = item.get("page_idx")
        page_number = int(raw_page) + 1 if isinstance(raw_page, int) else None
        text_level = item.get("text_level")
        if isinstance(text_level, (int, float)):
            text_level = int(text_level)
        else:
            text_level = None

        metadata: dict[str, Any] = {
            "mineru_block_type": raw_type,
            "mineru_reading_order": order,
        }
        if text_level is not None:
            metadata["text_level"] = text_level

        if norm_type == "image":
            img_path = image_path_from_block(item)
            resolved = False
            if img_path and img_path in image_map:
                resolved = True
            elif img_path:
                filename = Path(img_path).name
                if filename in image_map:
                    img_path = image_map[filename]["path"]
                    resolved = True
            captions: list[str] = []
            for ckey in ("image_caption", "chart_caption"):
                raw = item.get(ckey)
                if isinstance(raw, list):
                    captions.extend(compact_text(v) for v in raw if compact_text(v))
            caption = "; ".join(dict.fromkeys(captions)) if captions else ""
            metadata["caption"] = caption
            metadata["image_path"] = img_path
            metadata["image_path_unresolved"] = not resolved
            elements.append(ParsedElement(
                element_type="image",
                text=caption or text,
                page_number=page_number,
                extractor="mineru",
                bbox=bbox,
                metadata=metadata,
            ))
        elif norm_type == "table":
            table_html = str(item.get("html") or item.get("content") or "")
            table_type = str(item.get("table_type") or "unknown")
            metadata.update({
                "table_html": table_html,
                "table_type": table_type,
                "page_idx": raw_page,
            })
            elements.append(ParsedElement(
                element_type="table",
                text=table_html or text,
                page_number=page_number,
                extractor="mineru",
                bbox=bbox,
                metadata=metadata,
            ))
        else:
            elements.append(ParsedElement(
                element_type=norm_type,
                text=text,
                page_number=page_number,
                extractor="mineru",
                bbox=bbox,
                metadata=metadata,
            ))
    return elements


def _preferred_md(names: list[str]) -> str:
    for preferred in ("full.md", "auto/full.md"):
        for name in names:
            if name.lower().endswith(preferred):
                return name
    return sorted(names, key=lambda item: (item.count("/"), len(item), item))[0]


# ── Service ───────────────────────────────────────────────────────


class MinerUParserService:

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str = MINERU_API_BASE_URL,
        model_version: str = MINERU_MODEL_VERSION,
        language: str = MINERU_LANGUAGE,
        timeout_seconds: int = MINERU_TIMEOUT_SECONDS,
        poll_interval_seconds: float = MINERU_POLL_INTERVAL_SECONDS,
        client: httpx.Client | None = None,
        submit_rate_limiter: MinuteRateLimiter = _submit_rate_limiter,
        result_rate_limiter: MinuteRateLimiter = _result_rate_limiter,
    ) -> None:
        self.api_key = (api_key if api_key is not None else MINERU_API_KEY).strip()
        self.base_url = base_url.rstrip("/")
        self.model_version = model_version
        self.language = language
        self.timeout_seconds = timeout_seconds
        self.poll_interval_seconds = poll_interval_seconds
        self.client = client
        self.submit_rate_limiter = submit_rate_limiter
        self.result_rate_limiter = result_rate_limiter

    def parse_pdf_file(
        self,
        file_path: str | Path,
        *,
        data_id: str | None = None,
        is_ocr: bool = False,
        enable_formula: bool = True,
        enable_table: bool = True,
        output_root: str | Path | None = None,
    ) -> MinerUParseResult:
        if not self.api_key:
            raise MinerUParserUnavailable("MINERU_API_KEY is not configured.")

        path = Path(file_path)
        if not path.is_file():
            raise MinerUParserError(f"File not found: {path}")

        owns_client = self.client is None
        client = self.client or httpx.Client(timeout=60)
        try:
            batch_id, upload_url = self._request_upload_url(
                client,
                file_name=path.name,
                data_id=data_id,
                is_ocr=is_ocr,
                enable_formula=enable_formula,
                enable_table=enable_table,
            )
            self._upload_file(client, upload_url, path)
            result = self._wait_for_result(client, batch_id, path.name)
            archive_bytes = self._download_result_zip(client, result["full_zip_url"])
            artifact_paths = (
                self._save_result_artifacts(archive_bytes, output_root, batch_id)
                if output_root is not None
                else {}
            )
            markdown, markdown_name = self._markdown_from_zip(archive_bytes)
            image_map = _load_markdown_image_map_from_bytes(archive_bytes)
            parsed_document = self._content_list_from_zip_to_document(
                archive_bytes,
                source_url=result["full_zip_url"],
                batch_id=batch_id,
                file_name=result.get("file_name") or path.name,
                markdown_file=markdown_name,
                image_map=image_map,
                extract_dir=artifact_paths.get("extract_dir"),
            )
            return MinerUParseResult(
                parsed_document=parsed_document,
                batch_id=batch_id,
                file_name=result.get("file_name") or path.name,
                full_zip_url=result["full_zip_url"],
                markdown_file=markdown_name,
                original_markdown=markdown,
                artifact_dir=artifact_paths.get("artifact_dir"),
                zip_path=artifact_paths.get("zip_path"),
                extract_dir=artifact_paths.get("extract_dir"),
                content_list_path=artifact_paths.get("content_list_path"),
                layout_path=artifact_paths.get("layout_path"),
                extracted_files=list(artifact_paths.get("extracted_files") or []),
            )
        finally:
            if owns_client:
                client.close()

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "*/*",
        }

    def _request_upload_url(
        self,
        client: httpx.Client,
        *,
        file_name: str,
        data_id: str | None,
        is_ocr: bool,
        enable_formula: bool,
        enable_table: bool,
    ) -> tuple[str, str]:
        file_payload: dict[str, object] = {"name": file_name, "is_ocr": is_ocr}
        if data_id:
            file_payload["data_id"] = data_id
        payload = {
            "files": [file_payload],
            "model_version": self.model_version,
            "language": self.language,
            "enable_formula": enable_formula,
            "enable_table": enable_table,
        }
        self.submit_rate_limiter.acquire(amount=1)
        response = client.post(f"{self.base_url}/api/v4/file-urls/batch", headers=self._headers(), json=payload)
        data = self._json_response(response)
        batch_id = data.get("batch_id")
        file_urls = data.get("file_urls") or []
        if not batch_id or not file_urls:
            raise MinerUParserError("MinerU did not return a batch_id and upload URL.")
        return str(batch_id), str(file_urls[0])

    def _upload_file(self, client: httpx.Client, upload_url: str, path: Path) -> None:
        with path.open("rb") as file_obj:
            response = client.put(upload_url, content=file_obj)
        if response.status_code != 200:
            raise MinerUParserError(f"MinerU upload failed with HTTP {response.status_code}.")

    def _wait_for_result(self, client: httpx.Client, batch_id: str, file_name: str) -> dict:
        deadline = time.monotonic() + self.timeout_seconds
        last_state = "unknown"
        while time.monotonic() <= deadline:
            self.result_rate_limiter.acquire(amount=1)
            response = client.get(f"{self.base_url}/api/v4/extract-results/batch/{batch_id}", headers=self._headers())
            data = self._json_response(response)
            for item in data.get("extract_result") or []:
                if item.get("file_name") not in {file_name, None} and len(data.get("extract_result") or []) > 1:
                    continue
                state = str(item.get("state") or "")
                last_state = state or last_state
                if state == "done":
                    full_zip_url = item.get("full_zip_url")
                    if not full_zip_url:
                        raise MinerUParserError("MinerU task completed without full_zip_url.")
                    return item
                if state == "failed":
                    raise MinerUParserError(str(item.get("err_msg") or "MinerU parsing failed."))
            time.sleep(self.poll_interval_seconds)
        raise TimeoutError(f"MinerU parsing timed out while task state was {last_state}.")

    def _download_result_zip(self, client: httpx.Client, full_zip_url: str) -> bytes:
        response = client.get(full_zip_url)
        if response.status_code != 200:
            raise MinerUParserError(f"MinerU result download failed with HTTP {response.status_code}.")
        return response.content

    def _markdown_from_zip(self, archive_bytes: bytes) -> tuple[str, str]:
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
            _check_zip_bomb(archive)
            markdown_names = [
                name for name in archive.namelist()
                if name.lower().endswith(".md") and not name.endswith("/")
            ]
            if not markdown_names:
                raise MinerUParserError("MinerU result zip did not contain a Markdown file.")
            markdown_name = _preferred_md(markdown_names)
            markdown = archive.read(markdown_name).decode("utf-8", errors="replace").strip()
        if not markdown:
            raise MinerUParserError("MinerU Markdown result is empty.")
        return markdown, markdown_name

    def _content_list_from_zip_to_document(
        self,
        archive_bytes: bytes,
        *,
        source_url: str,
        batch_id: str,
        file_name: str,
        markdown_file: str,
        image_map: dict[str, dict[str, str]],
        extract_dir: str | Path | None = None,
    ) -> ParsedDocument:
        content_list_v2, content_list_v1 = _read_content_lists(archive_bytes)
        base_meta: dict[str, Any] = {
            "source_type": "pdf",
            "parser_engine": "mineru",
            "mineru_batch_id": batch_id,
            "mineru_file_name": file_name,
            "mineru_markdown_file": markdown_file,
            "mineru_full_zip_url": source_url,
        }
        extract_root = Path(extract_dir) if extract_dir else None
        warnings: list[str] = []

        if content_list_v2 is not None:
            try:
                pages = _build_pages_from_v2(content_list_v2, image_map)
                base_meta["content_list_format"] = "v2"
                self._merge_base_meta(pages, base_meta)
                self._resolve_image_paths(pages, extract_root)
                return ParsedDocument(
                    pages=pages,
                    source_type="pdf",
                    parser_version="mineru_api_v4",
                    parser_engine="mineru",
                    pymupdf_available=True,
                    table_extraction_enabled=True,
                    table_extraction_reason=None,
                    warnings=warnings,
                )
            except Exception as exc:
                warnings.append(f"v2 content list parsing failed: {exc}; falling back to v1")

        if content_list_v1 is not None:
            try:
                pages = _build_pages_from_v1(content_list_v1, image_map)
                base_meta["content_list_format"] = "v1"
                self._merge_base_meta(pages, base_meta)
                self._resolve_image_paths(pages, extract_root)
                return ParsedDocument(
                    pages=pages,
                    source_type="pdf",
                    parser_version="mineru_api_v4",
                    parser_engine="mineru",
                    pymupdf_available=True,
                    table_extraction_enabled=True,
                    table_extraction_reason=None,
                    warnings=warnings,
                )
            except Exception as exc:
                warnings.append(f"v1 content list parsing failed: {exc}; falling back to markdown")

        warnings.append("No content_list found in MinerU result; falling back to markdown-only paragraph")
        return self._markdown_to_parsed_document(
            _read_markdown_text_from_zip(archive_bytes) or "",
            source_url=source_url,
            batch_id=batch_id,
            file_name=file_name,
            markdown_file=markdown_file,
            warnings=warnings,
        )

    def _markdown_to_parsed_document(
        self,
        markdown: str,
        *,
        source_url: str,
        batch_id: str,
        file_name: str,
        markdown_file: str,
        warnings: list[str] | None = None,
    ) -> ParsedDocument:
        metadata = {
            "source_type": "pdf",
            "parser_engine": "mineru",
            "mineru_batch_id": batch_id,
            "mineru_file_name": file_name,
            "mineru_markdown_file": markdown_file,
            "mineru_full_zip_url": source_url,
        }
        return ParsedDocument(
            pages=[
                ParsedPage(
                    page_number=1,
                    profile=None,
                    elements=[
                        ParsedElement(
                            element_type="paragraph",
                            text=markdown,
                            page_number=None,
                            extractor="mineru",
                            metadata=metadata,
                        )
                    ],
                )
            ],
            source_type="pdf",
            parser_version="mineru_api_v4",
            parser_engine="mineru",
            pymupdf_available=True,
            table_extraction_enabled=True,
            table_extraction_reason=None,
            warnings=warnings or [],
        )

    def _save_result_artifacts(
        self,
        archive_bytes: bytes,
        output_root: str | Path,
        batch_id: str,
    ) -> dict[str, str | list[str]]:
        artifact_dir = Path(output_root) / batch_id
        extract_dir = artifact_dir / "extracted"
        zip_path = artifact_dir / "result.zip"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        extract_dir.mkdir(parents=True, exist_ok=True)
        zip_path.write_bytes(archive_bytes)

        extracted_files: list[str] = []
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
            _check_zip_bomb(archive)
            for name in archive.namelist():
                if name.endswith("/"):
                    continue
                destination = extract_dir / name
                try:
                    destination.resolve().relative_to(extract_dir.resolve())
                except ValueError:
                    raise MinerUParserError(f"Unsafe path in MinerU result zip: {name}")
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(archive.read(name))
                extracted_files.append(str(destination.resolve()))

        content_list_path = self._first_existing(
            extract_dir,
            suffixes=(
                "_content_list_v2.json",
                "content_list_v2.json",
                "_content_list.json",
                "content_list.json",
            ),
        )
        layout_path = self._first_existing(
            extract_dir,
            suffixes=(
                "_layout.pdf",
                "layout.pdf",
                "_layout.json",
                "layout.json",
            ),
        )
        return {
            "artifact_dir": str(artifact_dir.resolve()),
            "zip_path": str(zip_path.resolve()),
            "extract_dir": str(extract_dir.resolve()),
            "content_list_path": str(content_list_path.resolve()) if content_list_path else "",
            "layout_path": str(layout_path.resolve()) if layout_path else "",
            "extracted_files": extracted_files,
        }

    @staticmethod
    def _merge_base_meta(pages: list[ParsedPage], base_meta: dict[str, Any]) -> None:
        for page in pages:
            for elem in page.elements:
                for k, v in base_meta.items():
                    if k not in elem.metadata:
                        elem.metadata[k] = v

    @staticmethod
    def _resolve_image_paths(pages: list[ParsedPage], extract_root: Path | None) -> None:
        if extract_root is None:
            return
        images_dir = extract_root / "images"
        if not images_dir.is_dir():
            return
        for page in pages:
            for elem in page.elements:
                if elem.element_type != "image":
                    continue
                rel = elem.metadata.get("image_path", "")
                if not rel:
                    continue
                candidate = extract_root / rel
                if candidate.is_file():
                    elem.metadata["image_path"] = str(candidate.resolve())
                    elem.metadata["image_path_unresolved"] = False
                else:
                    filename = Path(rel).name
                    for found in images_dir.iterdir():
                        if found.name == filename:
                            elem.metadata["image_path"] = str(found.resolve())
                            elem.metadata["image_path_unresolved"] = False
                            break

    @staticmethod
    def _first_existing(root: Path, *, suffixes: tuple[str, ...]) -> Path | None:
        for suffix in suffixes:
            for path in sorted(root.rglob("*")):
                if not path.is_file():
                    continue
                if path.name.lower().endswith(suffix.lower()):
                    return path
        return None

    def _json_response(self, response: httpx.Response) -> dict:
        if response.status_code != 200:
            snippet = response.text[:500] if response.text else "(empty response)"
            raise MinerUParserError(
                f"MinerU API returned HTTP {response.status_code}: {snippet}"
            )
        try:
            payload = response.json()
        except (json.JSONDecodeError, ValueError) as exc:
            snippet = response.text[:500] if response.text else "(empty response)"
            raise MinerUParserError(
                f"MinerU API returned non-JSON response (HTTP {response.status_code}): "
                f"{exc}. Body: {snippet}"
            ) from exc
        if not isinstance(payload, dict):
            snippet = str(payload)[:500]
            raise MinerUParserError(f"MinerU API returned non-dict JSON: {snippet}")
        if payload.get("code") != 0:
            raise MinerUParserError(str(payload.get("msg") or payload.get("code") or "MinerU API error."))
        data = payload.get("data")
        if not isinstance(data, dict):
            raise MinerUParserError("MinerU API returned invalid data.")
        return data


# ── Module-level helpers (for zip reading, no self access needed) ─


def _check_zip_bomb(archive: zipfile.ZipFile) -> None:
    total = sum(
        info.file_size or 0
        for info in archive.infolist()
        if not info.is_dir()
    )
    if total > _MAX_ZIP_UNCOMPRESSED_BYTES:
        raise MinerUParserError(
            f"Result zip uncompressed size {total} exceeds limit {_MAX_ZIP_UNCOMPRESSED_BYTES}"
        )


def _read_content_lists(archive_bytes: bytes) -> tuple[list[list[dict[str, Any]]] | None, list[dict[str, Any]] | None]:
    content_list_v2: list[list[dict[str, Any]]] | None = None
    content_list_v1: list[dict[str, Any]] | None = None
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
        _check_zip_bomb(archive)
        names_lower = {n.lower(): n for n in archive.namelist()}
        v2_key = _first_key(names_lower, ("_content_list_v2.json", "content_list_v2.json"))
        v1_key = _first_key(names_lower, ("_content_list.json", "content_list.json"))
        if v2_key is not None:
            try:
                raw = json.loads(archive.read(names_lower[v2_key]).decode("utf-8", errors="replace"))
                if isinstance(raw, list) and all(isinstance(p, list) for p in raw):
                    content_list_v2 = raw
            except (json.JSONDecodeError, ValueError):
                pass
        if v1_key is not None:
            try:
                raw = json.loads(archive.read(names_lower[v1_key]).decode("utf-8", errors="replace"))
                if isinstance(raw, list):
                    content_list_v1 = raw
            except (json.JSONDecodeError, ValueError):
                pass
    return content_list_v2, content_list_v1


def _first_key(names_lower: dict[str, str], suffixes: tuple[str, ...]) -> str | None:
    for suffix in suffixes:
        key = names_lower.get(suffix)
        if key is not None:
            return key
    for lower_name, original_name in names_lower.items():
        for suffix in suffixes:
            if lower_name.endswith(suffix):
                return original_name
    return None


def _read_markdown_text_from_zip(archive_bytes: bytes) -> str | None:
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
        _check_zip_bomb(archive)
        md_names = [
            n for n in archive.namelist()
            if n.lower().endswith(".md") and not n.endswith("/")
        ]
        if not md_names:
            return None
        md_name = _preferred_md(md_names)
        return archive.read(md_name).decode("utf-8", errors="replace").strip()


def _load_markdown_image_map_from_bytes(archive_bytes: bytes) -> dict[str, dict[str, str]]:
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
        return _load_markdown_image_map(archive)


def _build_pages_from_v2(
    content_list_v2: list[list[dict[str, Any]]],
    image_map: dict[str, dict[str, str]],
) -> list[ParsedPage]:
    pages: list[ParsedPage] = []
    image_counter: list[int] = [0]
    for page_idx, page_items in enumerate(content_list_v2):
        if not isinstance(page_items, list):
            continue
        elements = _v2_to_elements(page_items, page_idx, image_map, image_counter)
        pages.append(ParsedPage(
            page_number=page_idx + 1,
            profile=None,
            elements=elements,
        ))
    if not pages:
        raise MinerUParserError("content_list_v2.json is empty (no pages)")
    return pages


def _build_pages_from_v1(
    content_list_v1: list[dict[str, Any]],
    image_map: dict[str, dict[str, str]],
) -> list[ParsedPage]:
    if not content_list_v1:
        raise MinerUParserError("content_list.json is empty")
    elements = _v1_to_elements(content_list_v1, image_map)
    page_map: dict[int | None, list[ParsedElement]] = {}
    for elem in elements:
        page_map.setdefault(elem.page_number, []).append(elem)
    if None in page_map and len(page_map) > 1:
        none_elems = page_map.pop(None)
        sorted_keys = sorted(k for k in page_map if k is not None)
        if sorted_keys:
            page_map[sorted_keys[0]] = none_elems + page_map[sorted_keys[0]]
        else:
            page_map[1] = none_elems
    sorted_pages = sorted(page_map.keys()) if any(k is not None for k in page_map) else [1]
    if not sorted_pages:
        sorted_pages = [1]
    pages: list[ParsedPage] = []
    for pn in sorted_pages:
        pn_int = pn if pn is not None else 1
        pages.append(ParsedPage(
            page_number=pn_int,
            profile=None,
            elements=page_map.get(pn, []) if pn is not None else page_map.get(None, []),
        ))
    if not pages:
        raise MinerUParserError("content_list.json produced no pages")
    return pages

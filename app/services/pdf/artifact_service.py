from __future__ import annotations

import hashlib
import io
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.config import DATA_DIR
from app.models import Paper, PaperStatus
from app.services.mineru_asset_builder import MinerUAssetBuilder
from app.services.pdf.audit import audit_summary_for_title
from app.services.pdf.parse_service import PaperParseService
from app.services.storage import StorageService
from app.services.object_store import ObjectStore


class LocalMinerUArtifactService:
    def __init__(self, db: Session, storage: StorageService | None = None, root: Path | None = None) -> None:
        self.db = db
        self.storage = storage or StorageService()
        self.root = (root or DATA_DIR / "pipeline_batch").resolve()

    def list_artifacts(self) -> list[dict[str, Any]]:
        if not self.root.is_dir():
            return []

        by_scope: dict[Path, Path] = {}
        for path in sorted(self.root.rglob("*content_list_v2*.json")):
            if not path.is_file():
                continue
            if not self._is_nested_content_list_v2(path):
                continue
            scope = self._artifact_scope_for_path(path)
            if scope is None:
                continue
            if scope not in by_scope:
                by_scope[scope] = path

        for zip_path in sorted(self.root.rglob("result.zip")):
            if not zip_path.is_file():
                continue
            scope = self._artifact_scope_for_path(zip_path)
            if scope is None or scope in by_scope:
                continue
            artifact_root = self._artifact_root_for_scope(scope)
            content_list = self._extract_content_list_v2_from_zip(zip_path, artifact_root)
            if content_list is None:
                continue
            by_scope[scope] = content_list

        for path in sorted(self.root.rglob("*content_list.json")):
            if not path.is_file() or "content_list_v2" in path.name:
                continue
            scope = self._artifact_scope_for_path(path)
            if scope is None or scope in by_scope:
                continue
            content_list = self._convert_content_list_v1_to_v2(path)
            if content_list is None:
                continue
            by_scope[scope] = content_list

        artifacts = [self._artifact_payload(path) for path in by_scope.values()]
        return sorted(artifacts, key=lambda item: str(item.get("title") or ""))

    def _artifact_scope_for_path(self, path: Path) -> Path | None:
        try:
            return Path(path.resolve().relative_to(self.root.resolve()).parts[0])
        except ValueError:
            return None

    def _artifact_root_for_scope(self, scope: Path) -> Path:
        base = self.root / scope
        structured = base / "structured"
        return structured if structured.is_dir() else base

    @staticmethod
    def _pick_content_list_v2_in_names(names: list[str]) -> str | None:
        lowered = {name.lower(): name for name in names if name and not name.endswith("/")}
        priority = (
            "_content_list_v2.json",
            "content_list_v2.json",
            "_content_list_v2_nested.json",
            "content_list_v2_nested.json",
        )
        for suffix in priority:
            for lower, original in sorted(lowered.items()):
                if lower.endswith(suffix):
                    return original

        for lower, original in sorted(lowered.items()):
            if "content_list_v2" in lower and lower.endswith(".json"):
                return original
        return None

    def _extract_content_list_v2_from_zip(self, zip_path: Path, artifact_root: Path) -> Path | None:
        cache = artifact_root / "content_list_v2_from_result_zip.json"
        if cache.is_file() and self._is_nested_content_list_v2(cache):
            return cache

        try:
            with zipfile.ZipFile(zip_path) as archive:
                candidate_name = self._pick_content_list_v2_in_names(archive.namelist())
                if candidate_name is None:
                    return None
                raw = archive.read(candidate_name)
        except Exception:
            return None

        try:
            data = json.loads(raw.decode("utf-8"))
        except Exception:
            return None
        if not isinstance(data, list) or not all(isinstance(page, list) for page in data):
            return None

        try:
            artifact_root.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            return cache
        except Exception:
            return None

    def _convert_content_list_v1_to_v2(self, content_list_v1: Path) -> Path | None:
        cache = content_list_v1.with_name("content_list_v2_from_v1.json")
        if cache.is_file() and self._is_nested_content_list_v2(cache):
            return cache

        try:
            data = json.loads(content_list_v1.read_text(encoding="utf-8"))
        except Exception:
            return None
        if not isinstance(data, list) or not all(isinstance(item, dict) for item in data):
            return None

        pages_by_idx: dict[int, list[dict[str, Any]]] = {}
        fallback_page = 0
        for item in data:
            page_idx = item.get("page_idx")
            if not isinstance(page_idx, int):
                page_idx = fallback_page
            block = dict(item)
            block["page_idx"] = page_idx
            pages_by_idx.setdefault(page_idx, []).append(block)

        if not pages_by_idx:
            return None
        pages = [pages_by_idx[idx] for idx in sorted(pages_by_idx)]
        try:
            cache.write_text(json.dumps(pages, ensure_ascii=False), encoding="utf-8")
            return cache
        except Exception:
            return None

    def import_artifact(self, content_list_path: str, *, title: str | None = None) -> Paper:
        content_list = self._resolve_data_path(content_list_path)
        if not content_list.is_file():
            raise ValueError("content_list file not found.")
        if not self._is_nested_content_list_v2(content_list):
            raise ValueError("chart-only import requires MinerU content_list_v2.json as list[list[block]].")

        artifact = self._artifact_payload(content_list)
        artifact_root = content_list.parent
        markdown_path = Path(str(artifact.get("markdown_path") or ""))
        layout_path = Path(str(artifact.get("layout_path") or ""))
        source_path = Path(str(artifact.get("source_path") or ""))

        markdown = (
            markdown_path.read_text(encoding="utf-8")
            if markdown_path.is_file()
            else self._markdown_from_content_list(content_list)
        )
        if not markdown.strip():
            raise ValueError("No markdown or content text could be built from this artifact.")

        file_hash = hashlib.sha256(f"local-mineru-artifact:{content_list.resolve()}".encode("utf-8")).hexdigest()
        existing = (
            self.db.query(Paper)
            .filter(Paper.file_hash == file_hash, Paper.status != PaperStatus.DELETED.value)
            .first()
        )
        display_title = (
            title.strip() if title and title.strip()
            else str(artifact.get("title") or content_list.parent.name)
        )
        if existing is not None:
            existing.title = display_title
            existing.updated_at = datetime.now(timezone.utc)
            self.db.commit()
            self.db.refresh(existing)
            return existing

        original_filename = source_path.name if source_path.is_file() else content_list.name
        paper = Paper(
            title=display_title,
            original_filename=original_filename,
            file_path="pending",
            file_size=source_path.stat().st_size if source_path.is_file() else content_list.stat().st_size,
            file_hash=file_hash,
            mime_type="application/pdf" if source_path.suffix.lower() == ".pdf" else "application/json",
            status=PaperStatus.PROCESSING.value,
        )
        self.db.add(paper)
        self.db.flush()

        store = ObjectStore(self.db, self.storage.adapter)
        marker_payload = {
            "content_list_path": str(content_list),
            "markdown_path": str(markdown_path) if markdown_path.is_file() else None,
            "layout_path": str(layout_path) if layout_path.is_file() else None,
            "source_path": str(source_path) if source_path.is_file() else None,
        }
        if source_path.is_file() and source_path.suffix.lower() == ".pdf":
            source_object = store.put_file(
                key=f"papers/{paper.id}/source/{file_hash}.pdf",
                source=source_path,
                media_type="application/pdf",
                metadata={"role": "source_pdf", "imported_from": str(source_path)},
            )
            paper.pdf_object_id = source_object.id
        else:
            source_object = store.put_json(
                key=f"papers/{paper.id}/source/local-mineru-artifact.json",
                payload=marker_payload,
                metadata={"role": "local_artifact_marker"},
            )
        paper.file_path = source_object.object_key
        paper.text_content = self._plain_text_preview(markdown)
        paper.mineru_markdown = None
        markdown_object = store.put_bytes(
            key=f"papers/{paper.id}/mineru/local/document.md",
            data=markdown.encode("utf-8"),
            media_type="text/markdown",
            metadata={"role": "mineru_markdown", "imported_from": str(markdown_path) if markdown_path.is_file() else None},
        )
        paper.mineru_markdown_object_id = markdown_object.id
        content_object = store.put_file(
            key=f"papers/{paper.id}/mineru/local/content_list.json",
            source=content_list,
            media_type="application/json",
            metadata={"role": "mineru_content_list", "imported_from": str(content_list)},
        )
        paper.mineru_content_object_id = content_object.id
        paper.mineru_content_list_path = content_object.object_key
        paper.mineru_extract_dir = f"papers/{paper.id}/mineru/local"
        if layout_path.is_file():
            layout_object = store.put_file(
                key=f"papers/{paper.id}/mineru/local/layout.json",
                source=layout_path,
                media_type="application/json",
                metadata={"role": "mineru_layout", "imported_from": str(layout_path)},
            )
            paper.mineru_layout_object_id = layout_object.id
            PaperParseService._store_layout_data(paper, str(layout_path))

        archive_buffer = io.BytesIO()
        with zipfile.ZipFile(archive_buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(item for item in artifact_root.rglob("*") if item.is_file()):
                archive.write(path, path.relative_to(artifact_root).as_posix())
        raw_object = store.put_bytes(
            key=f"papers/{paper.id}/mineru/local/raw.zip",
            data=archive_buffer.getvalue(),
            media_type="application/zip",
            metadata={"role": "mineru_raw_output", "imported_from": str(artifact_root)},
        )
        paper.mineru_artifact_dir = raw_object.object_key

        MinerUAssetBuilder(self.db, self.storage).ingest(
            paper,
            markdown,
            str(artifact_root),
            content_list_path=str(content_list),
            layout_path=str(layout_path) if layout_path.is_file() else None,
        )
        self.db.flush()
        paper.status = PaperStatus.DONE.value
        paper.error_message = None
        self.db.commit()
        self.db.refresh(paper)
        return paper

    def _artifact_payload(self, content_list: Path) -> dict[str, Any]:
        root = content_list.parent
        markdown = root / "full.md"
        layout = root / "layout.json"
        source = self._find_source_file(root)
        return {
            "id": self._relative_to_data(content_list),
            "title": self._title_for_path(content_list),
            "content_list_path": self._relative_to_data(content_list),
            "absolute_content_list_path": str(content_list.resolve()),
            "markdown_path": str(markdown.resolve()) if markdown.is_file() else None,
            "layout_path": str(layout.resolve()) if layout.is_file() else None,
            "source_path": str(source.resolve()) if source else None,
            "image_count": len([p for p in (root / "images").glob("*") if p.is_file()])
            if (root / "images").is_dir() else 0,
            "kind": "content_list_v2" if "content_list_v2" in content_list.name else "content_list",
            "audit_summary": audit_summary_for_title(self._title_for_path(content_list)),
        }

    def _resolve_data_path(self, value: str) -> Path:
        from app.config import DATA_DIR

        raw = Path(value)
        path = raw if raw.is_absolute() else DATA_DIR / raw
        resolved = path.resolve()
        resolved.relative_to(DATA_DIR.resolve())
        return resolved

    def _relative_to_data(self, path: Path) -> str:
        from app.config import DATA_DIR

        try:
            return path.resolve().relative_to(DATA_DIR.resolve()).as_posix()
        except ValueError:
            return str(path.resolve())

    @staticmethod
    def _is_nested_content_list_v2(path: Path) -> bool:
        if "content_list_v2" not in path.name:
            return False
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return False
        return isinstance(data, list) and all(isinstance(page, list) for page in data)

    def _title_for_path(self, content_list: Path) -> str:
        try:
            relative = content_list.resolve().relative_to(self.root)
            paper_dir = relative.parts[0] if relative.parts else content_list.parent.name
        except ValueError:
            paper_dir = content_list.parent.name
        return paper_dir.replace("_", " ").replace("-", " ", 1)

    @staticmethod
    def _find_source_file(root: Path) -> Path | None:
        for pattern in ("source.pdf", "*_origin.pdf", "*.pdf"):
            found = next((p for p in root.glob(pattern) if p.is_file()), None)
            if found:
                return found
        return None

    @staticmethod
    def _markdown_from_content_list(content_list: Path) -> str:
        try:
            data = json.loads(content_list.read_text(encoding="utf-8"))
        except Exception:
            return ""
        items = MinerUAssetBuilder._load_content_list(str(content_list))
        lines: list[str] = []
        for item in items:
            item_type = str(item.get("type") or "")
            text = MinerUAssetBuilder._compact_text(item.get("text") or item.get("content") or "")
            if item_type in {"image", "chart"} and item.get("img_path"):
                captions: list[str] = []
                for key in ("image_caption", "chart_caption", "caption"):
                    raw = item.get(key)
                    if isinstance(raw, list):
                        captions.extend(
                            MinerUAssetBuilder._compact_text(v)
                            for v in raw if MinerUAssetBuilder._compact_text(v)
                        )
                    elif raw:
                        captions.append(MinerUAssetBuilder._compact_text(raw))
                alt = "; ".join(dict.fromkeys(captions)) or text or item_type
                lines.append(f"![{alt}]({item.get('img_path')})")
            elif text:
                lines.append(text)
        if not lines and isinstance(data, list):
            lines.append(content_list.stem)
        return "\n\n".join(lines)

    @staticmethod
    def _plain_text_preview(markdown: str) -> str:
        text = MinerUAssetBuilder._compact_text(markdown.replace("![", "[").replace("](", " ").replace(")", " "))
        return text[:20000]

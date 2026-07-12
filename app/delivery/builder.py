from __future__ import annotations

import hashlib
import json
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
from openpyxl import Workbook
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import PIPELINE_VERSION
from app.models import (
    DeliveryArtifact,
    DeliveryVersion,
    ExtractionRun,
    Paper,
    PaperAsset,
    PaperStatus,
    Project,
    RunArtifact,
    StructuredResult,
)
from app.services.object_store import ObjectStore
from app.services.storage import StorageAdapter, get_storage_adapter


@dataclass(frozen=True, slots=True)
class DeliveryBuildResult:
    version: str
    manifest_uri: str
    manifest: dict[str, Any]


class DeliveryBuilder:
    def __init__(self, db: Session, storage: StorageAdapter | None = None) -> None:
        self.db = db
        self.storage = storage or get_storage_adapter()

    def build(
        self,
        *,
        version: str,
        project_id: int = 1,
        data_scope: dict[str, Any] | None = None,
        snapshot_at: datetime | None = None,
    ) -> DeliveryBuildResult:
        existing = self.db.query(DeliveryVersion).filter(DeliveryVersion.version == version).one_or_none()
        if existing is not None:
            raise ValueError(f"Delivery version {version!r} already exists and cannot be overwritten")
        project = self.db.get(Project, project_id)
        if project is None:
            raise ValueError(f"Project {project_id} does not exist")
        snapshot = snapshot_at or datetime.now(timezone.utc)
        scope = _normalize_scope(data_scope, project_id=project_id)
        config_hash = _sha256_json({"scope": scope, "snapshot_at": snapshot.isoformat(), "pipeline": PIPELINE_VERSION})
        delivery = DeliveryVersion(
            project_id=project_id,
            version=version,
            status="building",
            data_scope=scope,
            snapshot_at=snapshot,
            database_schema_version=self._database_schema_version(),
            pipeline_version=PIPELINE_VERSION,
            model_prompt_versions={},
            config_hash=config_hash,
            record_counts={},
        )
        try:
            self.db.add(delivery)
            self.db.flush()
            delivery_id = delivery.id
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            raise ValueError(f"Delivery version {version!r} already exists and cannot be overwritten") from exc
        try:
            if self.db.bind is not None and self.db.bind.dialect.name == "postgresql":
                self.db.connection(execution_options={"isolation_level": "REPEATABLE READ"})
            delivery = self.db.get(DeliveryVersion, delivery_id)
            if delivery is None:
                raise RuntimeError(f"Delivery reservation {delivery_id} disappeared")
            tables = self._snapshot_tables(project_id=project_id, snapshot_at=snapshot, data_scope=scope)
            model_prompt_versions = _model_prompt_versions(tables["extraction_runs"])
            delivery.model_prompt_versions = model_prompt_versions
            delivery.record_counts = {name: len(rows) for name, rows in tables.items()}
            with tempfile.TemporaryDirectory(prefix=f"delivery-{version}-") as temp_dir:
                root = Path(temp_dir)
                files = self._write_artifacts(root=root, tables=tables, snapshot_at=snapshot)
                manifest_files = []
                store = ObjectStore(self.db, self.storage)
                for artifact_format, path, row_count in files:
                    info = store.put_file(
                        key=f"deliveries/{version}/{path.name}",
                        source=path,
                        media_type=_media_type(path),
                        metadata={"role": "delivery_artifact", "delivery_version": version},
                    )
                    artifact = DeliveryArtifact(
                        delivery_version_id=delivery.id,
                        object_id=info.id,
                        format=artifact_format,
                        filename=path.name,
                        sha256=info.sha256,
                        size_bytes=info.size_bytes,
                        media_type=info.media_type,
                        row_count=row_count,
                    )
                    self.db.add(artifact)
                    manifest_files.append(
                        {
                            "filename": path.name,
                            "format": artifact_format,
                            "sha256": info.sha256,
                            "size_bytes": info.size_bytes,
                            "media_type": info.media_type,
                            "row_count": row_count,
                            "object_uri": info.uri,
                        }
                    )
                manifest = {
                    "manifest_schema_version": "delivery-manifest.v1",
                    "delivery_version": version,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "snapshot_at": snapshot.isoformat(),
                    "data_scope": scope,
                    "files": sorted(manifest_files, key=lambda item: item["filename"]),
                    "database_schema_version": delivery.database_schema_version,
                    "pipeline_version": delivery.pipeline_version,
                    "model_prompt_versions": model_prompt_versions,
                    "record_counts": delivery.record_counts,
                    "config_hash": config_hash,
                    "build_status": "published",
                }
                manifest_path = root / "manifest.json"
                manifest_path.write_text(
                    json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8"
                )
                manifest_object = store.put_file(
                    key=f"deliveries/{version}/manifest.json",
                    source=manifest_path,
                    media_type="application/json",
                    metadata={"role": "delivery_manifest", "delivery_version": version},
                )
                delivery.manifest_object_id = manifest_object.id
                delivery.status = "published"
                delivery.published_at = datetime.now(timezone.utc)
                self.db.commit()
                return DeliveryBuildResult(version=version, manifest_uri=manifest_object.uri, manifest=manifest)
        except Exception as exc:
            self.db.rollback()
            failed = self.db.query(DeliveryVersion).filter(DeliveryVersion.version == version).one_or_none()
            if failed is None:
                failed = DeliveryVersion(
                    project_id=project_id,
                    version=version,
                    status="failed",
                    data_scope=scope,
                    snapshot_at=snapshot,
                    database_schema_version=self._database_schema_version(),
                    pipeline_version=PIPELINE_VERSION,
                    model_prompt_versions={},
                    config_hash=config_hash,
                    record_counts={},
                    error_message=str(exc),
                )
                self.db.add(failed)
            else:
                failed.status = "failed"
                failed.error_message = str(exc)
            self.db.commit()
            raise

    def _snapshot_tables(
        self,
        *,
        project_id: int,
        snapshot_at: datetime,
        data_scope: dict[str, Any],
    ) -> dict[str, list[dict[str, Any]]]:
        paper_filters = [Paper.project_id == project_id, Paper.created_at <= snapshot_at]
        if "paper_ids" in data_scope:
            paper_filters.append(Paper.id.in_(data_scope["paper_ids"]))
        if "paper_statuses" in data_scope:
            paper_filters.append(Paper.status.in_(data_scope["paper_statuses"]))
        papers = self.db.execute(
            select(Paper)
            .where(*paper_filters)
            .order_by(Paper.id)
        ).scalars().all()
        paper_ids = [paper.id for paper in papers]
        assets = self.db.execute(
            select(PaperAsset)
            .where(PaperAsset.paper_id.in_(paper_ids), PaperAsset.created_at <= snapshot_at)
            .order_by(PaperAsset.id)
        ).scalars().all() if paper_ids else []
        runs = self.db.execute(
            select(ExtractionRun)
            .where(
                ExtractionRun.paper_id.in_(paper_ids),
                ExtractionRun.completed_at.is_not(None),
                ExtractionRun.completed_at <= snapshot_at,
                ExtractionRun.status.in_(["succeeded", "partial_failure", "failed"]),
            )
            .order_by(ExtractionRun.created_at, ExtractionRun.id)
        ).scalars().all() if paper_ids else []
        run_ids = [run.id for run in runs]
        results = self.db.execute(
            select(StructuredResult)
            .where(StructuredResult.run_id.in_(run_ids), StructuredResult.created_at <= snapshot_at)
            .order_by(StructuredResult.run_id, StructuredResult.result_type, StructuredResult.natural_key)
        ).scalars().all() if run_ids else []
        run_artifacts = self.db.execute(
            select(RunArtifact)
            .where(RunArtifact.run_id.in_(run_ids), RunArtifact.created_at <= snapshot_at)
            .order_by(RunArtifact.run_id, RunArtifact.role, RunArtifact.filename)
        ).scalars().all() if run_ids else []
        return {
            "papers": [_paper_row(item) for item in papers],
            "paper_assets": [_asset_row(item) for item in assets],
            "extraction_runs": [_run_row(item) for item in runs],
            "structured_results": [_result_row(item) for item in results],
            "run_artifacts": [_run_artifact_row(item) for item in run_artifacts],
        }

    def _write_artifacts(
        self, *, root: Path, tables: dict[str, list[dict[str, Any]]], snapshot_at: datetime
    ) -> list[tuple[str, Path, int | None]]:
        outputs: list[tuple[str, Path, int | None]] = []
        arrow_tables: dict[str, pa.Table] = {}
        for name, rows in tables.items():
            arrow = _arrow_table(name, rows)
            arrow_tables[name] = arrow
            path = root / f"{name}.parquet"
            pq.write_table(arrow, path, compression="zstd", use_dictionary=False, write_statistics=True)
            outputs.append(("parquet", path, len(rows)))

        duckdb_path = root / "snapshot.duckdb"
        connection = duckdb.connect(str(duckdb_path))
        try:
            for name, arrow in arrow_tables.items():
                connection.register("snapshot_batch", arrow)
                connection.execute(f'CREATE TABLE "{name}" AS SELECT * FROM snapshot_batch')
                connection.unregister("snapshot_batch")
            connection.execute("CHECKPOINT")
        finally:
            connection.close()
        outputs.append(("duckdb", duckdb_path, sum(len(rows) for rows in tables.values())))

        excel_path = root / "snapshot.xlsx"
        _write_excel(excel_path, tables=tables, snapshot_at=snapshot_at)
        outputs.append(("excel", excel_path, sum(len(rows) for rows in tables.values())))

        markdown_path = root / "README.md"
        markdown_path.write_text(_delivery_markdown(tables, snapshot_at=snapshot_at), encoding="utf-8")
        outputs.append(("markdown", markdown_path, None))
        return outputs

    def _database_schema_version(self) -> str:
        return str(self.db.execute(text("SELECT version_num FROM alembic_version")).scalar_one())


_COLUMNS = {
    "papers": ["id", "project_id", "title", "original_filename", "pdf_object_id", "file_hash", "file_size", "mime_type", "status", "page_count", "created_at", "updated_at"],
    "paper_assets": ["id", "paper_id", "figure_id", "object_id", "is_active", "asset_type", "asset_index", "label", "page_number", "mime_type", "width", "height", "caption", "metadata_json", "created_at"],
    "extraction_runs": ["id", "task_id", "paper_id", "input_object_id", "source_asset_id", "parent_run_id", "attempt", "model_provider", "model_name", "model_version", "prompt_version", "pipeline_version", "config_snapshot", "status", "started_at", "completed_at", "error_type", "error_message", "raw_output_object_id", "normalized_schema_version", "created_at"],
    "structured_results": ["id", "run_id", "paper_id", "source_asset_id", "result_type", "natural_key", "schema_version", "content_hash", "page_number", "figure_id", "panel_id", "payload", "created_at"],
    "run_artifacts": ["id", "run_id", "object_id", "role", "filename", "created_at"],
}


def _arrow_table(name: str, rows: list[dict[str, Any]]) -> pa.Table:
    normalized = [{column: row.get(column) for column in _COLUMNS[name]} for row in rows]
    if normalized:
        return pa.Table.from_pylist(normalized)
    return pa.table({column: pa.array([], type=pa.string()) for column in _COLUMNS[name]})


def _paper_row(item: Paper) -> dict[str, Any]:
    return _row(item, _COLUMNS["papers"])


def _asset_row(item: PaperAsset) -> dict[str, Any]:
    return _row(item, _COLUMNS["paper_assets"])


def _run_row(item: ExtractionRun) -> dict[str, Any]:
    return _row(item, _COLUMNS["extraction_runs"])


def _result_row(item: StructuredResult) -> dict[str, Any]:
    return _row(item, _COLUMNS["structured_results"])


def _run_artifact_row(item: RunArtifact) -> dict[str, Any]:
    return _row(item, _COLUMNS["run_artifacts"])


def _row(item: Any, columns: list[str]) -> dict[str, Any]:
    row: dict[str, Any] = {}
    for column in columns:
        value = getattr(item, column)
        if isinstance(value, datetime):
            value = value.isoformat()
        elif isinstance(value, (dict, list)):
            value = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        row[column] = value
    return row


def _write_excel(path: Path, *, tables: dict[str, list[dict[str, Any]]], snapshot_at: datetime) -> None:
    workbook = Workbook()
    workbook.remove(workbook.active)
    workbook.properties.created = snapshot_at.replace(tzinfo=None)
    workbook.properties.modified = snapshot_at.replace(tzinfo=None)
    for name, rows in tables.items():
        sheet = workbook.create_sheet(title=name[:31])
        sheet.append(_COLUMNS[name])
        for row in rows:
            sheet.append([row.get(column) for column in _COLUMNS[name]])
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions
    workbook.save(path)
    _normalize_zip_timestamps(path)


def _normalize_zip_timestamps(path: Path) -> None:
    replacement = path.with_suffix(".normalized.xlsx")
    with zipfile.ZipFile(path, "r") as source, zipfile.ZipFile(replacement, "w", zipfile.ZIP_DEFLATED) as target:
        for name in sorted(source.namelist()):
            info = zipfile.ZipInfo(name, date_time=(2000, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            target.writestr(info, source.read(name))
    replacement.replace(path)


def _delivery_markdown(tables: dict[str, list[dict[str, Any]]], *, snapshot_at: datetime) -> str:
    lines = ["# Extraction data delivery", "", f"Snapshot: {snapshot_at.isoformat()}", "", "## Record counts", ""]
    for name in sorted(tables):
        lines.append(f"- {name}: {len(tables[name])}")
    lines.extend(["", "DuckDB and Parquet are immutable analytical snapshots, not online databases.", ""])
    return "\n".join(lines)


def _model_prompt_versions(rows: list[dict[str, Any]]) -> dict[str, list[str]]:
    return {
        "models": sorted({str(row["model_version"]) for row in rows if row.get("model_version")}),
        "prompts": sorted({str(row["prompt_version"]) for row in rows if row.get("prompt_version")}),
    }


def _sha256_json(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _normalize_scope(data_scope: dict[str, Any] | None, *, project_id: int) -> dict[str, Any]:
    requested = dict(data_scope or {})
    unknown = set(requested) - {"project_id", "paper_ids", "paper_statuses"}
    if unknown:
        raise ValueError(f"Unsupported delivery data_scope keys: {', '.join(sorted(unknown))}")
    requested_project = int(requested.get("project_id", project_id))
    if requested_project != project_id:
        raise ValueError("data_scope.project_id must match the requested project_id")
    scope: dict[str, Any] = {"project_id": project_id}
    if "paper_ids" in requested:
        if not isinstance(requested["paper_ids"], (list, tuple, set)):
            raise ValueError("data_scope.paper_ids must be a list of integer IDs")
        scope["paper_ids"] = sorted({int(value) for value in requested["paper_ids"]})
    if "paper_statuses" in requested:
        if not isinstance(requested["paper_statuses"], (list, tuple, set)):
            raise ValueError("data_scope.paper_statuses must be a list of status strings")
        statuses = sorted(
            {str(value.value if isinstance(value, PaperStatus) else value) for value in requested["paper_statuses"]}
        )
        allowed = {status.value for status in PaperStatus}
        invalid = set(statuses) - allowed
        if invalid:
            raise ValueError(f"Unsupported paper statuses: {', '.join(sorted(invalid))}")
        scope["paper_statuses"] = statuses
    return scope


def _media_type(path: Path) -> str:
    return {
        ".parquet": "application/vnd.apache.parquet",
        ".duckdb": "application/vnd.duckdb",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".md": "text/markdown",
    }.get(path.suffix.lower(), "application/octet-stream")

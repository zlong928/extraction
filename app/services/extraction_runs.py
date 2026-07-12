from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.config import OPENAI_BASE_URL, OPENAI_MODEL, PIPELINE_VERSION, PROMPT_VERSION
from app.models import ExtractionRun, Paper, PaperAsset, PendingJob, RunArtifact, StorageObject, StructuredResult
from app.repositories import JobRepository
from app.services.object_store import ObjectStore
from app.services.storage import StorageAdapter


NORMALIZED_RESULT_SCHEMA_VERSION = "normalized-result.v1"


def create_extraction_run(
    db: Session,
    *,
    job: PendingJob,
    paper: Paper,
    input_object: StorageObject,
    source_asset_id: int | None = None,
    parent_run_id: str | None = None,
    config_snapshot: dict[str, Any] | None = None,
) -> ExtractionRun:
    existing = db.query(ExtractionRun).filter(ExtractionRun.task_id == job.id).one_or_none()
    if existing is not None:
        return existing
    run = ExtractionRun(
        task_id=job.id,
        paper_id=paper.id,
        input_object_id=input_object.id,
        source_asset_id=source_asset_id,
        parent_run_id=parent_run_id,
        attempt=job.attempt,
        model_provider=_model_provider(),
        model_name=OPENAI_MODEL,
        model_version=OPENAI_MODEL,
        prompt_version=PROMPT_VERSION,
        pipeline_version=PIPELINE_VERSION,
        config_snapshot=config_snapshot or {},
        status="running",
    )
    db.add(run)
    db.flush()
    return run


def finalize_extraction_run(
    db: Session,
    *,
    run: ExtractionRun,
    job: PendingJob,
    raw_responses: list[dict[str, Any]],
    result: Any,
    storage_adapter: StorageAdapter | None = None,
) -> None:
    JobRepository(db).assert_ownership(job, for_update=True)
    raw_object = _persist_raw_responses(
        db,
        run=run,
        raw_responses=raw_responses,
        storage_adapter=storage_adapter,
    )
    run.raw_output_object_id = raw_object.id
    _persist_normalized_results(db, run=run, result=result)
    run.normalized_schema_version = NORMALIZED_RESULT_SCHEMA_VERSION
    run.status = str(getattr(result, "status", "succeeded") or "succeeded")
    if run.status not in {"succeeded", "partial_failure"}:
        run.status = "failed"
    run.completed_at = datetime.now(timezone.utc)
    if run.status in {"succeeded", "partial_failure"}:
        JobRepository(db).complete(job)
    else:
        errors = getattr(result, "errors", []) or []
        message = json.dumps(errors, ensure_ascii=False, default=str)[:4000] or "extraction failed"
        run.error_message = message
        JobRepository(db).fail(job, message)


def fail_extraction_run(
    db: Session,
    *,
    run: ExtractionRun,
    job: PendingJob,
    exc: Exception,
    raw_responses: list[dict[str, Any]] | None = None,
    storage_adapter: StorageAdapter | None = None,
) -> None:
    JobRepository(db).assert_ownership(job, for_update=True)
    if raw_responses is not None and run.raw_output_object_id is None:
        raw_object = _persist_raw_responses(
            db,
            run=run,
            raw_responses=raw_responses,
            storage_adapter=storage_adapter,
        )
        run.raw_output_object_id = raw_object.id
    run.status = "failed"
    run.error_type = type(exc).__name__
    run.error_message = str(exc)
    run.completed_at = datetime.now(timezone.utc)
    JobRepository(db).fail(job, str(exc))


def _persist_raw_responses(
    db: Session,
    *,
    run: ExtractionRun,
    raw_responses: list[dict[str, Any]],
    storage_adapter: StorageAdapter | None,
) -> StorageObject:
    raw_object = ObjectStore(db, storage_adapter).put_json(
        key=f"runs/{run.id}/model-raw-responses.json",
        payload={"run_id": run.id, "responses": raw_responses},
        metadata={"role": "model_raw_responses", "run_id": run.id},
    )
    run.raw_output_object_id = raw_object.id
    db.add(
        RunArtifact(
            run_id=run.id,
            object_id=raw_object.id,
            role="model_raw_responses",
            filename="model-raw-responses.json",
        )
    )
    db.flush()
    return raw_object


def _persist_normalized_results(db: Session, *, run: ExtractionRun, result: Any) -> None:
    asset_by_panel: dict[str, PaperAsset] = {}
    asset_by_name: dict[str, PaperAsset] = {}
    for asset in db.query(PaperAsset).filter(
        PaperAsset.paper_id == run.paper_id, PaperAsset.is_active.is_(True)
    ).all():
        try:
            metadata = json.loads(asset.metadata_json or "{}")
        except json.JSONDecodeError:
            metadata = {}
        panel_id = str(metadata.get("panel_id") or "")
        if panel_id:
            asset_by_panel[panel_id] = asset
        asset_by_name[asset.file_path.rsplit("/", 1)[-1]] = asset
    groups = {
        "evidence_packet": getattr(result, "evidence_packets", []) or [],
        "chart_digitization": getattr(result, "chart_digitization_results", []) or [],
        "chart_fact": getattr(result, "chart_facts", []) or getattr(result, "panel_fact_rows", []) or [],
        "chart_point": getattr(result, "chart_points", []) or [],
        "heatmap_candidate": getattr(result, "heatmap_candidates", []) or [],
        "visual_fact": getattr(result, "visual_fact_results", []) or [],
        "image_observation": getattr(result, "image_observations", []) or [],
    }
    for result_type, records in groups.items():
        for index, record in enumerate(records):
            payload = _jsonable(record)
            canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            content_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            base_key = str(
                payload.get("fact_id")
                or payload.get("candidate_id")
                or payload.get("observation_name")
                or payload.get("panel_id")
                or result_type
            )
            panel_id = _as_text(payload.get("panel_id"))
            source_image = str(payload.get("source_image") or payload.get("image_ref") or "")
            source_asset = asset_by_panel.get(panel_id or "") or asset_by_name.get(source_image.rsplit("/", 1)[-1])
            scope_key = panel_id or _as_text(payload.get("figure_id")) or str(source_asset.id if source_asset else "global")
            natural_key = (
                f"{_key_component(scope_key, 160)}:{_key_component(base_key, 260)}:"
                f"{index:08d}:{content_hash[:16]}"
            )
            db.add(
                StructuredResult(
                    run_id=run.id,
                    paper_id=run.paper_id,
                    source_asset_id=run.source_asset_id or (source_asset.id if source_asset else None),
                    result_type=result_type,
                    natural_key=natural_key,
                    schema_version=NORMALIZED_RESULT_SCHEMA_VERSION,
                    content_hash=content_hash,
                    page_number=_as_int(payload.get("page_number")) or (source_asset.page_number if source_asset else None),
                    figure_id=_as_text(payload.get("figure_id")),
                    panel_id=panel_id,
                    payload=payload,
                )
            )
    db.flush()


def _jsonable(value: Any) -> dict[str, Any]:
    if is_dataclass(value):
        value = asdict(value)
    elif hasattr(value, "model_dump"):
        value = value.model_dump()
    elif hasattr(value, "csv_dict"):
        value = value.csv_dict()
    if isinstance(value, dict):
        return json.loads(json.dumps(value, ensure_ascii=False, default=str))
    return {"value": json.loads(json.dumps(value, ensure_ascii=False, default=str))}


def _model_provider() -> str:
    if "openai.com" in OPENAI_BASE_URL:
        return "openai"
    return "openai-compatible"


def _as_text(value: Any) -> str | None:
    return str(value) if value not in (None, "") else None


def _as_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _key_component(value: Any, limit: int) -> str:
    return str(value).replace("\x00", "")[:limit]

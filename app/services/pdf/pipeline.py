from __future__ import annotations

import logging
import json
import os
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.models import BatchItem, BatchRun, ExtractionRun, Paper, PaperAsset, PaperStatus
from app.models import PendingJob, StorageObject
from app.repositories import JobClaim, JobRepository, LostJobLease
from app.services.agent.llm_client import LLMClient
from app.services.extraction.llm_config import build_vlm_config
from app.services.pdf.audit import _audit_summary_from_path
from app.services.extraction_runs import (
    StagedRunArtifact,
    create_extraction_run,
    fail_extraction_run,
    finalize_extraction_run,
    stage_raw_responses,
)
from app.services.object_store import ObjectStore
from app.services.storage import StorageService
from content_pipeline import run_content_pipeline
from content_pipeline.contracts.audit import ExtractionPipelineOptions
from content_pipeline.llm.client import ContentPipelineLLMClient
from sqlalchemy.orm import object_session

logger = logging.getLogger(__name__)


class TerminalPersistenceError(RuntimeError):
    """A terminal database write failed and must be recovered as transport work."""


def prepare_chart_only_run_for_paper(paper: Paper) -> None:
    paper.status = PaperStatus.PROCESSING.value
    paper.error_message = None
    paper.updated_at = datetime.now(timezone.utc)


def check_content_pipeline_llm_preflight() -> None:
    config = build_vlm_config()
    config.update(
        {
            "stream": False,
            "timeout": 30,
            "http_retries": 0,
            "retry_backoff_seconds": 0,
            "allow_non_stream_fallback": False,
            "fallback_models": "",
        }
    )
    client = LLMClient(config)
    try:
        client.chat_text(
            [{"role": "user", "content": "Reply with OK only."}],
            phase="llm_preflight",
            max_tokens=64,
        )
    except Exception as exc:
        raise RuntimeError(f"LLM preflight check failed: {exc}") from exc


def build_backend_content_pipeline_client(config_snapshot: dict[str, Any] | None = None) -> ContentPipelineLLMClient:
    config = build_vlm_config()
    if config_snapshot is not None:
        execution = config_snapshot.get("execution", {})
        frozen_vlm = execution.get("vlm", {}) if isinstance(execution, dict) else {}
        if isinstance(frozen_vlm, dict):
            config.update(frozen_vlm)
        semantic_config = config_snapshot.get("result_semantics", {})
        if isinstance(semantic_config, dict):
            config.update({key: value for key, value in semantic_config.items() if key not in {"mineru", "chart_only"}})
    return ContentPipelineLLMClient(LLMClient(config))


def run_chart_only_for_paper(paper: Paper, *, job: PendingJob | JobClaim | None = None) -> dict[str, Any]:
    if not paper.mineru_content_list_path:
        raise ValueError("Paper has no MinerU content_list path.")
    db = object_session(paper)
    storage = StorageService()
    run = None
    model_client = None
    terminal_persistence_started = False
    if job is not None:
        if db is None:
            raise RuntimeError("ExtractionRun persistence requires an attached database session")
        if isinstance(job, PendingJob):
            job = JobClaim.from_job(job)
        JobRepository(db).assert_ownership(job, for_update=True)
        input_object = db.get(StorageObject, paper.mineru_content_object_id or paper.pdf_object_id)
        if input_object is None:
            raise ValueError("Paper has no persisted input object for extraction")
        batch_snapshot = _batch_execution_snapshot(db, paper=paper, job=job)
        run = create_extraction_run(
            db,
            job=job,
            paper=paper,
            input_object=input_object,
            parent_run_id=_parent_run_id(db, job),
            config_snapshot=batch_snapshot
            or {
                "chart_only": _content_pipeline_chart_only_enabled(),
                "llm_workers": max(1, int(os.getenv("CONTENT_PIPELINE_LLM_WORKERS", "3"))),
            },
        )
        db.commit()
        db.refresh(run)
    try:
        with _pipeline_input_workspace(paper, storage, run=run) as (content_list, layout_path, image_root, workspace):
            output_dir = workspace / "output"
            output_dir.mkdir(parents=True, exist_ok=True)
            run_config = run.config_snapshot if run is not None else {}
            result_semantics = run_config.get("result_semantics", {})
            execution = run_config.get("execution", {}) if isinstance(run_config, dict) else {}
            frozen_llm_workers = execution.get("llm_workers") if isinstance(execution, dict) else None
            llm_workers = max(
                1,
                int(
                    frozen_llm_workers
                    if frozen_llm_workers is not None
                    else run_config.get("llm_workers", os.getenv("CONTENT_PIPELINE_LLM_WORKERS", "3"))
                ),
            )
            model_client = build_backend_content_pipeline_client(run_config)
            result, summary = run_content_pipeline(
                content_list_path=content_list,
                layout_path=layout_path,
                image_root=image_root,
                paper_id=f"paper-{paper.id}",
                use_llm=True,
                output_dir=output_dir,
                options=ExtractionPipelineOptions(
                    fail_fast=False,
                    max_workers=llm_workers,
                    llm_max_workers=llm_workers,
                    chart_only=bool(
                        result_semantics.get("chart_only", run_config.get("chart_only", _content_pipeline_chart_only_enabled()))
                    ),
                ),
                model_client=model_client,
            )
            audit_summary = _audit_summary_from_path(output_dir / "extraction_audit.json") or {}
            if db is not None and run is not None:
                terminal_persistence_started = True
                try:
                    store = ObjectStore(db, storage.adapter)
                    staged_outputs = _stage_pipeline_outputs(
                        store,
                        run_id=run.id,
                        claim_generation=job.claim_generation,
                        output_dir=output_dir,
                    )
                    staged_raw = stage_raw_responses(
                        store,
                        run=run,
                        job=job,
                        raw_responses=model_client.raw_responses,
                    )
                    finalize_extraction_run(
                        db,
                        run=run,
                        job=job,
                        raw_responses=model_client.raw_responses,
                        result=result,
                        storage_adapter=storage.adapter,
                        staged_raw_object=staged_raw,
                        staged_artifacts=staged_outputs,
                    )
                except LostJobLease:
                    raise
                except Exception as exc:
                    raise TerminalPersistenceError(
                        f"Could not persist terminal ExtractionRun {run.id}"
                    ) from exc
    except Exception as exc:
        if terminal_persistence_started:
            if db is not None:
                db.rollback()
            raise
        if db is not None and run is not None and job is not None:
            run_id = run.id
            db.rollback()
            durable_run = db.get(type(run), run_id)
            if durable_run is not None and durable_run.status == "running":
                try:
                    fail_extraction_run(
                        db,
                        run=durable_run,
                        job=job,
                        exc=exc,
                        raw_responses=model_client.raw_responses if model_client is not None else [],
                        storage_adapter=storage.adapter,
                    )
                    db.commit()
                except LostJobLease:
                    db.rollback()
                except Exception as persistence_exc:
                    db.rollback()
                    raise TerminalPersistenceError(
                        f"Could not persist failed ExtractionRun {run.id}"
                    ) from persistence_exc
        raise
    audit_summary["source"] = "current_run"
    audit_summary["status"] = result.status
    audit_summary["chart_facts"] = len(
        getattr(result, "chart_facts", []) or getattr(result, "panel_fact_rows", []) or []
    )
    audit_summary["metric_candidates"] = len(getattr(result, "metric_candidates", []) or [])
    audit_summary["benchmark_metrics"] = len(getattr(result, "metric_rows", []) or [])
    audit_summary["chart_points"] = summary.chart_points_count
    audit_summary["digitization_results"] = summary.digitization_count
    if audit_summary.get("failure_events"):
        paper.error_message = f"Chart-only extraction completed with {audit_summary['failure_events']} failure events."
    return audit_summary


@contextmanager
def _pipeline_input_workspace(paper: Paper, storage: StorageService, run: ExtractionRun | None = None):
    if run is not None and (frozen_input := run.config_snapshot.get("batch_execution")):
        db = object_session(paper)
        if db is None:
            raise RuntimeError("Frozen batch inputs require an attached database session")
        content_object = db.get(StorageObject, frozen_input.get("content_object_id"))
        if content_object is None:
            raise ValueError("Frozen batch content_list object is unavailable")
        with tempfile.TemporaryDirectory(prefix=f"pipeline-paper-{paper.id}-") as temp_dir:
            root = Path(temp_dir)
            content_path = root / "content_list.json"
            content_path.write_bytes(storage.get_bytes(content_object.object_key))
            layout_path = None
            layout_object_id = frozen_input.get("layout_object_id")
            if layout_object_id:
                layout_object = db.get(StorageObject, layout_object_id)
                if layout_object is None:
                    raise ValueError("Frozen batch layout object is unavailable")
                layout_path = root / "layout.json"
                layout_path.write_bytes(storage.get_bytes(layout_object.object_key))
            asset_ids = [int(asset_id) for asset_id in frozen_input.get("asset_ids", [])]
            assets = (
                db.query(PaperAsset)
                .filter(PaperAsset.id.in_(asset_ids))
                .order_by(PaperAsset.asset_index, PaperAsset.id)
                .all()
                if asset_ids
                else []
            )
            for asset in assets:
                _materialize_pipeline_asset(root, asset, storage)
            yield content_path, layout_path, root, root
        return
    legacy_content = Path(paper.mineru_content_list_path or "")
    if legacy_content.is_file():
        extract_dir = Path(paper.mineru_extract_dir or legacy_content.parent)
        image_root = extract_dir / "images" if (extract_dir / "images").is_dir() else extract_dir
        layout = extract_dir / "layout.json"
        with tempfile.TemporaryDirectory(prefix=f"pipeline-output-{paper.id}-") as temp_dir:
            yield legacy_content, layout if layout.is_file() else None, image_root, Path(temp_dir)
        return

    with tempfile.TemporaryDirectory(prefix=f"pipeline-paper-{paper.id}-") as temp_dir:
        root = Path(temp_dir)
        content_path = root / "content_list.json"
        content_path.write_bytes(storage.get_bytes(paper.mineru_content_list_path or ""))
        layout_path = None
        if paper.mineru_layout_object_id and paper.mineru_layout_object is not None:
            layout_path = root / "layout.json"
            layout_path.write_bytes(storage.get_bytes(paper.mineru_layout_object.object_key))
        for asset in sorted((item for item in paper.assets if item.is_active), key=lambda item: item.asset_index):
            _materialize_pipeline_asset(root, asset, storage)
        yield content_path, layout_path, root, root


def _materialize_pipeline_asset(root: Path, asset: PaperAsset, storage: StorageService) -> None:
    metadata = json.loads(asset.metadata_json or "{}")
    relative = str(metadata.get("mineru_img_path") or f"images/{Path(asset.file_path).name}").lstrip("/")
    destination = root / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(storage.get_bytes(asset.file_path))


def _batch_execution_snapshot(db, *, paper: Paper, job: PendingJob | JobClaim) -> dict[str, Any] | None:
    if job.batch_item_id is None:
        return None
    item = db.get(BatchItem, job.batch_item_id)
    if item is None:
        raise ValueError("Batch Job references a missing BatchItem")
    batch = db.get(BatchRun, item.batch_run_id)
    if batch is None:
        raise ValueError("BatchItem references a missing BatchRun")
    snapshot = dict(batch.config_snapshot)
    snapshot["result_config_hash"] = batch.result_config_hash
    snapshot["batch_execution"] = {
        "content_object_id": paper.mineru_content_object_id,
        "layout_object_id": paper.mineru_layout_object_id,
        "asset_ids": [asset.id for asset in paper.assets if asset.is_active],
    }
    if not snapshot["batch_execution"]["content_object_id"]:
        raise ValueError("Batch Job has no persisted MinerU content input")
    return snapshot


def _parent_run_id(db, job: PendingJob | JobClaim) -> str | None:
    ancestor_job_id = job.retry_of_job_id
    visited: set[int] = set()
    while ancestor_job_id is not None and ancestor_job_id not in visited:
        visited.add(ancestor_job_id)
        parent = db.query(ExtractionRun.id).filter(ExtractionRun.task_id == ancestor_job_id).one_or_none()
        if parent is not None:
            return parent[0]
        ancestor = db.get(PendingJob, ancestor_job_id)
        if ancestor is None or ancestor.paper_id != job.paper_id:
            return None
        ancestor_job_id = ancestor.retry_of_job_id
    return None


def _stage_pipeline_outputs(
    store: ObjectStore,
    *,
    run_id: str,
    claim_generation: int,
    output_dir: Path,
) -> list[StagedRunArtifact]:
    artifacts: list[StagedRunArtifact] = []
    for path in sorted(item for item in output_dir.rglob("*") if item.is_file()):
        relative = path.relative_to(output_dir).as_posix()
        media_type = _output_media_type(path)
        staged = store.stage_file(
            run_id=run_id,
            claim_generation=claim_generation,
            role="pipeline_output",
            filename=relative,
            source=path,
            media_type=media_type,
        )
        artifacts.append(
            StagedRunArtifact(
                info=staged,
                role="pipeline_output",
                filename=relative,
                metadata={"role": "pipeline_output", "run_id": run_id, "filename": relative},
                updates_latest_audit=relative == "extraction_audit.json",
            )
        )
    return artifacts


def _output_media_type(path: Path) -> str:
    return {
        ".json": "application/json",
        ".jsonl": "application/x-ndjson",
        ".csv": "text/csv",
        ".md": "text/markdown",
    }.get(path.suffix.lower(), "application/octet-stream")


def _content_pipeline_chart_only_enabled() -> bool:
    value = os.getenv("CONTENT_PIPELINE_CHART_ONLY")
    if value is None:
        return True
    return value.strip().lower() not in {"0", "false", "no", "off"}

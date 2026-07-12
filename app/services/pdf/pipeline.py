from __future__ import annotations

import logging
import json
import os
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.models import Paper, PaperStatus
from app.models import PendingJob, RunArtifact, StorageObject
from app.repositories import LostJobLease
from app.services.agent.llm_client import LLMClient
from app.services.extraction.llm_config import build_vlm_config
from app.services.pdf.audit import _audit_summary_from_path
from app.services.extraction_runs import create_extraction_run, fail_extraction_run, finalize_extraction_run
from app.services.object_store import ObjectStore
from app.services.storage import StorageService
from content_pipeline import run_content_pipeline
from content_pipeline.contracts.audit import ExtractionPipelineOptions
from content_pipeline.llm.client import ContentPipelineLLMClient
from sqlalchemy.orm import object_session

logger = logging.getLogger(__name__)


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


def build_backend_content_pipeline_client() -> ContentPipelineLLMClient:
    return ContentPipelineLLMClient(LLMClient(build_vlm_config()))


def run_chart_only_for_paper(paper: Paper, *, job: PendingJob | None = None) -> dict[str, Any]:
    if not paper.mineru_content_list_path:
        raise ValueError("Paper has no MinerU content_list path.")
    db = object_session(paper)
    storage = StorageService()
    run = None
    model_client = None
    if job is not None:
        if db is None:
            raise RuntimeError("ExtractionRun persistence requires an attached database session")
        input_object = db.get(StorageObject, paper.mineru_content_object_id or paper.pdf_object_id)
        if input_object is None:
            raise ValueError("Paper has no persisted input object for extraction")
        run = create_extraction_run(
            db,
            job=job,
            paper=paper,
            input_object=input_object,
            config_snapshot={
                "chart_only": _content_pipeline_chart_only_enabled(),
                "llm_workers": max(1, int(os.getenv("CONTENT_PIPELINE_LLM_WORKERS", "3"))),
            },
        )
        db.commit()
        db.refresh(run)
    try:
        with _pipeline_input_workspace(paper, storage) as (content_list, layout_path, image_root, workspace):
            output_dir = workspace / "output"
            output_dir.mkdir(parents=True, exist_ok=True)
            llm_workers = max(1, int(os.getenv("CONTENT_PIPELINE_LLM_WORKERS", "3")))
            model_client = build_backend_content_pipeline_client()
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
                    chart_only=_content_pipeline_chart_only_enabled(),
                ),
                model_client=model_client,
            )
            audit_summary = _audit_summary_from_path(output_dir / "extraction_audit.json") or {}
            if db is not None and run is not None:
                _persist_pipeline_outputs(db, paper=paper, run_id=run.id, output_dir=output_dir, storage=storage)
                finalize_extraction_run(
                    db,
                    run=run,
                    job=job,
                    raw_responses=model_client.raw_responses,
                    result=result,
                    storage_adapter=storage.adapter,
                )
    except Exception as exc:
        if db is not None and run is not None and job is not None:
            run_id = run.id
            job_id = job.id
            db.rollback()
            durable_run = db.get(type(run), run_id)
            durable_job = db.get(type(job), job_id)
            if durable_run is not None and durable_job is not None and durable_run.status == "running":
                try:
                    fail_extraction_run(
                        db,
                        run=durable_run,
                        job=durable_job,
                        exc=exc,
                        raw_responses=model_client.raw_responses if model_client is not None else [],
                        storage_adapter=storage.adapter,
                    )
                    db.commit()
                except LostJobLease:
                    db.rollback()
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
def _pipeline_input_workspace(paper: Paper, storage: StorageService):
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
            metadata = json.loads(asset.metadata_json or "{}")
            relative = str(metadata.get("mineru_img_path") or f"images/{Path(asset.file_path).name}").lstrip("/")
            destination = root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(storage.get_bytes(asset.file_path))
        yield content_path, layout_path, root, root


def _persist_pipeline_outputs(db, *, paper: Paper, run_id: str, output_dir: Path, storage: StorageService) -> None:
    store = ObjectStore(db, storage.adapter)
    for path in sorted(item for item in output_dir.rglob("*") if item.is_file()):
        relative = path.relative_to(output_dir).as_posix()
        media_type = _output_media_type(path)
        stored = store.put_file(
            key=f"runs/{run_id}/outputs/{relative}",
            source=path,
            media_type=media_type,
            metadata={"role": "pipeline_output", "run_id": run_id, "filename": relative},
        )
        db.add(
            RunArtifact(
                run_id=run_id,
                object_id=stored.id,
                role="pipeline_output",
                filename=relative,
            )
        )
        if relative == "extraction_audit.json":
            paper.latest_audit_object_id = stored.id


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

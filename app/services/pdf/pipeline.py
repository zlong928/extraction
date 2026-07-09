from __future__ import annotations

import logging
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import DATA_DIR
from app.models import Paper, PaperStatus
from app.services.agent.llm_client import LLMClient
from app.services.extraction.llm_config import build_vlm_config
from app.services.pdf.audit import _audit_summary_from_path
from content_pipeline import run_content_pipeline
from content_pipeline.contracts.audit import ExtractionPipelineOptions

logger = logging.getLogger(__name__)


def prepare_chart_only_run_for_paper(paper: Paper) -> None:
    output_dir = DATA_DIR / "content_pipeline_results" / f"paper_{paper.id}"
    output_dir.mkdir(parents=True, exist_ok=True)
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


def run_chart_only_for_paper(paper: Paper) -> dict[str, Any]:
    if not paper.mineru_content_list_path:
        raise ValueError("Paper has no MinerU content_list path.")
    content_list = Path(paper.mineru_content_list_path)
    if not content_list.is_file():
        raise ValueError("MinerU content_list file not found.")
    extract_dir = Path(paper.mineru_extract_dir or content_list.parent)
    image_root = extract_dir / "images" if (extract_dir / "images").is_dir() else extract_dir
    layout_path = extract_dir / "layout.json"
    output_dir = DATA_DIR / "content_pipeline_results" / f"paper_{paper.id}"
    run_dir = _content_pipeline_temp_dir(output_dir)
    if run_dir.exists():
        shutil.rmtree(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    llm_workers = max(1, int(os.getenv("CONTENT_PIPELINE_LLM_WORKERS", "3")))
    try:
        result, summary = run_content_pipeline(
            content_list_path=content_list,
            layout_path=layout_path if layout_path.is_file() else None,
            image_root=image_root,
            paper_id=f"paper-{paper.id}",
            use_llm=True,
            output_dir=run_dir,
            options=ExtractionPipelineOptions(
                fail_fast=False,
                max_workers=llm_workers,
                llm_max_workers=llm_workers,
                chart_only=_content_pipeline_chart_only_enabled(),
            ),
        )
        _promote_chart_only_run(run_dir, output_dir)
    except Exception:
        shutil.rmtree(run_dir, ignore_errors=True)
        raise
    audit_summary = _audit_summary_from_path(output_dir / "extraction_audit.json") or {}
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


def _content_pipeline_temp_dir(output_dir: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    return output_dir.with_name(f"{output_dir.name}.run-{stamp}-{os.getpid()}")


def _content_pipeline_chart_only_enabled() -> bool:
    value = os.getenv("CONTENT_PIPELINE_CHART_ONLY")
    if value is None:
        return True
    return value.strip().lower() not in {"0", "false", "no", "off"}


def _promote_chart_only_run(run_dir: Path, output_dir: Path) -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    previous_dir = output_dir.with_name(f"{output_dir.name}.previous-{stamp}-{os.getpid()}")
    if output_dir.exists():
        output_dir.rename(previous_dir)
    try:
        run_dir.rename(output_dir)
    except Exception:
        if output_dir.exists():
            shutil.rmtree(output_dir, ignore_errors=True)
        if previous_dir.exists():
            previous_dir.rename(output_dir)
        raise
    shutil.rmtree(previous_dir, ignore_errors=True)

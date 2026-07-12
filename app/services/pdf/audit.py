from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from app.config import DATA_DIR
from app.models import Paper, PaperStatus
from app.services.storage import StorageService

logger = logging.getLogger(__name__)


def audit_summary_for_title(title: str | None) -> dict[str, Any] | None:
    audit = _find_audit_file_for_title(title or "")
    if audit is None:
        return None
    return _audit_summary_from_path(audit)


def _audit_summary_from_path(audit: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(audit.read_text(encoding="utf-8"))
    except Exception:
        return None

    return _audit_summary_from_data(data, audit_ref=str(audit))


def _audit_summary_from_data(data: dict[str, Any], *, audit_ref: str) -> dict[str, Any]:
    panel_count = _int_at(data, "figure_panel_graph", "panel_count")
    figure_count = _int_at(data, "figure_panel_graph", "figure_count")
    semantic_done = len(data.get("panel_semantic_results") or []) if isinstance(
        data.get("panel_semantic_results"), list
    ) else 0
    metric_rows = len(data.get("metric_rows") or []) if isinstance(data.get("metric_rows"), list) else 0
    metric_candidates = len(data.get("metric_candidates") or []) if isinstance(
        data.get("metric_candidates"), list
    ) else 0
    rejected_rows = len(data.get("rejected_metric_rows") or []) if isinstance(
        data.get("rejected_metric_rows"), list
    ) else 0
    observations = len(data.get("image_observations") or []) if isinstance(
        data.get("image_observations"), list
    ) else 0
    digitized = len(data.get("chart_digitization_results") or []) if isinstance(
        data.get("chart_digitization_results"), list
    ) else 0
    chart_facts = len(
        data.get("chart_facts") or data.get("panel_fact_rows") or data.get("chart_points") or []
    )
    chart_points = chart_facts
    explicit_errors = len(data.get("errors") or []) if isinstance(data.get("errors"), list) else 0
    failure_events = _audit_failure_count(data)
    errors = explicit_errors + failure_events
    total = panel_count or semantic_done or 0
    if total and semantic_done >= total:
        progress = 100
    elif total:
        progress = int((semantic_done / total) * 100)
    else:
        progress = 0
    result_state = "benchmark_metrics_ready" if metric_rows else "chart_facts_ready" if chart_facts else "semantic_only"
    if errors and not (metric_rows or chart_points or observations or digitized):
        result_state = "pipeline_errors"
    return {
        "audit_path": audit_ref,
        "figure_count": figure_count,
        "panel_count": panel_count,
        "processed_panels": semantic_done,
        "progress_percent": progress,
        "metric_rows": metric_rows,
        "benchmark_metrics": metric_rows,
        "metric_candidates": metric_candidates,
        "rejected_metric_rows": rejected_rows,
        "chart_facts": chart_facts,
        "chart_points": chart_points,
        "image_observations": observations,
        "digitization_results": digitized,
        "errors": errors,
        "failure_events": failure_events,
        "first_error": _first_audit_error(data),
        "result_state": result_state,
    }


def audit_summary_for_paper(paper: Paper) -> dict[str, Any] | None:
    if paper.status == PaperStatus.PROCESSING.value:
        running = _running_audit_summary_for_paper(
            paper.id,
            panel_count=len([asset for asset in paper.assets if asset.is_active]),
            figure_count=len(paper.figures),
        )
        if running:
            return running
        return {
            "audit_path": None,
            "figure_count": len(paper.figures),
            "panel_count": len([asset for asset in paper.assets if asset.is_active]),
            "processed_panels": 0,
            "progress_percent": 0,
            "metric_rows": 0,
            "benchmark_metrics": 0,
            "metric_candidates": 0,
            "rejected_metric_rows": 0,
            "chart_facts": 0,
            "chart_points": 0,
            "image_observations": 0,
            "digitization_results": 0,
            "errors": 0,
            "failure_events": 0,
            "first_error": None,
            "result_state": "running",
            "source": "running_events",
        }
    if paper.latest_audit_object is not None:
        try:
            data = json.loads(StorageService().get_bytes(paper.latest_audit_object.object_key))
            summary = _audit_summary_from_data(data, audit_ref=paper.latest_audit_object.uri)
            summary["source"] = "current_run"
            return summary
        except Exception:
            logger.exception("failed to read audit object for paper_id=%s", paper.id)
    current_run = DATA_DIR / "content_pipeline_results" / f"paper_{paper.id}" / "extraction_audit.json"
    if current_run.is_file():
        summary = _audit_summary_from_path(current_run)
        if summary:
            summary["source"] = "current_run"
            return summary
    historical = _find_historical_audit_for_paper(paper)
    if historical:
        summary = _audit_summary_from_path(historical)
        if summary:
            summary["source"] = "historical_audit"
            return summary
    return None


def audit_payload_for_paper(paper: Paper) -> tuple[dict[str, Any] | None, str | None, str | None]:
    if paper.latest_audit_object is not None:
        try:
            payload = json.loads(StorageService().get_bytes(paper.latest_audit_object.object_key))
            return payload, "current_run", paper.latest_audit_object.uri
        except Exception:
            logger.exception("failed to read audit table object for paper_id=%s", paper.id)
    path, source = audit_table_path_for_paper(paper)
    if path is None:
        return None, source, None
    try:
        return json.loads(path.read_text(encoding="utf-8")), source, str(path)
    except Exception:
        return None, source, str(path)


def audit_table_path_for_paper(paper: Paper) -> tuple[Path | None, str | None]:
    current_run = DATA_DIR / "content_pipeline_results" / f"paper_{paper.id}" / "extraction_audit.json"
    if current_run.is_file() and _audit_path_has_trusted_table_outputs(current_run):
        return current_run, "current_run"
    historical = _find_historical_audit_for_paper(paper)
    if historical and _audit_path_has_trusted_table_outputs(historical):
        return historical, "historical_audit"
    if current_run.is_file():
        return current_run, "current_run"
    if historical:
        return historical, "historical_audit"
    return None, None


def _running_audit_summary_for_paper(
    paper_id: int, *, panel_count: int = 0, figure_count: int = 0
) -> dict[str, Any] | None:
    events_path = DATA_DIR / "content_pipeline_results" / f"paper_{paper_id}" / "extraction_audit_events.jsonl"
    if not events_path.is_file():
        return None
    event_count = 0
    panel_ids: set[str] = set()
    errors = 0
    chart_facts = 0
    metric_candidates = 0
    benchmark_metrics = 0
    image_observations = 0
    digitization_results = 0
    first_error: str | None = None
    last_event: str | None = None
    try:
        with events_path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                event_count += 1
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                panel_id = event.get("panel_id")
                if isinstance(panel_id, str) and panel_id:
                    panel_ids.add(panel_id)
                event_name = event.get("event") or event.get("phase_name")
                if isinstance(event_name, str) and event_name:
                    last_event = event_name
                if event_name == "chart_facts_extracted":
                    chart_facts = max(chart_facts, int(event.get("fact_count") or 0))
                elif event_name == "metric_candidates_created":
                    metric_candidates += int(event.get("candidate_count") or 0)
                elif event_name == "benchmark_metrics_accepted":
                    benchmark_metrics += 1
                elif event_name == "image_observation_completed":
                    image_observations += int(event.get("observation_count") or 0)
                elif event_name == "chart_digitization_completed":
                    digitization_results += 1
                message = event.get("message")
                if event.get("exception_type") or (isinstance(event_name, str) and "failed" in event_name):
                    errors += 1
                    if first_error is None and isinstance(message, str):
                        first_error = message
    except OSError:
        return None
    processed = len(panel_ids)
    total = max(panel_count, processed)
    progress = int((processed / total) * 100) if total else 0
    return {
        "audit_path": str(events_path),
        "figure_count": figure_count,
        "panel_count": total,
        "processed_panels": processed,
        "progress_percent": progress,
        "metric_rows": benchmark_metrics,
        "benchmark_metrics": benchmark_metrics,
        "metric_candidates": metric_candidates,
        "rejected_metric_rows": 0,
        "chart_facts": chart_facts,
        "chart_points": chart_facts,
        "image_observations": image_observations,
        "digitization_results": digitization_results,
        "errors": errors,
        "failure_events": errors,
        "first_error": first_error,
        "result_state": f"running: {last_event or event_count} events",
        "source": "running_events",
    }


def _find_historical_audit_for_paper(paper: Paper) -> Path | None:
    candidates: list[str] = []
    if paper.mineru_content_list_path:
        try:
            path = Path(paper.mineru_content_list_path)
            parts = path.resolve().relative_to(
                (DATA_DIR / "pipeline_batch").resolve()
            ).parts
            if parts:
                candidates.append(parts[0].replace("_", " ").replace("-", " ", 1))
        except Exception:
            candidates.append(Path(paper.mineru_content_list_path).parent.name)
    candidates.extend([paper.title, paper.original_filename])
    for candidate in candidates:
        audit = _find_audit_file_for_title(candidate or "")
        if audit:
            return audit
    return None


def _find_audit_file_for_title(title: str) -> Path | None:
    root = DATA_DIR / "content_pipeline_results"
    if not root.is_dir():
        return None
    wanted = _norm_audit_key(title)
    if not wanted:
        return None
    best: tuple[int, int, Path] | None = None
    for audit in root.rglob("extraction_audit.json"):
        if _audit_path_is_fake(audit):
            continue
        key = _norm_audit_key(audit.parent.name)
        score = 0
        if wanted and wanted in key:
            score = len(wanted)
        elif key and key in wanted:
            score = len(key)
        if not score:
            continue
        quality = _audit_quality_score(audit)
        if quality <= 0:
            continue
        if best is None or score > best[0] or (score == best[0] and quality > best[1]):
            best = (score, quality, audit)
    return best[2] if best else None


def _norm_audit_key(value: str) -> str:
    text = value.lower().replace("_", " ")
    text = re.sub(r"^\d{2}\s+(?=\d{3})", "", text)
    text = re.sub(r"[^a-z0-9]+", "", text)
    return text


def _int_at(data: dict[str, Any], *keys: str) -> int:
    current: Any = data
    for key in keys:
        if not isinstance(current, dict):
            return 0
        current = current.get(key)
    return int(current) if isinstance(current, int) else 0


def _audit_path_has_trusted_table_outputs(audit: Path) -> bool:
    if _audit_path_is_fake(audit):
        return False
    try:
        data = json.loads(audit.read_text(encoding="utf-8"))
    except Exception:
        return False
    if _audit_data_is_test_like(data):
        return False
    return bool(
        data.get("metric_rows")
        or data.get("chart_facts")
        or data.get("panel_fact_rows")
        or data.get("chart_points")
        or data.get("chart_digitization_results")
        or data.get("image_observations")
    )


def _audit_path_is_fake(audit: Path) -> bool:
    text = audit.as_posix().lower()
    return "/batch_fake" in text or "_fake_" in text or "/fake_" in text or text.endswith("_fake/extraction_audit.json")


def _audit_quality_score(audit: Path) -> int:
    try:
        data = json.loads(audit.read_text(encoding="utf-8"))
    except Exception:
        return 0
    if _audit_data_is_test_like(data):
        return 0
    return (
        len(data.get("metric_rows") or []) * 3
        + len(data.get("chart_facts") or data.get("panel_fact_rows") or data.get("chart_points") or []) * 2
        + len(data.get("image_observations") or [])
    )


def _audit_data_is_test_like(data: dict[str, Any]) -> bool:
    points = data.get("chart_points") or []
    if not isinstance(points, list) or not points:
        return False
    suspicious = 0
    for point in points:
        if not isinstance(point, dict):
            continue
        series = str(point.get("series_name") or "").lower()
        x_unit = str(point.get("x_unit") or "").lower()
        y_unit = str(point.get("y_unit") or "").lower()
        x_axis = str(point.get("x_axis_label") or "").lower()
        y_axis = str(point.get("y_axis_label") or "").lower()
        if (
            "test series" in series
            or (x_unit == "day" and y_unit == "g" and x_axis == "time" and "water uptake" in y_axis)
        ):
            suspicious += 1
    return suspicious > 0 and suspicious == len([p for p in points if isinstance(p, dict)])


def _audit_failure_count(data: dict[str, Any]) -> int:
    trace = data.get("audit_trace") or []
    if not isinstance(trace, list):
        return 0
    count = 0
    for item in trace:
        if not isinstance(item, dict):
            continue
        event = str(item.get("event") or "")
        if item.get("exception_type") or event.endswith("_failed") or event == "all_panel_extraction_failed":
            count += 1
    return count


def _first_audit_error(data: dict[str, Any]) -> str | None:
    explicit = data.get("errors") or []
    if isinstance(explicit, list):
        for item in explicit:
            if isinstance(item, dict) and item.get("message"):
                return str(item.get("message"))[:300]
            if isinstance(item, str) and item:
                return item[:300]
    trace = data.get("audit_trace") or []
    if isinstance(trace, list):
        for item in trace:
            if not isinstance(item, dict):
                continue
            if item.get("exception_type") or str(item.get("event") or "").endswith("_failed"):
                message = str(item.get("message") or item.get("event") or item.get("exception_type") or "")
                return message[:300] if message else None
    return None

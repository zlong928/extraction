from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Any, Iterable

from content_pipeline.contracts.metric_contract import ProjectedMetricRow
from content_pipeline.contracts.panel_facts import PanelFactRow


METRIC_CSV_FIELDS = [
    "source_image",
    "material_or_matrix",
    "biological_agent",
    "application_task",
    "assay",
    "metric_name",
    "metric_category",
    "target",
    "condition",
    "comparison",
    "value",
    "value_min",
    "value_max",
    "unit",
    "value_type",
    "direction",
]

CHART_FACT_CSV_FIELDS = [
    "fact_id",
    "paper_id",
    "figure_id",
    "panel_id",
    "source_image",
    "chart_type",
    "series_name",
    "point_index",
    "x_label",
    "x_unit",
    "x_value",
    "y_label",
    "y_unit",
    "y_value",
    "z_label",
    "z_unit",
    "z_value",
    "scale_factor",
    "category_label",
    "confidence",
    "digitization_status",
    "needs_review",
    "source_phase",
    "warnings",
    "errors",
    "value_source",
    "evidence_ids",
]

HEATMAP_CANDIDATE_CSV_FIELDS = [
    "candidate_id",
    "paper_id",
    "figure_id",
    "panel_id",
    "source_image",
    "metric_name",
    "series",
    "condition",
    "value",
    "value_min",
    "value_max",
    "unit",
    "scale_factor",
    "evidence_type",
    "confidence",
    "needs_review",
    "source_phase",
    "evidence_ids",
]

IMAGE_FACT_CSV_FIELDS = [
    "paper_id", "figure_id", "panel_id", "source_image", "image_kind",
    "observation_name", "target_entity", "qualitative_value", "numeric_value",
    "unit", "condition", "method", "confidence", "needs_verification",
    "review_status", "review_reason", "evidence_ids",
]


def write_metric_csv(path: str | Path, rows: Iterable[ProjectedMetricRow]) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=METRIC_CSV_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow(public_metric_csv_dict(row))
    return out


def write_chart_fact_csv(path: str | Path, rows: Iterable[PanelFactRow]) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=CHART_FACT_CSV_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row.csv_dict())
    return out


def write_panel_fact_csv(path: str | Path, rows: Iterable[PanelFactRow]) -> Path:
    return write_chart_fact_csv(path, rows)


def write_heatmap_candidate_csv(
    path: str | Path,
    rows: Iterable[dict[str, Any]],
    *,
    image_by_panel: dict[str, str] | None = None,
) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    image_by_panel = image_by_panel or {}
    with out.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=HEATMAP_CANDIDATE_CSV_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow(_heatmap_candidate_csv_dict(row, image_by_panel))
    return out


def write_chart_fact_tables(output_dir: str | Path, rows: Iterable[PanelFactRow]) -> dict[str, str]:
    grouped: dict[str, list[PanelFactRow]] = {}
    for row in rows:
        grouped.setdefault(row.panel_id or "unknown_panel", []).append(row)
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, str] = {}
    for panel_id, panel_rows in sorted(grouped.items()):
        path = write_chart_fact_csv(out_dir / f"{_safe_panel_filename(panel_id)}.csv", panel_rows)
        paths[panel_id] = str(path)
    return paths


def write_image_fact_csv(path: str | Path, rows: Iterable[Any]) -> Path:
    """Write ImageObservation rows to CSV. Each row must have csv_dict()."""
    from content_pipeline.contracts.visual import ImageObservation
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=IMAGE_FACT_CSV_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row.csv_dict())
    return out


def write_image_fact_tables(output_dir: str | Path, rows: Iterable[Any]) -> dict[str, str]:
    """Write per-panel ImageObservation CSV files, grouped by panel_id."""
    from content_pipeline.contracts.visual import ImageObservation
    grouped: dict[str, list[Any]] = {}
    for row in rows:
        grouped.setdefault(row.panel_id or "unknown_panel", []).append(row)
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, str] = {}
    for panel_id, panel_rows in sorted(grouped.items()):
        path = write_image_fact_csv(out_dir / f"{_safe_panel_filename(panel_id)}.csv", panel_rows)
        paths[panel_id] = str(path)
    return paths


def write_heatmap_candidate_tables(
    output_dir: str | Path,
    rows: Iterable[dict[str, Any]],
    *,
    image_by_panel: dict[str, str] | None = None,
) -> dict[str, str]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row.get("panel_id") or "unknown_panel"), []).append(row)
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, str] = {}
    for panel_id, panel_rows in sorted(grouped.items()):
        path = write_heatmap_candidate_csv(
            out_dir / f"{_safe_panel_filename(panel_id)}.csv",
            panel_rows,
            image_by_panel=image_by_panel,
        )
        paths[panel_id] = str(path)
    return paths


def write_panel_fact_tables(output_dir: str | Path, rows: Iterable[PanelFactRow]) -> dict[str, str]:
    return write_chart_fact_tables(output_dir, rows)


def public_metric_csv_dict(row: ProjectedMetricRow) -> dict[str, str]:
    data = row.csv_dict()
    return {
        field: _source_image(row) if field == "source_image" else data.get(field, "")
        for field in METRIC_CSV_FIELDS
    }


def _heatmap_candidate_csv_dict(row: dict[str, Any], image_by_panel: dict[str, str]) -> dict[str, str]:
    return {
        field: _csv_cell(
            image_by_panel.get(str(row.get("panel_id") or ""), "") or row.get("source_image", "")
            if field == "source_image"
            else row.get(field, "")
        )
        for field in HEATMAP_CANDIDATE_CSV_FIELDS
    }


def _csv_cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        import json
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _source_image(row: ProjectedMetricRow) -> str:
    figure_id = str(row.figure_id or "").strip()
    panel_id = str(row.panel_id or row.source_panel_id or "").strip()
    figure_label = _human_figure_label(figure_id) or _human_figure_label(panel_id)
    if not figure_label:
        return panel_id or figure_id
    panel_label = _panel_suffix(figure_id, panel_id)
    return f"{figure_label} ({panel_label})" if panel_label else figure_label


def _human_figure_label(value: str) -> str:
    if not value:
        return ""
    match = re.search(r"(?i)\b(?:fig(?:ure)?)[-_ ]*(\d+[a-z]?)\b", value)
    if match:
        return f"Figure {match.group(1)}"
    match = re.search(r"\b(\d+)\b", value)
    if match:
        return f"Figure {match.group(1)}"
    return value


def _panel_suffix(figure_id: str, panel_id: str) -> str:
    if not panel_id:
        return ""
    suffix = ""
    if figure_id and panel_id.startswith(figure_id):
        suffix = panel_id[len(figure_id):]
    elif figure_id:
        figure_label = _human_figure_label(figure_id).lower().replace(" ", "-")
        normalized_panel = panel_id.lower()
        if normalized_panel.startswith(figure_label):
            suffix = panel_id[len(figure_label):]
    if not suffix:
        match = re.search(r"(?i)(?:^|[-_ ])(?:panel[-_ ]*)?([a-z])$", panel_id)
        suffix = match.group(1) if match else ""
    suffix = suffix.strip("-_ ()")
    return suffix if suffix and suffix.lower() not in {"p1", "panel1"} else ""


def _safe_panel_filename(panel_id: str) -> str:
    safe = re.sub(r"[\\/]+", "_", panel_id.strip())
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", safe)
    return safe or "unknown_panel"

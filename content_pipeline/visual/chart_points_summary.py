from __future__ import annotations

from collections import defaultdict
from typing import Any

from content_pipeline.contracts.semantic import PanelSemanticResult
from content_pipeline.contracts.visual import ChartDigitizationResult, ChartPoint


def summarize_chart_digitization_results(
    results: list[ChartDigitizationResult],
    semantic_by_panel: dict[str, PanelSemanticResult] | None = None,
) -> dict[str, dict[str, Any]]:
    """Build compact per-panel chart summaries for metric extraction.

    The metric extractor should not receive the full point table. It only needs enough
    structure to derive benchmark summaries without re-reading chart coordinates.
    """
    return {
        result.panel_id: summarize_chart_result(result, panel_semantic=(semantic_by_panel or {}).get(result.panel_id))
        for result in results
    }


def summarize_chart_result(result: ChartDigitizationResult, panel_semantic: PanelSemanticResult | None = None) -> dict[str, Any]:
    points = list(result.points)
    series_summaries = [_series_summary(name, series_points) for name, series_points in _points_by_series(points).items()]
    evidence_ids = list(dict.fromkeys(eid for point in points for eid in point.evidence_ids)) or list(result.evidence_ids)
    summary = {
        "paper_id": result.paper_id,
        "figure_id": result.figure_id,
        "panel_id": result.panel_id,
        "chart_type": result.chart_type,
        "digitization_status": result.digitization_status,
        "axis_readability": result.axis_readability,
        "legend_readability": result.legend_readability,
        "calibration_status": result.calibration_status,
        "x_axis": {
            "label": result.x_axis.label,
            "unit": result.x_axis.unit,
            "scale": result.x_axis.scale,
        },
        "y_axis": {
            "label": result.y_axis.label,
            "unit": result.y_axis.unit,
            "scale": result.y_axis.scale,
        },
        "point_count": len(points),
        "data_point_count": len(points),
        "series_count": len(series_summaries),
        "series": series_summaries[:8],
        "needs_verification": result.needs_verification or any(point.needs_verification for point in points),
        "extraction_method": result.extraction_method,
        "extraction_confidence": result.extraction_confidence,
        "warnings": list(result.warnings)[:12],
        "evidence_ids": evidence_ids[:8],
    }
    return summary


def _points_by_series(points: list[ChartPoint]) -> dict[str, list[ChartPoint]]:
    grouped: dict[str, list[ChartPoint]] = defaultdict(list)
    for point in points:
        grouped[point.series_name or "series"].append(point)
    return dict(grouped)


def _series_summary(series_name: str, points: list[ChartPoint]) -> dict[str, Any]:
    ordered = sorted(points, key=lambda p: (p.x_value is None, p.x_value if p.x_value is not None else p.point_index, p.point_index))
    numeric_y = [point for point in ordered if point.y_value is not None]
    first = numeric_y[0] if numeric_y else (ordered[0] if ordered else None)
    final = numeric_y[-1] if numeric_y else (ordered[-1] if ordered else None)
    y_values = [point.y_value for point in numeric_y if point.y_value is not None]
    min_point = min(numeric_y, key=lambda point: point.y_value if point.y_value is not None else float("inf")) if numeric_y else None
    max_point = max(numeric_y, key=lambda point: point.y_value if point.y_value is not None else float("-inf")) if numeric_y else None
    trend = ""
    if first and final and first.y_value is not None and final.y_value is not None and first.y_value != final.y_value:
        trend = "increased" if final.y_value > first.y_value else "decreased"
    return {
        "series_name": series_name,
        "point_count": len(ordered),
        "first_point": _point_summary(first),
        "final_point": _point_summary(final),
        "min_point": _point_summary(min_point),
        "max_point": _point_summary(max_point),
        "key_points": _unique_point_summaries([first, min_point, max_point, final]),
        "first_chart_point_id": first.chart_point_id if first else "",
        "final_chart_point_id": final.chart_point_id if final else "",
        "y_min": min(y_values) if y_values else None,
        "y_max": max(y_values) if y_values else None,
        "trend": trend,
        "needs_verification": any(point.needs_verification for point in ordered),
        "evidence_ids": list(dict.fromkeys(eid for point in ordered for eid in point.evidence_ids))[:6],
    }


def _unique_point_summaries(points: list[ChartPoint | None]) -> list[dict[str, Any]]:
    keyed: dict[str, dict[str, Any]] = {}
    for point in points:
        if point is None:
            continue
        key = point.chart_point_id or f"{point.series_name}:{point.point_index}:{point.x_value}:{point.y_value}"
        keyed.setdefault(key, _point_summary(point) or {})
    return [summary for summary in keyed.values() if summary]


def _point_summary(point: ChartPoint | None) -> dict[str, Any] | None:
    if point is None:
        return None
    return {
        "chart_point_id": point.chart_point_id,
        "point_index": point.point_index,
        "x_value": point.x_value,
        "x_unit": point.x_unit,
        "x_axis_label": point.x_axis_label,
        "y_value": point.y_value,
        "y_unit": point.y_unit,
        "y_axis_label": point.y_axis_label,
        "category_label": point.category_label,
        "confidence": point.confidence,
        "needs_verification": point.needs_verification,
        "evidence_ids": list(point.evidence_ids),
    }

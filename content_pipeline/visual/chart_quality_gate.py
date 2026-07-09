from __future__ import annotations

from collections import Counter
from typing import Any

from content_pipeline.contracts.visual import ChartAxis, ChartDigitizationResult, ChartPoint


def apply_chart_quality_gate(result: ChartDigitizationResult) -> ChartDigitizationResult:
    warnings = list(result.warnings)
    _derive_result_axes_from_points(result)
    _propagate_result_axes_to_points(result)

    result_reasons = _chart_quality_reasons(result)
    if result_reasons:
        result.needs_verification = True
        result.extraction_confidence = min(result.extraction_confidence or 0.0, _confidence_cap(result_reasons))
        warnings.extend(result_reasons)

    for point in result.points:
        point_reasons = [*_point_quality_reasons(point, result), *_point_source_reasons(point, result)]
        if not point_reasons:
            continue
        point.needs_verification = True
        point.confidence = min(point.confidence, _confidence_cap(point_reasons))
        point.review_reason = _append_reason(point.review_reason, ";".join(point_reasons))
        point.review_status = "review_required"
        warnings.extend(point_reasons)

    if not result.points and result.digitization_status in {"digitized", "partially_digitized"}:
        result.needs_verification = True
        result.extraction_confidence = min(result.extraction_confidence or 0.0, 0.4)
        warnings.append("digitized_status_without_valid_points")

    result.warnings = list(dict.fromkeys(warnings))
    return result


def _derive_result_axes_from_points(result: ChartDigitizationResult) -> None:
    if not result.x_axis.label:
        result.x_axis.label = _most_common_nonempty(point.x_axis_label for point in result.points)
    if not result.x_axis.unit:
        result.x_axis.unit = _most_common_nonempty(point.x_unit for point in result.points)
    if result.x_axis.scale in {"", "unknown"}:
        result.x_axis.scale = _derived_axis_scale(_most_common_nonempty(point.x_axis_scale for point in result.points), result.x_axis, result.calibration_status)
    if not result.y_axis.label:
        result.y_axis.label = _most_common_nonempty(point.y_axis_label for point in result.points)
    if not result.y_axis.unit:
        result.y_axis.unit = _most_common_nonempty(point.y_unit for point in result.points)
    if result.y_axis.scale in {"", "unknown"}:
        result.y_axis.scale = _derived_axis_scale(_most_common_nonempty(point.y_axis_scale for point in result.points), result.y_axis, result.calibration_status)
    if result.y2_axis is not None:
        if not result.y2_axis.unit:
            result.y2_axis.unit = _most_common_nonempty(point.y2_unit for point in result.points)
        if result.y2_axis.scale in {"", "unknown"}:
            result.y2_axis.scale = _derived_axis_scale("", result.y2_axis, result.calibration_status)


def _propagate_result_axes_to_points(result: ChartDigitizationResult) -> None:
    for point in result.points:
        if not point.x_axis_label and result.x_axis.label:
            point.x_axis_label = result.x_axis.label
        if not point.x_unit and result.x_axis.unit:
            point.x_unit = result.x_axis.unit
        if point.x_axis_scale in {"", "unknown"} and result.x_axis.scale:
            point.x_axis_scale = result.x_axis.scale
        if not point.y_axis_label and result.y_axis.label:
            point.y_axis_label = result.y_axis.label
        if not point.y_unit and result.y_axis.unit:
            point.y_unit = result.y_axis.unit
        if point.y_axis_scale in {"", "unknown"} and result.y_axis.scale:
            point.y_axis_scale = result.y_axis.scale
        if result.y2_axis is not None and not point.y2_unit and result.y2_axis.unit:
            point.y2_unit = result.y2_axis.unit


def _chart_quality_reasons(result: ChartDigitizationResult) -> list[str]:
    reasons: list[str] = []
    if result.digitization_status in {"axis_unreadable", "legend_unreadable", "too_low_resolution", "failed", "unknown"}:
        reasons.append(f"digitization_status_{result.digitization_status or 'unknown'}")
    if result.axis_readability in {"unreadable", "partially_readable", "unknown"}:
        reasons.append(f"axis_readability_{result.axis_readability or 'unknown'}")
    if result.calibration_status in {"unavailable", "unknown"}:
        reasons.append(f"calibration_status_{result.calibration_status or 'unknown'}")
    if result.points:
        if not result.x_axis.label:
            reasons.append("missing_x_axis_label")
        if not result.y_axis.label and any(point.y_value is not None for point in result.points):
            reasons.append("missing_y_axis_label")
        if not result.x_axis.unit and not any(point.category_label for point in result.points):
            reasons.append("missing_x_axis_unit")
        if not result.y_axis.unit and any(point.y_value is not None for point in result.points):
            reasons.append("missing_y_axis_unit")
        if result.x_axis.scale in {"", "unknown"} and not any(point.category_label for point in result.points):
            reasons.append("missing_x_axis_scale")
        if result.y_axis.scale in {"", "unknown"} and any(point.y_value is not None for point in result.points):
            reasons.append("missing_y_axis_scale")
        if _uses_y2_axis(result):
            if result.y2_axis is None:
                reasons.append("missing_y2_axis")
            else:
                if not result.y2_axis.label:
                    reasons.append("missing_y2_axis_label")
                if not result.y2_axis.unit:
                    reasons.append("missing_y2_axis_unit")
                if result.y2_axis.scale in {"", "unknown"}:
                    reasons.append("missing_y2_axis_scale")
    return list(dict.fromkeys(reasons))


def _point_quality_reasons(point: ChartPoint, result: ChartDigitizationResult) -> list[str]:
    reasons: list[str] = []
    if point.x_value is not None and not point.x_axis_label and not point.category_label:
        reasons.append("missing_x_axis_label")
    if point.y_value is not None and not point.y_axis_label:
        reasons.append("missing_y_axis_label")
    if point.x_value is not None and not point.x_unit and not point.category_label:
        reasons.append("missing_x_axis_unit")
    if point.y_value is not None and not point.y_unit:
        reasons.append("missing_y_axis_unit")
    if point.x_value is not None and point.x_axis_scale in {"", "unknown"} and result.calibration_status != "category_only":
        reasons.append("missing_x_axis_scale")
    if point.y_value is not None and point.y_axis_scale in {"", "unknown"}:
        reasons.append("missing_y_axis_scale")
    if point.y2_value is not None and not point.y2_unit:
        reasons.append("missing_y2_axis_unit")
    return list(dict.fromkeys(reasons))


def _point_source_reasons(point: ChartPoint, result: ChartDigitizationResult) -> list[str]:
    if not str(result.extraction_method or "").startswith("llm_visual_digitization"):
        return []
    exact_sources = {"table_exact", "caption_exact", "text_exact"}
    if str(point.value_source or "").strip() in exact_sources:
        return []
    return ["visual_digitization_needs_verification"]


def _derived_axis_scale(candidate: str, axis: ChartAxis, calibration_status: str) -> str:
    if candidate and candidate != "unknown":
        return candidate
    if calibration_status == "category_only":
        return "categorical"
    if axis.tick_values or axis.range_min is not None or axis.range_max is not None or calibration_status == "calibrated_from_ticks":
        return "linear"
    return "unknown"


def _confidence_cap(reasons: list[str]) -> float:
    reason_set = set(reasons)
    if "missing_x_axis_label" in reason_set and "missing_y_axis_label" in reason_set:
        return 0.55
    if any(reason.startswith("digitization_status_") for reason in reasons):
        return 0.55
    if any(reason.startswith("axis_readability_") for reason in reasons):
        return 0.6
    if any(reason.startswith("calibration_status_") for reason in reasons):
        return 0.6
    if "missing_x_axis_label" in reason_set or "missing_y_axis_label" in reason_set:
        return 0.6
    if "missing_x_axis_unit" in reason_set or "missing_y_axis_unit" in reason_set:
        return 0.65
    if "missing_x_axis_scale" in reason_set or "missing_y_axis_scale" in reason_set:
        return 0.65
    return 0.65


def _uses_y2_axis(result: ChartDigitizationResult) -> bool:
    return result.y2_axis is not None or any(point.y2_value is not None for point in result.points)


def _append_reason(existing: str, reason: str) -> str:
    parts = [part for part in [existing, reason] if part]
    return ";".join(dict.fromkeys(part for chunk in parts for part in chunk.split(";") if part))


def _most_common_nonempty(values: Any) -> str:
    counts = Counter(str(value).strip() for value in values if str(value or "").strip() and str(value).strip() != "unknown")
    if not counts:
        return ""
    return counts.most_common(1)[0][0]

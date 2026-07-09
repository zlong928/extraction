from __future__ import annotations

import copy
from typing import Any

from content_pipeline.contracts.visual import (
    ChartAxis,
    ChartDigitizationResult,
    ChartPoint,
    ImageObservation,
    VisualExtractionContext,
    VisualFactCandidate,
    VisualFactExtractionResult,
)


def chart_digitization_from_payload(payload: dict[str, Any], context: VisualExtractionContext) -> ChartDigitizationResult:
    chart_type = _text(payload.get("chart_type") or context.chart_type_hint or "chart")
    x_axis = _axis(_axis_payload(payload, "x"))
    y_axis = _axis(_axis_payload(payload, "y"))
    _apply_axis_hint_fallbacks(x_axis=x_axis, y_axis=y_axis, payload=payload, context=context)
    y2_axis_payload = _axis_payload(payload, "y2")
    y2_axis = _axis(y2_axis_payload) if y2_axis_payload else None
    visual_evidence_ids = _visual_evidence_ids(context)
    evidence_ids = _chart_evidence_ids(payload.get("evidence_ids"), context, visual_evidence_ids)
    method = _text(payload.get("extraction_method") or "llm_visual_digitization")
    confidence = _float(payload.get("extraction_confidence") or payload.get("confidence"), 0.0) or 0.0
    needs_verification = _bool(payload.get("needs_verification"), default=True)
    digitization_status = _text(payload.get("digitization_status") or "unknown")
    axis_readability = _text(payload.get("axis_readability") or "unknown")
    legend_readability = _text(payload.get("legend_readability") or "unknown")
    calibration_status = _text(payload.get("calibration_status") or "unknown")
    raw_points = _dedupe_point_payloads(_iter_point_payloads(payload))
    series_names = _series_names(payload, raw_points)
    points: list[ChartPoint] = []
    warnings: list[str] = [_text(item) for item in _as_list(payload.get("warnings")) if _text(item)]
    for index, point_payload in enumerate(raw_points, start=1):
        point = _point_from_payload(
            point_payload,
            context=context,
            chart_type=chart_type,
            x_axis=x_axis,
            y_axis=y_axis,
            y2_axis=y2_axis,
            default_evidence_ids=evidence_ids,
            default_visual_evidence_ids=visual_evidence_ids,
            default_method=method,
            default_confidence=confidence,
            default_needs_verification=needs_verification,
            point_index=index,
        )
        if point is not None:
            points.append(point)
    raw_points_snapshot = [copy.deepcopy(point) for point in points]
    if not points:
        warnings.append("no_valid_chart_points")
    result = ChartDigitizationResult(
        paper_id=context.paper_id,
        figure_id=context.figure_id,
        panel_id=context.panel_id,
        chart_type=chart_type,
        digitization_status=digitization_status,
        axis_readability=axis_readability,
        legend_readability=legend_readability,
        calibration_status=calibration_status,
        x_axis=x_axis,
        y_axis=y_axis,
        y2_axis=y2_axis,
        series=series_names,
        points=points,
        extraction_method=method,
        extraction_confidence=confidence,
        needs_verification=needs_verification,
        warnings=list(dict.fromkeys(warnings)),
        evidence_ids=evidence_ids,
        heatmap_candidates=_heatmap_candidates(payload, context),
        raw_output=dict(payload),
        raw_points=raw_points_snapshot,
    )
    return validate_chart_points(result)


def image_observations_from_payload(payload: dict[str, Any], context: VisualExtractionContext) -> list[ImageObservation]:
    image_kind = _text(payload.get("image_kind") or context.image_kind_hint or "image")
    visual_facts = visual_fact_result_from_payload(payload, context)
    return image_observations_from_visual_fact_result(visual_facts, context, image_kind=image_kind)


def image_observations_from_visual_fact_result(
    result: VisualFactExtractionResult,
    context: VisualExtractionContext,
    *,
    image_kind: str = "",
) -> list[ImageObservation]:
    kind = _text(image_kind or context.image_kind_hint or "image")
    observations: list[ImageObservation] = []
    for candidate in result.visual_fact_candidates:
        observations.append(ImageObservation(
            paper_id=context.paper_id,
            figure_id=context.figure_id,
            panel_id=context.panel_id,
            image_kind=kind,
            observation_name=candidate.attribute_slot or candidate.fact_type,
            target_entity=candidate.subject_slot,
            qualitative_value=candidate.value_slot,
            condition=candidate.condition_slot,
            method="visual_fact_candidates_adapter",
            confidence=candidate.confidence,
            needs_verification=True,
            evidence_ids=candidate.evidence_ids,
            raw_output=dict(candidate.raw_output),
        ))
    return validate_image_observations(observations)


def visual_fact_result_from_payload(payload: dict[str, Any], context: VisualExtractionContext) -> VisualFactExtractionResult:
    default_evidence_ids = _valid_evidence_ids(payload.get("evidence_ids"), context) or _default_evidence_ids(context)
    segment_status = _caption_segment_status(context)
    candidates: list[VisualFactCandidate] = []
    for index, item in enumerate(_as_list(payload.get("visual_fact_candidates")), start=1):
        if not isinstance(item, dict):
            continue
        evidence_ids = _valid_evidence_ids(item.get("evidence_ids"), context) or default_evidence_ids
        fact_type = _text(item.get("fact_type") or "other_visual_fact")
        subject = _text(item.get("subject_slot") or item.get("subject") or item.get("target_entity"))
        attribute = _text(item.get("attribute_slot") or item.get("attribute") or item.get("observation_name"))
        value = _text(item.get("value_slot") or item.get("value") or item.get("qualitative_value"))
        if not fact_type or not (subject or attribute or value):
            continue
        candidates.append(VisualFactCandidate(
            fact_id=_text(item.get("fact_id")) or f"{context.panel_id}-vf-{index}",
            fact_type=fact_type,
            subject_slot=subject,
            attribute_slot=attribute,
            value_slot=value,
            comparator_slot=_text(item.get("comparator_slot") or item.get("comparator")),
            condition_slot=_text(item.get("condition_slot") or item.get("condition")),
            location_slot=_text(item.get("location_slot") or item.get("location")),
            evidence_ids=evidence_ids,
            visual_grounding=_dict(item.get("visual_grounding")) or {"image_ref": context.image_ref, "region": None},
            caption_segment_status=_text(item.get("caption_segment_status")) or segment_status,
            support_level=_text(item.get("support_level")) or _support_level(evidence_ids, context),
            confidence=_float(item.get("confidence") or payload.get("confidence"), 0.0) or 0.0,
            raw_output=dict(item),
        ))
    if not candidates and payload.get("observations"):
        for index, item in enumerate(_as_list(payload.get("observations")), start=1):
            if not isinstance(item, dict):
                continue
            name = _text(item.get("observation_name") or item.get("name") or item.get("type"))
            value = _text(item.get("qualitative_value") or item.get("value"))
            target = _text(item.get("target_entity") or item.get("entity") or item.get("target"))
            evidence_ids = _valid_evidence_ids(item.get("evidence_ids"), context) or default_evidence_ids
            if not evidence_ids or not (name or value or target):
                continue
            candidates.append(VisualFactCandidate(
                fact_id=f"{context.panel_id}-legacy-vf-{index}",
                fact_type="other_visual_fact",
                subject_slot=target,
                attribute_slot=name,
                value_slot=value,
                condition_slot=_text(item.get("condition")),
                evidence_ids=evidence_ids,
                visual_grounding={"image_ref": context.image_ref, "region": None},
                caption_segment_status=segment_status,
                support_level=_support_level(evidence_ids, context),
                confidence=_float(item.get("confidence") or payload.get("confidence"), 0.0) or 0.0,
                raw_output={**dict(item), "adapter_source": "legacy_observation"},
            ))
    return VisualFactExtractionResult(
        panel_id=context.panel_id,
        visual_fact_candidates=candidates,
        unsupported_claims=[item for item in _as_list(payload.get("unsupported_claims")) if isinstance(item, dict)],
        confidence=_float(payload.get("confidence"), 0.0) or 0.0,
        raw_output=dict(payload),
    )


def validate_chart_points(result: ChartDigitizationResult) -> ChartDigitizationResult:
    accepted: list[ChartPoint] = []
    warnings = list(result.warnings)
    for point in result.points:
        reasons = []
        if point.y_value is None and point.z_value is None:
            reasons.append("missing_y_or_z")
        if point.x_value is None and not point.category_label and point.z_value is None:
            warnings.append("missing_x_or_category")
        if not point.evidence_ids:
            reasons.append("missing_evidence_ids")
        if not 0.0 <= point.confidence <= 1.0:
            reasons.append("confidence_out_of_range")
        point.review_status = "review_required" if point.needs_verification else "reviewed"
        if point.needs_verification and not point.review_reason:
            point.review_reason = "visual_digitization_needs_verification"
        warnings.extend(reasons)
        accepted.append(point)
    result.points = accepted
    result.warnings = list(dict.fromkeys(warnings))
    return result


def _axis(raw: dict[str, Any]) -> ChartAxis:
    if isinstance(raw, dict):
        raw = {**raw, **_axis_from_label(str(raw.get("text") or ""))}
    else:
        raw = _axis_from_label(str(raw or ""))
    if not _text(raw.get("label")) and not _text(raw.get("name")) and _text(raw.get("title")):
        title_axis = _axis_from_label(str(raw.get("title") or ""))
        raw = {**raw, **{key: value for key, value in title_axis.items() if value or not _text(raw.get(key))}}
    range_values = raw.get("range") if isinstance(raw.get("range"), list) else []
    return ChartAxis(
        label=_text(raw.get("label") or raw.get("name")),
        unit=_text(raw.get("unit")),
        scale=_text(raw.get("scale") or raw.get("type") or "unknown"),
        range_min=_float(raw.get("range_min") if raw.get("range_min") is not None else raw.get("min") if raw.get("min") is not None else (range_values[0] if len(range_values) >= 1 else None)),
        range_max=_float(raw.get("range_max") if raw.get("range_max") is not None else raw.get("max") if raw.get("max") is not None else (range_values[1] if len(range_values) >= 2 else None)),
        tick_values=[v for v in (_float(item) for item in _as_list(raw.get("tick_values"))) if v is not None],
    calibration_confidence=_float(raw.get("calibration_confidence"), 0.0) or 0.0,
)


def _iter_point_payloads(payload: dict[str, Any]) -> list[dict[str, Any]]:
    points: list[dict[str, Any]] = []
    for point in _as_list(payload.get("data_points")):
        if not isinstance(point, dict):
            continue
        points.extend(_expand_point_payload(point))
    for series in _as_list(payload.get("series")):
        if not isinstance(series, dict):
            continue
        if not any(key in series for key in ("series_name", "name", "data_points", "points")):
            for nested_name, nested_series in series.items():
                if not isinstance(nested_series, dict):
                    continue
                for point in _as_list(nested_series.get("data_points") or nested_series.get("points")):
                    if not isinstance(point, dict):
                        continue
                    point = dict(point)
                    point.setdefault("series_name", nested_name)
                    points.extend(_expand_point_payload(point))
            continue
        series_name = series.get("series_name") or series.get("name") or ""
        for point in _as_list(series.get("data_points") or series.get("points")):
            if not isinstance(point, dict):
                continue
            point = dict(point)
            if series_name and not point.get("series_name"):
                point["series_name"] = series_name
            points.extend(_expand_point_payload(point))
    return points


def _expand_point_payload(point: dict[str, Any]) -> list[dict[str, Any]]:
    nested_points = point.get("points") or point.get("data_points")
    if isinstance(nested_points, list) and nested_points:
        series_name = _text(point.get("series_name") or point.get("series") or point.get("name"))
        expanded_nested: list[dict[str, Any]] = []
        for nested in nested_points:
            if not isinstance(nested, dict):
                continue
            next_point = dict(nested)
            if series_name and not next_point.get("series_name"):
                next_point["series_name"] = series_name
            expanded_nested.extend(_expand_point_payload(next_point))
        if expanded_nested:
            return expanded_nested
    values = point.get("values")
    if isinstance(values, dict) and values:
        group = _text(point.get("gene") or point.get("group") or point.get("series_name") or point.get("series"))
        expanded: list[dict[str, Any]] = []
        for category, value in values.items():
            next_point = {key: val for key, val in point.items() if key != "values"}
            next_point.setdefault("series_name", group)
            next_point.setdefault("category", category)
            next_point.setdefault("value", value)
            expanded.append(next_point)
        return expanded
    box_values = _boxplot_values(point)
    if box_values:
        series_name = _text(point.get("series_name") or point.get("series") or point.get("label") or point.get("category"))
        return [
            {
                **{key: val for key, val in point.items() if key not in {"min", "q1", "median", "q3", "max"}},
                "series_name": series_name,
                "category": stat_name,
                "value": stat_value,
                "curve_role": "boxplot_statistic",
            }
            for stat_name, stat_value in box_values
        ]
    return [point]


def _boxplot_values(point: dict[str, Any]) -> list[tuple[str, Any]]:
    if _text(point.get("type")).lower() not in {"boxplot", "box_plot", "box"}:
        return []
    values: list[tuple[str, Any]] = []
    for key in ("min", "q1", "median", "q3", "max"):
        if point.get(key) not in (None, ""):
            values.append((key, point.get(key)))
    return values


def _point_from_payload(
    point_payload: dict[str, Any],
    *,
    context: VisualExtractionContext,
    chart_type: str,
    x_axis: ChartAxis,
    y_axis: ChartAxis,
    y2_axis: ChartAxis | None,
    default_evidence_ids: list[str],
    default_visual_evidence_ids: list[str],
    default_method: str,
    default_confidence: float,
    default_needs_verification: bool,
    point_index: int,
) -> ChartPoint | None:
    x_alias = _axis_value_alias(point_payload, "x")
    y_alias = _axis_value_alias(point_payload, "y")
    x_value = _float(point_payload.get("x_value") if "x_value" in point_payload else point_payload.get("x") if "x" in point_payload else x_alias[0])
    y_value = _float(point_payload.get("y_value") if "y_value" in point_payload else point_payload.get("y") if "y" in point_payload else y_alias[0])
    raw_x = point_payload.get("x_value") if "x_value" in point_payload else point_payload.get("x") if "x" in point_payload else x_alias[0]
    category = _text(point_payload.get("category") or point_payload.get("category_label"))
    if not category and point_payload.get("label") is not None and raw_x in (None, ""):
        category = _text(point_payload.get("label"))
    if not category and raw_x in (None, "") and point_payload.get("value") is not None:
        category = _text(point_payload.get("series_name") or point_payload.get("series"))
    if not category and raw_x not in (None, "") and x_value is None:
        category = _text(raw_x)
    x_axis_label = _text(point_payload.get("x_axis_label") or x_alias[1] or x_axis.label)
    y_axis_label = _text(point_payload.get("y_axis_label") or y_alias[1] or y_axis.label)
    series_name = _text(point_payload.get("series_name") or point_payload.get("series") or "")
    evidence_ids = _chart_evidence_ids(point_payload.get("evidence_ids"), context, default_visual_evidence_ids) or default_evidence_ids
    confidence = _float(point_payload.get("confidence"), 0.0) or default_confidence
    needs_verification = _bool(point_payload.get("needs_verification"), default=default_needs_verification)
    if not category and raw_x in (None, "") and x_value is None and x_axis_label:
        category = x_axis_label
    x_unit = _text(point_payload.get("x_unit") or x_alias[2] or x_axis.unit)
    y_unit = _text(point_payload.get("y_unit") or point_payload.get("unit") or y_alias[2] or y_axis.unit)
    x_axis_scale = _text(point_payload.get("x_axis_scale") or x_axis.scale) or "unknown"
    y_axis_scale = _text(point_payload.get("y_axis_scale") or y_axis.scale) or "unknown"
    y2_value = _float(point_payload.get("y2_value") if "y2_value" in point_payload else point_payload.get("y2"))
    y2_unit = _text(point_payload.get("y2_unit") or (y2_axis.unit if y2_axis else ""))
    z_value = point_payload.get("z_value") if "z_value" in point_payload else point_payload.get("z")
    z_value = _float(z_value) if _float(z_value) is not None else _text(z_value)
    z_value = z_value if z_value != "" else None
    chart_point_id = _chart_point_id(context.paper_id, context.figure_id, context.panel_id, series_name or "series", point_index)
    if y_value is None:
        y_value = _float(point_payload.get("value"))
    return ChartPoint(
        paper_id=context.paper_id,
        figure_id=context.figure_id,
        panel_id=context.panel_id,
        chart_type=chart_type,
        chart_point_id=chart_point_id,
        series_name=series_name,
        point_index=point_index,
        x_value=x_value,
        x_unit=x_unit,
        x_axis_label=x_axis_label,
        x_axis_scale=x_axis_scale,
        y_value=y_value,
        y_unit=y_unit,
        y_axis_label=y_axis_label,
        y_axis_scale=y_axis_scale,
        y2_value=y2_value,
        y2_unit=y2_unit,
        z_value=z_value,
        z_label=_text(point_payload.get("z_label") or point_payload.get("z_axis_label") or point_payload.get("value_label")),
        z_unit=_text(point_payload.get("z_unit")),
        scale_factor=_text(point_payload.get("scale_factor")),
        category_label=category,
        category_index=_int(point_payload.get("category_index")),
        error_bar=_text(point_payload.get("error_bar")),
        significance=_text(point_payload.get("significance")),
        curve_role=_text(point_payload.get("curve_role")),
        track_id=_text(point_payload.get("track_id")),
        extraction_method=_text(point_payload.get("extraction_method") or default_method),
        axis_source=_text(point_payload.get("axis_source") or "vlm_axis_read"),
        value_source=_text(point_payload.get("value_source") or "vlm_visual_estimate"),
        confidence=confidence or 0.0,
        needs_verification=needs_verification,
        evidence_ids=evidence_ids,
    )


def _dedupe_point_payloads(points: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, ...]] = set()
    for point in points:
        key = tuple(
            _text(point.get(field))
            for field in (
                "series_name",
                "series",
                "x_value",
                "x",
                "x_axis_label",
                "x_unit",
                "y_value",
                "y",
                "y_axis_label",
                "y_unit",
                "z_value",
                "z",
                "z_label",
                "z_unit",
                "category_label",
                "category",
            )
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(point)
    return deduped


def _heatmap_candidates(payload: dict[str, Any], context: VisualExtractionContext) -> list[dict[str, Any]]:
    raw_candidates = payload.get("heatmap_candidates")
    if raw_candidates is None:
        raw_candidates = payload.get("benchmark_candidates")
    candidates: list[dict[str, Any]] = []
    for index, item in enumerate(_as_list(raw_candidates), start=1):
        if not isinstance(item, dict):
            continue
        metric_name = _text(item.get("metric_name") or item.get("name") or item.get("type"))
        if not metric_name:
            continue
        value_range = item.get("value_range") if isinstance(item.get("value_range"), list) else None
        value = item.get("value")
        if value in (None, ""):
            value = item.get("value_or_range") or item.get("range") or item.get("estimate")
        candidate = {
            "candidate_id": f"{context.paper_id}:{context.figure_id}:{context.panel_id}:heatmap:{index}",
            "paper_id": context.paper_id,
            "figure_id": context.figure_id,
            "panel_id": context.panel_id,
            "source_phase": "heatmap_candidate",
            "metric_name": metric_name,
            "series": _text(item.get("series") or item.get("series_name")),
            "condition": _text(item.get("condition")),
            "value": _text(value),
            "value_min": _text(value_range[0]) if value_range and len(value_range) >= 1 else _text(item.get("value_min")),
            "value_max": _text(value_range[1]) if value_range and len(value_range) >= 2 else _text(item.get("value_max")),
            "unit": _text(item.get("unit")),
            "scale_factor": _text(item.get("scale_factor")),
            "evidence_type": _text(item.get("evidence_type") or "heatmap_pattern"),
            "confidence": _float(item.get("confidence"), 0.0) or 0.0,
            "needs_review": _bool(item.get("needs_review"), default=bool(value_range)),
            "evidence_ids": ";".join(_valid_evidence_ids(item.get("evidence_ids"), context)),
        }
        candidates.append(candidate)
    return candidates


def validate_image_observations(observations: list[ImageObservation]) -> list[ImageObservation]:
    return observations


def _axis_payload(payload: dict[str, Any], axis: str) -> Any:
    axis_labels = payload.get("axis_labels") if isinstance(payload.get("axis_labels"), dict) else {}
    aliases = [f"{axis}_axis", axis, f"{axis}_label", f"{axis}_axis_label"]
    for key in aliases:
        if payload.get(key):
            return payload.get(key)
    for key in aliases:
        if axis_labels.get(key):
            return axis_labels.get(key)
    axes = payload.get("axes") if isinstance(payload.get("axes"), dict) else {}
    for key in aliases:
        if axes.get(key):
            return axes.get(key)
    return {}


def _apply_axis_hint_fallbacks(
    *,
    x_axis: ChartAxis,
    y_axis: ChartAxis,
    payload: dict[str, Any],
    context: VisualExtractionContext,
) -> None:
    hints = payload.get("axis_unit_hints")
    x_hint, y_hint = _axis_hints(hints)
    if x_hint:
        _fill_axis_from_hint(x_axis, x_hint)
    if y_hint:
        _fill_axis_from_hint(y_axis, y_hint)


def _axis_hints(raw: Any) -> tuple[str, str]:
    if isinstance(raw, dict):
        return _text(raw.get("x_axis") or raw.get("x") or raw.get("x_label")), _text(raw.get("y_axis") or raw.get("y") or raw.get("y_label"))
    hints = [_text(item) for item in _as_list(raw) if _text(item)]
    unit_only = [item for item in hints if _looks_like_unit_only(item)]
    if len(unit_only) >= 2 and not any("(" in item and ")" in item for item in hints):
        x_unit = next((item for item in unit_only if _looks_like_time_unit(item) or item in {"%", "mm", "µm", "nm"}), unit_only[0])
        y_unit = next((item for item in unit_only if item != x_unit), "")
        return x_unit, y_unit
    x_hint = next((item for item in hints if _looks_like_x_axis_hint(item)), "")
    y_hint = next((item for item in hints if item != x_hint and _looks_like_y_axis_hint(item)), "")
    if not y_hint and len(hints) == 1 and not x_hint:
        y_hint = hints[0]
    elif not x_hint and len(hints) >= 2:
        x_hint = hints[0]
    if not y_hint and len(hints) >= 2:
        y_hint = next((item for item in hints if item != x_hint), "")
    return x_hint, y_hint


def _fill_axis_from_hint(axis: ChartAxis, hint: str) -> None:
    if _looks_like_unit_only(hint):
        if not axis.unit:
            axis.unit = hint
        if not axis.label and _looks_like_time_unit(hint):
            axis.label = "Time"
        return
    parsed = _axis_from_label(hint)
    if not axis.label and parsed.get("label"):
        axis.label = parsed["label"]
    if not axis.unit and parsed.get("unit"):
        axis.unit = parsed["unit"]


def _looks_like_x_axis_hint(value: str) -> bool:
    text = value.lower()
    return any(token in text for token in ("time", "day", "hour", "min", "depth", "strain", "cycle"))


def _looks_like_y_axis_hint(value: str) -> bool:
    text = value.lower()
    return any(token in text for token in ("intensity", "hydrogen", "h2", "stress", "rate", "expression", "concentration", "density", "uptake", "production"))


def _looks_like_unit_only(value: str) -> bool:
    text = value.strip()
    lower = text.lower()
    if not text:
        return False
    unit_tokens = ("/", "^", "µ", "%", "mol", "mmol", "mg", "g", "ml", "l", "mpa", "pa", "a.u.", "hour", "hours", "day", "days", "min", "s")
    label_tokens = ("time", "depth", "hydrogen", "intensity", "stress", "strain", "rate", "expression", "concentration", "density")
    return any(token in lower for token in unit_tokens) and not any(token in lower for token in label_tokens) and "(" not in text


def _looks_like_time_unit(value: str) -> bool:
    return value.strip().lower() in {"h", "hr", "hrs", "hour", "hours", "day", "days", "min", "s", "sec", "seconds"}


def _axis_value_alias(point_payload: dict[str, Any], axis: str) -> tuple[Any, str, str]:
    for key, value in point_payload.items():
        key_text = str(key)
        lower = key_text.lower()
        if not lower.startswith(f"{axis}_"):
            continue
        if lower in {f"{axis}_value", f"{axis}_axis_label", f"{axis}_axis_scale", f"{axis}_unit"}:
            continue
        label_unit = _label_unit_from_axis_key(key_text, axis)
        if label_unit[0] or label_unit[1]:
            return value, label_unit[0], label_unit[1]
    return None, "", ""


def _label_unit_from_axis_key(key: str, axis: str) -> tuple[str, str]:
    text = key[len(axis) + 1 :]
    unit = ""
    unit_aliases = {
        "mm_per_mm": "mm/mm",
        "mpa": "MPa",
        "pa": "Pa",
        "mmol_m2": "mmol/m^2",
        "mmol_per_m2": "mmol/m^2",
        "l_m2": "L/m^2",
        "l_per_m2": "L/m^2",
        "min": "min",
        "h": "h",
    }
    lower = text.lower()
    for suffix, parsed_unit in sorted(unit_aliases.items(), key=lambda item: len(item[0]), reverse=True):
        marker = f"_{suffix}"
        if lower.endswith(marker):
            unit = parsed_unit
            text = text[: -len(marker)]
            break
    label = text.replace("_", " ").strip()
    return label, unit


def _axis_from_label(value: str) -> dict[str, str]:
    text = _text(value)
    if not text:
        return {}
    label = text
    unit = ""
    if "(" in text and ")" in text and text.rfind("(") < text.rfind(")"):
        label = text[: text.rfind("(")].strip()
        unit = text[text.rfind("(") + 1 : text.rfind(")")].strip()
    return {"label": label or text, "unit": unit}


def _series_names(payload: dict[str, Any], points: list[dict[str, Any]]) -> list[str]:
    names: list[str] = []
    for item in _as_list(payload.get("series")):
        names.append(_text(item if isinstance(item, str) else item.get("series_name") if isinstance(item, dict) else ""))
    for point in points:
        names.append(_text(point.get("series_name") or point.get("series")))
    return list(dict.fromkeys(name for name in names if name))


def _chart_point_id(paper_id: str, figure_id: str, panel_id: str, series_name: str, point_index: int) -> str:
    safe_series = "_".join((series_name or "series").strip().split())
    return f"{paper_id}:{figure_id}:{panel_id}:{safe_series}:{point_index}"


def _valid_evidence_ids(value: Any, context: VisualExtractionContext) -> list[str]:
    allowed = {str(item.get("evidence_id")) for item in context.evidence_map if item.get("evidence_id")}
    return [item for item in _string_list(value) if item in allowed]


def _chart_evidence_ids(value: Any, context: VisualExtractionContext, visual_evidence_ids: list[str]) -> list[str]:
    valid = _valid_evidence_ids(value, context)
    if visual_evidence_ids:
        return list(dict.fromkeys([*valid, *visual_evidence_ids]))
    return valid or _default_evidence_ids(context)


def _visual_evidence_ids(context: VisualExtractionContext) -> list[str]:
    return [
        str(item.get("evidence_id"))
        for item in context.evidence_map
        if item.get("evidence_id")
        and item.get("use_policy") == "use_for_extraction"
        and item.get("source_type") in {"chart", "image"}
    ][:3]


def _default_evidence_ids(context: VisualExtractionContext) -> list[str]:
    extractable = [item for item in context.evidence_map if item.get("evidence_id") and item.get("use_policy") == "use_for_extraction"]
    visual = [
        str(item.get("evidence_id"))
        for item in extractable
        if item.get("source_type") in {"chart", "image"}
    ]
    if visual:
        return visual[:3]
    return [str(item.get("evidence_id")) for item in extractable or context.evidence_map if item.get("evidence_id")][:3]


def _caption_segment_status(context: VisualExtractionContext) -> str:
    contract = context.panel_evidence_contract if isinstance(context.panel_evidence_contract, dict) else {}
    caption = contract.get("caption") if isinstance(contract.get("caption"), dict) else {}
    segment = caption.get("caption_segment") if isinstance(caption.get("caption_segment"), dict) else {}
    return _text(segment.get("status")) or "missing"


def _support_level(evidence_ids: list[str], context: VisualExtractionContext) -> str:
    by_id = {str(item.get("evidence_id")): item for item in context.evidence_map if item.get("evidence_id")}
    items = [by_id[eid] for eid in evidence_ids if eid in by_id]
    has_visual = any(item.get("source_type") in {"image", "chart"} for item in items)
    has_caption = any(item.get("source_type") == "caption" or item.get("text_level") in {"caption_body", "caption_segment"} for item in items)
    if has_visual and has_caption:
        return "visual_and_caption_grounded"
    if has_caption:
        return "caption_grounded"
    return "visual_only" if has_visual else "caption_grounded"


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _string_list(value: Any) -> list[str]:
    return [_text(item) for item in _as_list(value) if _text(item)]


def _text(value: Any) -> str:
    return str(value or "").strip()


def _float(value: Any, default: float | None = None) -> float | None:
    if value in (None, ""):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _bool(value: Any, *, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value in (None, ""):
        return default
    return str(value).strip().lower() in {"1", "true", "yes"}

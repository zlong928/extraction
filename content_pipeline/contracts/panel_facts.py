from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from content_pipeline.contracts.visual import ChartDigitizationResult, ChartPoint


@dataclass(slots=True)
class PanelFactRow:
    fact_id: str
    paper_id: str
    figure_id: str
    panel_id: str
    source_image: str
    chart_type: str = ""
    series_name: str = ""
    point_index: str = ""
    x_label: str = ""
    x_unit: str = ""
    x_value: str = ""
    y_label: str = ""
    y_unit: str = ""
    y_value: str = ""
    z_label: str = ""
    z_unit: str = ""
    z_value: str = ""
    scale_factor: str = ""
    category_label: str = ""
    confidence: str = ""
    digitization_status: str = ""
    needs_review: str = ""
    source_phase: str = "chart_digitization"
    warnings: str = ""
    errors: str = ""
    value_source: str = ""
    evidence_ids: str = ""

    def csv_dict(self) -> dict[str, str]:
        return {
            "fact_id": self.fact_id,
            "paper_id": self.paper_id,
            "figure_id": self.figure_id,
            "panel_id": self.panel_id,
            "source_image": self.source_image,
            "chart_type": self.chart_type,
            "series_name": self.series_name,
            "point_index": self.point_index,
            "x_label": self.x_label,
            "x_unit": self.x_unit,
            "x_value": self.x_value,
            "y_label": self.y_label,
            "y_unit": self.y_unit,
            "y_value": self.y_value,
            "z_label": self.z_label,
            "z_unit": self.z_unit,
            "z_value": self.z_value,
            "scale_factor": self.scale_factor,
            "category_label": self.category_label,
            "confidence": self.confidence,
            "digitization_status": self.digitization_status,
            "needs_review": self.needs_review,
            "source_phase": self.source_phase,
            "warnings": self.warnings,
            "errors": self.errors,
            "value_source": self.value_source,
            "evidence_ids": self.evidence_ids,
        }


def build_panel_fact_rows(
    *,
    chart_digitization_results: list[ChartDigitizationResult],
    packet_by_panel: dict[str, Any],
    audit_trace: list[dict[str, Any]],
) -> list[PanelFactRow]:
    rows: list[PanelFactRow] = []
    panels_with_result: set[str] = set()
    for result in chart_digitization_results:
        panels_with_result.add(result.panel_id)
        image_ref = _image_ref(packet_by_panel.get(result.panel_id))
        warnings = _join(result.warnings)
        evidence_ids = _join(result.evidence_ids)
        fact_points = result.raw_points or result.points
        if fact_points:
            for point in fact_points:
                rows.append(_row_from_point(result=result, point=point, image_ref=image_ref, warnings=warnings))
            continue
        rows.append(PanelFactRow(
            fact_id=f"{result.paper_id}:{result.figure_id}:{result.panel_id}:axis",
            paper_id=result.paper_id,
            figure_id=result.figure_id,
            panel_id=result.panel_id,
            source_image=image_ref,
            chart_type=result.chart_type,
            x_label=result.x_axis.label,
            x_unit=result.x_axis.unit,
            y_label=result.y_axis.label,
            y_unit=result.y_axis.unit,
            confidence=_num(result.extraction_confidence),
            digitization_status=result.digitization_status or "unknown",
            needs_review=str(bool(result.needs_verification)),
            warnings=warnings,
            errors=_join(_result_errors(result)),
            evidence_ids=evidence_ids,
        ))

    for event in audit_trace:
        if event.get("event") == "chart_digitization_skipped":
            panel_id = str(event.get("panel_id") or "")
            if panel_id and panel_id not in panels_with_result:
                rows.append(_row_from_skip_event(event=event, packet=packet_by_panel.get(panel_id)))
                panels_with_result.add(panel_id)
        elif event.get("event") == "chart_digitization_failed":
            panel_id = str(event.get("panel_id") or "")
            if panel_id and panel_id not in panels_with_result:
                rows.append(_row_from_failure_event(event=event, packet=packet_by_panel.get(panel_id)))
                panels_with_result.add(panel_id)
    return sorted(rows, key=lambda row: (row.figure_id, row.panel_id, _sort_index(row.point_index)))


def _row_from_point(*, result: ChartDigitizationResult, point: ChartPoint, image_ref: str, warnings: str) -> PanelFactRow:
    fact_id = point.chart_point_id or f"{result.paper_id}:{result.figure_id}:{result.panel_id}:point:{point.point_index}"
    return PanelFactRow(
        fact_id=fact_id,
        paper_id=result.paper_id,
        figure_id=result.figure_id,
        panel_id=result.panel_id,
        source_image=image_ref,
        chart_type=point.chart_type or result.chart_type,
        series_name=point.series_name,
        point_index=str(point.point_index),
        x_label=point.x_axis_label or result.x_axis.label,
        x_unit=point.x_unit or result.x_axis.unit,
        x_value=_value(point.x_value if point.x_value is not None else point.category_label),
        y_label=point.y_axis_label or result.y_axis.label,
        y_unit=point.y_unit or result.y_axis.unit,
        y_value=_value(point.y_value),
        z_label=point.z_label,
        z_unit=point.z_unit,
        z_value=_value(point.z_value),
        scale_factor=point.scale_factor,
        category_label=point.category_label,
        confidence=_num(point.confidence or result.extraction_confidence),
        digitization_status=result.digitization_status or "digitized",
        needs_review=str(bool(point.needs_verification or result.needs_verification)),
        warnings=warnings,
        errors="",
        value_source=_public_value_source(point.value_source),
        evidence_ids=_join(point.evidence_ids or result.evidence_ids),
    )


def _row_from_skip_event(*, event: dict[str, Any], packet: Any) -> PanelFactRow:
    reason = str(event.get("reason") or "")
    quality = event.get("quality") if isinstance(event.get("quality"), dict) else {}
    if reason == "visual_asset_missing":
        status = "failed"
        errors = "image missing"
        warnings = ""
    elif reason == "visual_asset_too_small":
        status = "too_low_resolution"
        errors = ""
        warnings = str(quality.get("reason") or reason)
    else:
        status = "failed"
        errors = reason
        warnings = ""
    panel_id = str(event.get("panel_id") or getattr(packet, "panel_id", "") or "")
    return PanelFactRow(
        fact_id=f"{getattr(packet, 'paper_id', '')}:{getattr(packet, 'figure_id', '')}:{panel_id}:skip",
        paper_id=str(getattr(packet, "paper_id", "") or ""),
        figure_id=str(getattr(packet, "figure_id", "") or ""),
        panel_id=panel_id,
        source_image=_image_ref(packet),
        chart_type=_chart_type_hint(packet),
        digitization_status=status,
        needs_review="True",
        warnings=warnings,
        errors=errors,
    )


def _row_from_failure_event(*, event: dict[str, Any], packet: Any) -> PanelFactRow:
    message = str(event.get("message") or event.get("exception_type") or "digitization failed")
    panel_id = str(event.get("panel_id") or getattr(packet, "panel_id", "") or "")
    return PanelFactRow(
        fact_id=f"{getattr(packet, 'paper_id', '')}:{getattr(packet, 'figure_id', '')}:{panel_id}:failed",
        paper_id=str(getattr(packet, "paper_id", "") or ""),
        figure_id=str(getattr(packet, "figure_id", "") or ""),
        panel_id=panel_id,
        source_image=_image_ref(packet),
        chart_type=_chart_type_hint(packet),
        digitization_status="failed",
        needs_review="True",
        warnings="",
        errors=message,
    )


def _result_errors(result: ChartDigitizationResult) -> list[str]:
    if result.digitization_status == "failed":
        raw_error = result.raw_output.get("error") or result.raw_output.get("errors")
        if isinstance(raw_error, list):
            return [str(item) for item in raw_error if item]
        if raw_error:
            return [str(raw_error)]
        return ["digitization failed"]
    return []


def _image_ref(packet: Any) -> str:
    return str(getattr(packet, "image_ref", "") or "")


def _chart_type_hint(packet: Any) -> str:
    provenance = getattr(packet, "provenance", {}) if packet is not None else {}
    if isinstance(provenance, dict):
        return str(provenance.get("visual_normalized_type") or "")
    return ""


def _public_value_source(value: str) -> str:
    normalized = str(value or "").strip()
    if normalized == "vlm_visual_estimate":
        return "visual_estimated"
    return normalized


def _value(value: Any) -> str:
    return "" if value is None else str(value)


def _num(value: Any) -> str:
    return "" if value is None else str(value)


def _join(values: Any) -> str:
    if values is None:
        return ""
    if isinstance(values, str):
        return values
    if isinstance(values, (list, tuple, set)):
        return ";".join(str(item) for item in values if item not in (None, ""))
    return str(values)


def _sort_index(value: str) -> int:
    try:
        return int(value)
    except ValueError:
        return 0

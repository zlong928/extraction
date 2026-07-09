from __future__ import annotations

import json
import re
from typing import Any

from content_pipeline.adapters.visual_fact_adapters import chart_digitization_from_payload
from content_pipeline.contracts.semantic import PanelSemanticResult
from content_pipeline.contracts.visual import ChartDigitizationResult, VisualExtractionContext
from content_pipeline.llm.prompt_contracts import PromptContract
from content_pipeline.visual.chart_quality_gate import apply_chart_quality_gate
from content_pipeline.visual.context_builder import build_visual_extraction_context
from content_pipeline.visual.heatmap_matrix import heatmap_matrix_payload
from content_pipeline.contracts.evidence import EvidencePacket

CHART_DIGITIZATION_PROMPT = (
    "You are a VLM chart digitizer. Extract visible chart data points, bars, or matrix values from the image. "
    "Do not generate benchmark metrics and do not summarize the chart as a metric. "
    "Read actual axis labels, units, scales, series names, legend bindings, data points, error bars, and significance marks from the image. "
    "Always output digitization_status, axis_readability, legend_readability, calibration_status, and data_point_count. "
    "Use digitization_status values: digitized, partially_digitized, axis_unreadable, legend_unreadable, too_low_resolution, no_chart_detected, or failed. "
    "Use axis_readability values: readable, partially_readable, unreadable, or unknown. "
    "Use legend_readability values: readable, partially_readable, unreadable, not_applicable, or unknown. "
    "Use calibration_status values: calibrated_from_ticks, estimated_from_axis, category_only, unavailable, or unknown. "
    "Use panel_evidence_contract.caption.caption_segment, tables, and axis_unit_hints only to clarify axis labels, units, series names, and table-exact values. "
    "Always normalize chart coordinates into data_points objects with keys: series_name, point_index, x_value, x_axis_label, x_unit, y_value, y_axis_label, y_unit, category_label, value_source, confidence, needs_verification, evidence_ids. "
    "For bar charts, put the bar/category label in category_label and the bar height in y_value; do not output label/value shorthand. "
    "For grouped bar charts, output one data_points item per visible bar with series_name as the group/target and category_label as the category. "
    "For box plots, output one data_points item per readable statistic (min, q1, median, q3, max) with category_label set to that statistic and curve_role=boxplot_statistic. "
    "For multi-series charts, do not nest named series as arbitrary object keys; use either flat data_points or series=[{series_name, data_points:[...]}]. "
    "For heatmaps, do not put colorbar values into y_value. Set chart_type=heatmap and output x_axis/y_axis for the two spatial or categorical dimensions, plus colorbar {bbox, orientation, tick_values or value_min/value_max, label, unit, scale_factor}. "
    "For heatmaps, output heatmap_panels with each panel bbox, series_name, condition, and optional x_values/y_values. Bboxes may be normalized 0-1 image coordinates or pixel coordinates. "
    "For heatmaps, output heatmap_candidates for chart-only review facts using fields metric_name, series, condition, value or value_range, unit, scale_factor, evidence_type, confidence, needs_review, evidence_ids. "
    "For each heatmap panel, output a separate concentration distribution pattern candidate. For each non-uniform or gradient heatmap panel, also output separate candidates for center concentration, boundary concentration, and gradient direction when visually supported. "
    "Do not merge center and boundary values into one heatmap_candidates item, and do not use value_or_range; use value for qualitative text or value_range:[min,max] for numeric ranges. "
    "For heatmaps, do not output benchmark metric candidates for ontology mapping; heatmap_candidates are chart-only review facts. "
    "If axis labels or units are visible or present in axis_unit_hints, populate x_axis/y_axis and repeat the labels/units on each data point. "
    "Do not use ontology metric constraints, application_task, or assay to decide whether to keep points. "
    "If the image contains no chart, set digitization_status=no_chart_detected and return no data_points. "
    "If the crop is too small or too low resolution to read reliably, set digitization_status=too_low_resolution and return warnings. "
    "If axes or legend are unreadable, do not invent points; set the corresponding readability/status fields and add warnings. "
    "If values are visually estimated from the chart, set extraction_method=llm_visual_digitization and needs_verification=true. "
    "If values come from an exact nearby table, set value_source=table_exact for those points when possible. "
    "If a value cannot be read, omit the point and add a warning. "
    "Every output evidence_ids value must cite evidence_id values from evidence_map."
)


class ChartDigitizationPhase:
    def extract(
        self,
        *,
        packet: EvidencePacket,
        panel_semantic: PanelSemanticResult | None,
        model_client: Any,
        audit: list[dict[str, Any]],
    ) -> ChartDigitizationResult:
        context = build_visual_extraction_context(
            packet=packet,
            panel_semantic=panel_semantic,
            include_benchmark_semantics=False,
        )
        payload = self.extract_from_context(context=context, model_client=model_client, audit=audit)
        result = apply_chart_quality_gate(chart_digitization_from_payload(payload, context))
        audit.append({
            "event": "chart_digitization_completed",
            "figure_id": result.figure_id,
            "panel_id": result.panel_id,
            "chart_type": result.chart_type,
            "digitization_status": result.digitization_status,
            "axis_readability": result.axis_readability,
            "legend_readability": result.legend_readability,
            "calibration_status": result.calibration_status,
            "point_count": len(result.points),
            "warning_count": len(result.warnings),
            "needs_verification": result.needs_verification,
        })
        return result

    def extract_from_context(self, *, context: VisualExtractionContext, model_client: Any, audit: list[dict[str, Any]]) -> dict[str, Any]:
        inputs = context.llm_inputs(phase_name="chart_digitization", include_benchmark_semantics=False)
        contract = PromptContract(
            object_name="chart_digitization_result",
            required_fields=[],
            field_types={
                "chart_type": "string",
                "digitization_status": "string enum",
                "axis_readability": "string enum",
                "legend_readability": "string enum",
                "calibration_status": "string enum",
                "x_axis": "object {label:string, unit:string, scale:string}",
                "y_axis": "object {label:string, unit:string, scale:string}",
                "data_points": "array of normalized point objects",
                "heatmap_panels": "array of heatmap panel region objects when chart_type is heatmap",
                "colorbar": "object with bbox, orientation, tick_values/value range, label, unit, scale_factor when chart_type is heatmap",
                "heatmap_candidates": "array of heatmap pattern/range candidate facts",
                "warnings": "array of strings",
                "evidence_ids": "array of evidence_id strings",
            },
            output_skeleton={
                "chart_type": "",
                "digitization_status": "",
                "axis_readability": "",
                "legend_readability": "",
                "calibration_status": "",
                "x_axis": {"label": "", "unit": "", "scale": ""},
                "y_axis": {"label": "", "unit": "", "scale": ""},
                "series": [],
                "heatmap_panels": [],
                "colorbar": {},
                "heatmap_candidates": [],
                "data_points": [
                    {
                        "series_name": "",
                        "point_index": 1,
                        "x_value": None,
                        "x_axis_label": "",
                        "x_unit": "",
                        "y_value": None,
                        "y_axis_label": "",
                        "y_unit": "",
                        "category_label": "",
                        "value_source": "visual_estimate",
                        "confidence": 0.0,
                        "needs_verification": True,
                        "evidence_ids": [],
                    }
                ],
                "data_point_count": 0,
                "extraction_confidence": 0.0,
                "needs_verification": True,
                "warnings": [],
                "evidence_ids": [],
            },
        )
        prompt = f"{CHART_DIGITIZATION_PROMPT}\n\n{contract.render()}"
        audit.append({"event": "llm_phase_started", "phase_name": "chart_digitization", "schema_validation": "disabled"})
        try:
            call_inputs = {**inputs, "image_ref": context.image_ref}
            if hasattr(model_client, "call_text"):
                raw = model_client.call_text(prompt=prompt, inputs=call_inputs)
            else:
                raw = model_client.call_json(prompt=prompt, inputs=call_inputs)
        except Exception as exc:
            event = {
                "phase_name": "chart_digitization",
                "exception_type": type(exc).__name__,
                "message": str(exc),
                "schema_validation": "disabled",
            }
            if audit and audit[-1].get("event") == "llm_phase_started":
                audit[-1].update(event)
            else:
                audit.append(event)
            return {
                "chart_type": context.chart_type_hint or "chart",
                "digitization_status": "failed",
                "warnings": [f"llm_call_failed:{type(exc).__name__}"],
                "raw_error": str(exc),
            }
        payload = _chart_payload_from_raw(raw)
        _apply_heatmap_matrix(payload, context=context, audit=audit)
        if payload.get("_raw_non_object_output"):
            audit.append({
                "event": "chart_digitization_non_object_output",
                "phase_name": "chart_digitization",
                "output_excerpt": str(payload.get("raw_output_text") or "")[:500],
            })
        audit.append({
            "event": "llm_phase_completed",
            "phase_name": "chart_digitization",
            "schema_validation": "disabled",
        })
        return payload


def _apply_heatmap_matrix(payload: dict[str, Any], *, context: VisualExtractionContext, audit: list[dict[str, Any]]) -> None:
    if "heatmap" not in str(payload.get("chart_type") or context.chart_type_hint or "").lower():
        return
    matrix_points, warnings = heatmap_matrix_payload(payload, image_ref=context.image_ref)
    existing_warnings = [str(item) for item in payload.get("warnings") or [] if item]
    if matrix_points:
        payload["data_points"] = matrix_points
        payload["series"] = []
        payload["digitization_status"] = payload.get("digitization_status") or "digitized"
        payload["calibration_status"] = "colorbar_calibrated"
        payload["extraction_method"] = "heatmap_colorbar_calibrated"
        audit.append({
            "event": "heatmap_matrix_extracted",
            "panel_id": context.panel_id,
            "point_count": len(matrix_points),
            "source": "colorbar_pixel_calibration",
        })
        return
    payload["data_points"] = []
    payload["series"] = []
    payload["digitization_status"] = payload.get("digitization_status") or "partially_digitized"
    payload["warnings"] = list(dict.fromkeys([*existing_warnings, *warnings, "heatmap_matrix_not_extracted"]))
    audit.append({
        "event": "heatmap_matrix_not_extracted",
        "panel_id": context.panel_id,
        "warnings": warnings,
    })


def _chart_payload_from_raw(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        payload = dict(raw)
    elif isinstance(raw, str):
        decoded = _decode_json_from_model_text(raw)
        if decoded is None:
            return {
                "chart_type": "chart",
                "digitization_status": "failed",
                "warnings": ["non_json_chart_digitization_output"],
                "raw_output_text": raw,
                "_raw_non_object_output": True,
            }
        payload = dict(decoded) if isinstance(decoded, dict) else {"raw_output": decoded, "_raw_non_object_output": True}
    else:
        payload = {"raw_output": raw, "_raw_non_object_output": True}
    nested = payload.get("chart_digitization_result")
    if isinstance(nested, dict):
        nested_payload = dict(nested)
        nested_payload.setdefault("raw_output_wrapper", payload)
        return nested_payload
    return payload


def _decode_json_from_model_text(raw: str) -> Any | None:
    for candidate in _json_text_candidates(raw):
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            try:
                return json.loads(candidate, strict=False)
            except json.JSONDecodeError:
                continue
    return None


def _json_text_candidates(raw: str) -> list[str]:
    text = raw.strip()
    candidates: list[str] = []
    if text:
        candidates.append(text)
    for match in re.finditer(r"```(?:json|JSON)?\s*(.*?)\s*```", text, flags=re.DOTALL):
        fenced = match.group(1).strip()
        if fenced and fenced not in candidates:
            candidates.append(fenced)
    return candidates

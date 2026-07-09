from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class VisualExtractionContext:
    paper_id: str
    figure_id: str
    panel_id: str
    image_ref: str
    visual_type: str
    tables: str = ""
    formulas: str = ""
    evidence_map: list[dict[str, Any]] = field(default_factory=list)
    section_hierarchy: list[dict[str, Any]] = field(default_factory=list)
    panel_semantic_profile: dict[str, Any] = field(default_factory=dict)
    chart_type_hint: str = ""
    image_kind_hint: str = ""
    axis_unit_hints: list[str] = field(default_factory=list)
    panel_evidence_contract: dict[str, Any] = field(default_factory=dict)
    panel_context_warnings: list[str] = field(default_factory=list)

    def llm_inputs(self, *, phase_name: str, include_benchmark_semantics: bool = True) -> dict[str, Any]:
        inputs = {
            "phase_name": phase_name,
            "paper_id": self.paper_id,
            "figure_id": self.figure_id,
            "panel_id": self.panel_id,
            "image_ref": self.image_ref,
            "visual_type": self.visual_type,
            "tables": self.tables,
            "formulas": self.formulas,
            "evidence_map": self.evidence_map,
            "section_hierarchy": self.section_hierarchy,
            "chart_type_hint": self.chart_type_hint,
            "image_kind_hint": self.image_kind_hint,
            "axis_unit_hints": self.axis_unit_hints,
            "panel_evidence_contract": self.panel_evidence_contract,
            "panel_context_warnings": self.panel_context_warnings,
        }
        segment = {}
        contract_caption = self.panel_evidence_contract.get("caption") if isinstance(self.panel_evidence_contract, dict) else {}
        if isinstance(contract_caption, dict):
            segment = contract_caption.get("caption_segment") if isinstance(contract_caption.get("caption_segment"), dict) else {}
        if segment.get("status") == "fallback_regex" and segment.get("text"):
            inputs["legacy_panel_caption_focus"] = str(segment.get("text") or "")
        if include_benchmark_semantics:
            inputs.update({
                "panel_semantic_profile": self.panel_semantic_profile,
            })
        return inputs


@dataclass(slots=True)
class ChartAxis:
    label: str = ""
    unit: str = ""
    scale: str = "unknown"
    range_min: float | None = None
    range_max: float | None = None
    tick_values: list[float] = field(default_factory=list)
    calibration_confidence: float = 0.0


@dataclass(slots=True)
class ChartPoint:
    paper_id: str
    figure_id: str
    panel_id: str
    chart_type: str
    chart_point_id: str = ""
    series_name: str = ""
    point_index: int = 0
    x_value: float | None = None
    x_unit: str = ""
    x_axis_label: str = ""
    x_axis_scale: str = "unknown"
    y_value: float | None = None
    y_unit: str = ""
    y_axis_label: str = ""
    y_axis_scale: str = "unknown"
    y2_value: float | None = None
    y2_unit: str = ""
    z_value: float | str | None = None
    z_label: str = ""
    z_unit: str = ""
    scale_factor: str = ""
    category_label: str = ""
    category_index: int | None = None
    error_bar: str = ""
    significance: str = ""
    curve_role: str = ""
    track_id: str = ""
    extraction_method: str = "llm_visual_digitization"
    axis_source: str = "vlm_axis_read"
    value_source: str = "vlm_visual_estimate"
    confidence: float = 0.0
    needs_verification: bool = True
    review_status: str = "pending"
    review_reason: str = ""
    evidence_ids: list[str] = field(default_factory=list)

    def csv_dict(self) -> dict[str, str]:
        return {
            "paper_id": self.paper_id,
            "figure_id": self.figure_id,
            "panel_id": self.panel_id,
            "chart_type": self.chart_type,
            "chart_point_id": self.chart_point_id,
            "series_name": self.series_name,
            "point_index": str(self.point_index),
            "x_value": "" if self.x_value is None else str(self.x_value),
            "x_unit": self.x_unit,
            "x_axis_label": self.x_axis_label,
            "x_axis_scale": self.x_axis_scale,
            "y_value": "" if self.y_value is None else str(self.y_value),
            "y_unit": self.y_unit,
            "y_axis_label": self.y_axis_label,
            "y_axis_scale": self.y_axis_scale,
            "y2_value": "" if self.y2_value is None else str(self.y2_value),
            "y2_unit": self.y2_unit,
            "z_value": "" if self.z_value is None else str(self.z_value),
            "z_label": self.z_label,
            "z_unit": self.z_unit,
            "scale_factor": self.scale_factor,
            "category_label": self.category_label,
            "category_index": "" if self.category_index is None else str(self.category_index),
            "error_bar": self.error_bar,
            "significance": self.significance,
            "curve_role": self.curve_role,
            "track_id": self.track_id,
            "extraction_method": self.extraction_method,
            "axis_source": self.axis_source,
            "value_source": self.value_source,
            "confidence": str(self.confidence),
            "needs_verification": str(self.needs_verification),
            "review_status": self.review_status,
            "review_reason": self.review_reason,
            "evidence_ids": ";".join(self.evidence_ids),
        }


@dataclass(slots=True)
class ChartDigitizationResult:
    paper_id: str
    figure_id: str
    panel_id: str
    chart_type: str
    digitization_status: str = "unknown"
    axis_readability: str = "unknown"
    legend_readability: str = "unknown"
    calibration_status: str = "unknown"
    x_axis: ChartAxis = field(default_factory=ChartAxis)
    y_axis: ChartAxis = field(default_factory=ChartAxis)
    y2_axis: ChartAxis | None = None
    series: list[str] = field(default_factory=list)
    points: list[ChartPoint] = field(default_factory=list)
    extraction_method: str = "llm_visual_digitization"
    extraction_confidence: float = 0.0
    needs_verification: bool = True
    warnings: list[str] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)
    heatmap_candidates: list[dict[str, Any]] = field(default_factory=list)
    raw_output: dict[str, Any] = field(default_factory=dict)
    raw_points: list[ChartPoint] = field(default_factory=list)


@dataclass(slots=True)
class VisualFactCandidate:
    fact_id: str
    fact_type: str
    subject_slot: str = ""
    attribute_slot: str = ""
    value_slot: str = ""
    comparator_slot: str = ""
    condition_slot: str = ""
    location_slot: str = ""
    evidence_ids: list[str] = field(default_factory=list)
    visual_grounding: dict[str, Any] = field(default_factory=dict)
    caption_segment_status: str = ""
    support_level: str = ""
    confidence: float = 0.0
    raw_output: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class VisualFactExtractionResult:
    panel_id: str
    contract_version: str = "visual_fact_extraction_result/v1"
    visual_fact_candidates: list[VisualFactCandidate] = field(default_factory=list)
    unsupported_claims: list[dict[str, Any]] = field(default_factory=list)
    confidence: float = 0.0
    raw_output: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ImageObservation:
    paper_id: str
    figure_id: str
    panel_id: str
    image_kind: str
    observation_name: str
    target_entity: str = ""
    qualitative_value: str = ""
    numeric_value: float | None = None
    unit: str = ""
    condition: str = ""
    method: str = "llm_image_observation"
    confidence: float = 0.0
    needs_verification: bool = True
    review_status: str = "pending"
    review_reason: str = ""
    evidence_ids: list[str] = field(default_factory=list)
    raw_output: dict[str, Any] = field(default_factory=dict)

    def csv_dict(self) -> dict[str, str]:
        return {
            "paper_id": self.paper_id,
            "figure_id": self.figure_id,
            "panel_id": self.panel_id,
            "image_kind": self.image_kind,
            "observation_name": self.observation_name,
            "target_entity": self.target_entity,
            "qualitative_value": self.qualitative_value,
            "numeric_value": "" if self.numeric_value is None else str(self.numeric_value),
            "unit": self.unit,
            "condition": self.condition,
            "method": self.method,
            "confidence": str(self.confidence),
            "needs_verification": str(self.needs_verification),
            "review_status": self.review_status,
            "review_reason": self.review_reason,
            "evidence_ids": ";".join(self.evidence_ids),
        }

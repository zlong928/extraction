from __future__ import annotations

from typing import Any

from content_pipeline.adapters.visual_fact_adapters import (
    image_observations_from_visual_fact_result,
    visual_fact_result_from_payload,
)
from content_pipeline.contracts.evidence import EvidencePacket
from content_pipeline.contracts.semantic import PanelSemanticResult
from content_pipeline.contracts.visual import ImageObservation, VisualExtractionContext, VisualFactExtractionResult
from content_pipeline.llm.image_observation_adapter import ImageObservationAdapter
from content_pipeline.llm.phase_runner import PhaseRunner, _DEGRADED_KEY
from content_pipeline.llm.prompt_contracts import PromptContract
from content_pipeline.visual.context_builder import build_visual_extraction_context


IMAGE_OBSERVATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": True,
    "required": ["confidence"],
    "properties": {
        "description": {"type": "string"},
        "visual_fact_candidates": {"type": "array"},
        "observations": {"type": "array"},
        "extraction_method": {"type": "string"},
        "confidence": {"type": "number"},
        "needs_verification": {"type": "boolean"},
        "warnings": {"type": "array"},
        "evidence_ids": {"type": "array", "items": {"type": "string"}},
    },
}


class ImageObservationPhase:
    def extract(
        self,
        *,
        packet: EvidencePacket,
        panel_semantic: PanelSemanticResult | None,
        model_client: Any,
        audit: list[dict[str, Any]],
    ) -> list[ImageObservation]:
        _, observations = self.extract_result_and_observations(
            packet=packet,
            panel_semantic=panel_semantic,
            model_client=model_client,
            audit=audit,
        )
        return observations

    def extract_result(
        self,
        *,
        packet: EvidencePacket,
        panel_semantic: PanelSemanticResult | None,
        model_client: Any,
        audit: list[dict[str, Any]],
    ) -> VisualFactExtractionResult:
        result, _ = self.extract_result_and_observations(
            packet=packet,
            panel_semantic=panel_semantic,
            model_client=model_client,
            audit=audit,
        )
        return result

    def extract_result_and_observations(
        self,
        *,
        packet: EvidencePacket,
        panel_semantic: PanelSemanticResult | None,
        model_client: Any,
        audit: list[dict[str, Any]],
    ) -> tuple[VisualFactExtractionResult, list[ImageObservation]]:
        context = build_visual_extraction_context(
            packet=packet,
            panel_semantic=panel_semantic,
            include_benchmark_semantics=False,
        )
        payload = self.extract_from_context(context=context, model_client=model_client, audit=audit)
        is_degraded = payload.get(_DEGRADED_KEY)
        if is_degraded:
            audit.append({
                "event": "image_observation_degraded",
                "panel_id": context.panel_id,
                "repair_meta": payload.get("_repair_meta"),
            })
        result = visual_fact_result_from_payload(payload, context)
        observations = image_observations_from_visual_fact_result(
            result,
            context,
            image_kind=str(payload.get("image_kind") or context.image_kind_hint or "image"),
        )
        audit.append({
            "event": "visual_fact_extraction_completed",
            "figure_id": context.figure_id,
            "panel_id": context.panel_id,
            "image_kind": payload.get("image_kind") or context.image_kind_hint,
            "visual_fact_candidate_count": len(result.visual_fact_candidates),
            "legacy_observation_count": len(observations),
            "degraded": is_degraded or False,
        })
        return result, observations

    def extract_from_context(self, *, context: VisualExtractionContext, model_client: Any, audit: list[dict[str, Any]]) -> dict[str, Any]:
        inputs = context.llm_inputs(phase_name="image_observation")
        evidence_role = str(context.panel_semantic_profile.get("evidence_role") or "").strip()
        prompt_template = (
            "You are a VLM visual fact extractor for non-chart scientific images. "
            "Do not extract coordinate chart points. Do not generate benchmark metrics. "
            "Return visual_fact_candidates as slot-based facts only: morphology, localization, qualitative_change, condition_assignment, schematic_relationship, presence_absence, or other_visual_fact. "
            "Each candidate must include fact_type, subject_slot, attribute_slot, value_slot, condition_slot when applicable, support_level, caption_segment_status, confidence, and evidence_ids. "
            "Use panel_evidence_contract and evidence_map as the only textual evidence authority for the current panel. "
            "Do not bury facts in free-form observations; observations are legacy only and may be empty when visual_fact_candidates are present. "
            "Every output evidence_ids value must cite evidence_id values from evidence_map."
        )
        if evidence_role == "schematic_context":
            prompt_template += (
                " This panel is a schematic or conceptual diagram. "
                "Focus on: system components, input/output entities, process flow, directional arrows, "
                "labeled relationships, and claimed mechanisms. "
                "Describe the overall system architecture and functional relationships between components."
            )
        elif evidence_role == "methods_context":
            prompt_template += (
                " This panel shows a methods or fabrication context. "
                "Focus on: device architecture, material arrangement, fabrication steps, "
                "structure dimensions, and spatial configuration. "
                "Describe how materials, biological agents, and structural components are assembled."
            )
        elif evidence_role == "supporting_observation":
            prompt_template += (
                " This panel provides qualitative supporting evidence. "
                "Focus on: morphology changes, spatial distribution, presence/absence of features, "
                "visual differences between conditions, and qualitative trends. "
                "Describe visible changes across timepoints, treatments, or comparison groups."
            )
        elif evidence_role == "primary_metric_panel":
            prompt_template += (
                " This panel contains direct metric or measurement evidence. "
                "Focus on: visible quantitative labels, measured values, scales, "
                "comparison markers (asterisks, brackets, bars), and any numeric annotations. "
                "Extract all visible quantitative information on the image."
            )
        payload = PhaseRunner().run_phase(
            phase_name="image_observation",
            prompt_template=prompt_template,
            compact_contract=PromptContract(
                object_name="image_observation_result",
                required_fields=list(IMAGE_OBSERVATION_SCHEMA["required"]),
            ),
            inputs=inputs,
            image_ref=context.image_ref,
            output_schema=IMAGE_OBSERVATION_SCHEMA,
            model_client=model_client,
            audit_trace=audit,
            phase_adapter=ImageObservationAdapter(),
        )
        return payload

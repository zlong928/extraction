from __future__ import annotations

from typing import Any

from content_pipeline.llm.phase_adapter import (
    PhaseAdapter,
    PhaseAdaptResult,
    clean_payload_keys,
)


class PanelClassifierAdapter(PhaseAdapter):
    def adapt_payload(self, raw: Any) -> PhaseAdaptResult:
        repairs: list[dict[str, Any]] = []

        if not isinstance(raw, dict):
            repairs.append({"path": "$", "repair": "payload_not_dict", "from": type(raw).__name__})
            return PhaseAdaptResult(payload=self.fallback_payload(), repairs=repairs)

        cleaned, key_repairs = clean_payload_keys(raw)
        repairs.extend(key_repairs)

        if not cleaned:
            repairs.append({"path": "$", "repair": "payload_empty"})
            return PhaseAdaptResult(payload=self.fallback_payload(), repairs=repairs)

        return PhaseAdaptResult(payload=cleaned, repairs=repairs)

    def fallback_payload(self, warnings: list[str] | None = None) -> dict[str, Any]:
        return {
            "panel_relevance": "unusable",
            "extraction_decision": "skip_metric_extraction",
            "application_task": "",
            "assay": "",
            "metric_category": "",
            "panel_type": "",
            "panel_role": "",
            "evidence_role": "unusable",
            "matched_target_group_ids": [],
            "allowed_metrics": [],
            "allowed_units": [],
            "expected_value_types": [],
            "needs_digitization": False,
            "digitization_reason": "",
            "exclusion_reason": "Panel classifier output was not parsable; metric extraction disabled.",
            "expected_metric_fields": [],
            "recommended_metric_set": [],
            "main_entities": {},
            "visible_modalities": {},
            "ontology_terms": {},
            "why_relevant": "",
            "confidence": 0.0,
            "warnings": warnings or ["panel_classifier_payload_unrecoverable"],
        }

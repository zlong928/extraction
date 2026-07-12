from __future__ import annotations

import json
from pathlib import Path

import pytest

from content_pipeline.contracts.errors import ExtractionPhaseError
from content_pipeline.llm.phase_runner import PhaseRunner
from content_pipeline.llm.phase_schemas import PANEL_CLASSIFICATION_SCHEMA
from content_pipeline.llm.schema_distiller import SchemaDistiller


_SCHEMA = {
    "title": "TinyOutput",
    "type": "object",
    "required": ["name", "status"],
    "properties": {
        "name": {"type": "string"},
        "status": {"type": "string", "enum": ["ok", "bad"]},
        "items": {"type": "array", "items": {"type": "string"}},
    },
}


def test_schema_distiller_does_not_embed_full_schema(tmp_path: Path) -> None:
    path = tmp_path / "schema.json"
    path.write_text(json.dumps(_SCHEMA), encoding="utf-8")

    contract = SchemaDistiller().distill_file(path)
    rendered = contract.render()

    assert "Required fields: name, status" in rendered
    assert "ok, bad" in rendered
    assert json.dumps(_SCHEMA) not in rendered
    assert "properties" not in rendered


def test_llm_phase_error_is_logged_not_swallowed(tmp_path: Path) -> None:
    class FailingModel:
        def call_json(self, *, prompt, inputs):
            raise RuntimeError("phase unavailable")

    path = tmp_path / "schema.json"
    path.write_text(json.dumps(_SCHEMA), encoding="utf-8")
    contract = SchemaDistiller().distill(_SCHEMA)
    audit = []

    with pytest.raises(ExtractionPhaseError):
        PhaseRunner().run_phase(
            phase_name="tiny",
            prompt_template="Return JSON.",
            compact_contract=contract,
            inputs={},
            image_ref=None,
            output_schema_path=path,
            model_client=FailingModel(),
            audit_trace=audit,
        )

    assert audit[0]["phase_name"] == "tiny"
    assert audit[0]["exception_type"] == "RuntimeError"


def test_llm_phase_bad_json_logged(tmp_path: Path) -> None:
    class BadJsonModel:
        def call_json(self, *, prompt, inputs):
            return "not json at all {{{"

    path = tmp_path / "schema.json"
    path.write_text(json.dumps(_SCHEMA), encoding="utf-8")
    contract = SchemaDistiller().distill(_SCHEMA)
    audit = []

    with pytest.raises(ExtractionPhaseError):
        PhaseRunner().run_phase(
            phase_name="bad_json",
            prompt_template="Return JSON.",
            compact_contract=contract,
            inputs={},
            image_ref=None,
            output_schema_path=path,
            model_client=BadJsonModel(),
            audit_trace=audit,
        )

    assert any("JSONDecodeError" in e.get("exception_type", "") for e in audit)


def test_llm_phase_schema_invalid_logged(tmp_path: Path) -> None:
    class SchemaInvalidModel:
        def call_json(self, *, prompt, inputs):
            return {"name": "test"}  # missing required 'status'

    path = tmp_path / "schema.json"
    path.write_text(json.dumps(_SCHEMA), encoding="utf-8")
    contract = SchemaDistiller().distill(_SCHEMA)
    audit = []

    payload = PhaseRunner().run_phase(
        phase_name="schema_invalid",
        prompt_template="Return JSON.",
        compact_contract=contract,
        inputs={},
        image_ref=None,
        output_schema_path=path,
        model_client=SchemaInvalidModel(),
        audit_trace=audit,
    )

    assert payload.get("_degraded") is True
    assert payload.get("warnings") and "schema_validation_failed_degraded" in payload["warnings"]
    assert any("ValidationError" in e.get("exception_type", "") or "Schema" in e.get("exception_type", "") for e in audit)


def test_prompt_does_not_contain_full_schema(tmp_path: Path) -> None:
    path = tmp_path / "schema.json"
    path.write_text(json.dumps(_SCHEMA), encoding="utf-8")
    contract = SchemaDistiller().distill(_SCHEMA)
    rendered = contract.render()

    assert "properties" not in rendered
    assert '"type": "object"' not in rendered


def test_llm_phase_unwraps_contract_object() -> None:
    class WrappedModel:
        def call_json(self, *, prompt, inputs):
            return {"tiny_output": {"name": "wrapped", "status": "ok", "items": ["a"]}}

    audit = []
    payload = PhaseRunner().run_phase(
        phase_name="wrapped",
        prompt_template="Return JSON.",
        compact_contract=SchemaDistiller().distill(_SCHEMA, object_name="tiny_output"),
        inputs={},
        image_ref=None,
        output_schema=_SCHEMA,
        model_client=WrappedModel(),
        audit_trace=audit,
    )

    assert payload == {"name": "wrapped", "status": "ok", "items": ["a"]}
    assert any(e.get("event") == "schema_unwrap_applied" for e in audit)


def test_llm_phase_unwraps_panel_semantic_profile_object() -> None:
    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["panel_relevance", "extraction_decision", "application_task", "assay", "metric_category"],
        "properties": {
            "panel_relevance": {"type": "string", "enum": ["benchmark_metric", "supporting_observation", "context_only", "unusable"]},
            "extraction_decision": {"type": "string", "enum": ["extract_metrics", "extract_observation_only", "skip"]},
            "application_task": {"type": "string"},
            "assay": {"type": "string"},
            "metric_category": {"type": "string"},
        },
    }

    class WrappedPanelModel:
        def call_json(self, *, prompt, inputs):
            return {
                "panel_semantic_profile": {
                    "panel_relevance": "benchmark_metric",
                    "extraction_decision": "extract_metrics",
                    "application_task": "sensing",
                    "assay": "response",
                    "metric_category": "performance_metric",
                }
            }

    audit = []
    payload = PhaseRunner().run_phase(
        phase_name="panel_semantic_classifier",
        prompt_template="Return JSON.",
        compact_contract=SchemaDistiller().distill(schema, object_name="panel_semantic_profile"),
        inputs={},
        image_ref=None,
        output_schema=schema,
        model_client=WrappedPanelModel(),
        audit_trace=audit,
    )

    assert payload["application_task"] == "sensing"
    assert any(e.get("event") == "schema_unwrap_applied" for e in audit)


def test_panel_semantic_classifier_schema_has_panel_type() -> None:
    assert "panel_type" in PANEL_CLASSIFICATION_SCHEMA["required"]
    assert PANEL_CLASSIFICATION_SCHEMA["properties"]["panel_type"]["type"] == "string"
    assert "enum" not in PANEL_CLASSIFICATION_SCHEMA["properties"]["panel_type"]
    assert "evidence_shape" not in PANEL_CLASSIFICATION_SCHEMA["required"]
    assert "evidence_shape" not in PANEL_CLASSIFICATION_SCHEMA["properties"]


def test_panel_semantic_classifier_accepts_new_payload_without_evidence_shape() -> None:
    class PanelTypeModel:
        def call_json(self, *, prompt, inputs):
            return {
                "panel_relevance": "benchmark_metric",
                "extraction_decision": "extract_target_metrics",
                "application_task": "water_transport",
                "assay": "water_uptake_assay",
                "metric_category": "material_structure_metric",
                "panel_type": "numeric_chart",
                "panel_role": "benchmark panel",
                "evidence_role": "primary_metric_panel",
                "matched_target_group_ids": ["water_transport.water_uptake"],
                "allowed_metrics": ["water_uptake"],
                "allowed_units": ["g"],
                "expected_value_types": ["exact_numeric"],
                "needs_digitization": True,
                "digitization_reason": "numeric chart values",
                "exclusion_reason": "",
                "expected_metric_fields": ["metric_name", "value", "unit"],
                "recommended_metric_set": ["water_uptake"],
                "main_entities": {},
                "visible_modalities": {"chart": True},
                "ontology_terms": {},
                "why_relevant": "matches the target metric group",
                "confidence": 0.8,
            }

    payload = PhaseRunner().run_phase(
        phase_name="panel_semantic_classifier",
        prompt_template="Return JSON.",
        compact_contract=SchemaDistiller().distill(PANEL_CLASSIFICATION_SCHEMA, object_name="panel_semantic_profile"),
        inputs={},
        image_ref=None,
        output_schema=PANEL_CLASSIFICATION_SCHEMA,
        model_client=PanelTypeModel(),
        audit_trace=[],
    )

    assert payload["panel_type"] == "numeric_chart"
    assert "evidence_shape" not in payload


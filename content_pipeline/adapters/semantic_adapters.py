from __future__ import annotations

from typing import Any

from content_pipeline.contracts.errors import ExtractionPhaseError
from content_pipeline.contracts.evidence import EvidencePacket
from content_pipeline.contracts.semantic import PanelSemanticResult



def unwrap_phase_payload(payload: Any, object_name: str, audit: list[dict[str, Any]] | None = None, *, phase_name: str = "") -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    inner = payload.get(object_name)
    if isinstance(inner, dict):
        if audit is not None:
            audit.append({"event": "schema_unwrap_applied", "phase_name": phase_name, "object_name": object_name})
        return inner
    return payload


def panel_semantic_from_payload(
    payload: dict[str, Any],
    packet: EvidencePacket,
    ontology_version: str,
) -> PanelSemanticResult:
    repaired = _copy_aliases(payload, {
        "relevance": "panel_relevance",
        "task": "application_task",
        "application": "application_task",
        "assay_class": "assay",
        "metric_type": "metric_category",
        "metrics": "recommended_metric_set",
        "fields": "expected_metric_fields",
    })
    confidence = _confidence(repaired, default=0.5)
    missing = [
        key for key in ("panel_relevance", "extraction_decision", "application_task", "assay", "metric_category", "panel_type")
        if not repaired.get(key)
    ]
    if missing:
        confidence = min(confidence, 0.4)
    panel_relevance = _enum_text(repaired.get("panel_relevance"), {"background": "context_only"}, default="unusable")
    extraction_decision = _enum_text(repaired.get("extraction_decision"), _EXTRACTION_DECISION_ALIASES, default="")
    if not extraction_decision:
        extraction_decision = "skip_metric_extraction"
    evidence_role = _enum_text(repaired.get("evidence_role"), {}, default="")
    if not evidence_role:
        if extraction_decision == "extract_supporting_observation":
            evidence_role = "supporting_observation"
        else:
            evidence_role = "unusable" if panel_relevance == "unusable" else "schematic_context"
    evidence_links = _string_list(repaired.get("evidence_links"))
    if not evidence_links and packet.primary_caption:
        evidence_links = [packet.primary_caption.evidence_id]
    return PanelSemanticResult(
        paper_id=packet.paper_id,
        figure_id=packet.figure_id,
        panel_id=str(packet.panel_id or ""),
        panel_relevance=panel_relevance,
        extraction_decision=extraction_decision,
        application_task=_text(repaired.get("application_task")),
        assay=_text(repaired.get("assay")),
        metric_category=_text(repaired.get("metric_category")),
        panel_type=_panel_type_or_raise(repaired.get("panel_type"), packet.panel_id),
        panel_role=_text(repaired.get("panel_role")),
        evidence_role=evidence_role,
        needs_digitization=_bool(repaired.get("needs_digitization")),
        digitization_reason=_text(repaired.get("digitization_reason")),
        exclusion_reason=_text(repaired.get("exclusion_reason")),
        main_entities=_dict(repaired.get("main_entities")),
        visible_modalities=_dict(repaired.get("visible_modalities")),
        ontology_terms=_dict(repaired.get("ontology_terms")) | {"ontology_version": ontology_version},
        evidence_links=evidence_links,
        why_relevant=_text(repaired.get("why_relevant")),
        confidence=confidence,
        raw_output=dict(payload),
    )


_EXTRACTION_DECISION_ALIASES = {
    "extract_metrics": "extract_target_metrics",
    "extract_observation_only": "extract_supporting_observation",
    "skip": "skip_metric_extraction",
}


def _copy_aliases(payload: dict[str, Any], aliases: dict[str, str]) -> dict[str, Any]:
    out = dict(payload)
    for src, dst in aliases.items():
        if src in out and dst not in out:
            out[dst] = out[src]
    return out


def _confidence(payload: dict[str, Any], default: float) -> float:
    try:
        value = float(payload.get("confidence", default))
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, value))


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return bool(value)


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _string_list(value: Any) -> list[str]:
    return [str(item) for item in _as_list(value) if item not in (None, "")]


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return ""
    return str(value)


def _enum_text(value: Any, aliases: dict[str, str], default: str) -> str:
    text = _text(value).strip()
    return aliases.get(text, text) or default


def _panel_type_or_raise(value: Any, panel_id: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ExtractionPhaseError(
            f"LLM failed: panel_classifier returned empty panel_type for {panel_id}"
        )
    return text

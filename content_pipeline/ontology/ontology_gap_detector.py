from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass, field
from typing import Any

from content_pipeline.contracts.runtime_ontology_overlay import RuntimeOntologyOverlayContract, contract_from_candidate
from content_pipeline.contracts.unit_dimension_registry import (
    dimension_for_unit,
    expected_units_for_dimension,
    expected_value_types_for_dimension,
    infer_metric_dimension,
    metric_unit_dimensions_compatible,
)

_GAP_REASONS = {
    "unit_not_allowed_for_metric",
    "metric_not_in_matched_target_group",
    "metric_not_allowed_by_panel_target",
    "metric_name_unknown",
    "unknown_target_group",
}


@dataclass(slots=True)
class OntologyGapCandidate:
    paper_id: str
    figure_id: str
    panel_id: str
    metric_name: str
    unit: str
    value: str
    value_type: str
    matched_target_group_id: str
    rejection_reason: str
    evidence_text: str
    evidence_ids: list[str] = field(default_factory=list)
    metric_dimension: str = ""
    unit_dimension: str = ""
    suggested_expected_units: list[str] = field(default_factory=list)
    suggested_value_types: list[str] = field(default_factory=list)
    support_count: int = 1
    approval_status: str = "auto_proposed_review_required"
    confidence: float = 0.0
    proposal_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def detect_ontology_gap_candidates(rejected_rows: list[Any], *, min_support_for_overlay: int = 2) -> list[OntologyGapCandidate]:
    prelim: list[OntologyGapCandidate] = []
    for row in rejected_rows:
        reason = str(getattr(row, "rejection_reason", "") or getattr(row, "verifier_reason", "") or "")
        if reason not in _GAP_REASONS:
            continue
        metric_name = str(getattr(row, "metric_name", "") or "").strip()
        unit = str(getattr(row, "unit", "") or "").strip()
        if not metric_name or metric_name == "metric_name_unknown" or not unit:
            continue
        evidence_text = str(getattr(row, "evidence_text", "") or "")
        metric_dimension = infer_metric_dimension(metric_name, evidence_text=evidence_text)
        unit_dimension = dimension_for_unit(unit)
        if not metric_dimension or not unit_dimension:
            continue
        if not metric_unit_dimensions_compatible(metric_name, unit, evidence_text=evidence_text):
            continue
        prelim.append(OntologyGapCandidate(
            paper_id=str(getattr(row, "paper_id", "") or ""),
            figure_id=str(getattr(row, "figure_id", "") or ""),
            panel_id=str(getattr(row, "panel_id", "") or getattr(row, "source_panel_id", "") or ""),
            metric_name=metric_name,
            unit=unit,
            value=str(getattr(row, "value", "") or ""),
            value_type=str(getattr(row, "value_type", "") or ""),
            matched_target_group_id=str(getattr(row, "matched_target_group_id", "") or ""),
            rejection_reason=reason,
            evidence_text=evidence_text,
            evidence_ids=list(getattr(row, "evidence_ids", []) or []),
            metric_dimension=metric_dimension,
            unit_dimension=unit_dimension,
            suggested_expected_units=list(expected_units_for_dimension(metric_dimension)),
            suggested_value_types=list(expected_value_types_for_dimension(metric_dimension)),
            confidence=_candidate_confidence(row, metric_dimension, unit_dimension),
            proposal_reason="metric name dimension and unit dimension agree; rejected by stable ontology contract",
        ))
    grouped: dict[tuple[str, str], list[OntologyGapCandidate]] = defaultdict(list)
    for candidate in prelim:
        grouped[(candidate.metric_name, candidate.metric_dimension)].append(candidate)
    out: list[OntologyGapCandidate] = []
    for (_metric_name, _dimension), items in grouped.items():
        support_count = len({(item.paper_id, item.figure_id, item.panel_id, item.unit) for item in items})
        status = "auto_accepted_overlay" if support_count >= min_support_for_overlay else "auto_proposed_review_required"
        for item in items:
            item.support_count = support_count
            item.approval_status = status
            out.append(item)
    return out


def overlay_contracts_from_candidates(candidates: list[OntologyGapCandidate], *, min_support_for_overlay: int = 2) -> dict[str, RuntimeOntologyOverlayContract]:
    contracts: dict[str, RuntimeOntologyOverlayContract] = {}
    for candidate in candidates:
        if candidate.support_count < min_support_for_overlay:
            continue
        if candidate.approval_status != "auto_accepted_overlay":
            continue
        contracts[candidate.metric_name] = contract_from_candidate(
            candidate.metric_name,
            dimension=candidate.metric_dimension,
            support_count=candidate.support_count,
            status="auto_accepted_overlay",
        )
    return contracts


def _candidate_confidence(row: Any, metric_dimension: str, unit_dimension: str) -> float:
    base = 0.55
    if getattr(row, "evidence_ids", None):
        base += 0.1
    if getattr(row, "matched_target_group_id", ""):
        base += 0.1
    if metric_dimension == unit_dimension:
        base += 0.15
    try:
        row_conf = float(getattr(row, "confidence", 0.0) or 0.0)
    except (TypeError, ValueError):
        row_conf = 0.0
    if row_conf:
        base = (base + row_conf) / 2
    return min(0.95, max(0.0, base))

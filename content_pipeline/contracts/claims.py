from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

CLAIM_STATUS_ACCEPTED = "accepted"
CLAIM_STATUS_CONFLICTING = "conflicting"
CLAIM_STATUS_REJECTED = "rejected"
CLAIM_STATUS_WEAK = "weak"


@dataclass(slots=True)
class Claim:
    claim_id: str
    paper_id: str
    figure_id: str
    panel_id: str
    metric_name: str
    metric_category: str
    target: str
    material_or_matrix: str
    biological_agent: str
    application_task: str
    assay: str
    condition: str
    comparison: str
    value: str
    value_min: str | None = None
    value_max: str | None = None
    unit: str = ""
    value_type: str = ""
    direction: str = ""
    source_panel_id: str = ""
    evidence_text: str = ""
    evidence_level: str = ""
    evidence_ids: list[str] = field(default_factory=list)
    confidence: float = 0.0
    status: str = CLAIM_STATUS_WEAK
    verifier_status: str = ""
    verifier_reason: str = ""
    extraction_source: str = ""
    needs_digitization_verification: bool = False
    matched_target_group_id: str = ""
    raw_output: dict[str, Any] = field(default_factory=dict)

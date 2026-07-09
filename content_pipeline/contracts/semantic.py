from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any



@dataclass(slots=True)
class PanelSemanticResult:
    paper_id: str
    figure_id: str
    panel_id: str
    panel_relevance: str
    extraction_decision: str
    application_task: str
    assay: str
    metric_category: str
    panel_type: str
    panel_role: str
    evidence_role: str = ""
    needs_digitization: bool = False
    digitization_reason: str = ""
    exclusion_reason: str = ""
    main_entities: dict[str, Any] = field(default_factory=dict)
    visible_modalities: dict[str, Any] = field(default_factory=dict)
    ontology_terms: dict[str, Any] = field(default_factory=dict)
    evidence_links: list[str] = field(default_factory=list)
    why_relevant: str = ""
    confidence: float = 0.0
    raw_output: dict[str, Any] = field(default_factory=dict)



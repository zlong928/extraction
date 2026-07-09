from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ExtractionRunResult:
    document_graph_summary: dict[str, Any]
    figure_panel_graph: dict[str, Any]
    evidence_packets: list[Any] = field(default_factory=list)
    chart_digitization_results: list[Any] = field(default_factory=list)
    chart_facts: list[Any] = field(default_factory=list)
    chart_points: list[Any] = field(default_factory=list)
    panel_fact_rows: list[Any] = field(default_factory=list)
    heatmap_candidates: list[Any] = field(default_factory=list)
    visual_fact_results: list[Any] = field(default_factory=list)
    image_observations: list[Any] = field(default_factory=list)
    output_paths: dict[str, str] = field(default_factory=dict)
    audit_trace: list[dict[str, Any]] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    status: str = "succeeded"


@dataclass(slots=True)
class ExtractionPipelineOptions:
    fail_fast: bool = True
    max_workers: int = 4
    llm_max_workers: int = 16
    chart_only: bool = False
    enable_quality_gates: bool = True

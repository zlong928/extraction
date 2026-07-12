from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


AUDIT_SCHEMA_VERSION = "audit.v1"
CSV_SCHEMA_VERSION = "csv.v2"
RUN_METADATA_SCHEMA_VERSION = "run-metadata.v1"
PROMPT_SET_ID = "content-pipeline-prompts.v1"


def build_run_metadata(model_client: Any | None = None) -> dict[str, str]:
    client = getattr(model_client, "client", model_client)
    model_id = str(getattr(client, "model", "unknown-model") or "unknown-model")
    return {
        "schema_version": RUN_METADATA_SCHEMA_VERSION,
        "run_id": str(uuid4()),
        "model_id": model_id,
        "prompt_set_id": PROMPT_SET_ID,
        "started_at": datetime.now(timezone.utc).isoformat(),
    }


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
    run_metadata: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class ExtractionPipelineOptions:
    fail_fast: bool = True
    max_workers: int = 4
    llm_max_workers: int = 16
    chart_only: bool = False
    enable_quality_gates: bool = True

from __future__ import annotations

from pathlib import Path
from typing import Any

from content_pipeline.contracts.runtime_ontology_overlay import load_runtime_ontology_overlay, write_runtime_ontology_overlay
from content_pipeline.ontology.ontology_gap_detector import OntologyGapCandidate, overlay_contracts_from_candidates


def build_runtime_overlay_from_candidates(candidates: list[OntologyGapCandidate], *, overlay_path: str | Path, min_support_for_overlay: int = 2, merge_existing: bool = True) -> dict[str, Any]:
    promoted = overlay_contracts_from_candidates(candidates, min_support_for_overlay=min_support_for_overlay)
    if merge_existing:
        merged = dict(load_runtime_ontology_overlay(str(overlay_path)))
        merged.update(promoted)
    else:
        merged = promoted
    write_runtime_ontology_overlay(overlay_path, merged)
    return merged

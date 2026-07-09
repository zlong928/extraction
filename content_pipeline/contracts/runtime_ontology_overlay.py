from __future__ import annotations

import json
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from content_pipeline.contracts.unit_dimension_registry import expected_units_for_dimension, expected_value_types_for_dimension


_ACCEPTED_STATUSES = {"auto_accepted_overlay", "promoted_overlay", "stable"}
_DEFAULT_OVERLAY_PATH = Path(__file__).resolve().parents[2] / "data" / "runtime_ontology_overlay.json"


@dataclass(frozen=True, slots=True)
class RuntimeOntologyOverlayContract:
    metric_id: str
    expected_units: tuple[str, ...]
    expected_value_types: tuple[str, ...]
    dimension: str = ""
    status: str = "auto_accepted_overlay"
    support_count: int = 0
    source: str = "runtime_overlay"


def overlay_path() -> Path:
    return Path(os.environ.get("CONTENT_PIPELINE_ONTOLOGY_OVERLAY", str(_DEFAULT_OVERLAY_PATH)))


@lru_cache(maxsize=4)
def load_runtime_ontology_overlay(path: str | None = None) -> dict[str, RuntimeOntologyOverlayContract]:
    overlay_file = Path(path) if path else overlay_path()
    if not overlay_file.exists():
        return {}
    try:
        payload = json.loads(overlay_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    contracts = payload.get("contracts", {}) if isinstance(payload, dict) else {}
    out: dict[str, RuntimeOntologyOverlayContract] = {}
    if not isinstance(contracts, dict):
        return out
    for metric_id, raw in contracts.items():
        if not isinstance(raw, dict):
            continue
        status = str(raw.get("status") or "")
        if status not in _ACCEPTED_STATUSES:
            continue
        dimension = str(raw.get("dimension") or "")
        units = tuple(str(item) for item in raw.get("expected_units") or expected_units_for_dimension(dimension) if item)
        value_types = tuple(str(item) for item in raw.get("expected_value_types") or expected_value_types_for_dimension(dimension) if item)
        if not metric_id or not units:
            continue
        out[str(metric_id)] = RuntimeOntologyOverlayContract(
            metric_id=str(metric_id),
            expected_units=units,
            expected_value_types=value_types or ("exact_numeric", "approximate_numeric", "trend"),
            dimension=dimension,
            status=status,
            support_count=_int(raw.get("support_count")),
            source=str(raw.get("source") or "runtime_overlay"),
        )
    return out


def write_runtime_ontology_overlay(path: str | Path, contracts: dict[str, RuntimeOntologyOverlayContract]) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": "runtime_overlay_v1",
        "contracts": {
            metric_id: {
                "dimension": contract.dimension,
                "expected_units": list(contract.expected_units),
                "expected_value_types": list(contract.expected_value_types),
                "status": contract.status,
                "support_count": contract.support_count,
                "source": contract.source,
            }
            for metric_id, contract in sorted(contracts.items())
        },
    }
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    load_runtime_ontology_overlay.cache_clear()
    return out


def contract_from_candidate(metric_id: str, *, dimension: str, support_count: int, status: str) -> RuntimeOntologyOverlayContract:
    return RuntimeOntologyOverlayContract(
        metric_id=metric_id,
        dimension=dimension,
        expected_units=expected_units_for_dimension(dimension),
        expected_value_types=expected_value_types_for_dimension(dimension) or ("exact_numeric", "approximate_numeric", "trend"),
        status=status,
        support_count=support_count,
        source="ontology_auto_builder",
    )


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0

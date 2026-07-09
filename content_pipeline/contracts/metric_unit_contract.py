from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from content_pipeline.contracts.runtime_ontology_overlay import load_runtime_ontology_overlay


@dataclass(frozen=True, slots=True)
class MetricUnitContract:
    metric_id: str
    expected_units: tuple[str, ...]
    expected_value_types: tuple[str, ...]
    source: str = "BENCHMARK_ONTOLOGY_V1"
    dimension: str = ""


_ONTOLOGY_PATH = Path(__file__).resolve().parents[2] / "docs" / "BENCHMARK_ONTOLOGY_V1.md"


@lru_cache(maxsize=1)
def metric_unit_contracts() -> dict[str, MetricUnitContract]:
    """Load metric -> expected-unit contracts from BENCHMARK_ONTOLOGY_V1.

    The verifier should not hard-code field-specific rules such as response_time
    vs. compressive_strength. The ontology owns those contracts; runtime code only
    parses and enforces them.
    """
    text = _ONTOLOGY_PATH.read_text(encoding="utf-8")
    table = _metric_contract_table(text)
    contracts: dict[str, MetricUnitContract] = {}
    for row in table:
        metric_id = row.get("metric_id", "").strip()
        units = tuple(_split_units(row.get("expected_units", "")))
        value_types = tuple(_split_units(row.get("expected_value_types", "")))
        if metric_id and units:
            contracts[metric_id] = MetricUnitContract(metric_id=metric_id, expected_units=units, expected_value_types=value_types)
    for metric_id, overlay_contract in load_runtime_ontology_overlay().items():
        contracts[metric_id] = MetricUnitContract(
            metric_id=metric_id,
            expected_units=overlay_contract.expected_units,
            expected_value_types=overlay_contract.expected_value_types,
            source=overlay_contract.source,
            dimension=overlay_contract.dimension,
        )
    return contracts


def expected_units_for_metric(metric_name: str) -> tuple[str, ...]:
    contract = metric_unit_contracts().get(str(metric_name or "").strip())
    return contract.expected_units if contract else ()


def expected_value_types_for_metric(metric_name: str) -> tuple[str, ...]:
    contract = metric_unit_contracts().get(str(metric_name or "").strip())
    return contract.expected_value_types if contract else ()


def unit_matches_metric(metric_name: str, unit: str) -> bool:
    expected = expected_units_for_metric(metric_name)
    if not expected:
        return True
    actual = _normalize_unit(unit)
    if not actual:
        return False
    return any(actual == _normalize_unit(item) for item in expected)


def value_contains_unit(value: Any, unit: str) -> bool:
    unit_norm = _normalize_unit(unit)
    if not unit_norm:
        return False
    value_text = str(value or "").lower()
    return unit_norm in {_normalize_unit(token) for token in value_text.replace("/", " / ").split()}


def _metric_contract_table(text: str) -> list[dict[str, str]]:
    lines = text.splitlines()
    start = next((idx for idx, line in enumerate(lines) if line.strip().startswith("| metric_id |")), -1)
    if start < 0:
        return []
    header = [cell.strip() for cell in lines[start].strip().strip("|").split("|")]
    rows: list[dict[str, str]] = []
    for line in lines[start + 2:]:
        stripped = line.strip()
        if not stripped.startswith("|"):
            break
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if len(cells) != len(header):
            continue
        rows.append(dict(zip(header, cells)))
    return rows


def _split_units(value: str) -> list[str]:
    units: list[str] = []
    for item in str(value or "").split(","):
        unit = item.strip()
        if unit:
            units.append(unit)
    return list(dict.fromkeys(units))


def _normalize_unit(value: Any) -> str:
    text = str(value or "").strip().lower()
    replacements = {
        "µ": "u",
        "μ": "u",
        " ": "",
        "·": "",
        "^": "",
        "per": "/",
    }
    for src, dst in replacements.items():
        text = text.replace(src, dst)
    return text

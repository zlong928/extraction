from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class RepairSeverity(Enum):
    INFO = "info"
    WARN = "warn"
    ERROR = "error"
    CRITICAL = "critical"


_REPAIR_SEVERITY_MAP: dict[str, RepairSeverity] = {
    "scalar_to_array": RepairSeverity.INFO,
    "null_to_empty_array": RepairSeverity.INFO,
    "null_to_string": RepairSeverity.INFO,
    "tuple_to_array": RepairSeverity.INFO,
    "scalar_to_object": RepairSeverity.INFO,
    "scalar_to_string": RepairSeverity.INFO,
    "string_to_number": RepairSeverity.INFO,
    "string_to_boolean": RepairSeverity.INFO,
    "copy_nested_axis": RepairSeverity.INFO,
    "normalize_field_path": RepairSeverity.INFO,
    "copy_alias_property": RepairSeverity.INFO,
    "copy_additional_property": RepairSeverity.INFO,
    "enum_value": RepairSeverity.WARN,
    "sequence_to_object": RepairSeverity.WARN,
    "array_to_string": RepairSeverity.WARN,
    "dict_to_string": RepairSeverity.WARN,
    "fill_known_required": RepairSeverity.WARN,
    "copy_scalar_value_to_missing_chart_type": RepairSeverity.WARN,
    "copy_confidence_alias": RepairSeverity.WARN,
    "axis_object_to_unknown_readability": RepairSeverity.WARN,
    "legend_object_to_unknown_readability": RepairSeverity.WARN,
    "infer_panel_relevance": RepairSeverity.WARN,
    "unwrap_single_key_object": RepairSeverity.WARN,
    "unwrap_single_key_schema_wrapper": RepairSeverity.WARN,
    "unwrap_contract_object": RepairSeverity.WARN,
    "unwrap_contract_object_single_key": RepairSeverity.WARN,
    "drop_additional_property": RepairSeverity.ERROR,
    "json_string_to_object": RepairSeverity.ERROR,
    "invalid_payload_to_empty_chart_semantic": RepairSeverity.CRITICAL,
    "noise_key_dropped": RepairSeverity.ERROR,
    "payload_not_dict": RepairSeverity.CRITICAL,
    "payload_empty": RepairSeverity.CRITICAL,
    "json_decode_failed": RepairSeverity.CRITICAL,
    "infer_image_kind_from_type_field": RepairSeverity.WARN,
}

HIGH_REPAIR_COUNT_THRESHOLD = 12
CRITICAL_REPAIR_THRESHOLD = 3


def classify_repair_severity(repair: dict[str, Any]) -> RepairSeverity:
    repair_type = repair.get("repair", "")
    return _REPAIR_SEVERITY_MAP.get(repair_type, RepairSeverity.WARN)


def count_by_severity(repairs: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for r in repairs:
        sev = classify_repair_severity(r).value
        counts[sev] = counts.get(sev, 0) + 1
    return counts


def should_degrade(repairs: list[dict[str, Any]]) -> bool:
    counts = count_by_severity(repairs)
    if counts.get("critical", 0) >= 1:
        return True
    if counts.get("error", 0) >= CRITICAL_REPAIR_THRESHOLD:
        return True
    if len(repairs) >= HIGH_REPAIR_COUNT_THRESHOLD:
        return True
    return False


@dataclass
class PhaseAdaptResult:
    payload: dict[str, Any]
    repairs: list[dict[str, Any]] = field(default_factory=list)


class PhaseAdapter(ABC):
    @abstractmethod
    def adapt_payload(self, raw: Any) -> PhaseAdaptResult:
        ...

    @abstractmethod
    def fallback_payload(self, warnings: list[str] | None = None) -> dict[str, Any]:
        ...


def decode_json_from_model_text(raw: str) -> Any | None:
    for candidate in _json_text_candidates(raw):
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            try:
                return json.loads(candidate, strict=False)
            except json.JSONDecodeError:
                continue
    return None


def _json_text_candidates(raw: str) -> list[str]:
    text = raw.strip()
    candidates: list[str] = []
    if text:
        candidates.append(text)
    for match in re.finditer(r"```(?:json|JSON)?\s*(.*?)\s*```", text, flags=re.DOTALL):
        fenced = match.group(1).strip()
        if fenced and fenced not in candidates:
            candidates.append(fenced)
    return candidates


NOISE_KEYS: set[str] = {":", ".", ",", " ", "", "null", "undefined", "none"}


def clean_payload_keys(raw: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    repairs: list[dict[str, Any]] = []
    cleaned: dict[str, Any] = {}
    for k, v in raw.items():
        clean_key = str(k).strip().strip("'\"")
        while clean_key.startswith("."):
            clean_key = clean_key[1:]
        clean_key = clean_key.strip()
        if not clean_key or clean_key.lower() in NOISE_KEYS:
            repairs.append({"path": repr(k), "repair": "noise_key_dropped"})
            continue
        cleaned[clean_key] = v
    return cleaned, repairs

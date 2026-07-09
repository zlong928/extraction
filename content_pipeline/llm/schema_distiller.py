from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from content_pipeline.llm.prompt_contracts import PromptContract


class SchemaDistiller:
    """Convert a full JSON schema into a compact prompt contract.

    The full schema remains a local validation artifact and is not embedded in
    prompts by this class.
    """

    def distill_file(self, schema_path: str | Path, *, object_name: str | None = None) -> PromptContract:
        schema = json.loads(Path(schema_path).read_text(encoding="utf-8"))
        return self.distill(schema, object_name=object_name)

    def distill(self, schema: dict[str, Any], *, object_name: str | None = None) -> PromptContract:
        root = schema.get("properties", {}) if isinstance(schema, dict) else {}
        required = [str(item) for item in schema.get("required", [])] if isinstance(schema.get("required"), list) else []
        field_types: dict[str, str] = {}
        enums: dict[str, list[Any]] = {}
        skeleton: dict[str, Any] = {}
        for name, spec in root.items():
            if not isinstance(spec, dict):
                continue
            field_types[name] = _type_summary(spec)
            if isinstance(spec.get("enum"), list):
                enums[name] = spec["enum"]
            skeleton[name] = _empty_for_type(spec)
        return PromptContract(
            object_name=object_name or str(schema.get("title") or "output"),
            required_fields=required,
            field_types=field_types,
            enum_values=enums,
            must_not=[
                "Do not invent values not supported by evidence_id references.",
                "Do not include fields outside the contract.",
                "Do not return prose outside JSON.",
            ],
            output_skeleton=skeleton,
        )


def _type_summary(spec: dict[str, Any]) -> str:
    if "type" in spec:
        typ = spec["type"]
        if typ == "array" and isinstance(spec.get("items"), dict):
            return f"array[{_type_summary(spec['items'])}]"
        return str(typ)
    if "anyOf" in spec:
        return " | ".join(_type_summary(s) for s in spec["anyOf"] if isinstance(s, dict))
    return "unknown"


def _empty_for_type(spec: dict[str, Any]) -> Any:
    typ = spec.get("type")
    if typ == "object":
        return {}
    if typ == "array":
        return []
    if typ == "boolean":
        return False
    if typ in {"number", "integer"}:
        return 0
    return ""

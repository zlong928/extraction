from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class PromptContract:
    object_name: str
    required_fields: list[str] = field(default_factory=list)
    field_types: dict[str, str] = field(default_factory=dict)
    enum_values: dict[str, list[Any]] = field(default_factory=dict)
    must_not: list[str] = field(default_factory=list)
    output_skeleton: dict[str, Any] = field(default_factory=dict)

    def render(self) -> str:
        lines = [f"Return a JSON object named {self.object_name}."]
        if self.required_fields:
            lines.append("Required fields: " + ", ".join(self.required_fields))
        if self.field_types:
            lines.append("Field types:")
            lines.extend(f"- {key}: {value}" for key, value in self.field_types.items())
        if self.enum_values:
            lines.append("Allowed enum values:")
            lines.extend(f"- {key}: {', '.join(map(str, values))}" for key, values in self.enum_values.items())
        if self.must_not:
            lines.append("Must not:")
            lines.extend(f"- {rule}" for rule in self.must_not)
        if self.output_skeleton:
            lines.append("Output skeleton:")
            lines.append(json.dumps(self.output_skeleton, ensure_ascii=False, indent=2))
        lines.append("Type rules:")
        lines.append("- If a field type is array, return a JSON array even when there is only one item.")
        lines.append("- If a field type is object, return a JSON object with the required child keys.")
        lines.append("- Use empty arrays [] or empty strings \"\" for unsupported observations instead of prose outside the schema.")
        return "\n".join(lines)

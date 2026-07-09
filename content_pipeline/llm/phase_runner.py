from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from jsonschema import validate

from content_pipeline.contracts.errors import ExtractionPhaseError, ExtractionSchemaError
from content_pipeline.llm.phase_adapter import (
    PhaseAdapter,
    count_by_severity,
    should_degrade,
)
from content_pipeline.llm.prompt_contracts import PromptContract


_DEGRADED_KEY = "_degraded"
_REPAIR_META_KEY = "_repair_meta"


class PhaseRunner:
    """Run one JSON-producing model phase with compact contract and local schema validation."""

    def run_phase(
        self,
        *,
        phase_name: str,
        prompt_template: str,
        compact_contract: PromptContract,
        inputs: dict[str, Any],
        image_ref: str | None,
        output_schema: str | Path | dict[str, Any] | None = None,
        output_schema_path: str | Path | dict[str, Any] | None = None,
        model_client: Any,
        audit_trace: list[dict[str, Any]] | None = None,
        pre_validate_normalizer: Callable[[Any], tuple[dict[str, Any], list[dict[str, Any]]]] | None = None,
        phase_adapter: PhaseAdapter | None = None,
    ) -> dict[str, Any]:
        if output_schema is None:
            output_schema = output_schema_path
        if output_schema is None:
            raise ValueError("run_phase requires output_schema or output_schema_path")
        audit = audit_trace if audit_trace is not None else []
        prompt = f"{prompt_template}\n\n{compact_contract.render()}"
        max_attempts = 2 if phase_name in {"metric_extractor", "image_observation", "panel_semantic_classifier"} else 1
        last_payload: dict[str, Any] | None = None
        last_error: Exception | None = None
        last_error_type = ""
        last_message = ""
        for attempt in range(1, max_attempts + 1):
            try:
                payload = self._run_phase_once(
                    phase_name=phase_name,
                    prompt=prompt,
                    compact_contract=compact_contract,
                    inputs=inputs,
                    image_ref=image_ref,
                    output_schema=output_schema,
                    model_client=model_client,
                    audit=audit,
                    pre_validate_normalizer=pre_validate_normalizer,
                    phase_adapter=phase_adapter,
                )
                if payload.get(_DEGRADED_KEY):
                    last_payload = payload
                else:
                    return payload
            except (ExtractionPhaseError, ExtractionSchemaError) as exc:
                last_error = exc
                last_error_type = type(exc).__name__
                last_message = str(exc)
                if attempt >= max_attempts:
                    break
                audit.append({
                    "event": "llm_phase_retry",
                    "phase_name": phase_name,
                    "attempt": attempt,
                    "max_attempts": max_attempts,
                    "exception_type": last_error_type,
                    "message": last_message,
                })
        if last_payload is not None:
            return last_payload
        if last_error is not None:
            audit.append({
                "event": "llm_phase_failed_after_retries",
                "phase_name": phase_name,
                "attempts": max_attempts,
                "exception_type": last_error_type,
                "message": last_message,
            })
            raise last_error
        raise ExtractionPhaseError(f"LLM phase failed: {phase_name}: unknown error")

    def _run_phase_once(
        self,
        *,
        phase_name: str,
        prompt: str,
        compact_contract: PromptContract,
        inputs: dict[str, Any],
        image_ref: str | None,
        output_schema: str | Path | dict[str, Any],
        model_client: Any,
        audit: list[dict[str, Any]],
        pre_validate_normalizer: Callable[[Any], tuple[dict[str, Any], list[dict[str, Any]]]] | None = None,
        phase_adapter: PhaseAdapter | None = None,
    ) -> dict[str, Any]:
        audit.append({"event": "llm_phase_started", "phase_name": phase_name})
        try:
            raw = model_client.call_json(prompt=prompt, inputs={**inputs, "image_ref": image_ref})
        except Exception as exc:
            event = {"phase_name": phase_name, "exception_type": type(exc).__name__, "message": str(exc)}
            if audit and audit[-1].get("event") == "llm_phase_started":
                audit[-1].update(event)
            else:
                audit.append(event)
            raise ExtractionPhaseError(f"LLM phase failed: {phase_name}: {exc}") from exc

        all_repairs: list[dict[str, Any]] = []

        if phase_adapter is not None:
            result = phase_adapter.adapt_payload(raw)
            payload = result.payload
            all_repairs.extend(result.repairs)
            audit.append({
                "event": "llm_phase_payload_received",
                "phase_name": phase_name,
                "payload_shape": _payload_shape(payload),
                "adapter": type(phase_adapter).__name__,
            })
            if _is_fallback(payload, phase_adapter):
                audit.append({
                    "phase_name": phase_name,
                    "event": "phase_adapter_fallback_used",
                    "repairs": result.repairs[:50],
                })
                payload[_DEGRADED_KEY] = True
                payload[_REPAIR_META_KEY] = {"severity": "critical", "repair_count": len(all_repairs)}
                audit.append({"event": "llm_phase_completed", "phase_name": phase_name,
                              "status": "degraded_adapter_fallback"})
                return payload
        else:
            audit.append({
                "event": "llm_phase_payload_received",
                "phase_name": phase_name,
                "payload_shape": _payload_shape(raw),
            })
            try:
                payload = json.loads(raw) if isinstance(raw, str) else raw
            except json.JSONDecodeError as exc:
                audit.append({"phase_name": phase_name, "exception_type": type(exc).__name__,
                             "message": str(exc), "output_excerpt": str(raw)[:500]})
                raise ExtractionPhaseError(f"LLM phase returned invalid JSON: {phase_name}") from exc

        if isinstance(output_schema, (str, Path)):
            schema = json.loads(Path(output_schema).read_text(encoding="utf-8"))
        else:
            schema = output_schema
        payload, unwrap_events = _unwrap_contract_object(
            payload=payload,
            compact_contract=compact_contract,
            schema=schema,
        )
        if unwrap_events:
            all_repairs.extend(unwrap_events)
            audit.append({"phase_name": phase_name, "event": "schema_unwrap_applied", "repairs": unwrap_events})
        if pre_validate_normalizer is not None:
            payload, pre_validate_events = pre_validate_normalizer(payload)
            all_repairs.extend(pre_validate_events)
            audit.append({
                "event": "llm_phase_payload_adapted",
                "phase_name": phase_name,
                "payload_shape": _payload_shape(payload),
                "adapter": getattr(pre_validate_normalizer, "__name__", "pre_validate_normalizer"),
            })
            if pre_validate_events:
                audit.append({"phase_name": phase_name, "event": "schema_repair_applied", "repairs": pre_validate_events[:50]})
        payload, repair_events = _repair_payload_for_schema(
            payload=payload,
            schema=schema,
            inputs=inputs,
            image_ref=image_ref,
        )
        all_repairs.extend(repair_events)
        if repair_events:
            audit.append({"phase_name": phase_name, "event": "schema_repair_applied", "repairs": repair_events[:50]})

        severity_counts = count_by_severity(all_repairs)
        is_degraded = should_degrade(all_repairs, _dict_key_count(payload))

        try:
            validate(instance=payload, schema=schema)
        except Exception as exc:
            audit.append({"phase_name": phase_name, "exception_type": type(exc).__name__,
                         "message": str(exc), "output_excerpt": str(payload)[:500]})
            payload[_DEGRADED_KEY] = True
            payload[_REPAIR_META_KEY] = {
                "severity": "critical",
                "repair_count": len(all_repairs),
                "severity_counts": severity_counts,
                "validation_error": str(exc)[:200],
            }
            warnings = _ensure_warnings_list(payload)
            warnings.append("schema_validation_failed_degraded")
            audit.append({"event": "llm_phase_completed", "phase_name": phase_name,
                          "status": "degraded_validation_failed"})
            return payload

        if is_degraded:
            payload[_DEGRADED_KEY] = True
            payload[_REPAIR_META_KEY] = {
                "severity": "warn",
                "repair_count": len(all_repairs),
                "severity_counts": severity_counts,
            }
            warnings = _ensure_warnings_list(payload)
            warnings.append("high_repair_count_degraded")
            audit.append({"event": "llm_phase_completed", "phase_name": phase_name,
                          "status": "degraded_high_repairs",
                          "repair_count": len(all_repairs),
                          "severity_counts": severity_counts})
            return payload

        audit.append({"event": "llm_phase_completed", "phase_name": phase_name})
        return payload


def _unwrap_contract_object(
    *,
    payload: Any,
    compact_contract: PromptContract,
    schema: dict[str, Any],
) -> tuple[Any, list[dict[str, Any]]]:
    if not isinstance(payload, dict):
        return payload, []
    object_name = getattr(compact_contract, "object_name", "")
    schema_props = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
    required = set(schema.get("required", []))
    if object_name and object_name in payload and isinstance(payload.get(object_name), dict) and object_name not in schema_props:
        inner = payload[object_name]
        inner_keys = set(inner.keys())
        if required and inner_keys.intersection(required):
            return inner, [{"path": object_name, "repair": "unwrap_contract_object"}]
        if len(payload) == 1:
            return inner, [{"path": object_name, "repair": "unwrap_contract_object_single_key"}]
    if len(payload) == 1:
        key, value = next(iter(payload.items()))
        if str(key) not in schema_props and isinstance(value, dict):
            value_keys = set(value.keys())
            if not required or value_keys.intersection(required) or str(key).strip().strip("'\"") in {":", ""}:
                return value, [{"path": str(key), "repair": "unwrap_single_key_schema_wrapper"}]
    return payload, []


def _repair_payload_for_schema(
    *,
    payload: Any,
    schema: dict[str, Any],
    inputs: dict[str, Any],
    image_ref: str | None,
) -> tuple[Any, list[dict[str, Any]]]:
    repairs: list[dict[str, Any]] = []
    repaired = _repair_value(payload, schema, path=[], inputs=inputs, image_ref=image_ref, repairs=repairs)
    return repaired, repairs


def _payload_shape(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return {"type": "dict", "keys": [str(key) for key in list(value.keys())[:30]], "key_count": len(value)}
    if isinstance(value, list):
        return {"type": "list", "length": len(value)}
    if isinstance(value, str):
        return {"type": "str", "length": len(value), "excerpt": value[:200]}
    return {"type": type(value).__name__}


def _repair_value(
    value: Any,
    schema: dict[str, Any],
    *,
    path: list[str],
    inputs: dict[str, Any],
    image_ref: str | None,
    repairs: list[dict[str, Any]],
) -> Any:
    if not isinstance(schema, dict):
        return value

    if "enum" in schema and isinstance(schema["enum"], list) and schema["enum"]:
        if value not in schema["enum"]:
            replacement = _enum_repair_value(value, schema["enum"])
            if replacement is not None:
                repairs.append({"path": _path(path), "repair": "enum_value", "from": _short(value), "to": replacement})
                value = replacement

    typ = schema.get("type")
    if isinstance(typ, list):
        if value is None and "null" in typ:
            return value
        typ = next((item for item in typ if item != "null"), typ[0] if typ else None)
    if typ == "object":
        if not isinstance(value, dict):
            if _object_can_be_repaired_from_scalar(schema):
                repaired_object = _object_from_scalar(value, schema)
                repairs.append({"path": _path(path), "repair": "scalar_to_object", "from": type(value).__name__})
                value = repaired_object
            elif _object_can_be_repaired_from_sequence(schema, value):
                repaired_object = _object_from_sequence(value)
                repairs.append({
                    "path": _path(path),
                    "repair": "sequence_to_object",
                    "from": type(value).__name__,
                    "to": "dict",
                })
                value = repaired_object
        if not isinstance(value, dict):
            return value
        props = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
        out = dict(value)
        _copy_alias_fields(out, props, repairs, path)
        for key in list(out.keys()):
            if key in props:
                child = props[key]
                out[key] = _repair_value(out[key], child, path=[*path, key], inputs=inputs,
                                         image_ref=image_ref, repairs=repairs)
            elif schema.get("additionalProperties") is False:
                if key == "panel_relevant" and "panel_relevance" not in out:
                    out["panel_relevance"] = out[key]
                    repairs.append({"path": _path([*path, "panel_relevance"]),
                                   "repair": "copy_additional_property", "from": key})
                if "evidence" in path and "text" in props and "text" not in out and key in {"description", "caption"}:
                    out["text"] = str(out[key] or "")
                    repairs.append({"path": _path([*path, "text"]), "repair": "copy_additional_property", "from": key})
                repairs.append({"path": _path([*path, key]), "repair": "drop_additional_property"})
                out.pop(key, None)
        for key in schema.get("required", []):
            if key not in out and key in props:
                known = _known_required_value(key, props[key], path=[*path, key],
                                              current_object=out, inputs=inputs, image_ref=image_ref)
                if known is not _MISSING:
                    out[key] = known
                    repairs.append({"path": _path([*path, key]), "repair": "fill_known_required", "to": _short(known)})
        if "panel_relevance" in props:
            _repair_panel_relevance(out, props, repairs, path)
        return out

    if typ == "array":
        items = schema.get("items") if isinstance(schema.get("items"), dict) else {}
        if value is None:
            repairs.append({"path": _path(path), "repair": "null_to_empty_array"})
            return []
        if isinstance(value, tuple):
            repairs.append({"path": _path(path), "repair": "tuple_to_array"})
            value = list(value)
        elif not isinstance(value, list):
            repairs.append({"path": _path(path), "repair": "scalar_to_array", "from": type(value).__name__})
            value = [value]
        repaired_items = []
        for index, item in enumerate(value):
            if path and path[-1] == "unsupported_claims" and not isinstance(item, dict):
                repaired = {"claim": "" if item is None else str(item)}
                repairs.append({
                    "path": _path([*path, str(index)]),
                    "repair": "unsupported_claim_to_object",
                    "from": type(item).__name__,
                })
                repaired_items.append(repaired)
                continue
            if isinstance(items, dict) and items.get("type") == "string" and isinstance(item, dict):
                as_string = _stringify_scalarish(item)
                repairs.append({
                    "path": _path([*path, str(index)]),
                    "repair": "dict_to_string",
                    "from": type(item).__name__,
                })
                repaired_items.append(as_string)
                continue
            repaired_items.append(_repair_value(
                item, items, path=[*path, str(index)], inputs=inputs, image_ref=image_ref, repairs=repairs))
        return repaired_items

    if typ == "string":
        if value is None:
            repairs.append({"path": _path(path), "repair": "null_to_string"})
            return ""
        if isinstance(value, list):
            if all(not isinstance(item, (dict, list)) for item in value):
                joined = "; ".join(str(item) for item in value if item not in (None, ""))
                repairs.append({"path": _path(path), "repair": "array_to_string", "to": _short(joined)})
                return joined
            return value
        if isinstance(value, dict):
            if path and path[-1] in {
                "metric_name",
                "metric_category",
                "target",
                "material_or_matrix",
                "biological_agent",
                "application_task",
                "assay",
                "condition",
                "comparison",
                "value",
                "unit",
                "value_type",
                "direction",
                "evidence_text",
                "evidence_level",
                "benchmark_relevance",
            }:
                as_string = _stringify_scalarish(value)
                repairs.append({"path": _path(path), "repair": "dict_to_string", "from": type(value).__name__})
                return as_string
            return value
        if not isinstance(value, str):
            repairs.append({"path": _path(path), "repair": "scalar_to_string", "from": type(value).__name__})
            return str(value)
        return value

    if typ in {"number", "integer"}:
        if isinstance(value, str):
            mapped = _map_confidence_string(value)
            if mapped is not None:
                repairs.append({"path": _path(path), "repair": "string_to_number", "from": value, "to": mapped})
                return mapped
            try:
                parsed = int(value) if typ == "integer" else float(value)
            except ValueError:
                return value
            repairs.append({"path": _path(path), "repair": "string_to_number", "from": value, "to": parsed})
            return parsed
        return value

    if typ == "boolean":
        if isinstance(value, str) and value.strip().lower() in {"true", "false", "yes", "no", "1", "0"}:
            parsed = value.strip().lower() in {"true", "yes", "1"}
            repairs.append({"path": _path(path), "repair": "string_to_boolean", "from": value, "to": parsed})
            return parsed
        return value

    return value


class _Missing:
    pass


_MISSING = _Missing()


def _known_required_value(
    key: str,
    schema: dict[str, Any],
    *,
    path: list[str],
    current_object: dict[str, Any],
    inputs: dict[str, Any],
    image_ref: str | None,
) -> Any:
    if key == "extraction_type" and isinstance(schema.get("enum"), list) and schema["enum"]:
        return schema["enum"][0]
    if key in {"figure_id", "panel_id", "domain_task", "source_pdf"}:
        if key == "source_pdf":
            return str(
                inputs.get("source_pdf")
                or image_ref
                or inputs.get("image_ref")
                or inputs.get("figure_image_ref")
                or ""
            )
        return str(inputs.get(key) or "")
    if key == "evidence_ids":
        requested = current_object.get("evidence_ids")
        if isinstance(requested, list):
            return [str(item) for item in requested if item not in (None, "")]
        evidence_map = inputs.get("evidence_map") if isinstance(inputs.get("evidence_map"), list) else []
        evidence_ids = [
            str(item.get("evidence_id"))
            for item in evidence_map
            if isinstance(item, dict) and item.get("evidence_id") not in (None, "")
        ]
        return list(dict.fromkeys(evidence_ids))[:3]
    if key == "evidence":
        return []
    if key == "metric_name":
        return str(current_object.get("metric_name") or current_object.get("name") or "metric_name_unknown")
    if key in {
        "domain",
        "panel_relevance",
        "extraction_decision",
        "application_task",
        "assay",
        "metric_category",
        "panel_type",
        "panel_role",
        "evidence_role",
        "target",
        "material_or_matrix",
        "biological_agent",
        "condition",
        "comparison",
        "value",
        "unit",
        "value_type",
        "direction",
        "evidence_text",
        "evidence_level",
        "benchmark_relevance",
        "digitization_reason",
        "exclusion_reason",
        "why_relevant",
    }:
        defaults = {
            "panel_relevance": "unusable",
            "extraction_decision": "skip_metric_extraction",
            "evidence_role": "unusable",
            "value_type": "categorical",
            "evidence_level": "visual",
            "exclusion_reason": "Required panel target fields were missing; metric extraction disabled.",
        }
        return str(current_object.get(key) or defaults.get(key, ""))
    list_defaults = {
        "benchmark_tasks",
        "target_metric_categories",
        "materials",
        "organisms",
        "treatments",
        "assays",
        "expected_outputs",
        "matched_target_group_ids",
        "allowed_metrics",
        "allowed_units",
        "expected_value_types",
        "expected_metric_fields",
        "recommended_metric_set",
    }
    if key in list_defaults:
        return []
    if key in {"main_entities", "visible_modalities", "ontology_terms"}:
        return {}
    if key in {"needs_digitization", "paper_task_gap_candidate"}:
        return False
    if key == "digitization_reason":
        return ""
    if key == "why_relevant":
        return ""
    if key == "confidence":
        return 0.0
    if "evidence" in path:
        if key == "source":
            return str(current_object.get("source") or inputs.get("image_ref") or image_ref or "image")
        if key == "text":
            return str(
                current_object.get("text")
                or current_object.get("description")
                or current_object.get("caption")
                or _contract_caption_text(inputs)
                or ""
            )
        if key == "evidence_level":
            source = str(current_object.get("source") or "").lower()
            if "caption" in source or current_object.get("caption"):
                return "caption"
            if current_object.get("text") or current_object.get("description"):
                return "both"
            return "visual"
    return _MISSING


def _contract_caption_text(inputs: dict[str, Any]) -> str:
    contract = inputs.get("panel_evidence_contract")
    if not isinstance(contract, dict):
        return ""
    caption = contract.get("caption")
    if not isinstance(caption, dict):
        return ""
    segment = caption.get("caption_segment")
    if not isinstance(segment, dict):
        return ""
    return str(segment.get("text") or "")


def _object_can_be_repaired_from_scalar(schema: dict[str, Any]) -> bool:
    props = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
    required = set(schema.get("required", []))
    return bool(required) and required.issubset(props.keys()) and required.issubset({"label", "unit"})


def _object_can_be_repaired_from_sequence(schema: dict[str, Any], value: Any) -> bool:
    if not isinstance(value, (list, tuple)):
        return False
    props = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
    required = schema.get("required", [])
    return not props and not required


def _object_from_scalar(value: Any, schema: dict[str, Any]) -> dict[str, Any]:
    text = "" if value is None else str(value)
    if {"label", "unit"}.issubset(set(schema.get("required", []))):
        label = text
        unit = ""
        if "(" in text and ")" in text and text.rfind("(") < text.rfind(")"):
            label = text[: text.rfind("(")].strip()
            unit = text[text.rfind("(") + 1: text.rfind(")")].strip()
        return {"label": label, "unit": unit}
    return {}


def _object_from_sequence(value: Any) -> dict[str, Any]:
    if not isinstance(value, (list, tuple)):
        return {}
    out: dict[str, Any] = {}
    for item in value:
        if isinstance(item, str):
            out[item] = True
            continue
        if isinstance(item, dict):
            for key, item_value in item.items():
                if isinstance(key, str):
                    out[key] = item_value
            continue
        if isinstance(item, (list, tuple)) and len(item) == 2:
            key, item_value = item
            if isinstance(key, str):
                out[key] = item_value
    return out


def _enum_repair_value(value: Any, enum_values: list[Any]) -> Any | None:
    if not isinstance(value, str):
        return None
    alias_map = {
        "background": "context_only",
        "qualitative_trend": "trend",
        "contextual": "supporting_observation",
        "contextual_only": "supporting_observation",
        "auxiliary": "supporting_observation",
        "observations_only": "extract_observation_only",
        "observation_only": "extract_observation_only",
        "extract_observations": "extract_observation_only",
        "skip": "skip_metric_extraction",
        "quantitative": "exact_numeric",
        "numerical": "exact_numeric",
        "numeric": "exact_numeric",
        "rate": "exact_numeric",
        "continuous": "exact_numeric",
        "concentration": "exact_numeric",
        "normalized_concentration": "exact_numeric",
        "normalized_ratio": "exact_numeric",
        "qualitative_comparison": "qualitative",
        "quantitative_trend": "trend",
        "ordinal": "qualitative",
        "direct": "visual",
        "direct_visual": "visual",
        "direct_measurement": "visual",
        "primary": "caption",
        "primary_caption": "caption",
        "caption_and_text": "both",
        "text_and_caption": "both",
        "caption+text": "both",
        "schematic": "schematic_context",
        "diagram": "schematic_context",
        "overview_schematic": "schematic_context",
        "conceptual_diagram": "schematic_context",
        "photograph": "supporting_observation",
        "photo": "supporting_observation",
        "macro_photo": "supporting_observation",
        "fluorescence": "supporting_observation",
        "fluorescence_image": "supporting_observation",
        "microscopy": "supporting_observation",
        "microscopy_image": "supporting_observation",
        "sem": "supporting_observation",
        "sem_image": "supporting_observation",
    }
    normalized = value.strip().lower()
    normalized_alias = alias_map.get(normalized)
    if normalized_alias in enum_values:
        return normalized_alias
    visual_aliases = {
        "quantitative",
        "numeric",
        "numerical",
        "chart",
        "plot",
        "image",
        "visual_estimate",
        "high",
        "medium",
        "low",
    }
    if "visual" in enum_values and normalized in visual_aliases:
        return "visual"
    if "caption" in enum_values and normalized in {"supporting_observation", "figure_caption", "primary_caption"}:
        return "caption"
    direct_visual_aliases = {
        "direct_visual",
        "direct_measurement",
        "qualitative_visual",
        "visual_estimate",
        "figure",
    }
    if "visual" in enum_values and normalized in direct_visual_aliases:
        return "visual"
    for item in enum_values:
        if isinstance(item, str) and item.strip().lower() == normalized:
            return item
    return None


def _repair_panel_relevance(
    out: dict[str, Any],
    props: dict[str, Any],
    repairs: list[dict[str, Any]],
    path: list[str],
) -> None:
    schema = props.get("panel_relevance")
    enum_values = schema.get("enum") if isinstance(schema, dict) else None
    if not isinstance(enum_values, list) or out.get("panel_relevance") in enum_values:
        return
    original = out.get("panel_relevance")
    extraction_decision = str(out.get("extraction_decision") or "").strip()
    evidence_role = str(out.get("evidence_role") or "").strip()
    text = str(original or "").strip().lower()
    inferred = ""
    if extraction_decision == "extract_target_metrics" or evidence_role == "primary_metric_panel":
        inferred = "benchmark_metric"
    elif extraction_decision == "extract_supporting_observation" or evidence_role == "supporting_observation":
        inferred = "supporting_observation"
    elif evidence_role == "unusable" or "unusable" in text:
        inferred = "unusable"
    elif extraction_decision == "skip_metric_extraction" or evidence_role in {"schematic_context", "methods_context"}:
        inferred = "context_only"
    if inferred in enum_values:
        out["panel_relevance"] = inferred
        repairs.append({
            "path": _path([*path, "panel_relevance"]),
            "repair": "infer_panel_relevance",
            "from": _short(original),
            "to": inferred,
        })


def _is_fallback(payload: dict[str, Any], adapter: PhaseAdapter) -> bool:
    fallback = adapter.fallback_payload()
    if not fallback:
        return False
    if payload.get("warnings") and any("unrecoverable" in str(w) or "not_parsable" in str(w) or "no_chart" in str(w) or "failed" in str(w) for w in _ensure_warnings_list(payload)):
        return True
    return payload == fallback


def _dict_key_count(payload: Any) -> int:
    return len(payload) if isinstance(payload, dict) else 0


def _ensure_warnings_list(payload: dict[str, Any]) -> list[str]:
    existing = payload.get("warnings")
    if isinstance(existing, list):
        return existing
    payload["warnings"] = []
    return payload["warnings"]


def _copy_alias_fields(
    out: dict[str, Any],
    props: dict[str, Any],
    repairs: list[dict[str, Any]],
    path: list[str],
) -> None:
    aliases = {
        "relevance": "panel_relevance",
        "task": "application_task",
        "application": "application_task",
        "assay_class": "assay",
        "metric_type": "metric_category",
        "metrics": "recommended_metric_set",
        "fields": "expected_metric_fields",
        "metric": "metric_name",
        "name": "metric_name",
        "category": "metric_category",
        "subject": "target",
        "entity": "target",
        "sample": "target",
        "material": "material_or_matrix",
        "material_matrix": "material_or_matrix",
        "organism": "biological_agent",
        "bio_agent": "biological_agent",
        "organism_name": "biological_agent",
        "evidence": "evidence_text",
        "evidence_sentence": "evidence_text",
        "support_text": "evidence_text",
        "timepoint": "condition",
        "time_point": "condition",
        "group": "condition",
    }
    for src, dst in aliases.items():
        if src in out and dst in props and dst not in out:
            if isinstance(out[src], bool):
                continue
            out[dst] = out[src]
            repairs.append({"path": _path([*path, dst]), "repair": "copy_alias_property", "from": src})


def _path(path: list[str]) -> str:
    return ".".join(path) if path else "$"


def _short(value: Any) -> Any:
    text = str(value)
    return text[:160] if len(text) > 160 else value


def _stringify_scalarish(value: dict[str, Any]) -> str:
    for key in (
        "metric_name",
        "observation",
        "description",
        "text",
        "evidence_text",
        "evidence_sentence",
        "support_text",
        "name",
        "value",
        "label",
        "observed_value",
        "categorical_value",
        "qualitative_value",
        "direction",
    ):
        candidate = value.get(key)
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return json.dumps(value, ensure_ascii=False)


def _map_confidence_string(value: str) -> float | None:
    normalized = value.strip().lower()
    confidence_map = {
        "high": 0.9,
        "very high": 1.0,
        "medium": 0.6,
        "moderate": 0.6,
        "low": 0.3,
        "very low": 0.1,
    }
    return confidence_map.get(normalized)

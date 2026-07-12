from __future__ import annotations

import json
import logging
from pathlib import Path
import base64
import mimetypes
import hashlib
from threading import Lock
from typing import Any, Callable


_LOG = logging.getLogger(__name__)


class ContentPipelineLLMClient:
    def __init__(self, client: Any | None = None) -> None:
        if client is None:
            raise ValueError("ContentPipelineLLMClient requires an injected model client.")
        self.client = client
        self.raw_responses: list[dict[str, Any]] = []
        self._raw_responses_lock = Lock()

    def call_json(self, *, prompt: str, inputs: dict[str, Any]) -> dict[str, Any]:
        phase = str(inputs.get("phase_name") or "content_pipeline_extraction")
        response = self.client.chat_json(
            self._messages(prompt=prompt, inputs=inputs),
            phase=phase,
        )
        self._record_raw_response(phase=phase, prompt=prompt, response=response)
        return response

    def call_text(self, *, prompt: str, inputs: dict[str, Any]) -> str:
        phase = str(inputs.get("phase_name") or "content_pipeline_extraction")
        response = self.client.chat_text(
            self._messages(prompt=prompt, inputs=inputs),
            phase=phase,
        )
        self._record_raw_response(phase=phase, prompt=prompt, response=response)
        return response

    def _record_raw_response(self, *, phase: str, prompt: str, response: Any) -> None:
        record = {
            "sequence": 0,
            "phase": phase,
            "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            "response": response,
        }
        with self._raw_responses_lock:
            record["sequence"] = len(self.raw_responses) + 1
            self.raw_responses.append(record)

    @staticmethod
    def _resolve_image_ref(image_ref: str) -> str:
        """Convert a local file path to a base64 data URI."""
        p = Path(image_ref)
        if not p.is_file():
            _LOG.warning("Image file not found: %s", image_ref)
            return image_ref
        mime_type = mimetypes.guess_type(str(p))[0] or "image/png"
        try:
            encoded = base64.b64encode(p.read_bytes()).decode("ascii")
            return f"data:{mime_type};base64,{encoded}"
        except Exception as exc:
            _LOG.warning("Failed to read image %s: %s", image_ref, exc)
            return image_ref

    @staticmethod
    def _messages(prompt: str, inputs: dict[str, Any]) -> list[dict[str, Any]]:
        image_url = ""
        image_ref = inputs.get("image_ref", "")
        if image_ref:
            image_url = ContentPipelineLLMClient._resolve_image_ref(image_ref)
        context = ContentPipelineLLMClient._filter_inputs(inputs)
        context_text = json.dumps(context, ensure_ascii=False, default=str, indent=2)
        return [
            {"role": "system", "content": [
                {"type": "text", "text": "You are a scientific content extraction VLM. Extract structured data from scientific figure images and their surrounding text context."},
            ]},
            {"role": "user", "content": [
                {"type": "text", "text": f"{prompt}\n\nStructured evidence and context:\n{context_text}"},
                *([{"type": "image_url", "image_url": {"url": image_url, "detail": "high"}}] if image_url else []),
            ]},
        ]

    @staticmethod
    def _filter_inputs(inputs: dict[str, Any]) -> dict[str, Any]:
        keep = {
            "paper_context",
            "figure_id",
            "panel_id",
            "panel_type",
            "domain_task",
            "source_pdf",
            "figure_image_ref",
            "image_ref",
            "phase_name",
            "panel_semantic_profile",
            "tables",
            "formulas",
            "evidence_map",
            "panel_evidence_contract",
            "legacy_panel_caption_focus",
            "section_hierarchy",
            "visual_type",
            "chart_type_hint",
            "chart_points_summary",
            "image_kind_hint",
            "axis_unit_hints",
            "panel_context_warnings",
        }
        return {k: v for k, v in inputs.items() if k in keep and v}


def build_content_pipeline_client(
    client_factory: Callable[[], Any] | None = None,
) -> ContentPipelineLLMClient | None:
    """Build from an injected provider adapter; the pipeline does not import app code."""
    if client_factory is None:
        _LOG.warning("No model client factory supplied; content pipeline LLM is disabled.")
        return None
    return ContentPipelineLLMClient(client_factory())


class FakeContentPipelineClient:
    """Fake VLM client for testing the LLM extraction path.

    behavior: "valid" | "bad_json" | "crash"
    response_map: dict[phase_name, dict] - returned when behavior=="valid"
    """

    def __init__(self, behavior: str = "valid", response_map: dict[str, dict] | None = None) -> None:
        self.behavior = behavior
        self.response_map = response_map or {}
        self.call_count = 0
        self.last_prompt: str = ""
        self.call_history: list[dict[str, Any]] = []

    def call_json(self, *, prompt: str, inputs: dict) -> dict:
        self.call_count += 1
        self.last_prompt = prompt
        self.call_history.append({"prompt": prompt, "inputs": dict(inputs)})
        if self.behavior == "crash":
            raise RuntimeError("fake VLM client crash")
        if self.behavior == "bad_json":
            return "{{{not valid json}}}"
        phase_name = str(inputs.get("phase_name") or "")
        payload = self.response_map.get(phase_name) or self._default_phase_payload(phase_name, inputs=inputs)
        payload = self._apply_phase_defaults(payload=payload, phase_name=phase_name)
        return payload

    def call_text(self, *, prompt: str, inputs: dict) -> str:
        payload = self.call_json(prompt=prompt, inputs=inputs)
        return json.dumps(payload, ensure_ascii=False)

    @staticmethod
    def _default_phase_payload(phase_name: str, *, inputs: dict[str, Any] | None = None) -> dict[str, Any]:
        inputs = inputs or {}
        if phase_name == "panel_semantic_classifier":
            return {
                "panel_relevance": "context_only",
                "extraction_decision": "skip_metric_extraction",
                "application_task": "",
                "assay": "",
                "metric_category": "",
                "panel_type": "line_plot",
                "panel_role": "caption_assigned_panel_task",
                "evidence_role": "supporting_observation",
                "needs_digitization": False,
                "digitization_reason": "",
                "exclusion_reason": "",
                "main_entities": {},
                "visible_modalities": {},
                "why_relevant": "test panel",
                "confidence": 0.5,
            }
        if phase_name == "chart_digitization":
            evidence_map = inputs.get("evidence_map") if isinstance(inputs.get("evidence_map"), list) else []
            evidence_ids = [str(evidence_map[0].get("evidence_id"))] if evidence_map and isinstance(evidence_map[0], dict) and evidence_map[0].get("evidence_id") else []
            return {
                "chart_type": "line_plot",
                "digitization_status": "digitized",
                "axis_readability": "readable",
                "legend_readability": "readable",
                "calibration_status": "estimated_from_axis",
                "data_point_count": 2,
                "x_axis": {"label": "Time", "unit": "day", "scale": "linear"},
                "y_axis": {"label": "water uptake", "unit": "g", "scale": "linear"},
                "series": ["test series"],
                "data_points": [
                    {"x_value": 0, "y_value": 1.0, "series_name": "test series", "evidence_ids": evidence_ids},
                    {"x_value": 1, "y_value": 2.0, "series_name": "test series", "evidence_ids": evidence_ids},
                ],
                "extraction_method": "llm_visual_digitization",
                "extraction_confidence": 0.6,
                "needs_verification": True,
                "warnings": [],
                "evidence_ids": evidence_ids,
            }
        if phase_name == "image_observation":
            evidence_map = inputs.get("evidence_map") if isinstance(inputs.get("evidence_map"), list) else []
            evidence_ids = [str(evidence_map[0].get("evidence_id"))] if evidence_map and isinstance(evidence_map[0], dict) and evidence_map[0].get("evidence_id") else []
            return {
                "visual_fact_candidates": [{
                    "fact_id": "fake-visible-signal",
                    "fact_type": "presence_absence",
                    "subject_slot": "test material",
                    "attribute_slot": "visible_signal",
                    "value_slot": "visible",
                    "condition_slot": "",
                    "evidence_ids": evidence_ids,
                    "visual_grounding": {"image_ref": inputs.get("image_ref", ""), "region": None},
                    "caption_segment_status": "missing",
                    "support_level": "visual_only",
                    "confidence": 0.6,
                }],
                "extraction_method": "llm_image_observation",
                "confidence": 0.6,
                "needs_verification": True,
                "evidence_ids": evidence_ids,
            }
        return {"confidence": 0.5}

    @classmethod
    def _apply_phase_defaults(cls, payload: dict[str, Any], phase_name: str) -> dict[str, Any]:
        if phase_name not in {"panel_semantic_classifier"}:
            return payload
        try:
            from content_pipeline.llm.phase_schemas import PANEL_CLASSIFICATION_SCHEMA
            schema = {
                "panel_semantic_classifier": PANEL_CLASSIFICATION_SCHEMA,
            }[phase_name]
            normalized = cls._normalize_with_schema(payload, schema)
            return normalized if isinstance(normalized, dict) else payload
        except Exception:
            return payload

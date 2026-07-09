from __future__ import annotations

import re
from typing import Any

from content_pipeline.adapters.semantic_adapters import panel_semantic_from_payload
from content_pipeline.contracts.errors import ExtractionPhaseError
from content_pipeline.contracts.evidence import EvidencePacket, panel_evidence_contract
from content_pipeline.contracts.panel_types import is_numeric_chart
from content_pipeline.contracts.semantic import PanelSemanticResult
from content_pipeline.llm.phase_runner import PhaseRunner, _DEGRADED_KEY
from content_pipeline.llm.phase_schemas import PANEL_CLASSIFICATION_SCHEMA
from content_pipeline.llm.panel_classifier_adapter import PanelClassifierAdapter
from content_pipeline.llm.prompt_contracts import PromptContract

_CHART_TYPE_SET = {"chart", "bar_chart", "line_chart", "scatter_plot", "scatter",
                   "pie_chart", "histogram", "heatmap", "box_plot", "boxplot",
                   "dot_plot", "area_chart", "bubble_chart", "radar_chart",
                   "stacked_bar", "grouped_bar", "multi_series_line",
                   "mass_spectrum", "spectrum"}


class PanelSemanticClassifier:
    def classify(self, *, packet: EvidencePacket, model_client: Any | None = None, audit: list[dict[str, Any]] | None = None) -> PanelSemanticResult:
        audit = audit if audit is not None else []
        inputs = _packet_inputs(packet)
        inputs["disambiguation_evidence_map"] = _disambiguation_evidence_map(inputs.get("evidence_map", []))
        inputs["evidence_map"] = _extractable_evidence_map(inputs.get("evidence_map", []))
        inputs.update({"phase_name": "panel_semantic_classifier"})
        payload = PhaseRunner().run_phase(
            phase_name="panel_semantic_classifier",
            prompt_template=(
                "Classify this panel using panel_evidence_contract as the source of truth for current panel identity, caption segment, evidence roles, and citation policy. "
                "Use evidence_map as the extractable projection from that contract: relation, source_type, use_policy, evidence_role, and evidence_id define what may support the current panel. "
                "Output a panel-local semantic profile. "
                "Describe the panel_type freely based on what the image shows (e.g., 'line_plot', 'log-log scatter plot', 'microscopy image', 'schematic diagram', 'bar_chart', 'photograph', 'table', 'histogram'). "
                "If the panel contains numeric axes and plotted data (lines, bars, scatter points, etc.), panel_type should be a chart/plot description. "
                "MinerU raw type is input provenance only; do not output evidence_shape. "
                "evidence_role must only be primary_metric_panel, supporting_observation, schematic_context, methods_context, or unusable. "
                "For direct metric panels set evidence_role=primary_metric_panel, extraction_decision=extract_target_metrics. "
                "For supporting observations set evidence_role=supporting_observation and extraction_decision=extract_supporting_observation. "
                "For schematic/context/methods panels set extraction_decision=skip_metric_extraction and fill exclusion_reason. "
                "If plot values require reading from an image, set needs_digitization=true and explain digitization_reason."
            ),
            compact_contract=PromptContract(
                object_name="panel_semantic_profile",
                required_fields=list(PANEL_CLASSIFICATION_SCHEMA["required"]),
            ),
            inputs=inputs,
            image_ref=packet.image_ref,
            output_schema=PANEL_CLASSIFICATION_SCHEMA,
            model_client=model_client,
            phase_adapter=PanelClassifierAdapter(),
        )
        if payload.get(_DEGRADED_KEY):
            audit.append({
                "event": "panel_classification_fallback",
                "panel_id": packet.panel_id,
                "reason": "degraded_llm_payload",
                "warnings": payload.get("warnings", []),
            })
            return self._fallback_semantic(packet, payload)
        try:
            result = panel_semantic_from_payload(payload, packet, "")
        except ExtractionPhaseError:
            audit.append({
                "event": "panel_classification_fallback",
                "panel_id": packet.panel_id,
                "reason": "empty_panel_type_in_payload",
                "warnings": payload.get("warnings", []),
            })
            return self._fallback_semantic(packet, payload)
        if is_numeric_chart(result.panel_type):
            result.panel_type = "numeric_chart"
        return result

    def _fallback_semantic(self, packet: EvidencePacket, payload: dict[str, Any]) -> PanelSemanticResult:
        provenance_type = str(packet.provenance.get("visual_normalized_type") or "").lower()
        raw_type = str(packet.provenance.get("raw_type") or "").lower()
        visual_type = provenance_type or raw_type
        if not provenance_type:
            panel_warnings = payload.get("warnings", [])
            if "missing_visual_normalized_type" not in panel_warnings:
                panel_warnings = list(dict.fromkeys([*panel_warnings, "missing_visual_normalized_type"]))
            payload["warnings"] = panel_warnings
        is_chart_block = visual_type in _CHART_TYPE_SET
        evidence_role = "supporting_observation"
        extraction_decision = "extract_supporting_observation"
        needs_digitization = False
        if is_chart_block:
            evidence_role = "primary_metric_panel"
            extraction_decision = "extract_target_metrics"
            needs_digitization = True
        evidence_links = []
        if packet.primary_caption:
            evidence_links = [packet.primary_caption.evidence_id]
        return PanelSemanticResult(
            paper_id=packet.paper_id,
            figure_id=packet.figure_id,
            panel_id=str(packet.panel_id or ""),
            panel_relevance="unusable",
            extraction_decision=extraction_decision,
            application_task="",
            assay="",
            metric_category="",
            panel_type=visual_type or "unknown",
            panel_role="",
            evidence_role=evidence_role,
            needs_digitization=needs_digitization,
            digitization_reason="",
            exclusion_reason="Panel classifier failed; fallback based on MinerU block type.",
            main_entities={},
            visible_modalities={},
            ontology_terms={},
            evidence_links=evidence_links,
            why_relevant="",
            confidence=0.0,
            raw_output={
                "fallback": True,
                "fallback_provenance_type": provenance_type or "missing",
                "fallback_raw_type": raw_type or "missing",
                "fallback_is_chart": is_chart_block,
                "fallback_warnings": payload.get("warnings", []),
            },
        )


def _packet_inputs(packet: EvidencePacket) -> dict[str, Any]:
    caption = packet.primary_caption.text if packet.primary_caption else ""
    allowed = "\n".join(item.text for item in packet.allowed_context if item.text)
    tables = "\n".join(item.text for item in packet.tables if item.text)
    formulas = "\n".join(item.text for item in packet.formulas if item.text)
    panel_caption_focus = _panel_caption_focus(packet=packet, caption_text="\n".join([caption, allowed]))
    contract = panel_evidence_contract(packet)
    segment = contract["caption"]["caption_segment"]
    status = str(segment.get("status") or "missing")
    if status == "missing" and panel_caption_focus:
        segment["text"] = panel_caption_focus
        segment["status"] = "fallback_regex"
        segment["confidence"] = min(float(packet.primary_caption.confidence if packet.primary_caption else 0.4), 0.5)
        segment["provenance"] = {"source": "legacy_panel_caption_focus"}
    evidence_map = _evidence_map_for_prompt(packet)
    result = {
        "figure_id": packet.figure_id,
        "panel_id": packet.panel_id,
        "image_ref": packet.image_ref or "",
        "panel_evidence_contract": contract,
        "tables": tables[:2000],
        "formulas": formulas[:1000],
        "evidence_map": evidence_map,
        "section_hierarchy": packet.section_hierarchy,
    }
    if segment.get("status") == "fallback_regex":
        result.update({
            "legacy_panel_caption_focus": panel_caption_focus,
        })
    return result


def _evidence_map_for_prompt(packet: EvidencePacket) -> list[dict[str, Any]]:
    ordered_items = []
    if packet.primary_caption:
        ordered_items.append(packet.primary_caption)
    ordered_items.extend(packet.allowed_context)
    ordered_items.extend(packet.tables)
    ordered_items.extend(packet.formulas)
    ordered_items.extend(packet.references)
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in ordered_items:
        evidence_id = str(getattr(item, "evidence_id", "") or "")
        if not evidence_id or evidence_id in seen:
            continue
        seen.add(evidence_id)
        item_text = str(getattr(item, "text", "") or "")
        caption_segment_ref = (
            getattr(item, "source_type", "") == "caption"
            and getattr(item, "relation", "") == "primary_caption"
            and getattr(item, "text_level", "") == "caption_segment"
        )
        result.append({
            "evidence_id": evidence_id,
            "block_id": getattr(item, "block_id", ""),
            "source_type": getattr(item, "source_type", ""),
            "relation": getattr(item, "relation", ""),
            "use_policy": getattr(item, "use_policy", ""),
            "text_level": getattr(item, "text_level", ""),
            "text_format": getattr(item, "text_format", ""),
            "evidence_role": getattr(item, "evidence_role", ""),
            "segment_status": getattr(item, "segment_status", ""),
            "segment_confidence": getattr(item, "segment_confidence", 0.0),
            "caption_segment_ref": caption_segment_ref,
            "page_idx": getattr(item, "page_idx", None),
            "reading_order": getattr(item, "reading_order", None),
            "text_excerpt": "" if caption_segment_ref else item_text[:240],
        })
    return result[:20]


def _extractable_evidence_map(items: Any) -> list[dict[str, Any]]:
    return [item for item in items if isinstance(item, dict) and item.get("use_policy") == "use_for_extraction"]


def _disambiguation_evidence_map(items: Any) -> list[dict[str, Any]]:
    return [item for item in items if isinstance(item, dict) and item.get("use_policy") != "use_for_extraction"]


def _panel_caption_focus(*, packet: EvidencePacket, caption_text: str) -> str:
    label = str(packet.panel_id or "").rsplit("-", 1)[-1].strip().lower()
    if not label or len(label) > 3:
        return caption_text[:500]
    text = " ".join(str(caption_text or "").split())
    if not text:
        return ""
    pattern = re.compile(
        r"\(?\s*" + re.escape(label) + r"\s*[\):.\s,;]",
        re.IGNORECASE,
    )
    match = pattern.search(text)
    if match:
        return text[match.start():][:500].split(".")[0] or text[match.start():][:500]
    return text[:500]




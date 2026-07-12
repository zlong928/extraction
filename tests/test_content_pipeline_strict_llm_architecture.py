"""Tests for the LLM classification architecture."""

from __future__ import annotations

import json
from pathlib import Path

from content_pipeline.adapters.semantic_adapters import panel_semantic_from_payload
from content_pipeline.llm.client import FakeContentPipelineClient
from content_pipeline.llm.panel_classifier_adapter import PanelClassifierAdapter
from content_pipeline.orchestration.pipeline_runner import run_content_graph_pipeline
from content_pipeline.contracts.audit import ExtractionPipelineOptions


def _write_fixture(tmp_path: Path) -> Path:
    (tmp_path / "images").mkdir()
    for n in ("a.png", "b.png"):
        (tmp_path / "images" / n).write_bytes(b"img")
    pages = [[
        {"type": "image", "content": {"image_source": {"path": "images/a.png"}}, "bbox": [0, 90, 180, 240]},
        {"type": "chart", "content": {"image_source": {"path": "images/b.png"}}, "bbox": [190, 90, 380, 240]},
    ]]
    cp = tmp_path / "source_content_list_v2.json"
    cp.write_text(json.dumps(pages), encoding="utf-8")
    return cp


def test_classifier_runs_for_image_panels(tmp_path: Path) -> None:
    cp = _write_fixture(tmp_path)
    fake = FakeContentPipelineClient(response_map={
        "panel_semantic_classifier": {
            "panel_relevance": "context_only", "extraction_decision": "skip_metric_extraction",
            "application_task": "", "assay": "", "metric_category": "",
            "panel_type": "photograph",
            "panel_role": "", "evidence_role": "supporting_observation",
            "matched_target_group_ids": [], "allowed_metrics": [],
            "needs_digitization": False, "digitization_reason": "",
            "exclusion_reason": "no paper-level metric target scope",
            "main_entities": {}, "visible_modalities": {"source_visual_type": "image"},
            "ontology_terms": {}, "why_relevant": "Test", "confidence": 0.5,
            "expected_metric_fields": [], "recommended_metric_set": [],
            "allowed_units": [], "expected_value_types": [],
        },
    })
    result = run_content_graph_pipeline(
        content_list_path=str(cp), layout_path=None, image_root=str(tmp_path),
        paper_id="strict", model_client=fake,
        output_dir=str(tmp_path / "out"),
        options=ExtractionPipelineOptions(fail_fast=True, max_workers=1),
    )
    assert result.status == "succeeded", result.errors
    psc = [c for c in fake.call_history if c["inputs"].get("phase_name") == "panel_semantic_classifier"]
    img = [c for c in fake.call_history if c["inputs"].get("phase_name") == "image_observation"]
    assert len(psc) == 2
    assert len(img) == 2


def test_panel_type_free_text_survives_payload_roundtrip() -> None:
    adapter = PanelClassifierAdapter()
    r = adapter.adapt_payload({"panel_type": "microscopy image", "confidence": 0.8})
    assert r.payload["panel_type"] == "microscopy image"


def test_panel_type_from_classifier_passes_to_result() -> None:
    class FakePacket:
        paper_id = "p"
        figure_id = "f"
        panel_id = "p1"
        image_ref = ""
        primary_caption = None
        provenance = {}
        section_hierarchy = []
        tables = []
        formulas = []
        allowed_context = []
        references = []
    payload = {
        "panel_relevance": "context_only", "extraction_decision": "skip_metric_extraction",
        "application_task": "", "assay": "", "metric_category": "",
        "panel_type": "microscopy image",
        "panel_role": "", "evidence_role": "supporting_observation",
        "confidence": 0.7,
    }
    r = panel_semantic_from_payload(payload, FakePacket(), ontology_version="")
    assert r.panel_type == "microscopy image"

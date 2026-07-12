from __future__ import annotations

import csv
import json
from pathlib import Path

from content_pipeline.contracts.audit import ExtractionPipelineOptions
from content_pipeline.export.audit_exporter import AuditExporter
from content_pipeline.llm.client import FakeContentPipelineClient
from content_pipeline.orchestration.pipeline_runner import run_content_graph_pipeline


def test_content_graph_pipeline_end_to_end(tmp_path: Path) -> None:
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    (image_dir / "fig1.png").write_bytes(b"img")
    pages = [[
        {"type": "title", "content": {"title_content": "Results", "level": 1}, "bbox": [0, 0, 400, 40]},
        {"type": "image", "content": {"image_caption": "Fig. 1 | (a) before; (b) after", "image_source": {"path": "images/fig1.png"}}, "bbox": [0, 50, 180, 200]},
        {"type": "paragraph", "content": "Fig. 1 shows increased growth.", "bbox": [0, 210, 400, 240]},
    ]]
    content_path = tmp_path / "content_list_v2.json"
    content_path.write_text(json.dumps(pages), encoding="utf-8")

    result = run_content_graph_pipeline(
        content_list_path=str(content_path),
        layout_path=None,
        image_root=str(tmp_path),
        paper_id="paper-1",
        query=None,
        model_client=FakeContentPipelineClient(),
        output_dir=str(tmp_path),
        options=ExtractionPipelineOptions(fail_fast=False),
    )

    assert result.status == "succeeded"
    assert result.document_graph_summary["image_count"] == 1
    assert result.figure_panel_graph["panel_count"] == 1
    assert result.evidence_packets
    assert result.evidence_packets[0].primary_caption is not None
    assert "audit_json" in result.output_paths
    assert "review_md" in result.output_paths
    assert (tmp_path / "extraction_audit.json").exists()
    assert (tmp_path / "review.md").exists()


def test_e2e_writes_panel_fact_csv_for_digitized_chart_without_metric_columns(tmp_path: Path) -> None:
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    _write_png_header(image_dir / "chart.png", width=260, height=180)
    pages = [[
        {
            "type": "chart",
            "content": {
                "image_source": {"path": "images/chart.png"},
                "chart_caption": [{"type": "text", "content": "Figure 1. Water uptake over time."}],
            },
            "bbox": [0, 50, 220, 220],
        },
    ]]
    content_path = tmp_path / "content_list_v2.json"
    content_path.write_text(json.dumps(pages), encoding="utf-8")

    chart_response_map = {
        "panel_semantic_classifier": {
            "panel_relevance": "benchmark_metric",
            "extraction_decision": "extract_target_metrics",
            "application_task": "",
            "assay": "",
            "metric_category": "water_uptake",
            "panel_type": "line_plot",
            "panel_role": "caption_assigned_panel_task",
            "evidence_role": "primary_metric_panel",
            "needs_digitization": True,
            "digitization_reason": "chart digitization needed for value extraction",
            "exclusion_reason": "",
            "main_entities": {},
            "visible_modalities": {},
            "why_relevant": "water uptake over time",
            "confidence": 0.8,
        },
    }
    result = run_content_graph_pipeline(
        content_list_path=str(content_path),
        layout_path=None,
        image_root=str(tmp_path),
        paper_id="paper-chart-facts",
        query=None,
        model_client=FakeContentPipelineClient(response_map=chart_response_map),
        output_dir=str(tmp_path),
        options=ExtractionPipelineOptions(fail_fast=False, max_workers=1),
    )

    assert result.panel_fact_rows
    panel_table_paths = [Path(value) for key, value in result.output_paths.items() if key.startswith("chart_fact_table:")]
    assert len(panel_table_paths) == 1
    panel_table = panel_table_paths[0]
    assert panel_table.parent.name == "chart_fact_tables"
    with panel_table.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))

    assert rows
    assert rows[0]["paper_id"] == "paper-chart-facts"
    assert rows[0]["source_image"].endswith("images/chart.png")
    assert rows[0]["x_label"] == "Time"
    assert rows[0]["x_unit"] == "day"
    assert rows[0]["y_label"] == "water uptake"
    assert rows[0]["y_unit"] == "g"
    assert rows[0]["digitization_status"] == "digitized"
    assert {row["y_value"] for row in rows} == {"1.0", "2.0"}
    forbidden_columns = {
        "matched_target_group_id",
        "mapped_metric_name",
        "metric_category",
        "application_task",
        "assay",
        "allowed_metrics",
        "target_metric_groups",
        "validation_status",
        "release_status",
        "rejection_reason",
        "review_status",
    }
    assert not forbidden_columns.intersection(rows[0])


def test_exporter_writes_heatmap_candidate_files_for_fig_4_k(tmp_path: Path) -> None:
    image_ref = "/tmp/extracted/images/fig-4-k.png"
    output_paths = AuditExporter().write_outputs(
        output_dir=tmp_path,
        audit_payload={
            "evidence_packets": [
                {
                    "paper_id": "paper-4",
                    "figure_id": "fig-4",
                    "panel_id": "fig-4-k",
                    "image_ref": image_ref,
                }
            ],
            "heatmap_candidates": [
                {
                    "candidate_id": "paper-4:fig-4:fig-4-k:heatmap:1",
                    "paper_id": "paper-4",
                    "figure_id": "fig-4",
                    "panel_id": "fig-4-k",
                    "source_phase": "heatmap_candidate",
                    "metric_name": "center concentration",
                    "series": "MHN@TA",
                    "condition": "12 h",
                    "value_min": "0.2",
                    "value_max": "0.4",
                    "unit": "mol m^-3",
                    "scale_factor": "1e-5",
                    "evidence_type": "heatmap_visual_estimate",
                    "confidence": 0.6,
                    "needs_review": True,
                }
            ],
        },
        panel_fact_rows=[],
    )

    heatmap_csv = tmp_path / "heatmap_candidates.csv"
    panel_csv = tmp_path / "heatmap_candidate_tables" / "fig-4-k.csv"
    assert output_paths["heatmap_candidate_csv"] == str(heatmap_csv)
    assert output_paths["heatmap_candidate_table:fig-4-k"] == str(panel_csv)
    with heatmap_csv.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    with panel_csv.open(newline="", encoding="utf-8-sig") as handle:
        panel_rows = list(csv.DictReader(handle))

    assert rows == panel_rows
    assert rows[0]["panel_id"] == "fig-4-k"
    assert rows[0]["source_image"] == image_ref
    assert rows[0]["metric_name"] == "center concentration"
    assert rows[0]["series"] == "MHN@TA"
    assert rows[0]["condition"] == "12 h"
    assert rows[0]["value_min"] == "0.2"
    assert rows[0]["value_max"] == "0.4"

    audit = json.loads((tmp_path / "extraction_audit.json").read_text(encoding="utf-8"))
    assert audit["output_paths"]["heatmap_candidate_csv"] == str(heatmap_csv)
    assert audit["output_paths"]["heatmap_candidate_table:fig-4-k"] == str(panel_csv)
    descriptions = {
        item["label"]: item["description"]
        for item in audit["output_file_descriptions"]
    }
    assert "Chart-only heatmap review candidates" in descriptions["heatmap_candidate_csv"]
    assert "frontend Heatmap Candidates preview" in descriptions["heatmap_candidate_table:fig-4-k"]
    review = (tmp_path / "review.md").read_text(encoding="utf-8")
    assert "## Output Files" in review
    assert "heatmap_candidate_table:fig-4-k" in review


def test_chart_only_mode_skips_taskplan_and_benchmark_metric_phases(tmp_path: Path) -> None:
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    _write_png_header(image_dir / "chart.png", width=260, height=180)
    pages = [[
        {
            "type": "chart",
            "content": {
                "image_source": {"path": "images/chart.png"},
                "chart_caption": [{"type": "text", "content": "Figure 1. Water uptake over time."}],
            },
            "bbox": [0, 50, 220, 220],
        },
    ]]
    content_path = tmp_path / "content_list_v2.json"
    content_path.write_text(json.dumps(pages), encoding="utf-8")
    chart_response_map = {
        "panel_semantic_classifier": {
            "panel_relevance": "benchmark_metric",
            "extraction_decision": "extract_target_metrics",
            "application_task": "",
            "assay": "",
            "metric_category": "water_uptake",
            "panel_type": "line_plot",
            "panel_role": "caption_assigned_panel_task",
            "evidence_role": "primary_metric_panel",
            "needs_digitization": True,
            "digitization_reason": "chart digitization needed for value extraction",
            "exclusion_reason": "",
            "main_entities": {},
            "visible_modalities": {},
            "why_relevant": "water uptake over time",
            "confidence": 0.8,
        },
    }
    client = FakeContentPipelineClient(response_map=chart_response_map)

    result = run_content_graph_pipeline(
        content_list_path=str(content_path),
        layout_path=None,
        image_root=str(tmp_path),
        paper_id="paper-chart-only",
        query=None,
        model_client=client,
        output_dir=str(tmp_path),
        options=ExtractionPipelineOptions(fail_fast=False, max_workers=1, chart_only=True),
    )

    phases = [entry["inputs"].get("phase_name") for entry in client.call_history]
    assert "chart_digitization" in phases
    assert result.chart_points
    assert result.chart_facts


def test_e2e_audit_trace_contains_evidence_ids(tmp_path: Path) -> None:
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    (image_dir / "fig1.png").write_bytes(b"img")
    pages = [[
        {"type": "image", "content": {"image_caption": "Fig. 1 | Test", "image_source": {"path": "images/fig1.png"}}, "bbox": [0, 50, 180, 200]},
    ]]
    content_path = tmp_path / "content_list_v2.json"
    content_path.write_text(json.dumps(pages), encoding="utf-8")

    run_content_graph_pipeline(
        content_list_path=str(content_path),
        layout_path=None,
        image_root=str(tmp_path),
        paper_id="paper-1",
        query=None,
        model_client=FakeContentPipelineClient(),
        output_dir=str(tmp_path),
        options=ExtractionPipelineOptions(fail_fast=False),
    )

    audit_path = tmp_path / "extraction_audit.json"
    assert audit_path.exists()
    audit = json.loads(audit_path.read_text(encoding="utf-8"))

    for packet in audit.get("evidence_packets", []):
        if packet.get("primary_caption"):
            assert packet["primary_caption"]["evidence_id"]


def test_e2e_writes_live_audit_events_jsonl(tmp_path: Path) -> None:
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    (image_dir / "fig1.png").write_bytes(b"img")
    pages = [[
        {"type": "image", "content": {"image_caption": "Fig. 1 | Test", "image_source": {"path": "images/fig1.png"}}, "bbox": [0, 50, 180, 200]},
    ]]
    content_path = tmp_path / "content_list_v2.json"
    content_path.write_text(json.dumps(pages), encoding="utf-8")

    result = run_content_graph_pipeline(
        content_list_path=str(content_path),
        layout_path=None,
        image_root=str(tmp_path),
        paper_id="paper-live-audit",
        query=None,
        model_client=FakeContentPipelineClient(),
        output_dir=str(tmp_path),
        options=ExtractionPipelineOptions(fail_fast=False),
    )

    events_path = tmp_path / "extraction_audit_events.jsonl"
    assert result.output_paths["audit_events_jsonl"] == str(events_path)
    lines = events_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == len(result.audit_trace)
    parsed_events = [json.loads(line) for line in lines]
    assert parsed_events == result.audit_trace
    assert any("panel" in str(event) for event in parsed_events)


def test_e2e_audit_records_filtered_content_blocks(tmp_path: Path) -> None:
    from content_pipeline.contracts.audit import ExtractionPipelineOptions
    from content_pipeline.llm.client import FakeContentPipelineClient

    image_dir = tmp_path / "images"
    image_dir.mkdir()
    (image_dir / "fig1.png").write_bytes(b"img")
    pages = [[
        {"type": "page_header", "content": "Header"},
        {"type": "title", "content": {"title_content": "Results", "level": 1}},
        {"type": "image", "content": {"image_caption": "Fig. 1 | Plot showing 50% growth", "image_source": {"path": "images/fig1.png"}}, "bbox": [0, 50, 180, 200]},
        {"type": "page_number", "content": "1"},
        {"type": "page_footer", "content": "Footer"},
    ]]
    content_path = tmp_path / "content_list_v2.json"
    content_path.write_text(json.dumps(pages), encoding="utf-8")

    result = run_content_graph_pipeline(
        content_list_path=str(content_path),
        layout_path=None,
        image_root=str(tmp_path),
        paper_id="paper-filtered",
        query=None,
        model_client=FakeContentPipelineClient(),
        output_dir=str(tmp_path / "filtered_output"),
        options=ExtractionPipelineOptions(fail_fast=False),
    )

    assert result.status == "succeeded"
    assert result.document_graph_summary["filtered_block_count"] == 3
    events = [event for event in result.audit_trace if event.get("event") == "content_blocks_filtered"]
    assert events
    assert events[0]["filtered_type_counts"]["page_header"] == 1
    audit = json.loads((tmp_path / "filtered_output" / "extraction_audit.json").read_text(encoding="utf-8"))
    assert any(event.get("event") == "content_blocks_filtered" for event in audit["audit_trace"])


def test_e2e_review_md_contains_key_sections(tmp_path: Path) -> None:
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    (image_dir / "fig1.png").write_bytes(b"img")
    pages = [[
        {"type": "image", "content": {"image_caption": "Fig. 1 | Test image", "image_source": {"path": "images/fig1.png"}}, "bbox": [0, 50, 180, 200]},
    ]]
    content_path = tmp_path / "content_list_v2.json"
    content_path.write_text(json.dumps(pages), encoding="utf-8")

    run_content_graph_pipeline(
        content_list_path=str(content_path),
        layout_path=None,
        image_root=str(tmp_path),
        paper_id="paper-1",
        query=None,
        model_client=FakeContentPipelineClient(),
        output_dir=str(tmp_path),
        options=ExtractionPipelineOptions(fail_fast=False),
    )

    review = (tmp_path / "review.md").read_text(encoding="utf-8")
    assert "## Run Summary" in review
    assert "## Panel Semantic Summary" in review
    assert "## Human Review Checklist" in review


def test_partial_failure_when_panel_extraction_fails(tmp_path: Path) -> None:
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    (image_dir / "fig1.png").write_bytes(b"img")
    pages = [[
        {"type": "image", "content": {"image_caption": "unknown shape test", "image_source": {"path": "images/fig1.png"}}, "bbox": [0, 50, 180, 200]},
    ]]
    content_path = tmp_path / "content_list_v2.json"
    content_path.write_text(json.dumps(pages), encoding="utf-8")

    result = run_content_graph_pipeline(
        content_list_path=str(content_path),
        layout_path=None,
        image_root=str(tmp_path),
        paper_id="paper-1",
        query=None,
        model_client=None,
        output_dir=None,
    )

    assert result.status in ("succeeded", "partial_failure", "failed")


def test_rejected_metric_rows_written(tmp_path: Path) -> None:
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    (image_dir / "fig1.png").write_bytes(b"img")
    pages = [[
        {"type": "image", "content": {"image_caption": "Fig. 1 | Showed unregistered value", "image_source": {"path": "images/fig1.png"}}, "bbox": [0, 50, 180, 200]},
    ]]
    content_path = tmp_path / "content_list_v2.json"
    content_path.write_text(json.dumps(pages), encoding="utf-8")

    result = run_content_graph_pipeline(
        content_list_path=str(content_path),
        layout_path=None,
        image_root=str(tmp_path),
        paper_id="paper-1",
        query=None,
        model_client=FakeContentPipelineClient(),
        output_dir=str(tmp_path),
        options=ExtractionPipelineOptions(fail_fast=False),
    )

    assert result.chart_facts is not None


def test_llm_path_with_fake_valid_client(tmp_path: Path) -> None:
    """E2E with a FakeContentPipelineClient returning valid extraction output."""
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    (image_dir / "fig1.png").write_bytes(b"img")
    pages = [[
        {"type": "image", "content": {"image_caption": "Fig. 1 | Plot showing 50% increase in growth",
                                       "image_source": {"path": "images/fig1.png"}}, "bbox": [0, 50, 180, 200]},
    ]]
    content_path = tmp_path / "content_list_v2.json"
    content_path.write_text(json.dumps(pages), encoding="utf-8")

    fake = FakeContentPipelineClient()

    result = run_content_graph_pipeline(
        content_list_path=str(content_path),
        layout_path=None,
        image_root=str(tmp_path),
        paper_id="paper-llm",
        query=None,
        model_client=fake,
        output_dir=str(tmp_path / "llm_output"),
        options=ExtractionPipelineOptions(fail_fast=False),
    )

    assert result.status == "succeeded", f"Expected succeeded, got {result.status}: {result.errors}"
    assert fake.call_count >= 1, "Fake client was never called"
    assert result.output_paths
    for label, path in result.output_paths.items():
        assert Path(path).exists(), f"Missing: {label}: {path}"


def test_llm_path_fake_crash_falls_back_to_rule(tmp_path: Path) -> None:
    """When fake VLM crashes, the strict pipeline reports partial failure."""
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    (image_dir / "fig1.png").write_bytes(b"img")
    pages = [[
        {"type": "image", "content": {"image_caption": "Fig. 1 | Cell viability assay",
                                       "image_source": {"path": "images/fig1.png"}}, "bbox": [0, 50, 180, 200]},
    ]]
    content_path = tmp_path / "content_list_v2.json"
    content_path.write_text(json.dumps(pages), encoding="utf-8")

    fake = FakeContentPipelineClient(behavior="crash")

    result = run_content_graph_pipeline(
        content_list_path=str(content_path),
        layout_path=None,
        image_root=str(tmp_path),
        paper_id="paper-crash",
        query=None,
        model_client=fake,
        output_dir=str(tmp_path / "crash_output"),
        options=ExtractionPipelineOptions(fail_fast=False),
    )

    assert result.status == "partial_failure"
    assert fake.call_count >= 1
    assert result.errors


def test_llm_path_fake_bad_json_falls_back_to_rule(tmp_path: Path) -> None:
    """When fake VLM returns bad JSON, the strict pipeline falls back to MinerU-based heuristics."""
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    (image_dir / "fig1.png").write_bytes(b"img")
    pages = [[
        {"type": "image", "content": {"image_caption": "Fig. 1 | Growth curve analysis",
                                       "image_source": {"path": "images/fig1.png"}}, "bbox": [0, 50, 180, 200]},
    ]]
    content_path = tmp_path / "content_list_v2.json"
    content_path.write_text(json.dumps(pages), encoding="utf-8")

    fake = FakeContentPipelineClient(behavior="bad_json")

    result = run_content_graph_pipeline(
        content_list_path=str(content_path),
        layout_path=None,
        image_root=str(tmp_path),
        paper_id="paper-badjson",
        query=None,
        model_client=fake,
        output_dir=str(tmp_path / "badjson_output"),
        options=ExtractionPipelineOptions(fail_fast=False),
    )

    assert result.status == "succeeded"
    assert fake.call_count >= 1
    fallbacks = [e for e in result.audit_trace if e.get("event") == "panel_classification_fallback"]
    assert fallbacks
    assert fallbacks[0].get("reason") in ("degraded_llm_payload", "empty_panel_type_in_payload")
    assert "panel_classifier_payload_unrecoverable" in str(fallbacks[0].get("warnings", ""))


def _write_png_header(path: Path, *, width: int, height: int) -> None:
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8 + width.to_bytes(4, "big") + height.to_bytes(4, "big") + b"\x00" * 16)

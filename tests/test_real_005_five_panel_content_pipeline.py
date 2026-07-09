from __future__ import annotations

import json
from pathlib import Path

import pytest

from content_pipeline.contracts.audit import ExtractionPipelineOptions
from content_pipeline.llm.client import FakeContentPipelineClient
from content_pipeline.orchestration.pipeline_runner import run_content_graph_pipeline


STRUCTURED_ROOT = Path("data/pipeline_batch/005-2025_Retrievable_hydrogel_networks_with_confined/structured")
CONTENT_LIST = STRUCTURED_ROOT / "content_list_v2.json"
LAYOUT_PATH = STRUCTURED_ROOT / "layout.json"


def test_real_005_content_pipeline_runs_from_content_list_v2_for_five_panels(tmp_path: Path) -> None:
    if not CONTENT_LIST.is_file() or not LAYOUT_PATH.is_file():
        pytest.skip("real 005 pipeline fixture data is not present")
    content_list_v2 = _write_five_panel_content_list_v2(tmp_path)
    output_dir = tmp_path / "real_005_five_panel_run"
    fake = FakeContentPipelineClient()

    result = run_content_graph_pipeline(
        content_list_path=str(content_list_v2),
        layout_path=str(LAYOUT_PATH),
        image_root=str(STRUCTURED_ROOT),
        paper_id="real-005-five-panel-test",
        model_client=fake,
        output_dir=str(output_dir),
        options=ExtractionPipelineOptions(fail_fast=True, max_workers=1, llm_max_workers=1),
    )

    assert result.status == "succeeded", result.errors
    assert result.figure_panel_graph["panel_count"] == 5
    assert len(result.evidence_packets) == 5
    assert len({packet.panel_id for packet in result.evidence_packets}) == 5

    phases = [entry["inputs"].get("phase_name") for entry in fake.call_history]
    assert result.chart_digitization_results or result.visual_fact_results


def _write_five_panel_content_list_v2(tmp_path: Path) -> Path:
    data = json.loads(CONTENT_LIST.read_text(encoding="utf-8"))
    is_v2 = bool(data and isinstance(data[0], list))

    flat: list[tuple[int, dict]] = []
    if is_v2:
        for page_idx, page in enumerate(data):
            for block in page:
                if isinstance(block, dict):
                    block["page_idx"] = page_idx
                    flat.append((page_idx, block))
    else:
        for idx, block in enumerate(data):
            if isinstance(block, dict):
                flat.append((int(block.get("page_idx", 0)), block))

    selected_indices: list[int] = []
    for idx, (_, block) in enumerate(flat):
        if block.get("type") in {"image", "chart"}:
            selected_indices.append(idx)
        if len(selected_indices) == 5:
            break
    assert len(selected_indices) == 5, f"Found {len(selected_indices)} visual blocks, need 5"

    selected_page_set = {flat[idx][0] for idx in selected_indices}
    selected_visual_set = set(selected_indices)
    pages: dict[int, list[dict]] = {}
    for idx, (page_idx, block) in enumerate(flat):
        if page_idx not in selected_page_set:
            continue
        typ = block.get("type")
        if typ in {"image", "chart"} and idx not in selected_visual_set:
            continue
        pages.setdefault(page_idx, []).append(block)

    out_content = [pages[p] for p in sorted(pages)]
    out = tmp_path / "real_005_five_panel_content_list_v2.json"
    out.write_text(json.dumps(out_content, ensure_ascii=False, indent=2), encoding="utf-8")
    return out

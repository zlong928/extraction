from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch


from content_pipeline.llm.client import FakeContentPipelineClient


def test_content_pipeline_cli_engine_label(tmp_path: Path) -> None:
    from typer.testing import CliRunner
    from scripts.cli import cli

    image_dir = tmp_path / "images"
    image_dir.mkdir()
    (image_dir / "fig1.png").write_bytes(b"img")
    pages = [[
        {"type": "image", "content": {"image_caption": "Fig. 1 | Test", "image_source": {"path": "images/fig1.png"}}, "bbox": [0, 50, 180, 200]},
    ]]
    content_path = tmp_path / "content_list_v2.json"
    content_path.write_text(json.dumps(pages), encoding="utf-8")

    runner = CliRunner()
    with patch("content_pipeline.cli_bridge.build_content_pipeline_client", return_value=FakeContentPipelineClient()):
        result = runner.invoke(cli, [
            "content-pipeline",
            "--content-list", str(content_path),
            "--image-root", str(tmp_path),
            "--paper-id", "test-paper",
            "--output-dir", str(tmp_path),
        ])

    assert result.exit_code == 0
    assert "content_graph_pipeline" in result.output


def test_legacy_pipeline_shows_warning(tmp_path: Path) -> None:
    from typer.testing import CliRunner
    from scripts.cli import cli

    img = tmp_path / "test.png"
    img.write_bytes(b"img")

    runner = CliRunner()
    result = runner.invoke(cli, ["pipeline", str(img)])

    assert "legacy_image_pipeline" in result.output or result.exit_code != 0


def test_output_paths_exist_after_pipeline_run(tmp_path: Path) -> None:
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    (image_dir / "fig1.png").write_bytes(b"img")
    pages = [[
        {"type": "image", "content": {"image_caption": "Fig. 1 | Test", "image_source": {"path": "images/fig1.png"}}, "bbox": [0, 50, 180, 200]},
    ]]
    content_path = tmp_path / "content_list_v2.json"
    content_path.write_text(json.dumps(pages), encoding="utf-8")

    from content_pipeline.orchestration.pipeline_runner import run_content_graph_pipeline
    result = run_content_graph_pipeline(
        content_list_path=str(content_path),
        layout_path=None,
        image_root=str(tmp_path),
        paper_id="test-paper",
        query=None,
        model_client=FakeContentPipelineClient(),
        output_dir=str(tmp_path / "output"),
    )

    assert result.output_paths
    for label, path in result.output_paths.items():
        assert Path(path).exists(), f"Output path missing: {label}: {path}"


def test_no_llm_does_not_claim_digitization(tmp_path: Path) -> None:
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    (image_dir / "fig1.png").write_bytes(b"img")
    pages = [[
        {"type": "image", "content": {"image_caption": "Fig. 1 | Plot showing 50% increase", "image_source": {"path": "images/fig1.png"}}, "bbox": [0, 50, 180, 200]},
    ]]
    content_path = tmp_path / "content_list_v2.json"
    content_path.write_text(json.dumps(pages), encoding="utf-8")

    from content_pipeline.orchestration.pipeline_runner import run_content_graph_pipeline
    result = run_content_graph_pipeline(
        content_list_path=str(content_path),
        layout_path=None,
        image_root=str(tmp_path),
        paper_id="test-paper",
        query=None,
        model_client=None,
        output_dir=str(tmp_path),
    )

    assert result.status == "failed"
    assert not result.chart_digitization_results
    assert (tmp_path / "extraction_audit.json").exists()

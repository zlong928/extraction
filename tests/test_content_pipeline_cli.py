from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


from content_pipeline.llm.client import FakeContentPipelineClient


def test_batch_submit_passes_folder_selection_and_returns_durable_id(tmp_path: Path) -> None:
    from typer.testing import CliRunner
    from scripts.cli import cli

    source = tmp_path / "source"
    source.mkdir()
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"result_semantics": {"model": "test"}}), encoding="utf-8")
    with patch("app.services.batches.BatchSubmissionService.submit") as submit:
        submit.return_value = SimpleNamespace(id="batch-1", status="running")
        result = CliRunner().invoke(cli, [
            "batch", "submit", str(source), "--submission-key", "submission-1", "--config", str(config),
            "--project-id", "7", "--concurrency", "3", "--limit", "2", "--json",
        ])

    assert result.exit_code == 0
    assert json.loads(result.output) == {"batch_run_id": "batch-1", "status": "running"}
    assert submit.call_args.kwargs["project_id"] == 7
    assert submit.call_args.kwargs["batch_concurrency"] == 3
    assert submit.call_args.kwargs["limit"] == 2


def test_batch_status_maps_terminal_failure_to_exit_one() -> None:
    from typer.testing import CliRunner
    from scripts.cli import cli

    snapshot = {
        "id": "batch-2", "status": "partial_failed", "counts": {"failed": 1}, "total": 1
    }
    with patch("app.services.batches.BatchOperationsService.snapshot", return_value=snapshot):
        result = CliRunner().invoke(cli, ["batch", "status", "batch-2", "--json"])

    assert result.exit_code == 1
    assert json.loads(result.output)["status"] == "partial_failed"


def test_batch_follow_interrupt_returns_130_without_cancelling() -> None:
    from typer.testing import CliRunner
    from scripts.cli import cli

    snapshot = {"id": "batch-3", "status": "running", "counts": {"processing": 1}, "total": 1}
    with patch("app.services.batches.BatchOperationsService.snapshot", return_value=snapshot), patch(
        "scripts.cli.time.sleep", side_effect=KeyboardInterrupt
    ), patch("app.services.batches.BatchLifecycleService.cancel") as cancel:
        result = CliRunner().invoke(cli, ["batch", "status", "batch-3", "--follow", "--json"])

    assert result.exit_code == 130
    cancel.assert_not_called()


def test_batch_retry_passes_selected_items_and_prints_jobs() -> None:
    from typer.testing import CliRunner
    from scripts.cli import cli

    with patch("app.services.batches.BatchLifecycleService.retry_failed_items", return_value=[41, 42]) as retry:
        result = CliRunner().invoke(
            cli,
            ["batch", "retry", "batch-4", "--item-id", "item-a", "--item-id", "item-b"],
        )

    assert result.exit_code == 0
    assert json.loads(result.output) == {"batch_run_id": "batch-4", "job_ids": [41, 42]}
    retry.assert_called_once_with("batch-4", ["item-a", "item-b"])


def test_batch_cancel_prints_terminal_status() -> None:
    from typer.testing import CliRunner
    from scripts.cli import cli

    with patch(
        "app.services.batches.BatchLifecycleService.cancel",
        return_value=SimpleNamespace(id="batch-5", status="cancelling"),
    ) as cancel:
        result = CliRunner().invoke(cli, ["batch", "cancel", "batch-5"])

    assert result.exit_code == 0
    assert json.loads(result.output) == {"batch_run_id": "batch-5", "status": "cancelling"}
    cancel.assert_called_once_with("batch-5")


def test_batch_export_passes_output_directory_and_prints_paths(tmp_path: Path) -> None:
    from typer.testing import CliRunner
    from scripts.cli import cli

    manifest = tmp_path / "manifest.json"
    events = tmp_path / "events.jsonl"
    with patch("app.services.batches.BatchOperationsService.export", return_value=(manifest, events)) as export:
        result = CliRunner().invoke(
            cli,
            ["batch", "export", "batch-6", "--output-dir", str(tmp_path)],
        )

    assert result.exit_code == 0
    assert json.loads(result.output) == {"manifest": str(manifest), "events": str(events)}
    export.assert_called_once_with("batch-6", tmp_path)


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

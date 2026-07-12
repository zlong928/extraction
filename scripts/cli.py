#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import time
from dataclasses import fields, is_dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

load_dotenv(ROOT / ".env")

from typing import Any, Optional

import typer
from rich.console import Console
from rich.table import Table

from app.core.constants import MARKDOWN_IMAGE_RE

cli = typer.Typer(
    name="extraction",
    help="论文图片提取 CLI",
    no_args_is_help=True,
)
batch_cli = typer.Typer(name="batch", help="Durable batch PDF processing")
cli.add_typer(batch_cli, name="batch")
console = Console()

_MARKDOWN_IMAGE_RE = MARKDOWN_IMAGE_RE
_VERBOSE = False


@cli.callback()
def _main(verbose: bool = typer.Option(False, "--verbose", "-v", help="输出完整详情")):
    global _VERBOSE
    _VERBOSE = verbose


def _out(*args, **kwargs):
    if _VERBOSE:
        console.print(*args, **kwargs)


def _always(*args, **kwargs):
    console.print(*args, **kwargs)


def _save_json(data: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _verbose_table(title: str, rows: list[tuple[str, str]]) -> None:
    if not _VERBOSE:
        return
    table = Table(title=title)
    table.add_column("属性", style="cyan")
    table.add_column("值", style="green")
    for k, v in rows:
        table.add_row(k, v)
    console.print(table)


def _plain_data(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _plain_data(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, dict):
        return {str(key): _plain_data(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_plain_data(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value


def _verbose_json(data: dict, label: str = "") -> None:
    if not _VERBOSE:
        return
    if label:
        console.print(f"\n[bold]{label}[/bold]")
    console.print_json(data=data)


_TERMINAL_BATCH_STATUSES = {"succeeded", "partial_failed", "failed", "cancelled"}


def _batch_exit_code(status: str) -> int:
    return 0 if status in {"succeeded", "cancelled"} else 1 if status in {"partial_failed", "failed"} else 0


def _print_json_line(payload: dict[str, Any]) -> None:
    typer.echo(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def _print_batch_snapshot(snapshot: dict[str, Any], *, json_output: bool) -> None:
    if json_output:
        _print_json_line(snapshot)
        return
    table = Table(title=f"Batch {snapshot['id']} [{snapshot['status']}]")
    table.add_column("State")
    table.add_column("Count", justify="right")
    for status, count in snapshot["counts"].items():
        table.add_row(status, str(count))
    table.add_row("total", str(snapshot["total"]))
    console.print(table)


def _follow_batch(batch_run_id: str, *, interval_seconds: float, json_output: bool) -> dict[str, Any]:
    from app.db import SessionLocal
    from app.services.batches import BatchOperationsService

    try:
        while True:
            with SessionLocal() as db:
                snapshot = BatchOperationsService(db).snapshot(batch_run_id)
            _print_batch_snapshot(snapshot, json_output=json_output)
            if snapshot["status"] in _TERMINAL_BATCH_STATUSES:
                return snapshot
            time.sleep(interval_seconds)
    except KeyboardInterrupt:
        raise typer.Exit(130)


@batch_cli.command("submit")
def batch_submit(
    source_root: Path = typer.Argument(..., exists=True, file_okay=False),
    submission_key: str = typer.Option(..., "--submission-key"),
    config: Path = typer.Option(..., "--config", exists=True, dir_okay=False),
    project_id: int = typer.Option(1, "--project-id", min=1),
    concurrency: int = typer.Option(1, "--concurrency", min=1),
    limit: Optional[int] = typer.Option(None, "--limit", min=1),
    follow: bool = typer.Option(False, "--follow"),
    interval_seconds: float = typer.Option(2.0, "--interval", min=0.1),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Submit a deterministic folder manifest and return immediately unless --follow is set."""
    from app.db import SessionLocal
    from app.services.batches import BatchSubmissionService

    try:
        config_snapshot = json.loads(config.read_text(encoding="utf-8"))
        if not isinstance(config_snapshot, dict):
            raise ValueError("Batch config must be a JSON object")
        with SessionLocal() as db:
            batch = BatchSubmissionService(db).submit(
                project_id=project_id,
                source_root=source_root,
                submission_key=submission_key,
                batch_concurrency=concurrency,
                config_snapshot=config_snapshot,
                limit=limit,
            )
            batch_id = batch.id
            status = batch.status
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    if not follow:
        if json_output:
            _print_json_line({"batch_run_id": batch_id, "status": status})
        else:
            _always(batch_id)
        raise typer.Exit(_batch_exit_code(status))
    snapshot = _follow_batch(batch_id, interval_seconds=interval_seconds, json_output=json_output)
    raise typer.Exit(_batch_exit_code(snapshot["status"]))


@batch_cli.command("status")
def batch_status(
    batch_run_id: str,
    follow: bool = typer.Option(False, "--follow"),
    interval_seconds: float = typer.Option(2.0, "--interval", min=0.1),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Show durable PostgreSQL progress, optionally until terminal."""
    from app.db import SessionLocal
    from app.services.batches import BatchOperationsService

    if follow:
        snapshot = _follow_batch(batch_run_id, interval_seconds=interval_seconds, json_output=json_output)
    else:
        with SessionLocal() as db:
            snapshot = BatchOperationsService(db).snapshot(batch_run_id)
        _print_batch_snapshot(snapshot, json_output=json_output)
    raise typer.Exit(_batch_exit_code(snapshot["status"]))


@batch_cli.command("retry")
def batch_retry(batch_run_id: str, item_ids: list[str] = typer.Option(..., "--item-id")) -> None:
    """Explicitly retry selected failed items and report newly scheduled Job IDs."""
    from app.db import SessionLocal
    from app.services.batches import BatchLifecycleService

    try:
        with SessionLocal() as db:
            job_ids = BatchLifecycleService(db).retry_failed_items(batch_run_id, item_ids)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    _print_json_line({"batch_run_id": batch_run_id, "job_ids": job_ids})


@batch_cli.command("cancel")
def batch_cancel(batch_run_id: str) -> None:
    """Cancel work that has not started; processing work is not preempted."""
    from app.db import SessionLocal
    from app.services.batches import BatchLifecycleService

    try:
        with SessionLocal() as db:
            batch = BatchLifecycleService(db).cancel(batch_run_id)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    _print_json_line({"batch_run_id": batch.id, "status": batch.status})


@batch_cli.command("export")
def batch_export(batch_run_id: str, output_dir: Path = typer.Option(..., "--output-dir")) -> None:
    """Rebuild manifest.json and events.jsonl from PostgreSQL facts."""
    from app.db import SessionLocal
    from app.services.batches import BatchOperationsService

    with SessionLocal() as db:
        manifest_path, events_path = BatchOperationsService(db).export(batch_run_id, output_dir)
    _print_json_line({"manifest": str(manifest_path), "events": str(events_path)})


def _build_cli_pipeline_client() -> Any | None:
    from app.services.agent.llm_client import LLMClient
    from app.services.extraction.llm_config import build_vlm_config
    client = LLMClient(build_vlm_config())
    if client is None:
        _out("[yellow]\u26a0 \u672a\u8bbe\u7f6e LLM API Key\uff0c\u4f7f\u7528\u672c\u5730\u89c4\u5219 fallback[/yellow]")
    return client


@cli.command()
def parse(
    pdf: Path = typer.Argument(..., help="PDF 文件路径", exists=True),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="输出目录"),
):
    """用 MinerU 解析 PDF，输出 Markdown + 图片"""
    from app.services.mineru_parser import MinerUParserService, MinerUParserUnavailable

    if not isinstance(output, Path):
        output = ROOT / "data" / "results" / "mineru" / pdf.stem
    output.mkdir(parents=True, exist_ok=True)

    try:
        _out(f"[bold green]正在用 MinerU 解析 {pdf.name}...[/bold green]")
        result = MinerUParserService().parse_pdf_file(
            pdf, data_id=f"cli-{pdf.stem}", output_root=output
        )
    except MinerUParserUnavailable:
        _always("[red]MINERU_API_KEY 未设置[/red]")
        raise typer.Exit(1)

    markdown = "\n\n".join(result.parsed_document.text_pages)
    extract_dir = Path(result.extract_dir) if result.extract_dir else output / "extracted"

    md_path = output / "full.md"
    md_path.write_text(markdown, encoding="utf-8")

    image_refs = []
    for m in _MARKDOWN_IMAGE_RE.finditer(markdown):
        alt = m.group(1).strip()
        img_rel = m.group(2).strip().lstrip("./")
        img_path = extract_dir / "images" / Path(img_rel).name
        if not img_path.exists():
            for f in (extract_dir / "images").rglob(Path(img_rel).name):
                img_path = f
                break
        image_refs.append({"alt": alt, "path": str(img_path), "exists": img_path.is_file()})

    output_data = {
        "batch_id": result.batch_id,
        "markdown": markdown,
        "extract_dir": str(extract_dir),
        "markdown_file": str(md_path),
        "image_count": len(image_refs),
        "image_refs": image_refs,
    }

    _verbose_table(f"解析完成: {pdf.name}", [
        ("Batch ID", result.batch_id),
        ("Markdown", str(md_path)),
        ("提取目录", str(extract_dir)),
        ("图片数量", str(len(image_refs))),
    ])
    _verbose_json(output_data, "完整输出")

    _always(f"✅ 解析完成：{pdf.name}")
    _always(f"   提取目录：{extract_dir}")
    _always(f"   图片数量：{len(image_refs)}")

    save = output / "parse_result.json"
    _save_json(output_data, save)
    _always(f"   结果文件：{save}")

    return output_data


@cli.command(name="content-pipeline")
def content_pipeline(
    content_list: Path = typer.Option(..., "--content-list", help="MinerU content_list_v2.json 路径", exists=True),
    image_root: Path = typer.Option(..., "--image-root", help="MinerU images 根目录", exists=True),
    layout: Optional[Path] = typer.Option(None, "--layout", help="MinerU layout.json 路径"),
    paper_id: str = typer.Option("cli-paper", "--paper-id", help="Paper ID"),
    query: str = typer.Option("", "--query", "-q", help="提取查询"),
    output_dir: Optional[Path] = typer.Option(None, "--output-dir", "-o", help="输出目录"),
    use_llm: bool = typer.Option(True, "--llm/--no-llm", help="使用 LLM 提取（严格链路默认开启；--no-llm 会失败）"),
):
    """运行 content_list_v2 优先的 Content Graph Evidence Pipeline。"""
    from content_pipeline.contracts.audit import ExtractionPipelineOptions
    from content_pipeline import run_content_pipeline as run_backend_pipeline

    out_dir = output_dir or ROOT / "data" / "content_pipeline_results" / content_list.parent.name
    result, summary = run_backend_pipeline(
        content_list_path=content_list,
        layout_path=layout,
        image_root=image_root,
        paper_id=paper_id,
        query=query or None,
        use_llm=use_llm,
        output_dir=out_dir,
        options=ExtractionPipelineOptions(fail_fast=False),
        on_llm_disabled=lambda msg: _out(f"[yellow]{msg}[/yellow]"),
        client_factory=_build_cli_pipeline_client,
    )
    extractor_modes = summary.extractor_modes
    digitization_count = summary.digitization_count
    " ".join(f"{k}={v}" for k, v in sorted(extractor_modes.items()))
    digitization_count = summary.digitization_count
    _always("=== Pipeline Results ===")
    _always("engine=content_graph_pipeline")
    _always(f"status={result.status}")
    _always(f"figures={result.figure_panel_graph.get('figure_count', 0)}")
    _always(f"panels={result.figure_panel_graph.get('panel_count', 0)}")
    _always(f"evidence_packets={len(result.evidence_packets)}")
    _always(f"chart_digitizations={digitization_count}")
    _always(f"chart_facts={len(result.chart_facts)}")
    _always(f"chart_points={len(result.chart_points)}")
    _always(f"visual_facts={len(result.visual_fact_results)}")
    _always(f"image_observations={len(result.image_observations)}")
    _always("Extraction modes:")
    for mode, count in summary.extractor_modes.items():
        _always(f"  {mode}: {count}")
    _always(f"Output path: {result.output_paths.get('audit_json', 'N/A')}")
    _always(f"Chart facts CSV: {result.output_paths.get('chart_fact_csv', 'N/A')}")
    if "heatmap_candidate_csv" in result.output_paths:
        _always(f"Heatmap candidates CSV: {result.output_paths['heatmap_candidate_csv']}")
    _always(f"Review: {result.output_paths.get('review_md', 'N/A')}")

if __name__ == "__main__":
    cli()

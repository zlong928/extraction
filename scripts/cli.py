#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
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


def _build_cli_pipeline_client() -> Any | None:
    from content_pipeline.llm.client import build_content_pipeline_client
    client = build_content_pipeline_client()
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
    )
    extractor_modes = summary.extractor_modes
    digitization_count = summary.digitization_count
    mode_breakdown = " ".join(f"{k}={v}" for k, v in sorted(extractor_modes.items()))
    digitization_count = summary.digitization_count
    _always("=== Pipeline Results ===")
    _always(f"engine=content_graph_pipeline")
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

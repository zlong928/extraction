#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from content_pipeline.adapters.visual_fact_adapters import chart_digitization_from_payload
from content_pipeline.contracts.panel_facts import build_panel_fact_rows
from content_pipeline.contracts.visual import VisualExtractionContext
from content_pipeline.export.csv_contracts import write_chart_fact_csv



def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run one-panel chart-digitization smoke test and dump CSV preview."
    )
    parser.add_argument(
        "--audit",
        type=Path,
        default=Path("data/content_pipeline_results/paper_7/extraction_audit.json"),
        help="Path to extraction_audit.json",
    )
    parser.add_argument(
        "--panel-id",
        default="fig-3-l",
        help="Target panel id (default: fig-3-l)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("tmp/fig3l_single_panel_test"),
        help="Output directory for CSV files",
    )
    return parser.parse_args()


def _find_item(items: list[dict], panel_id: str):
    for item in items:
        if isinstance(item, dict) and item.get("panel_id") == panel_id:
            return item
    return None


def _write_chart_points_csv(path: Path, points: list) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(points[0].csv_dict().keys()) if points else [])
        if points:
            writer.writeheader()
            for point in points:
                writer.writerow(point.csv_dict())
    return path


def _print_preview(path: Path, max_lines: int = 8) -> None:
    print(f"\n== {path} ==")
    if not path.exists():
        print("(not found)")
        return

    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        for i, row in enumerate(reader, 1):
            print(row)
            if i >= max_lines:
                break


def main() -> None:
    args = parse_args()
    audit_path = args.audit
    panel_id = args.panel_id
    output_dir = args.output_dir

    payload = json.loads(audit_path.read_text(encoding="utf-8"))
    chart_result = _find_item(payload.get("chart_digitization_results", []) or [], panel_id)
    if chart_result is None:
        raise SystemExit(f"chart_digitization_results not found for panel_id={panel_id}")

    packet_item = _find_item(payload.get("evidence_packets", []) or [], panel_id)
    if packet_item is None:
        raise SystemExit(f"evidence packet not found for panel_id={panel_id}")

    raw_output = chart_result.get("raw_output") or {}
    context = VisualExtractionContext(
        paper_id=str(chart_result.get("paper_id", "")),
        figure_id=str(chart_result.get("figure_id", "")),
        panel_id=str(chart_result.get("panel_id", panel_id)),
        image_ref=str(packet_item.get("image_ref", "")),
        visual_type="chart",
        caption_text=str(packet_item.get("primary_caption", "") or ""),
        caption_rich_text="",
        caption_structured={},
        tables=str(packet_item.get("tables", "") or ""),
        formulas="",
        evidence_map=list(packet_item.get("evidence_map", []) or []),
        section_hierarchy=list(packet_item.get("section_hierarchy", []) or []),
        spatial_context=list(packet_item.get("spatial_context", []) or []),
        reading_context=list(packet_item.get("reading_context", []) or []),
        paper_task_plan_summary=dict(payload.get("paper_task_plan", {}) or {}),
    )

    rebuilt = chart_digitization_from_payload(raw_output, context)

    # emulate runner artifact behavior for this single panel
    packet_namespace = SimpleNamespace(
        paper_id=str(packet_item.get("paper_id", "")),
        figure_id=str(packet_item.get("figure_id", "")),
        panel_id=str(packet_item.get("panel_id", panel_id)),
        image_ref=str(packet_item.get("image_ref", "")),
    )
    panel_facts = build_panel_fact_rows(
        chart_digitization_results=[rebuilt],
        packet_by_panel={panel_id: packet_namespace},
        audit_trace=[],
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    chart_points_path = output_dir / f"{panel_id}_chart_points.csv"
    chart_facts_path = output_dir / f"{panel_id}_chart_facts.csv"

    _write_chart_points_csv(chart_points_path, rebuilt.points or rebuilt.raw_points)
    write_chart_fact_csv(chart_facts_path, panel_facts)

    print("=== backend_smoke_single_panel ===")
    print(f"panel: {panel_id}")
    print(f"audit_file: {audit_path}")
    print(f"point_input_count: {len(raw_output.get('data_points') or [])}")
    print(f"rebuilt.points_count: {len(rebuilt.points)}")
    print(f"rebuilt.raw_points_count: {len(rebuilt.raw_points)}")
    print(f"rebuilt.warnings: {rebuilt.warnings}")
    print(f"chart_facts_rows: {len(panel_facts)}")

    _print_preview(chart_points_path)
    _print_preview(chart_facts_path)


if __name__ == "__main__":
    main()

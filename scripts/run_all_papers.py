#!/usr/bin/env python3
"""Run content_pipeline on all 6 restored papers.

Produces the same output structure as before:
  data/content_pipeline_results/{paper_name}/
    extraction_audit.json
    review.md
    chart_facts.csv
    ...

Usage:
  .venv/bin/python scripts/run_all_papers.py [--real-llm]
"""

from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from content_pipeline import run_content_graph_pipeline
from content_pipeline.contracts.audit import ExtractionPipelineOptions

REAL_LLM = "--real-llm" in sys.argv

PAPERS = [
    "001-2017_3D_HA_3D_printing_of_bacteria_into_functional_complex_materials",
    "002-2023_Highly_Efficient_Nitrogen_Fixing_Microbial_Hydrogel_Device",
    "003-2024_Living_Porous_Ceramics_for_Bacteria_Regulated_Gas_Sensing_and_Carbon_Capture",
    "004-2025_Dual_carbon_sequestration_with_photosynthetic_living_materials",
    "005-2025_Retrievable_hydrogel_networks_with_confined",
    "006-2026_Mesospace_domain_orchestrates_microbial",
]

OUTPUT_ROOT = ROOT / "data" / "content_pipeline_results"
BATCH_ROOT = ROOT / "data" / "pipeline_batch"


def run_one(name: str) -> dict:
    structured = BATCH_ROOT / name / "structured"
    content_list = structured / "content_list_v2.json"
    layout = structured / "layout.json"
    image_root = str(structured)

    if not content_list.is_file():
        return {"name": name, "status": "skipped", "error": "content_list not found"}

    output_dir = OUTPUT_ROOT / name
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if REAL_LLM:
        from content_pipeline.llm.client import build_content_pipeline_client
        client = build_content_pipeline_client()
        if client is None:
            return {"name": name, "status": "failed", "error": "No LLM client available"}
        print(f"  🤖 REAL LLM", end="")
    else:
        from content_pipeline.llm.client import FakeContentPipelineClient
        client = FakeContentPipelineClient(behavior="valid")
        print(f"  🤖 Fake LLM ", end="")

    opts = ExtractionPipelineOptions(
        fail_fast=False,
        max_workers=4,
        llm_max_workers=4,
        chart_only=False,
    )

    t0 = time.time()
    result = run_content_graph_pipeline(
        content_list_path=str(content_list),
        layout_path=str(layout) if layout.is_file() else None,
        image_root=image_root,
        paper_id=name,
        model_client=client,
        output_dir=str(output_dir),
        options=opts,
    )
    elapsed = time.time() - t0

    event_counts = {}
    for r in result.audit_trace:
        ev = r.get("event", "(empty)")
        event_counts[ev] = event_counts.get(ev, 0) + 1

    return {
        "name": name,
        "status": result.status,
        "elapsed_s": round(elapsed, 1),
        "figures": result.figure_panel_graph.get("figure_count", 0),
        "panels": result.figure_panel_graph.get("panel_count", 0),
        "evidence_packets": len(result.evidence_packets),
        "events": event_counts,
        "errors": len(result.errors),
        "output_dir": str(output_dir),
    }


def main():
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)

    results = []
    for name in PAPERS:
        print(f"\n📄 {name}")
        r = run_one(name)
        results.append(r)
        print(f"   → {r['status']} ({r.get('elapsed_s', '?')}s)")
        if r.get("figures"):
            print(f"   figures={r['figures']} panels={r['panels']} packets={r['evidence_packets']}")
        if r.get("errors"):
            print(f"   ⚠️  {r['errors']} errors")
        if r.get("error"):
            print(f"   ❌ {r['error']}")

    print(f"\n{'='*50}")
    print(f"Output root: {OUTPUT_ROOT}")
    print()
    print(f"{'Paper':50s} {'Status':12s} {'Figs':5s} {'Panels':6s} {'Time':6s}")
    print("-" * 80)
    for r in results:
        name = r["name"][:48]
        status = r.get("status", "?")
        figs = str(r.get("figures", ""))
        panels = str(r.get("panels", ""))
        t = str(r.get("elapsed_s", "?"))
        print(f"{name:50s} {status:12s} {figs:5s} {panels:6s} {t:6s}s")

    # Save summary JSON
    summary_path = OUTPUT_ROOT / "_run_summary.json"
    summary_path.write_text(json.dumps(results, indent=2, default=str))
    print(f"\n📊 Summary saved: {summary_path}")


if __name__ == "__main__":
    main()

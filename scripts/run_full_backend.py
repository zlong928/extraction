"""
Run the full backend pipeline against the 2017 content list using the real LLM model.
Analyzes output for unexpected panel behavior.
"""

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from content_pipeline.orchestration.pipeline_runner import run_content_graph_pipeline
from content_pipeline.contracts.audit import ExtractionPipelineOptions


DEFAULT_PAPER_DIR = "data/pipeline_batch/001-2017_3D_HA_3D_printing_of_bacteria_into_functional_complex_materials"
CONTENT_LIST = sys.argv[1] if len(sys.argv) > 1 else f"{DEFAULT_PAPER_DIR}/structured/content_list_v2.json"
IMAGE_ROOT = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_PAPER_DIR
OUTPUT_DIR = sys.argv[3] if len(sys.argv) > 3 else "data/content_pipeline_results/full_backend_2017_replay"


def build_real_client() -> Any:
    from content_pipeline.llm.client import build_content_pipeline_client

    client = build_content_pipeline_client()
    if client is None:
        raise RuntimeError(
            "No LLM client available. Set VLM_API_KEY or OPENAI_API_KEY in .env"
        )
    return client


def analyze_results(audit: list[dict[str, Any]]) -> dict[str, Any]:
    analysis: dict[str, Any] = {
        "total_events": len(audit),
        "events_by_type": {},
        "schema_repairs": [],
        "degraded_events": [],
        "failure_events": [],
        "llm_phase_counts": {},
        "visual_fact_counts": {},
        "chart_completions": [],
        "panel_issues": [],
    }

    for event in audit:
        ev = event.get("event", "(empty)")
        analysis["events_by_type"][ev] = analysis["events_by_type"].get(ev, 0) + 1

        if ev == "schema_repair_applied":
            analysis["schema_repairs"].append(event)

        if "degraded" in ev.lower():
            analysis["degraded_events"].append(event)

        if "failed" in ev.lower() or event.get("exception_type"):
            analysis["failure_events"].append(event)

        if ev in ("llm_phase_started", "llm_phase_completed"):
            ph = event.get("phase_name", "")
            analysis["llm_phase_counts"][ph] = analysis["llm_phase_counts"].get(ph, 0) + 1

        if ev == "visual_fact_extraction_completed":
            pid = event.get("panel_id", "")
            cnt = event.get("visual_fact_candidate_count", 0)
            obs = event.get("legacy_observation_count", 0)
            analysis["visual_fact_counts"][pid] = {
                "candidates": cnt,
                "observations": obs,
                "degraded": event.get("degraded", False),
            }

        if ev == "chart_digitization_completed":
            analysis["chart_completions"].append(event)

        if ev == "image_observation_degraded":
            analysis["panel_issues"].append(
                f"image_observation_degraded: panel={event.get('panel_id','')} "
                f"meta={event.get('repair_meta',{})}"
            )

        if ev == "image_observation_failed":
            analysis["panel_issues"].append(
                f"image_observation_failed: panel={event.get('panel_id','')} "
                f"msg={event.get('message','')[:100]}"
            )

        if ev == "panel_extraction_skipped":
            pid = event.get("panel_id", "")
            reason = event.get("reason", "")
            exc_reason = event.get("exclusion_reason", "")
            roled = event.get("evidence_role", "")
            if "unusable" in roled or "missing" in reason:
                analysis["panel_issues"].append(
                    f"panel_skipped_unusable: panel={pid} reason={reason} "
                    f"role={roled}"
                )

    # Aggregate repair types
    repair_types: dict[str, int] = {}
    for r in analysis["schema_repairs"]:
        for repair in r.get("repairs", []):
            rt = repair.get("repair", "unknown")
            repair_types[rt] = repair_types.get(rt, 0) + 1
    analysis["repair_types"] = repair_types
    analysis["total_repairs"] = sum(repair_types.values())

    return analysis


def print_header(title: str) -> None:
    print()
    print("=" * 72)
    print(f"  {title}")
    print("=" * 72)


def print_analysis(analysis: dict[str, Any]) -> None:
    print_header("OVERALL STATS")
    print(f"  Total audit events:            {analysis['total_events']}")
    print(f"  Total schema repairs:          {analysis['total_repairs']}")
    print(f"  Degraded events:               {len(analysis['degraded_events'])}")
    print(f"  Failure/error events:           {len(analysis['failure_events'])}")

    print_header("LLM PHASE COUNTS")
    for phase, cnt in sorted(analysis["llm_phase_counts"].items()):
        print(f"  {phase:40s} {cnt}")

    print_header("SCHEMA REPAIR TYPES")
    if analysis["repair_types"]:
        for rt, cnt in sorted(analysis["repair_types"].items(), key=lambda x: -x[1]):
            print(f"  {rt:40s} {cnt}")
    else:
        print("  (none)")

    print_header("VISUAL FACT EXTRACTION RESULTS")
    for pid, info in sorted(analysis["visual_fact_counts"].items()):
        tag = " [DEGRADED]" if info["degraded"] else ""
        print(
            f"  {pid:25s} candidates={info['candidates']:3d} "
            f"observations={info['observations']:3d}{tag}"
        )

    print_header("CHART DIGITIZATION RESULTS")
    for c in analysis["chart_completions"]:
        print(
            f"  {c.get('panel_id',''):25s} type={c.get('chart_type',''):20s} "
            f"status={c.get('digitization_status',''):25s} "
            f"points={c.get('point_count',0)}"
        )

    print_header("PANEL ISSUES / WARNINGS")
    if analysis["panel_issues"]:
        for issue in analysis["panel_issues"]:
            print(f"  {issue}")
    else:
        print("  (none)")

    print_header("DEGRADED / ADAPTER EVENTS")
    if analysis["degraded_events"]:
        for d in analysis["degraded_events"]:
            print(f"  {d.get('event',''):45s} phase={d.get('phase_name',''):30s}")
    else:
        print("  (none)")


def main() -> None:
    print_header("BUILDING REAL LLM CLIENT")
    client = build_real_client()
    print(f"  Client type: {type(client).__name__}")
    print(f"  Model:       {os.getenv('OPENAI_MODEL', 'step-3.7-flash')}")

    print_header("RUNNING PIPELINE")
    start = time.time()

    result = run_content_graph_pipeline(
        content_list_path=CONTENT_LIST,
        layout_path=None,
        image_root=IMAGE_ROOT,
        paper_id="full_backend_2017",
        query=None,
        model_client=client,
        output_dir=OUTPUT_DIR,
        options=ExtractionPipelineOptions(
            fail_fast=False,
            max_workers=3,
            llm_max_workers=3,
            chart_only=False,
        ),
    )

    elapsed = time.time() - start
    print(f"  Elapsed: {elapsed:.1f}s")
    print(f"  Status:  {result.status}")

    print_analysis({
        "total_events": len(result.audit_trace),
        "events_by_type": {},
        "schema_repairs": [e for e in result.audit_trace if e.get("event") == "schema_repair_applied"],
        "degraded_events": [e for e in result.audit_trace if "degraded" in e.get("event", "").lower()],
        "failure_events": [e for e in result.audit_trace if "failed" in e.get("event", "").lower() or e.get("exception_type")],
        "llm_phase_counts": {},
        "visual_fact_counts": {},
        "chart_completions": [e for e in result.audit_trace if e.get("event") == "chart_digitization_completed"],
        "panel_issues": [],
    })

    # Re-analyze with actual data
    from content_pipeline.orchestration.pipeline_runner import LiveAuditTrace

    audit = []
    if isinstance(result.audit_trace, LiveAuditTrace):
        audit_path = result.audit_trace.path
        if os.path.exists(audit_path):
            with open(audit_path) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        audit.append(json.loads(line))
    else:
        audit = list(result.audit_trace)

    if audit:
        print_header("DETAILED ANALYSIS FROM AUDIT FILE")
        analysis = analyze_results(audit)
        print_analysis(analysis)
    else:
        print_header("WARNING: No audit trace available for detailed analysis")

    print_header("DONE")
    print(f"  Output directory: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()

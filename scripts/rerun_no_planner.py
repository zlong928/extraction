"""Re-run the no_planner pipeline with realistic LLM output patterns matching the original run."""

from __future__ import annotations

from typing import Any

from content_pipeline.orchestration.pipeline_runner import run_content_graph_pipeline
from content_pipeline.contracts.audit import ExtractionPipelineOptions


class RealisticFakeClient:
    """Replicates actual LLM output patterns from the original no_planner run."""

    def __init__(self):
        self.call_count = 0
        self.call_history: list[dict[str, Any]] = []
        self._phase_call_counts: dict[str, int] = {}

    def call_json(self, *, prompt: str, inputs: dict) -> dict:
        self.call_count += 1
        phase = str(inputs.get("phase_name", ""))
        self._phase_call_counts.setdefault(phase, 0)
        idx = self._phase_call_counts[phase]
        self._phase_call_counts[phase] = idx + 1
        self.call_history.append({
            "phase": phase, "idx": idx, "prompt": prompt[:200], "inputs": dict(inputs),
        })
        return self._payload_for_phase(phase, idx)

    def _payload_for_phase(self, phase: str, idx: int) -> dict:
        if phase == "panel_semantic_classifier":
            patterns = [
                {},
                {},
                {},
                self._good_panel_semantic(),
                {},
                self._good_panel_semantic(),
                self._good_panel_semantic(),
                self._good_panel_semantic(),
                {},
                {},
            ]
            return patterns[idx] if idx < len(patterns) else self._good_panel_semantic()

        if phase == "chart_visual_semantic":
            patterns = [
                {},
                {"value": "shear stress", "confidence": 0.8, "source": "image",
                 "evidence_ids": ["ev-1"]},
                {},
                {},
                {"value": "viscosity", "confidence": 0.7, "source": "image",
                 "evidence_ids": ["ev-2"]},
                {},
                self._good_chart_visual(),
                {},
                {": ": "", "axis_readability": "readable",
                 "Flink (%)": "flink"},
            ]
            return patterns[idx] if idx < len(patterns) else self._good_chart_visual()

        if phase == "image_observation":
            patterns = [
                {},
                {": ": "", "confidence": 0.85,
                 "visual_fact_candidates": ["fact"], "observations": ["obs"]},
                {},
                self._good_image_obs(),
                self._good_image_obs(),
                {},
                self._good_image_obs(),
                {".image_kind": "microscopy", "confidence": 0.9,
                 "visual_fact_candidates": []},
                {"": "", "confidence": 0.5},
                {},
            ]
            return patterns[idx] if idx < len(patterns) else self._good_image_obs()

        if phase == "chart_digitization":
            return self._good_chart_digitization()

        return {}

    def _good_panel_semantic(self) -> dict:
        return {
            "panel_relevance": "context_only",
            "extraction_decision": "skip_metric_extraction",
            "application_task": "",
            "assay": "",
            "metric_category": "",
            "panel_type": "photograph",
            "panel_role": "caption_assigned_panel_task",
            "evidence_role": "supporting_observation",
            "matched_target_group_ids": [],
            "allowed_metrics": [],
            "allowed_units": [],
            "expected_value_types": [],
            "needs_digitization": False,
            "digitization_reason": "",
            "exclusion_reason": "no paper-level metric target scope; metric extraction disabled",
            "expected_metric_fields": [],
            "recommended_metric_set": [],
            "main_entities": {},
            "visible_modalities": {"source_visual_type": "image"},
            "ontology_terms": {},
            "why_relevant": "test panel",
            "confidence": 0.5,
        }

    def _good_chart_visual(self) -> dict:
        return {
            "chart_type": "line_plot",
            "axis_readability": "readable",
            "legend_readability": "readable",
            "x_axis": {"label": "Time", "unit": "day"},
            "y_axis": {"label": "Viscosity", "unit": "mPa.s"},
            "axis_labels": ["Time", "Viscosity"],
            "units": ["day", "mPa.s"],
            "legend_items": ["PEGDA+SA"],
            "series_names": [],
            "visible_text": [],
            "semantic_notes": "",
            "extraction_confidence": 0.7,
            "warnings": [],
        }

    def _good_image_obs(self) -> dict:
        return {
            "image_kind": "microscopy",
            "confidence": 0.85,
            "visual_fact_candidates": [
                {
                    "fact_type": "presence_absence",
                    "subject_slot": "cells",
                    "attribute_slot": "viability",
                    "value_slot": "present",
                    "evidence_ids": ["ev-visual"],
                }
            ],
        }

    def _good_chart_digitization(self) -> dict:
        return {
            "chart_type": "line_plot",
            "digitization_status": "digitized",
            "axis_readability": "readable",
            "legend_readability": "readable",
            "calibration_status": "estimated_from_axis",
            "x_axis": {"label": "Time", "unit": "day"},
            "y_axis": {"label": "Viscosity", "unit": "mPa.s"},
            "data_points": [
                {
                    "series_name": "PEGDA+SA",
                    "x_value": 0,
                    "y_value": 100,
                    "confidence": 0.8,
                }
            ],
            "extraction_confidence": 0.7,
            "warnings": [],
            "evidence_ids": ["ev-visual"],
        }


def main() -> None:
    import sys

    fake = RealisticFakeClient()

    default_paper = "data/pipeline_batch/001-2017_3D_HA_3D_printing_of_bacteria_into_functional_complex_materials"
    content_list_path = sys.argv[1] if len(sys.argv) > 1 else f"{default_paper}/structured/content_list_v2.json"
    image_root = sys.argv[2] if len(sys.argv) > 2 else default_paper
    output_dir = sys.argv[3] if len(sys.argv) > 3 else "data/content_pipeline_results/real_001_2017_content_list_v2_no_planner_replay_realistic"
    result = run_content_graph_pipeline(
        content_list_path=content_list_path,
        layout_path=None,
        image_root=image_root,
        paper_id="real_001_2017_no_planner_replay_realistic",
        query=None,
        model_client=fake,
        output_dir=output_dir,
        options=ExtractionPipelineOptions(
            fail_fast=False, max_workers=4, llm_max_workers=4,
            chart_only=False, allow_weak_claims_in_metrics=False,
        ),
    )

    print("=== RESULT ===")
    print(f"  Status: {result.status}")
    print(f"  Errors: {len(result.errors)}")

    events: dict[str, int] = {}
    for r in result.audit_trace:
        ev = r.get("event", "(empty)")
        events[ev] = events.get(ev, 0) + 1

    print()
    print("=== EVENT COUNTS (vs original in parens) ===")
    orig = {
        "llm_phase_started": 38, "llm_phase_completed": 29,
        "schema_repair_applied": 24, "llm_phase_payload_received": 28,
        "llm_phase_payload_adapted": 9, "image_observation_failed": 6,
        "llm_phase_failed_after_retries": 6,
        "panel_task_created": 19, "chart_digitization_completed": 9,
        "visual_fact_extraction_completed": 3,
        "chart_digitization_considered": 19, "chart_target_matching_skipped": 9,
        "local_chart_panel_semantic_created": 9,
        "visual_asset_quality_assessed": 9,
    }
    for ev, cnt in sorted(events.items(), key=lambda x: -x[1]):
        oc = orig.get(ev, 0)
        note = f" (was {oc})" if oc else ""
        print(f"  {ev:50s} {cnt:3d}{note}")

    print()
    print("=== DEGRADED / ADAPTER / NEW EVENTS ===")
    for r in result.audit_trace:
        ev = r.get("event", "")
        if any(kw in ev.lower() for kw in ("degraded", "adapter", "fallback", "repair_meta")):
            ph = r.get("phase_name", "")
            st = r.get("status", "")
            pi = r.get("panel_id", "")
            rp = len(r.get("repairs", []))
            print(f"  {ev:45s} phase={ph:<30s} status={st:<30s} repairs={rp}")

    print()
    print("=== COMPARISON: ORIGINAL FAILURES vs NEW DEGRADED ===")
    new_degraded = [
        r for r in result.audit_trace
        if "degraded" in r.get("event", "").lower()
    ]
    new_failures = [
        r for r in result.audit_trace
        if "failed" in r.get("event", "").lower()
    ]
    print("  Original failures (ExtractionSchemaError): 6")
    print(f"  New degraded events:                       {len(new_degraded)}")
    print(f"  New failure events:                        {len(new_failures)}")
    for d in new_degraded:
        ph = d.get("phase_name", "")
        st = d.get("status", "")
        pi = d.get("panel_id", "")
        print(f"    degraded: {ph} status={st} panel={pi}")
    for f in new_failures:
        print(f"    failure:  {f.get('phase_name','')} {f.get('status','')} {f.get('panel_id','')}")


if __name__ == "__main__":
    main()

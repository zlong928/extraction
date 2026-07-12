from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from content_pipeline.contracts.audit import AUDIT_SCHEMA_VERSION, build_run_metadata
from content_pipeline.export.csv_contracts import (
    write_chart_fact_csv,
    write_chart_fact_tables,
    write_heatmap_candidate_csv,
    write_heatmap_candidate_tables,
    write_image_fact_tables,
)


class AuditExporter:
    def write_outputs(
        self,
        *,
        output_dir: str | Path,
        audit_payload: dict[str, Any],
        panel_fact_rows: list[Any] | None = None,
        image_observations: list[Any] | None = None,
        options: Any | None = None,
        run_metadata: dict[str, str] | None = None,
    ) -> dict[str, str]:
        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        metadata = dict(audit_payload.get("run_metadata") or run_metadata or build_run_metadata())
        audit_payload["schema_version"] = AUDIT_SCHEMA_VERSION
        audit_payload["run_metadata"] = metadata
        audit_path = out_dir / "extraction_audit.json"
        chart_fact_csv_path = write_chart_fact_csv(
            out_dir / "chart_facts.csv", panel_fact_rows or [], metadata=metadata
        )
        panel_table_paths = write_chart_fact_tables(
            out_dir / "chart_fact_tables", panel_fact_rows or [], metadata=metadata
        )
        heatmap_candidates = [item for item in audit_payload.get("heatmap_candidates") or [] if isinstance(item, dict)]
        image_by_panel = _image_by_panel(audit_payload)
        heatmap_candidate_csv_path = write_heatmap_candidate_csv(
            out_dir / "heatmap_candidates.csv",
            heatmap_candidates,
            image_by_panel=image_by_panel,
            metadata=metadata,
        )
        heatmap_candidate_table_paths = write_heatmap_candidate_tables(
            out_dir / "heatmap_candidate_tables",
            heatmap_candidates,
            image_by_panel=image_by_panel,
            metadata=metadata,
        )
        image_observations = [item for item in audit_payload.get("image_observations") or [] if hasattr(item, "csv_dict")]
        image_fact_table_paths = write_image_fact_tables(
            out_dir / "image_fact_tables", image_observations or [], metadata=metadata
        )
        review_path = out_dir / "review.md"
        paths = {
            "audit_json": str(audit_path),
            "chart_fact_csv": str(chart_fact_csv_path),
            "chart_fact_tables_dir": str(out_dir / "chart_fact_tables"),
            "heatmap_candidate_csv": str(heatmap_candidate_csv_path),
            "heatmap_candidate_tables_dir": str(out_dir / "heatmap_candidate_tables"),
            "image_fact_tables_dir": str(out_dir / "image_fact_tables"),
            "review_md": str(review_path),
        }
        for panel_id, path in panel_table_paths.items():
            paths[f"chart_fact_table:{panel_id}"] = path
        for panel_id, path in heatmap_candidate_table_paths.items():
            paths[f"heatmap_candidate_table:{panel_id}"] = path
        for panel_id, path in image_fact_table_paths.items():
            paths[f"image_fact_table:{panel_id}"] = path
        descriptions = _output_file_descriptions(paths, heatmap_candidate_count=len(heatmap_candidates))
        audit_payload["output_paths"] = paths
        audit_payload["output_file_descriptions"] = descriptions
        review_md = self._build_review(audit_payload, options)
        review_path.write_text(review_md, encoding="utf-8")
        _write_json(audit_path, audit_payload)
        return paths

    def _build_review(self, payload: dict[str, Any], options: Any | None) -> str:
        lines: list[str] = []
        lines.append("# Extraction Review")
        lines.append("")

        doc_summary = payload.get("document_graph_summary", {})
        fp_graph = payload.get("figure_panel_graph", {})
        lines.append("## Run Summary")
        lines.append(f"- Block count: {doc_summary.get('block_count', 0)}")
        lines.append(f"- Figure count: {fp_graph.get('figure_count', 0)}")
        lines.append(f"- Panel count: {fp_graph.get('panel_count', 0)}")
        lines.append("")

        lines.append("## Panel Semantic Summary")
        panel_semantics = payload.get("panel_semantic_results", [])
        extraction_counts: dict[str, int] = {}
        for panel in panel_semantics:
            decision = getattr(panel, "extraction_decision", "unknown")
            extraction_counts[decision] = extraction_counts.get(decision, 0) + 1
        for decision, count in sorted(extraction_counts.items()):
            lines.append(f"- {decision}: {count}")
        lines.append(f"- Chart digitization results: {len(payload.get('chart_digitization_results', []) or [])}")
        lines.append(f"- Chart facts: {len(payload.get('chart_facts') or payload.get('panel_fact_rows') or [])}")
        lines.append(f"- Heatmap candidates: {len(payload.get('heatmap_candidates', []) or [])}")
        lines.append(f"- Image observations: {len(payload.get('image_observations', []) or [])}")
        lines.append("")

        if options is not None:
            lines.append("## Pipeline Options")
            for field in ("fail_fast", "max_workers", "llm_max_workers", "enable_quality_gates"):
                val = getattr(options, field, "N/A")
                lines.append(f"- {field}: {val}")
            lines.append("")

        output_descriptions = payload.get("output_file_descriptions") or []
        if output_descriptions:
            lines.append("## Output Files")
            for item in output_descriptions:
                if not isinstance(item, dict):
                    continue
                label = item.get("label", "output")
                path = item.get("path", "")
                description = item.get("description", "")
                lines.append(f"- {label}: {path} - {description}")
            lines.append("")

        payload.get("evidence_packets", [])
        lines.append("## Figure / Panel Summary")
        for figure_id, summary in _figure_panel_summary(panel_semantics).items():
            lines.append(f"- {figure_id}: panel_type={summary['primary_panel_type']} panels={summary['panel_count']}")
        lines.append("")

        audit_trace = payload.get("audit_trace", [])
        error_entries = [
            entry for entry in audit_trace
            if isinstance(entry, dict) and (entry.get("exception_type") or entry.get("message"))
        ]
        if error_entries:
            lines.append("## LLM / Schema / Runtime Errors")
            for entry in error_entries:
                lines.append(f"- {entry.get('phase_name', 'runtime')}: {entry.get('exception_type', '')} {str(entry.get('message', ''))[:200]}")
            lines.append("")

        lines.append("## Human Review Checklist")
        lines.append("- [ ] Review chart digitization results against original figures")
        lines.append("- [ ] Confirm image observations are not treated as exact numeric metrics")
        lines.append("")

        return "\n".join(lines)


def _figure_panel_summary(panel_semantics: list[Any]) -> dict[str, dict[str, Any]]:
    summary: dict[str, dict[str, Any]] = {}
    for panel in panel_semantics:
        figure_id = str(getattr(panel, "figure_id", "") or "unknown")
        item = summary.setdefault(figure_id, {"panel_count": 0, "panel_type_counts": {}})
        item["panel_count"] += 1
        panel_type = str(getattr(panel, "panel_type", "") or "unknown")
        item["panel_type_counts"][panel_type] = item["panel_type_counts"].get(panel_type, 0) + 1
    for item in summary.values():
        panel_type_counts = item.pop("panel_type_counts")
        item["primary_panel_type"] = max(panel_type_counts, key=panel_type_counts.get) if panel_type_counts else "unknown"
    return summary


def _write_json(path: str | Path, payload: Any) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(_to_jsonable(payload), ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def _image_by_panel(payload: dict[str, Any]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for packet in payload.get("evidence_packets") or []:
        panel_id = _field(packet, "panel_id")
        image_ref = _field(packet, "image_ref")
        if panel_id and image_ref:
            mapping[str(panel_id)] = str(image_ref)
    return mapping


def _output_file_descriptions(paths: dict[str, str], *, heatmap_candidate_count: int) -> list[dict[str, str]]:
    descriptions: list[dict[str, str]] = []
    base_descriptions = {
        "audit_json": "Full structured audit payload, including raw digitization results and heatmap candidate records.",
        "chart_fact_csv": "Chart digitization facts and chart point rows; heatmap review candidates are exported separately.",
        "chart_fact_tables_dir": "Per-panel chart fact CSV files.",
        "heatmap_candidate_csv": f"Chart-only heatmap review candidates ({heatmap_candidate_count} rows); not benchmark metric rows.",
        "heatmap_candidate_tables_dir": "Per-panel heatmap candidate CSV files keyed by panel_id.",
        "review_md": "Human-readable run summary and output file index.",
        "audit_events_jsonl": "Live audit event stream captured during the run.",
    }
    for key, path in paths.items():
        if key.startswith("chart_fact_table:"):
            panel_id = key.split(":", 1)[1]
            description = f"Chart fact CSV for panel {panel_id}."
        elif key.startswith("heatmap_candidate_table:"):
            panel_id = key.split(":", 1)[1]
            description = f"Heatmap candidate field CSV for panel {panel_id}; corresponds to frontend Heatmap Candidates preview."
        else:
            description = base_descriptions.get(key, "Pipeline output file.")
        descriptions.append({"label": key, "path": path, "description": description})
    return descriptions


def _field(value: Any, name: str) -> Any:
    if isinstance(value, dict):
        return value.get(name)
    return getattr(value, name, None)


def _to_jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return _to_jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(k): _to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_to_jsonable(v) for v in value]
    if isinstance(value, Path):
        return str(value)
    return value

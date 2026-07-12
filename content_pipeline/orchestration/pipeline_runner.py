from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from pathlib import Path
from threading import Lock
from typing import Any

from content_pipeline.contracts.audit import (
    AUDIT_SCHEMA_VERSION,
    ExtractionPipelineOptions,
    ExtractionRunResult,
    build_run_metadata,
)
from content_pipeline.contracts.errors import ExtractionPipelineError
from content_pipeline.contracts.panel_facts import build_panel_fact_rows
from content_pipeline.evidence.context_selector import EvidenceContextSelector
from content_pipeline.evidence.evidence_packet import EvidencePacketBuilder
from content_pipeline.export.audit_exporter import AuditExporter
from content_pipeline.graph.document_graph import DocumentGraphBuilder
from content_pipeline.graph.figure_panel_graph import FigurePanelGraphBuilder
from content_pipeline.graph.layout_graph import LayoutGraphBuilder
from content_pipeline.mineru.content_block_normalizer import ContentBlockNormalizer
from content_pipeline.llm.rate_limiter import LLMRateLimiter
from content_pipeline.visual.chart_points_summary import summarize_chart_digitization_results
from content_pipeline.visual.quality_gate import assess_visual_asset_quality
from content_pipeline.llm.chart_digitization_phase import ChartDigitizationPhase
from content_pipeline.llm.image_observation_phase import ImageObservationPhase


class ContentGraphPipelineRunner:
    def run(
        self,
        *,
        content_list_path: str,
        layout_path: str | None,
        image_root: str,
        paper_id: str,
        query: str | None = None,
        model_client=None,
        output_dir: str | None = None,
        options: ExtractionPipelineOptions | None = None,
    ) -> ExtractionRunResult:
        options = options or ExtractionPipelineOptions()
        run_metadata = build_run_metadata(model_client)
        audit: list[dict[str, Any]] = _audit_trace_for_output(output_dir)
        all_errors: list[dict[str, Any]] = []
        partial_failure = False
        try:
            if model_client is None:
                raise ExtractionPipelineError("Strict content pipeline requires model_client; rule fallback is disabled.")
            normalizer = ContentBlockNormalizer(image_root=image_root)
            blocks = normalizer.load(content_list_path)
            document_graph_builder = DocumentGraphBuilder()
            document_graph = document_graph_builder.build(blocks)
            normalization_report = dict(normalizer.last_report)
            filter_report = document_graph_builder.last_report
            if normalization_report:
                audit.append({"event": "content_blocks_normalized", **normalization_report})
            if filter_report.get("filtered_count"):
                audit.append(dict(filter_report))
            layout_graph = LayoutGraphBuilder().build(layout_path, document_graph)
            figure_panel_graph = FigurePanelGraphBuilder().build(document_graph, layout_graph)

            selector = EvidenceContextSelector()
            packet_builder = EvidencePacketBuilder()
            packets = []
            packet_by_panel = {}

            for figure in figure_panel_graph.figures:
                for panel in figure.panels:
                    selected = selector.select_for_panel(document_graph, figure, panel)
                    packet = packet_builder.build(paper_id=paper_id, document_graph=document_graph, figure=figure, panel=panel, selected=selected)
                    packets.append(packet)
                    packet_by_panel[panel.panel_id] = packet

            panel_semantic_results = self._classify_panels(
                packet_by_panel=packet_by_panel,
                model_client=model_client,
                options=options,
                audit=audit,
            )
            panel_classification_failures = [e for e in audit if e.get("event") == "panel_classification_failed"]
            if panel_classification_failures:
                partial_failure = True
            if not panel_semantic_results and packet_by_panel:
                partial_failure = True
                event = {
                    "event": "all_panel_classification_failed",
                    "panel_count": len(packet_by_panel),
                    "failure_count": len(panel_classification_failures),
                }
                audit.append(event)
                all_errors.append(event)
            semantic_by_panel = {result.panel_id: result for result in panel_semantic_results}

            chart_digitization_results = self._digitize_chart_panels(
                packet_by_panel=packet_by_panel,
                semantic_by_panel=semantic_by_panel,
                model_client=model_client,
                options=options,
                audit=audit,
            )

            visual_fact_results, image_observations = self._extract_visual_facts_and_observations(
                packet_by_panel=packet_by_panel,
                semantic_by_panel=semantic_by_panel,
                model_client=model_client,
                options=options,
                audit=audit,
            )
            image_observation_failures = [e for e in audit if e.get("event") == "image_observation_failed"]
            image_observation_degraded = [e for e in audit if e.get("event") == "image_observation_degraded"]
            if image_observation_failures:
                partial_failure = True
                all_errors.extend(image_observation_failures)
            if image_observation_degraded:
                all_errors.extend(image_observation_degraded)

            chart_points_summary_by_panel = summarize_chart_digitization_results(chart_digitization_results, semantic_by_panel)

            chart_points = [
                point
                for result in chart_digitization_results
                for point in (result.raw_points if result.raw_points else result.points)
            ]
            panel_fact_rows = build_panel_fact_rows(
                chart_digitization_results=chart_digitization_results,
                packet_by_panel=packet_by_panel,
                audit_trace=audit,
            )
            chart_facts = panel_fact_rows
            heatmap_candidates = _heatmap_candidates_from_chart_results(chart_digitization_results)
            if chart_facts:
                audit.append({
                    "event": "chart_facts_extracted",
                    "fact_count": len(chart_facts),
                    "panel_count": len({row.panel_id for row in chart_facts}),
                    "source_phase": "chart_digitization",
                })
            output_paths: dict[str, str] = {}
            if output_dir:
                audit_payload = {
                    "schema_version": AUDIT_SCHEMA_VERSION,
                    "run_metadata": run_metadata,
                    "options": {k: getattr(options, k) for k in ("fail_fast", "max_workers", "llm_max_workers", "chart_only", "enable_quality_gates")},
                    "document_graph_summary": document_graph.summary(),
                    "normalization_report": normalization_report,
                    "document_filter_report": filter_report,
                    "figure_panel_graph": {"figure_count": len(figure_panel_graph.figures), "panel_count": len(figure_panel_graph.panel_nodes())},
                    "figure_nodes": [{"figure_id": f.figure_id, "label": f.label, "page_idx": f.page_idx,
                                      "panels": [{"panel_id": p.panel_id, "panel_label": p.panel_label,
                                                   "local_context_block_ids": p.local_context_block_ids,
                                                   "caption_block_ids": p.caption_block_ids,
                                                   "related_table_ids": p.related_table_ids,
                                                   "related_formula_ids": p.related_formula_ids,
                                                   "related_reference_ids": p.related_reference_ids,
                                                   "provenance": p.provenance} for p in f.panels],
                                      "provenance": f.provenance} for f in figure_panel_graph.figures],
                    "evidence_packets": packets,
                    "panel_semantic_results": panel_semantic_results,
                    "chart_digitization_results": chart_digitization_results,
                    "chart_facts": chart_facts,
                    "chart_points": chart_points,
                    "panel_fact_rows": panel_fact_rows,
                    "heatmap_candidates": heatmap_candidates,
                    "chart_points_summary_by_panel": chart_points_summary_by_panel,
                    "visual_fact_results": visual_fact_results,
                    "image_observations": image_observations,
                    "audit_trace": audit,
                    "warnings": [],
                    "errors": all_errors,
                }
                output_paths = AuditExporter().write_outputs(
                    output_dir=output_dir,
                    audit_payload=audit_payload,
                    panel_fact_rows=panel_fact_rows,
                    image_observations=image_observations,
                    options=options,
                    run_metadata=run_metadata,
                )
                if isinstance(audit, LiveAuditTrace):
                    output_paths["audit_events_jsonl"] = str(audit.path)
            status = "partial_failure" if partial_failure else "succeeded"
            return ExtractionRunResult(
                document_graph_summary=document_graph.summary(),
                figure_panel_graph={"figure_count": len(figure_panel_graph.figures), "panel_count": len(figure_panel_graph.panel_nodes())},
                evidence_packets=packets,
                chart_digitization_results=chart_digitization_results,
                chart_facts=chart_facts,
                chart_points=chart_points,
                panel_fact_rows=panel_fact_rows,
                heatmap_candidates=heatmap_candidates,
                visual_fact_results=visual_fact_results,
                image_observations=image_observations,
                output_paths=output_paths,
                audit_trace=audit,
                errors=all_errors,
                status=status,
                run_metadata=run_metadata,
            )
        except ExtractionPipelineError as exc:
            entry = {"exception_type": type(exc).__name__, "message": str(exc)}
            audit.append(entry)
            all_errors.append(entry)
            if output_dir:
                _write_failure_audit(
                    output_dir=output_dir,
                    run_metadata=run_metadata,
                    audit_trace=audit,
                    errors=all_errors,
                    options=options,
                )
            if options and not options.fail_fast:
                return ExtractionRunResult(document_graph_summary={}, figure_panel_graph={}, audit_trace=audit, errors=all_errors, status="partial_failure", run_metadata=run_metadata)
            return ExtractionRunResult(document_graph_summary={}, figure_panel_graph={}, audit_trace=audit, errors=all_errors, status="failed", run_metadata=run_metadata)

    def _classify_panels(
        self,
        *,
        packet_by_panel: dict[str, Any],
        model_client: Any,
        options: ExtractionPipelineOptions,
        audit: list[dict[str, Any]],
    ) -> list[Any]:
        from content_pipeline.llm.semantic_phases import PanelSemanticClassifier

        rate_limiter = LLMRateLimiter.get_instance()
        results: list[Any] = []

        def run_one(panel_id: str) -> Any:
            rate_limiter.check_circuit_breaker()
            rate_limiter.acquire()
            packet = packet_by_panel[panel_id]
            classifier = PanelSemanticClassifier()
            try:
                result = classifier.classify(packet=packet, model_client=model_client, audit=audit)
                rate_limiter.record_success()
            except Exception:
                rate_limiter.record_failure()
                raise
            audit.append({
                "event": "panel_classified",
                "panel_id": result.panel_id,
                "panel_type": result.panel_type,
                "source": "llm_classifier",
            })
            return result

        with ThreadPoolExecutor(max_workers=_llm_worker_count(options)) as executor:
            futures = {executor.submit(run_one, panel_id): panel_id for panel_id in packet_by_panel}
            for future in as_completed(futures):
                panel_id = futures[future]
                try:
                    results.append(future.result())
                except Exception as exc:
                    failure = {"panel_id": panel_id, "exception_type": type(exc).__name__, "message": str(exc)}
                    audit.append({"event": "panel_classification_failed", **failure})
                    if options.fail_fast:
                        raise ExtractionPipelineError(f"panel classification failed for {panel_id}: {exc}") from exc
        return sorted(results, key=lambda r: r.panel_id)

    def _digitize_chart_panels(
        self,
        *,
        packet_by_panel: dict[str, Any],
        semantic_by_panel: dict[str, Any],
        model_client: Any,
        options: ExtractionPipelineOptions,
        audit: list[dict[str, Any]],
    ) -> list[Any]:
        rate_limiter = LLMRateLimiter.get_instance()
        results: list[Any] = []
        phase = ChartDigitizationPhase()

        def run_one(panel_id: str) -> Any | None:
            packet = packet_by_panel[panel_id]
            panel_semantic = semantic_by_panel.get(panel_id)
            should_digitize = _digitization_decision(packet, panel_semantic)
            is_fallback = getattr(panel_semantic, "raw_output", {}).get("fallback", False) if panel_semantic is not None else False
            audit.append({
                "event": "chart_digitization_considered",
                "panel_id": panel_id,
                "panel_type": getattr(panel_semantic, "panel_type", "") if panel_semantic is not None else "",
                "extraction_decision": getattr(panel_semantic, "extraction_decision", "") if panel_semantic is not None else "",
                "needs_digitization": getattr(panel_semantic, "needs_digitization", False) if panel_semantic is not None else False,
                "should_digitize": should_digitize,
                "fallback": is_fallback,
            })
            if not should_digitize:
                if is_fallback:
                    audit.append({
                        "event": "chart_digitization_skipped_by_fallback",
                        "panel_id": panel_id,
                        "image_ref": getattr(packet, "image_ref", "") or "",
                        "reason": "panel_classification_fallback_non_chart",
                        "fallback_panel_type": getattr(panel_semantic, "panel_type", "") if panel_semantic is not None else "",
                    })
                return None
            quality = assess_visual_asset_quality(str(getattr(packet, "image_ref", "") or ""))
            audit.append({
                "event": "visual_asset_quality_assessed",
                "panel_id": panel_id,
                "image_ref": getattr(packet, "image_ref", "") or "",
                "quality": quality,
            })
            if quality.get("readability") == "too_small":
                audit.append({
                    "event": "chart_digitization_skipped",
                    "panel_id": panel_id,
                    "reason": "visual_asset_too_small",
                    "quality": quality,
                })
                return None
            if quality.get("readability") == "missing_file":
                audit.append({
                    "event": "chart_digitization_skipped",
                    "panel_id": panel_id,
                    "reason": "visual_asset_missing",
                    "quality": quality,
                })
                return None
            low_resolution = quality.get("readability") == "low_resolution"
            if low_resolution:
                audit.append({
                    "event": "low_resolution_digitization_attempted",
                    "panel_id": panel_id,
                    "quality": quality,
                })
            rate_limiter.check_circuit_breaker()
            rate_limiter.acquire()
            try:
                result = phase.extract(
                    packet=packet,
                    panel_semantic=panel_semantic,
                    model_client=model_client,
                    audit=audit,
                )
                rate_limiter.record_success()
                if low_resolution:
                    result.needs_verification = True
                    result.warnings = list(dict.fromkeys([*result.warnings, "low_resolution_visual_asset"]))
                    for point in result.points:
                        point.needs_verification = True
                        point.review_status = "review_required"
                        if not point.review_reason:
                            point.review_reason = "low_resolution_visual_asset"
                return result
            except Exception as exc:
                rate_limiter.record_failure()
                failure = {"event": "chart_digitization_failed", "panel_id": panel_id, "exception_type": type(exc).__name__, "message": str(exc)}
                audit.append(failure)
                if options.fail_fast:
                    raise ExtractionPipelineError(f"chart digitization failed for {panel_id}: {exc}") from exc
                return None

        with ThreadPoolExecutor(max_workers=_llm_worker_count(options)) as executor:
            futures = {executor.submit(run_one, panel_id): panel_id for panel_id in packet_by_panel}
            for future in as_completed(futures):
                result = future.result()
                if result is not None:
                    results.append(result)
        return sorted(results, key=lambda r: r.panel_id)

    def _extract_visual_facts_and_observations(
        self,
        *,
        packet_by_panel: dict[str, Any],
        semantic_by_panel: dict[str, Any],
        model_client: Any,
        options: ExtractionPipelineOptions,
        audit: list[dict[str, Any]],
    ) -> tuple[list[Any], list[Any]]:
        rate_limiter = LLMRateLimiter.get_instance()
        visual_fact_results: list[Any] = []
        image_observations: list[Any] = []
        phase = ImageObservationPhase()

        def run_one(panel_id: str) -> tuple[list[Any], list[Any]]:
            packet = packet_by_panel[panel_id]
            panel_semantic = semantic_by_panel.get(panel_id)
            is_fallback = getattr(panel_semantic, "raw_output", {}).get("fallback", False) if panel_semantic is not None else False
            if _digitization_decision(packet, panel_semantic):
                audit.append({
                    "event": "image_observation_skipped",
                    "panel_id": panel_id,
                    "reason": "assigned_to_chart_digitization",
                    "extraction_decision": getattr(panel_semantic, "extraction_decision", "") if panel_semantic is not None else "",
                    "fallback": is_fallback,
                })
                return [], []
            if is_fallback:
                audit.append({
                    "event": "image_observation_fallback_active",
                    "panel_id": panel_id,
                    "image_ref": getattr(packet, "image_ref", "") or "",
                    "evidence_role": getattr(panel_semantic, "evidence_role", "") if panel_semantic is not None else "",
                    "panel_type": getattr(panel_semantic, "panel_type", "") if panel_semantic is not None else "",
                })
            rate_limiter.check_circuit_breaker()
            rate_limiter.acquire()
            try:
                visual_result, observations = phase.extract_result_and_observations(
                    packet=packet,
                    panel_semantic=panel_semantic,
                    model_client=model_client,
                    audit=audit,
                )
                rate_limiter.record_success()
                return ([visual_result] if visual_result else []), list(observations)
            except Exception as exc:
                rate_limiter.record_failure()
                audit.append({"event": "image_observation_failed", "panel_id": panel_id, "message": str(exc)})
                if options.fail_fast:
                    raise
            return [], []
        with ThreadPoolExecutor(max_workers=_llm_worker_count(options)) as executor:
            futures = {executor.submit(run_one, panel_id): panel_id for panel_id in packet_by_panel}
            for future in as_completed(futures):
                visual_results, observations = future.result()
                visual_fact_results.extend(visual_results)
                image_observations.extend(observations)
        return (
            sorted(visual_fact_results, key=lambda r: r.panel_id),
            sorted(image_observations, key=lambda r: r.panel_id),
        )


def run_content_graph_pipeline(**kwargs) -> ExtractionRunResult:
    return ContentGraphPipelineRunner().run(**kwargs)


def _write_failure_audit(
    *,
    output_dir: str,
    run_metadata: dict[str, str],
    audit_trace: list[dict[str, Any]],
    errors: list[dict[str, Any]],
    options: ExtractionPipelineOptions,
) -> None:
    try:
        AuditExporter().write_outputs(
            output_dir=output_dir,
            audit_payload={
                "schema_version": AUDIT_SCHEMA_VERSION,
                "run_metadata": run_metadata,
                "status": "failed",
                "audit_trace": audit_trace,
                "errors": errors,
            },
            panel_fact_rows=[],
            options=options,
            run_metadata=run_metadata,
        )
    except Exception as exc:  # pragma: no cover - only exercised on output failure
        export_error = {
            "event": "failure_audit_write_failed",
            "exception_type": type(exc).__name__,
            "message": str(exc),
        }
        audit_trace.append(export_error)
        errors.append(export_error)


class LiveAuditTrace(list[dict[str, Any]]):
    """List-compatible audit trace that mirrors appended events to JSONL."""

    def __init__(self, path: str | Path):
        super().__init__()
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("", encoding="utf-8")
        self._lock = Lock()

    def append(self, item: dict[str, Any]) -> None:  # type: ignore[override]
        line = json.dumps(_jsonable(item), ensure_ascii=False, separators=(",", ":"))
        with self._lock:
            super().append(item)
            self._write_line(line)

    def extend(self, items: Any) -> None:  # type: ignore[override]
        for item in items:
            self.append(item)

    def _write_line(self, line: str) -> None:
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(line)
            handle.write("\n")
            handle.flush()


def _audit_trace_for_output(output_dir: str | None) -> list[dict[str, Any]]:
    if not output_dir:
        return []
    return LiveAuditTrace(Path(output_dir) / "extraction_audit_events.jsonl")


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    if isinstance(value, Path):
        return str(value)
    return value


def _digitization_decision(packet: Any, panel_semantic: Any) -> bool:
    if panel_semantic is None:
        return False
    extraction_decision = getattr(panel_semantic, "extraction_decision", "")
    needs_digitization = getattr(panel_semantic, "needs_digitization", False)
    return extraction_decision == "extract_target_metrics" and needs_digitization


def _heatmap_candidates_from_chart_results(chart_results: list[Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for result in chart_results:
        candidates.extend(dict(item) for item in getattr(result, "heatmap_candidates", []) or [] if isinstance(item, dict))
    return candidates


def _llm_worker_count(options: ExtractionPipelineOptions) -> int:
    return getattr(options, "llm_max_workers", getattr(options, "max_workers", 16))

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from content_pipeline.contracts.audit import ExtractionPipelineOptions, ExtractionRunResult
from content_pipeline.llm.client import build_content_pipeline_client
from content_pipeline.orchestration.pipeline_runner import run_content_graph_pipeline


@dataclass(frozen=True)
class ContentPipelineRunSummary:
    extractor_modes: dict[str, int]
    digitization_count: int
    chart_points_count: int = 0
    image_observation_count: int = 0


def summarize_content_pipeline_result(result: ExtractionRunResult) -> ContentPipelineRunSummary:
    extractor_modes: dict[str, int] = {}
    digitization_count = len(getattr(result, "chart_digitization_results", []) or [])
    if digitization_count:
        extractor_modes["chart_digitization"] = digitization_count
    return ContentPipelineRunSummary(
        extractor_modes=extractor_modes,
        digitization_count=digitization_count,
        chart_points_count=len(getattr(result, "chart_facts", []) or getattr(result, "chart_points", []) or []),
        image_observation_count=len(getattr(result, "image_observations", []) or []),
    )


def run_content_pipeline(
    *,
    content_list_path: str | Path,
    image_root: str | Path,
    paper_id: str,
    use_llm: bool = False,
    layout_path: str | Path | None = None,
    query: str | None = None,
    output_dir: str | Path | None = None,
    options: ExtractionPipelineOptions | None = None,
    on_llm_disabled: Callable[[str], None] | None = None,
) -> tuple[ExtractionRunResult, ContentPipelineRunSummary]:
    options = options or ExtractionPipelineOptions()
    model_client = None
    if use_llm:
        model_client = build_content_pipeline_client()
        if model_client is None and on_llm_disabled is not None:
            on_llm_disabled("⚠ 未设置 LLM API Key，将使用本地规则 fallback")
    result = run_content_graph_pipeline(
        content_list_path=str(content_list_path),
        layout_path=str(layout_path) if layout_path is not None else None,
        image_root=str(image_root),
        paper_id=paper_id,
        query=query,
        model_client=model_client,
        output_dir=str(output_dir) if output_dir is not None else None,
        options=options,
    )
    return result, summarize_content_pipeline_result(result)

# Backend image extraction runtime flow

The current runtime image extraction path is:

```text
PaperParseService.parse()
  -> MinerUParserService.parse_pdf_file()
  -> MinerUAssetBuilder.ingest(markdown, extract_dir, content_list_path, layout_path)
  -> PaperAsset records with local metadata

ImageExtractionService.run_job()
  -> local asset readiness gate
  -> direct local asset-scope routing for chart_crop / panel_crop / microscopy_crop
  -> pipeline/classify_figure.py for full_figure assets
  -> pipeline/extract_panel.py
  -> pipeline/fuse_figure.py
```

`app/services/mineru_asset_builder.py` is the runtime source of truth for converting MinerU markdown/images/content_list/layout output into `PaperAsset` records. It attaches deterministic local metadata before any LLM/VLM call happens, including image dimensions, file size, MinerU type, page index, bounding box, layout page metadata, parent figure id, figure group key, group size, panel index, sibling asset indices, full caption, panel id, asset scope, extraction readiness, and skip reason.

`app/services/local_image_profiler.py` owns deterministic local profiling. It maps local MinerU/layout/caption hints into `asset_scope`, `evidence_shape`, `recommended_extractor`, `figure_role`, readiness, confidence, and uncertainty. It intentionally does not perform OCR, CV segmentation, or chart digitization.

Batch extraction should use local readiness metadata to avoid enqueueing obvious non-extractable assets such as tiny placeholder images, panel labels, or assets without enough caption/context.

`ImageExtraction.status` is represented by the runtime statuses `pending`, `processing`, `done`, `failed`, and `skipped`; the public `ExtractionRead` schema restricts responses to those values.

`app/services/extraction/image_llm_orchestrator.py`, `app/services/extraction/figure_extraction_pipeline.py`, and `app/services/agent/visual_agents.py` are legacy or non-runtime paths for the current `run_job()` flow. Do not modify those files when changing the active API/worker image extraction path unless the runtime caller is intentionally moved back to them.

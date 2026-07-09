from __future__ import annotations

import io
import zipfile
import base64
import json
import uuid
import struct
import zlib
from pathlib import Path

from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.main import app
from app.models import Figure, ImageExtraction, Panel, Paper, PaperAsset
from app.services.document_parser import ParsedDocument, ParsedElement, ParsedPage
from app.services.image_extraction import ImageExtractionService
from app.services.mineru_parser import MinerUParseResult
from app.services.mineru_asset_builder import MinerUAssetBuilder
from app.services.pdf import PaperParseService
from app.services.storage import StorageService


def _png_bytes(width: int, height: int) -> bytes:
    raw_rows = b"".join(
        b"\x00" + bytes(value for x in range(width) for value in (x % 256, y % 256, (x * y) % 256))
        for y in range(height)
    )
    compressed = zlib.compress(raw_rows)

    def chunk(kind: bytes, payload: bytes) -> bytes:
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", compressed)
        + chunk(b"IEND", b"")
    )


def _sample_pdf(label: str = "chart") -> bytes:
    marker = f"{label}-{uuid.uuid4()}".encode("utf-8")
    return (
        b"%PDF-1.4\n"
        b"% "
        + marker
        + b"\n"
        b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Count 1/Kids[3 0 R]>>endobj\n"
        b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]>>endobj\n"
        b"trailer<</Root 1 0 R>>\n%%EOF\n"
    )


def _mineru_artifact(tmp_path: Path) -> tuple[str, ParsedDocument]:
    image_path = tmp_path / "images" / "chart.png"
    image_path.parent.mkdir(parents=True, exist_ok=True)
    image_path.write_bytes(_png_bytes(64, 48))

    markdown = "## Results\n![Figure 1: Stress strain curve](images/chart.png)\nNearby text: stress increased with strain."
    parsed = ParsedDocument(
        pages=[
            ParsedPage(
                page_number=1,
                elements=[ParsedElement(element_type="paragraph", text=markdown, extractor="mineru")],
            )
        ],
        source_type="pdf",
        parser_engine="mineru",
        parser_version="mineru_api_v4",
    )
    return markdown, parsed


def test_upload_queues_mineru_parse(monkeypatch) -> None:
    queued: list[dict] = []
    monkeypatch.setattr("app.queue.redis_queue.RedisQueue.enqueue", lambda self, payload: queued.append(payload))

    with TestClient(app) as client:
        response = client.post(
            "/papers/upload",
            files={"file": ("paper-queued.pdf", _sample_pdf("queued"), "application/pdf")},
            data={"title": "Queued Chart Paper"},
        )

    assert response.status_code == 201, response.text
    paper = response.json()
    assert paper["status"] == "pending"
    assert queued[-1]["task_type"] == "paper_parse"
    assert queued[-1]["paper_id"] == paper["id"]


def test_failed_duplicate_upload_requeues_same_paper(monkeypatch) -> None:
    queued: list[dict] = []
    monkeypatch.setattr("app.queue.redis_queue.RedisQueue.enqueue", lambda self, payload: queued.append(payload))
    content = _sample_pdf("retry-upload")

    with TestClient(app) as client:
        first = client.post(
            "/papers/upload",
            files={"file": ("paper-retry.pdf", content, "application/pdf")},
            data={"title": "Retry Upload"},
        )
        assert first.status_code == 201, first.text
        paper_id = first.json()["id"]

    with SessionLocal() as db:
        paper = db.get(Paper, paper_id)
        assert paper is not None
        paper.status = "failed"
        paper.error_message = "MINERU_API_KEY is not configured."
        db.commit()

    with TestClient(app) as client:
        second = client.post(
            "/papers/upload",
            files={"file": ("paper-retry.pdf", content, "application/pdf")},
            data={"title": "Retry Upload Again"},
        )
        assert second.status_code == 201, second.text
        payload = second.json()
        assert payload["id"] == paper_id
        assert payload["status"] == "pending"
        assert payload["error_message"] is None

    assert queued[-1]["task_type"] == "paper_parse"
    assert queued[-1]["paper_id"] == paper_id


def test_retry_paper_parse_endpoint_resets_failed_paper(monkeypatch) -> None:
    queued: list[dict] = []
    monkeypatch.setattr("app.queue.redis_queue.RedisQueue.enqueue", lambda self, payload: queued.append(payload))

    with TestClient(app) as client:
        upload = client.post(
            "/papers/upload",
            files={"file": ("paper-retry-endpoint.pdf", _sample_pdf("retry"), "application/pdf")},
            data={"title": "Retry Endpoint"},
        )
        assert upload.status_code == 201, upload.text
        paper_id = upload.json()["id"]

    with SessionLocal() as db:
        paper = db.get(Paper, paper_id)
        assert paper is not None
        paper.status = "failed"
        paper.error_message = "parse failed"
        db.commit()

    with TestClient(app) as client:
        retried = client.post(f"/papers/{paper_id}/retry")
        assert retried.status_code == 202, retried.text
        assert retried.json()["status"] == "pending"
        assert retried.json()["error_message"] is None

    assert queued[-1] == {"task_type": "paper_parse", "paper_id": paper_id}


def test_reparse_clears_stale_assets_figures_panels_and_extractions(monkeypatch, tmp_path: Path) -> None:
    parsed = ParsedDocument(
        pages=[
            ParsedPage(
                page_number=1,
                elements=[ParsedElement(element_type="paragraph", text="No figures in replacement parse.", extractor="mineru")],
            )
        ],
        source_type="pdf",
        parser_engine="mineru",
        parser_version="mineru_api_v4",
    )

    def fake_parse_pdf_file(self, *_args, **kwargs):
        return MinerUParseResult(
            parsed_document=parsed,
            batch_id="reparse-cleanup",
            file_name="paper.pdf",
            full_zip_url="result.zip",
            markdown_file="full.md",
            original_markdown="# Replacement\n\nNo figures.",
            artifact_dir=str(tmp_path),
            extract_dir=str(tmp_path),
            content_list_path="",
            layout_path=None,
            extracted_files=[],
        )

    monkeypatch.setattr("app.services.mineru_parser.MinerUParserService.parse_pdf_file", fake_parse_pdf_file)

    with SessionLocal() as db:
        paper = Paper(
            title=f"Reparse Cleanup {uuid.uuid4()}",
            original_filename="cleanup.pdf",
            file_path="papers/cleanup.pdf",
            file_size=123,
            file_hash=f"cleanup-{uuid.uuid4()}",
            status="done",
        )
        db.add(paper)
        db.flush()
        figure = Figure(paper_id=paper.id, figure_id="Figure 1", panel_count=1)
        db.add(figure)
        db.flush()
        asset = PaperAsset(
            paper_id=paper.id,
            figure_id=figure.id,
            asset_type="figure",
            asset_index=0,
            file_path="assets/stale.png",
        )
        db.add(asset)
        db.flush()
        panel = Panel(figure_id=figure.id, asset_id=asset.id, panel_id="Figure 1-P1")
        extraction = ImageExtraction(asset_id=asset.id, figure_id=figure.id)
        db.add_all([panel, extraction])
        db.commit()

        PaperParseService(db).parse(paper)

        assert db.query(PaperAsset).filter(PaperAsset.paper_id == paper.id).count() == 0
        assert db.query(Figure).filter(Figure.paper_id == paper.id).count() == 0
        assert not db.query(Panel).join(PaperAsset, Panel.asset_id == PaperAsset.id).filter(PaperAsset.paper_id == paper.id).count()
        assert not db.query(ImageExtraction).join(Figure, ImageExtraction.figure_id == Figure.id).filter(Figure.paper_id == paper.id).count()
        assert paper.status == "done"
        assert paper.text_content == "No figures in replacement parse."


def _ingest_one_asset(tmp_path: Path, image_bytes: bytes, *, alt: str, nearby: str = "Nearby text") -> PaperAsset:
    extract_dir = tmp_path / f"mineru-{uuid.uuid4()}"
    image_dir = extract_dir / "images"
    image_dir.mkdir(parents=True)
    (image_dir / "asset.png").write_bytes(image_bytes)
    markdown = f"## Results\n![{alt}](images/asset.png)\n{nearby}"

    with SessionLocal() as db:
        paper = Paper(
            title=f"Asset Quality {uuid.uuid4()}",
            original_filename="asset-quality.pdf",
            file_path="papers/asset-quality.pdf",
            file_size=123,
            file_hash=f"asset-quality-{uuid.uuid4()}",
            status="done",
        )
        db.add(paper)
        db.flush()
        storage = StorageService(root=tmp_path / "uploads")
        assets = MinerUAssetBuilder(db, storage).ingest(paper, markdown, str(extract_dir))
        assert len(assets) == 1
        db.add(assets[0])
        db.commit()
        asset_id = assets[0].id
        asset = db.get(PaperAsset, asset_id)
        assert asset is not None
        db.expunge(asset)
        return asset


def test_mineru_ingestion_marks_tiny_image_as_skip(tmp_path: Path) -> None:
    asset = _ingest_one_asset(
        tmp_path,
        base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="),
        alt="Figure 1: tiny placeholder",
    )

    metadata = json.loads(asset.metadata_json or "{}")
    assert asset.width == 1
    assert asset.height == 1
    assert metadata["image_width"] == 1
    assert metadata["image_height"] == 1
    assert metadata["file_size"] > 0
    assert metadata["asset_scope"] == "noise"
    assert metadata["extraction_readiness"] == "skip"
    assert "too small" in metadata["skip_reason"]
    assert metadata["data_extraction_possible"] is False


def test_mineru_ingestion_records_ready_image_quality_metadata(tmp_path: Path) -> None:
    asset = _ingest_one_asset(
        tmp_path,
        _png_bytes(64, 48),
        alt="Figure 2: stress strain curve",
    )

    metadata = json.loads(asset.metadata_json or "{}")
    assert asset.width == 64
    assert asset.height == 48
    assert metadata["image_width"] == 64
    assert metadata["image_height"] == 48
    assert metadata["file_size"] > 256
    assert metadata["asset_scope"] == "full_figure"
    assert metadata["extraction_readiness"] == "ready"
    assert metadata["skip_reason"] is None
    assert metadata["full_caption"] == "stress strain curve"
    assert metadata["data_extraction_possible"] is True


def test_mineru_asset_builder_uses_content_list_metadata(tmp_path: Path) -> None:
    extract_dir = tmp_path / "mineru-content"
    image_dir = extract_dir / "images"
    image_dir.mkdir(parents=True)
    (image_dir / "panel.png").write_bytes(_png_bytes(80, 60))
    content_list_path = extract_dir / "content_list.json"
    content_list_path.write_text(
        json.dumps(
            [
                {"type": "text", "content": "Figure 3. Growth performance panels.", "page_idx": 4},
                {
                    "type": "chart",
                    "img_path": "images/panel.png",
                    "page_idx": 4,
                    "bbox": [10, 20, 110, 120],
                    "chart_caption": ["Figure 3. Growth performance panels. a) Biomass over time."],
                },
            ]
        ),
        encoding="utf-8",
    )
    layout_path = extract_dir / "layout.json"
    layout_path.write_text(json.dumps({"pages": [{"page_idx": 4, "width": 1000, "height": 1200}]}), encoding="utf-8")
    markdown = "## Results\n![a)](images/panel.png)\n"

    with SessionLocal() as db:
        paper = Paper(
            title=f"Content List {uuid.uuid4()}",
            original_filename="content-list.pdf",
            file_path="papers/content-list.pdf",
            file_size=123,
            file_hash=f"content-list-{uuid.uuid4()}",
            status="done",
        )
        db.add(paper)
        db.flush()
        storage = StorageService(root=tmp_path / "uploads")
        assets = MinerUAssetBuilder(db, storage).ingest(
            paper,
            markdown,
            str(extract_dir),
            content_list_path=str(content_list_path),
            layout_path=str(layout_path),
        )
        assert len(assets) == 1
        asset = assets[0]

    metadata = json.loads(asset.metadata_json or "{}")
    assert asset.page_number == 5
    assert asset.caption == "a) Biomass over time."
    assert metadata["mineru_type"] == "chart"
    assert metadata["bbox"] == [10, 20, 110, 120]
    assert metadata["page_idx"] == 4
    assert metadata["content_list_caption"].startswith("Figure 3")
    assert metadata["layout_page"]["width"] == 1000
    assert metadata["parent_figure_id"] == "Figure 3"
    assert metadata["figure_group_key"].endswith(":Figure 3")
    assert metadata["figure_group_size"] == 1
    assert metadata["panel_index"] == 1
    assert metadata["asset_scope"] == "chart_crop"
    assert metadata["evidence_shape_hint"] == "experimental_performance_plot"
    assert metadata["recommended_extractor_hint"] == "plot_numeric_extractor"
    assert metadata["panel_id"] == "a"
    assert metadata["extraction_readiness"] in {"ready", "low_confidence"}


def test_mineru_asset_builder_groups_multiple_panels_under_parent_figure(tmp_path: Path) -> None:
    extract_dir = tmp_path / "mineru-panels"
    image_dir = extract_dir / "images"
    image_dir.mkdir(parents=True)
    (image_dir / "panel-a.png").write_bytes(_png_bytes(80, 60))
    (image_dir / "panel-b.png").write_bytes(_png_bytes(80, 60))
    content_list_path = extract_dir / "content_list.json"
    caption = "Figure 5. Multi-panel growth summary. a) Biomass image. b) Performance plot."
    content_list_path.write_text(
        json.dumps(
            [
                {"type": "image", "img_path": "images/panel-a.png", "page_idx": 0, "bbox": [10, 20, 110, 120], "image_caption": [caption]},
                {"type": "chart", "img_path": "images/panel-b.png", "page_idx": 0, "bbox": [120, 20, 220, 120], "chart_caption": [caption]},
            ]
        ),
        encoding="utf-8",
    )
    markdown = "## Results\n![a)](images/panel-a.png)\n![b)](images/panel-b.png)\n"

    with SessionLocal() as db:
        paper = Paper(
            title=f"Panel Group {uuid.uuid4()}",
            original_filename="panel-group.pdf",
            file_path="papers/panel-group.pdf",
            file_size=123,
            file_hash=f"panel-group-{uuid.uuid4()}",
            status="done",
        )
        db.add(paper)
        db.flush()
        assets = MinerUAssetBuilder(db, StorageService(root=tmp_path / "uploads")).ingest(
            paper,
            markdown,
            str(extract_dir),
            content_list_path=str(content_list_path),
        )

    assert len(assets) == 2
    first = json.loads(assets[0].metadata_json or "{}")
    second = json.loads(assets[1].metadata_json or "{}")
    assert first["parent_figure_id"] == "Figure 5"
    assert second["parent_figure_id"] == "Figure 5"
    assert first["figure_group_size"] == 2
    assert second["figure_group_size"] == 2
    assert first["is_multi_panel_group"] is True
    assert second["is_multi_panel_group"] is True
    assert first["sibling_asset_indices"] == [0, 1]
    assert second["panel_index"] == 2


def test_extract_all_skips_assets_marked_not_ready() -> None:
    with SessionLocal() as db:
        paper = Paper(
            title=f"Readiness Batch {uuid.uuid4()}",
            original_filename="readiness.pdf",
            file_path="papers/readiness.pdf",
            file_size=123,
            file_hash=f"readiness-{uuid.uuid4()}",
            status="done",
        )
        db.add(paper)
        db.flush()
        ready_asset = PaperAsset(
            paper_id=paper.id,
            asset_type="figure",
            asset_index=0,
            file_path="papers/ready.png",
            mime_type="image/png",
            metadata_json=json.dumps({"extraction_readiness": "ready"}),
        )
        skipped_asset = PaperAsset(
            paper_id=paper.id,
            asset_type="figure",
            asset_index=1,
            file_path="papers/skip.png",
            mime_type="image/png",
            metadata_json=json.dumps({"extraction_readiness": "skip", "skip_reason": "too small: 1x1"}),
        )
        db.add_all([ready_asset, skipped_asset])
        db.commit()
        paper_id = paper.id
        ready_id = ready_asset.id
        skipped_id = skipped_asset.id

    with SessionLocal() as db:
        paper = db.get(Paper, paper_id)
        assert paper is not None
        jobs, created_count, skipped_asset_ids = ImageExtractionService(db).create_jobs_for_paper(paper)
        assert created_count == 1
        assert len(jobs) == 1
        assert jobs[0].asset_id == ready_id
        assert skipped_asset_ids == [skipped_id]


def test_run_job_skips_noise_asset_without_llm(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.image_extraction.PromptSchemaModelClient",
        lambda: (_ for _ in ()).throw(AssertionError("LLM client should not be created for skipped assets")),
    )

    with SessionLocal() as db:
        paper = Paper(
            title=f"Noise Asset {uuid.uuid4()}",
            original_filename="noise.pdf",
            file_path="papers/noise.pdf",
            file_size=123,
            file_hash=f"noise-{uuid.uuid4()}",
            status="done",
        )
        db.add(paper)
        db.flush()
        asset = PaperAsset(
            paper_id=paper.id,
            asset_type="figure",
            asset_index=0,
            file_path="papers/noise.png",
            mime_type="image/png",
            metadata_json=json.dumps({"asset_scope": "noise", "extraction_readiness": "skip", "skip_reason": "too small: 1x1"}),
        )
        db.add(asset)
        db.flush()
        job = ImageExtraction(asset_id=asset.id, status="pending")
        db.add(job)
        db.commit()
        job_id = job.id

        completed = ImageExtractionService(db).run_job(job_id)
        assert completed is not None
        assert completed.status == "skipped"
        assert completed.csv_path is None
        result = json.loads(completed.result_json or "{}")
        assert result["extraction_method"] == "local_asset_readiness_gate"
        assert result["review_status"] == "skipped"
        assert result["asset_metadata"]["skip_reason"] == "too small: 1x1"


def test_single_skipped_asset_extract_is_allowed_for_manual_override() -> None:
    with SessionLocal() as db:
        paper = Paper(
            title=f"Manual Skip Override {uuid.uuid4()}",
            original_filename="manual.pdf",
            file_path="papers/manual.pdf",
            file_size=123,
            file_hash=f"manual-{uuid.uuid4()}",
            status="done",
        )
        db.add(paper)
        db.flush()
        asset = PaperAsset(
            paper_id=paper.id,
            asset_type="figure",
            asset_index=0,
            file_path="papers/skip.png",
            mime_type="image/png",
            metadata_json=json.dumps({"extraction_readiness": "skip", "skip_reason": "too small: 1x1"}),
        )
        db.add(asset)
        db.commit()
        asset_id = asset.id

    with TestClient(app) as client:
        response = client.post(f"/papers/assets/{asset_id}/extract")
        assert response.status_code == 201, response.text
        payload = response.json()
        assert payload["asset_id"] == asset_id
        assert payload["status"] == "pending"


def test_numeric_extraction_csv_uses_clean_download_columns(tmp_path: Path) -> None:
    csv_path = tmp_path / "clean.csv"
    rows = [
        {
            "figure_label": "Figure 1",
            "image_type": "multi_line_plot",
            "indicator": "Extract all quantitative chart data with axes, units, series, and data points.",
            "series_name": "C",
            "x_value": 0,
            "x_unit": "d",
            "x_axis_label": "Time (d)",
            "x_scale": "linear",
            "y_value": 0,
            "y_unit": "mg COD L^-1",
            "y_axis_label": "Hexanoic acid production (mg COD L^-1)",
            "y_scale": "linear",
            "error_bar": "±0",
            "confidence": 0.9,
            "review_status": "reviewed",
            "review_notes": "axis_units_and_points_checked",
            "extraction_method": "llm_assisted",
        }
    ]

    with SessionLocal() as db:
        ImageExtractionService(db)._write_csv(csv_path, rows)

    header = csv_path.read_text(encoding="utf-8-sig").splitlines()[0]
    assert header == (
        "figure_label,image_type,series_name,x_value,x_unit,x_axis_label,x_scale,"
        "y_value,y_unit,y_axis_label,y_scale,error_bar,confidence"
    )
    assert "indicator" not in header
    assert "review_status" not in header
    assert "review_notes" not in header
    assert "extraction_method" not in header


def test_mineru_parse_then_llm_agent_extraction(monkeypatch, tmp_path: Path) -> None: 
    queued: list[dict] = []
    def _queue_enqueue(self, payload):
        if payload.get("task_type") != "image_extraction":
            queued.append(payload)
    monkeypatch.setattr("app.queue.redis_queue.RedisQueue.enqueue", _queue_enqueue)

    markdown, parsed = _mineru_artifact(tmp_path)

    def fake_parse_pdf_file(self, file_path, **kwargs):
        return MinerUParseResult(
            parsed_document=parsed,
            batch_id="batch-test",
            file_name="paper.pdf",
            full_zip_url="https://example.test/result.zip",
            markdown_file="full.md",
            artifact_dir=str(tmp_path),
            zip_path=str(tmp_path / "result.zip"),
            extract_dir=str(tmp_path),
            content_list_path="",
            layout_path="",
            extracted_files=[],
        )

    monkeypatch.setattr("app.services.mineru_parser.MinerUParserService.parse_pdf_file", fake_parse_pdf_file)

    with TestClient(app) as client:
        upload = client.post(
            "/papers/upload",
            files={"file": ("paper-mineru.pdf", _sample_pdf("mineru"), "application/pdf")},
            data={"title": "MinerU Chart Paper"},
        )
        assert upload.status_code == 201, upload.text
        paper_id = upload.json()["id"]

    with SessionLocal() as db:
        parsed_paper = PaperParseService(db).parse_or_fail(paper_id)
        assert parsed_paper is not None
        assert parsed_paper.status == "done"
        asset = db.query(PaperAsset).filter(PaperAsset.paper_id == paper_id).first()
        assert asset is not None
        assert asset.caption == "## Results Nearby text: stress increased with strain."

        class FakePromptSchemaClient:
            def __init__(self):
                self.client = type("TokenClient", (), {"token_stats": {"total": {"total_tokens": 1}}})()

            def call_json(self, *, prompt, inputs):
                if "panel_extractions" in inputs:
                    return {
                        "figure_fusion": {
                            "figure_id": "Figure 1",
                            "source_pdf": "papers/1/paper.pdf",
                            "figure_title_or_caption_summary": "Stress strain curve",
                            "overall_evidence_role": "functional_performance_evidence",
                            "main_claim_supported": "Stress increases with strain.",
                            "supporting_panels": [
                                {
                                    "panel_id": "Figure 1-P1",
                                    "evidence_type": "experimental_performance_plot",
                                    "key_extracted_result": "Stress increases with strain.",
                                    "supports": "performance trend",
                                }
                            ],
                            "cross_panel_logic": [],
                            "domain_summary": "Stress-strain trend extraction.",
                            "extractable_database_items": [{"type": "trend", "value": "stress increases with strain"}],
                            "strong_evidence": ["caption and nearby text mention stress-strain trend"],
                            "weak_or_contextual_evidence": [],
                            "uncertainties": ["No coordinate digitization was performed."],
                            "recommended_downstream_use": ["literature_summary", "data_table"],
                        }
                    }
                if "recommended_extractor" in inputs:
                    return {
                        "extraction_type": "plot_numeric_extractor",
                        "source_pdf": "papers/1/paper.pdf",
                        "figure_id": "Figure 1",
                        "panel_id": "Figure 1-P1",
                        "evidence_shape": "experimental_performance_plot",
                        "domain_task": "material_mechanics_and_stability",
                        "extracted_fields": {
                            "plot_type": "line_plot",
                            "x_axis": {"label": "Strain", "unit": ""},
                            "y_axis": {"label": "Stress", "unit": ""},
                            "series": [
                                {
                                    "name": "stress",
                                    "condition": "",
                                    "values_reported_in_text_or_labels": "",
                                    "trend_description": "stress increased with strain",
                                    "final_or_peak_value": "",
                                    "error_bar": "",
                                }
                            ],
                            "comparison_groups": [],
                            "main_metric": "stress",
                            "best_performing_group": "",
                            "statistical_annotations": [],
                            "main_result": "Stress increased with strain.",
                            "domain_interpretation": "Mechanical response trend is visible from context.",
                            "uncertainty": ["No coordinate digitization was performed."],
                        },
                        "main_result": "Stress increased with strain.",
                        "evidence": [{"source": "nearby_text", "text": markdown, "evidence_level": "caption"}],
                        "confidence": 0.8,
                        "uncertainty": ["No coordinate digitization was performed."],
                    }
                return {
                    "image_profile": {
                        "figure_id": "Figure 1",
                        "source_pdf": "papers/1/paper.pdf",
                        "page_number": 1,
                        "caption_text": "Stress strain curve",
                        "is_composite_figure": False,
                        "panel_count_estimate": 1,
                        "primary_evidence_shape": "experimental_performance_plot",
                        "secondary_evidence_shapes": [],
                        "domain": "mechanics",
                        "domain_tasks": ["material_mechanics_and_stability"],
                        "figure_role": "functional_performance_evidence",
                        "main_scientific_question": "How does stress vary with strain?",
                        "main_entities": {
                            "microorganisms": [],
                            "materials": [],
                            "devices_or_structures": [],
                            "chemicals_or_substrates": [],
                            "products_or_outputs": [],
                            "methods_or_instruments": [],
                        },
                        "visible_modalities": {
                            "has_schematic": False,
                            "has_workflow": False,
                            "has_photo": False,
                            "has_microscopy": False,
                            "has_fluorescence": False,
                            "has_plot": True,
                            "has_omics": False,
                            "has_chemical_characterization": False,
                            "has_simulation": False,
                            "has_molecular_structure": False,
                        },
                        "panel_profiles": [
                            {
                                "panel_id": "Figure 1-P1",
                                "evidence_shape": "experimental_performance_plot",
                                "domain_task": "material_mechanics_and_stability",
                                "panel_role": "functional_performance_evidence",
                                "recommended_extractor": "plot_numeric_extractor",
                                "recommended_metric_set": ["plot_type"],
                                "requires_caption_context": True,
                                "confidence": 0.8,
                                "uncertainty_reason": "No coordinate digitization was performed.",
                            }
                        ],
                        "recommended_global_extractor": "plot_numeric_extractor",
                        "extraction_priority": "figure_level",
                        "confidence": 0.8,
                        "uncertainty_reasons": ["No coordinate digitization was performed."],
                    }
                }

        monkeypatch.setattr("app.services.image_extraction.PromptSchemaModelClient", FakePromptSchemaClient)
        job = ImageExtractionService(db).create_job(asset, query="extract stress curve")
        completed = ImageExtractionService(db).run_job(job.id)
        assert completed is not None
        assert completed.status == "done"
        assert completed.csv_path
        assert completed.result_json
        result = json.loads(completed.result_json)
        assert result["extraction_method"] == "vlm_quantitative_metric_pipeline"
        assert result["panel_extractions"][0]["extraction_type"] in {"plot_numeric_extractor", "overview_schematic_extractor"}
        next_job = ImageExtractionService(db).create_job(asset, query="extract stress curve")
        assert next_job.id != completed.id
        assert next_job.status == "pending"

    with TestClient(app) as client:
        refreshed_paper = client.get(f"/papers/{paper_id}")
        assert refreshed_paper.status_code == 200
        refreshed_asset = refreshed_paper.json()["assets"][0]
        assert refreshed_asset["latest_extraction"]["id"] == next_job.id
        assert refreshed_asset["latest_extraction"]["status"] == "pending"
        assert refreshed_asset["latest_extraction"]["csv_url"] is None
        csv_response = client.get(f"/extractions/{completed.id}/csv")
        assert csv_response.status_code == 200
        assert "metric_name" in csv_response.text


def test_retry_image_extraction_endpoint() -> None:
    with SessionLocal() as db:
        paper = Paper(
            title="Image Retry",
            original_filename="image-retry.pdf",
            file_path="uploads/image-retry.pdf",
            file_size=123,
            file_hash="image-retry-hash",
            status="done",
        )
        db.add(paper)
        db.flush()
        asset = PaperAsset(
            paper_id=paper.id,
            asset_type="figure",
            asset_index=0,
            file_path="assets/missing.png",
            mime_type="image/png",
        )
        db.add(asset)
        db.flush()
        job = ImageExtraction(
            asset_id=asset.id,
            status="failed",
            query="extract",
            csv_path="results/old.csv",
            result_json='{"old": true}',
            error_message="llm failed",
        )
        db.add(job)
        db.commit()
        job_id = job.id

    with TestClient(app) as client:
        response = client.post(f"/extractions/{job_id}/retry")
        assert response.status_code == 202, response.text
        payload = response.json()
        assert payload["status"] == "pending"
        assert payload["error_message"] is None
        assert payload["csv_url"] is None
        assert payload["result"] is None

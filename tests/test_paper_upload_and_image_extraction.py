from __future__ import annotations

import base64
import json
import uuid
import struct
import zlib
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from app.db import SessionLocal, create_db_and_tables
from app.main import app
from app.models import Figure, ImageExtraction, Panel, Paper, PaperAsset, PendingJob
from app.services.document_parser import ParsedDocument, ParsedElement, ParsedPage
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
    assert queued[-1]["job_id"] > 0
    assert "paper_id" not in queued[-1]


def test_duplicate_pending_upload_does_not_redispatch_active_job(monkeypatch) -> None:
    queued: list[dict] = []
    monkeypatch.setattr("app.queue.redis_queue.RedisQueue.enqueue", lambda self, payload: queued.append(payload))
    content = _sample_pdf("duplicate-pending")

    with TestClient(app) as client:
        first = client.post(
            "/papers/upload",
            files={"file": ("original.pdf", content, "application/pdf")},
        )
        second = client.post(
            "/papers/upload",
            files={"file": ("duplicate.pdf", content, "application/pdf")},
        )

    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text
    assert second.json()["id"] == first.json()["id"]
    assert len(queued) == 1


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
        first_job = db.query(PendingJob).filter(PendingJob.paper_id == paper_id).one()
        first_job.status = "failed"
        first_job.completed_at = datetime.now(timezone.utc)
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

    assert len(queued) == 2
    assert queued[-1]["task_type"] == "paper_parse"
    assert queued[-1]["job_id"] != queued[0]["job_id"]
    assert "paper_id" not in queued[-1]


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
        first_job = db.query(PendingJob).filter(PendingJob.paper_id == paper_id).one()
        first_job.status = "failed"
        first_job.completed_at = datetime.now(timezone.utc)
        paper.status = "failed"
        paper.error_message = "parse failed"
        db.commit()

    with TestClient(app) as client:
        retried = client.post(f"/papers/{paper_id}/retry")
        assert retried.status_code == 202, retried.text
        assert retried.json()["status"] == "pending"
        assert retried.json()["error_message"] is None

    assert len(queued) == 2
    assert queued[-1]["schema_version"] == 2
    assert queued[-1]["task_type"] == "paper_parse"
    assert queued[-1]["job_id"] != queued[0]["job_id"]
    assert "paper_id" not in queued[-1]


def test_reparse_archives_stale_assets_and_preserves_historical_extractions(monkeypatch, tmp_path: Path) -> None:
    create_db_and_tables()
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

        storage = StorageService(root=tmp_path / "storage")
        storage.put_bytes(paper.file_path, _sample_pdf("reparse-cleanup"), media_type="application/pdf")
        PaperParseService(db, storage=storage).parse(paper)

        archived = db.query(PaperAsset).filter(PaperAsset.paper_id == paper.id).one()
        assert archived.is_active is False
        assert db.query(Figure).filter(Figure.paper_id == paper.id).count() == 0
        assert not db.query(Panel).join(PaperAsset, Panel.asset_id == PaperAsset.id).filter(PaperAsset.paper_id == paper.id).count()
        assert db.query(ImageExtraction).filter(ImageExtraction.asset_id == archived.id).count() == 1
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

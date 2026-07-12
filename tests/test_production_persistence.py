from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.sql.sqltypes import LargeBinary
from sqlalchemy.orm import sessionmaker

from app.db import SessionLocal, create_db_and_tables
from app.delivery import DeliveryBuilder
from app.models import (
    ExtractionRun,
    ImmutableRecordError,
    Paper,
    Project,
    RunArtifact,
    StorageObject,
    StructuredResult,
)
from app.repositories import JobRepository, LostJobLease
from app.services.extraction_runs import create_extraction_run, fail_extraction_run, finalize_extraction_run
from app.services.object_store import ObjectStore
from app.services.storage import LocalStorageAdapter, S3StorageAdapter, StorageService


def test_local_storage_adapter_round_trips_stable_object_reference(tmp_path: Path) -> None:
    adapter = LocalStorageAdapter(tmp_path / "objects")
    first = adapter.put_bytes("papers/1/source.pdf", b"%PDF-test", media_type="application/pdf")

    assert first.sha256 == hashlib.sha256(b"%PDF-test").hexdigest()
    assert first.size_bytes == len(b"%PDF-test")
    assert first.media_type == "application/pdf"
    assert adapter.get_bytes(first.key) == b"%PDF-test"
    assert adapter.put_bytes(first.key, b"%PDF-test", media_type="application/pdf").sha256 == first.sha256
    with pytest.raises(ValueError):
        adapter.put_bytes(first.key, b"different", media_type="application/pdf")
    with adapter.materialize(first.key, suffix=".pdf") as path:
        assert path.read_bytes() == b"%PDF-test"
    with pytest.raises(ValueError):
        adapter.put_bytes("../escape", b"bad", media_type="text/plain")
    legacy = tmp_path / "legacy-content.json"
    legacy.write_text("[]", encoding="utf-8")
    compatibility = StorageService(adapter=adapter)
    assert compatibility.exists(str(legacy))
    assert compatibility.get_bytes(str(legacy)) == b"[]"


def test_s3_storage_adapter_uses_compatible_object_api(tmp_path: Path) -> None:
    class FakeBody(io.BytesIO):
        pass

    class FakeS3:
        def __init__(self) -> None:
            self.objects: dict[tuple[str, str], bytes] = {}
            self.metadata: dict[tuple[str, str], dict[str, str]] = {}
            self.content_types: dict[tuple[str, str], str] = {}

        def put_object(self, *, Bucket, Key, Body, Metadata, ContentType, IfNoneMatch):
            assert IfNoneMatch == "*"
            if (Bucket, Key) in self.objects:
                error = RuntimeError("precondition failed")
                error.response = {"Error": {"Code": "PreconditionFailed"}}
                raise error
            self.objects[(Bucket, Key)] = Body.read() if hasattr(Body, "read") else bytes(Body)
            self.metadata[(Bucket, Key)] = Metadata
            self.content_types[(Bucket, Key)] = ContentType
            return {"ETag": '"etag-1"'}

        def get_object(self, *, Bucket, Key):
            return {"Body": FakeBody(self.objects[(Bucket, Key)])}

        def head_object(self, *, Bucket, Key):
            if (Bucket, Key) not in self.objects:
                error = RuntimeError("missing")
                error.response = {"Error": {"Code": "404"}}
                raise error
            return {
                "ETag": '"etag-head"',
                "Metadata": self.metadata[(Bucket, Key)],
                "ContentLength": len(self.objects[(Bucket, Key)]),
                "ContentType": self.content_types[(Bucket, Key)],
            }

        def delete_object(self, *, Bucket, Key):
            self.objects.pop((Bucket, Key), None)

    adapter = S3StorageAdapter(bucket="bucket", prefix="prefix", client=FakeS3())
    stored = adapter.put_bytes("runs/1/raw.json", b"{}", media_type="application/json")

    assert stored.uri == "s3://bucket/prefix/runs/1/raw.json"
    assert stored.etag == "etag-1"
    assert adapter.exists(stored.key)
    assert adapter.get_bytes(stored.key) == b"{}"
    source = tmp_path / "large.json"
    source.write_bytes(b'{"streamed":true}')
    streamed = adapter.put_file("runs/1/streamed.json", source, media_type="application/json")
    assert streamed.etag == "etag-1"
    assert adapter.get_bytes(streamed.key) == source.read_bytes()
    assert adapter.put_bytes(stored.key, b"{}", media_type="application/json").etag == "etag-head"
    with pytest.raises(ValueError):
        adapter.put_bytes(stored.key, b'{"changed":true}', media_type="application/json")
    adapter.delete(stored.key)
    assert not adapter.exists(stored.key)


def test_job_submission_is_idempotent_and_claim_is_single_winner(tmp_path: Path) -> None:
    create_db_and_tables()
    with SessionLocal() as db:
        paper = _paper_with_object(db, LocalStorageAdapter(tmp_path / "objects"))
        repository = JobRepository(db)
        first, created = repository.get_or_create(
            paper_id=paper.id, task_type="paper_parse", idempotency_key=f"test:{paper.id}"
        )
        second, created_again = repository.get_or_create(
            paper_id=paper.id, task_type="paper_parse", idempotency_key=f"test:{paper.id}"
        )
        assert created is True
        assert created_again is False
        assert first.id == second.id
        assert repository.claim(first.id, worker_id="worker-a") is not None
        db.commit()
        assert repository.claim(first.id, worker_id="worker-b") is None


def test_job_claim_generation_fences_stale_worker(tmp_path: Path) -> None:
    create_db_and_tables()
    with SessionLocal() as db:
        paper = _paper_with_object(db, LocalStorageAdapter(tmp_path / "objects"))
        repository = JobRepository(db)
        job, _ = repository.get_or_create(
            paper_id=paper.id, task_type="paper_parse", idempotency_key=f"fencing:{paper.id}"
        )
        claimed = repository.claim(job.id, worker_id="worker-a")
        assert claimed is not None and claimed.claim_generation == 1
        assert repository.renew(
            claimed.id,
            worker_id="worker-a",
            claim_generation=claimed.claim_generation - 1,
        ) is False
        stale_claim = SimpleNamespace(
            id=claimed.id,
            lease_owner=claimed.lease_owner,
            claim_generation=claimed.claim_generation,
        )
        db.commit()
        with SessionLocal() as competing_db:
            competing_db.execute(
                text(
                    "UPDATE pending_jobs SET lease_owner = 'worker-b', "
                    "claim_generation = claim_generation + 1 WHERE id = :id"
                ),
                {"id": claimed.id},
            )
            competing_db.commit()
        with pytest.raises(LostJobLease):
            repository.assert_ownership(stale_claim)


def test_completed_extraction_run_is_immutable(tmp_path: Path) -> None:
    create_db_and_tables()
    with SessionLocal() as db:
        paper = _paper_with_object(db, LocalStorageAdapter(tmp_path / "objects"))
        job, _ = JobRepository(db).get_or_create(
            paper_id=paper.id,
            task_type="chart_only_run",
            idempotency_key=f"immutable:{paper.id}",
        )
        run = create_extraction_run(
            db,
            job=job,
            paper=paper,
            input_object=paper.pdf_object,
            config_snapshot={"test": True},
        )
        run.status = "succeeded"
        run.completed_at = datetime.now(timezone.utc)
        db.commit()

        run.error_message = "attempted overwrite"
        with pytest.raises(ImmutableRecordError):
            db.commit()
        db.rollback()

        with pytest.raises(DBAPIError):
            db.execute(text("UPDATE extraction_runs SET error_message = 'bulk overwrite' WHERE id = :id"), {"id": run.id})
            db.commit()
        db.rollback()

        db.add(
            StructuredResult(
                run_id=run.id,
                paper_id=paper.id,
                result_type="chart_fact",
                natural_key="late-result",
                schema_version="normalized-result.v1",
                content_hash="0" * 64,
                payload={"late": True},
            )
        )
        with pytest.raises(ImmutableRecordError):
            db.commit()
        db.rollback()


def test_retry_creates_new_run_and_keeps_raw_and_normalized_results(tmp_path: Path) -> None:
    create_db_and_tables()
    adapter = LocalStorageAdapter(tmp_path / "objects")
    with SessionLocal() as db:
        paper = _paper_with_object(db, adapter)
        first_job, _ = JobRepository(db).get_or_create(
            paper_id=paper.id,
            task_type="chart_only_run",
            idempotency_key=f"run:first:{paper.id}",
        )
        assert JobRepository(db).claim(first_job.id, worker_id="test-worker") is not None
        first_run = create_extraction_run(
            db,
            job=first_job,
            paper=paper,
            input_object=paper.pdf_object,
            config_snapshot={"temperature": 0},
        )
        result = SimpleNamespace(
            status="succeeded",
            errors=[],
            chart_facts=[{"fact_id": "fact-1", "panel_id": "a", "y_value": 2.5}],
            panel_fact_rows=[],
            heatmap_candidates=[],
            image_observations=[
                {"observation_name": "signal", "panel_id": "a", "value": "up"},
                {"observation_name": "signal", "panel_id": "b", "value": "down"},
            ],
        )
        finalize_extraction_run(
            db,
            run=first_run,
            job=first_job,
            raw_responses=[{"sequence": 1, "phase": "chart", "response": {"y": 2.5}}],
            result=result,
            storage_adapter=adapter,
        )
        db.commit()
        assert first_run.raw_output_object_id
        normalized = db.query(StructuredResult).filter(StructuredResult.run_id == first_run.id).all()
        assert len(normalized) == 3
        assert len({item.natural_key for item in normalized}) == 3
        assert db.query(RunArtifact).filter(RunArtifact.run_id == first_run.id).count() == 1

        retry_job, _ = JobRepository(db).get_or_create(
            paper_id=paper.id,
            task_type="chart_only_run",
            idempotency_key=f"run:retry:{paper.id}",
            attempt=2,
        )
        retry_run = create_extraction_run(
            db,
            job=retry_job,
            paper=paper,
            input_object=paper.pdf_object,
            parent_run_id=first_run.id,
            config_snapshot={"temperature": 0},
        )
        assert retry_run.id != first_run.id
        assert retry_run.parent_run_id == first_run.id
        assert db.get(ExtractionRun, first_run.id).status == "succeeded"


def test_failed_extraction_run_retains_raw_responses(tmp_path: Path) -> None:
    create_db_and_tables()
    adapter = LocalStorageAdapter(tmp_path / "objects")
    with SessionLocal() as db:
        paper = _paper_with_object(db, adapter)
        job, _ = JobRepository(db).get_or_create(
            paper_id=paper.id,
            task_type="chart_only_run",
            idempotency_key=f"failed-raw:{paper.id}",
        )
        assert JobRepository(db).claim(job.id, worker_id="test-worker") is not None
        run = create_extraction_run(
            db,
            job=job,
            paper=paper,
            input_object=paper.pdf_object,
            config_snapshot={"test": True},
        )
        fail_extraction_run(
            db,
            run=run,
            job=job,
            exc=RuntimeError("provider failed after response"),
            raw_responses=[{"phase": "chart", "response": {"partial": True}}],
            storage_adapter=adapter,
        )
        db.commit()
        payload = json.loads(adapter.get_bytes(f"runs/{run.id}/model-raw-responses.json"))
        assert payload["responses"][0]["response"] == {"partial": True}
        assert run.status == "failed"


def test_delivery_builder_writes_manifest_checksums_and_all_formats(tmp_path: Path) -> None:
    create_db_and_tables()
    adapter = LocalStorageAdapter(tmp_path / "objects")
    version = f"test-{uuid4()}"
    equivalent_version = f"test-{uuid4()}"
    with SessionLocal() as db:
        _paper_with_object(db, adapter)
        snapshot_at = datetime.now(timezone.utc)
        result = DeliveryBuilder(db, adapter).build(version=version, snapshot_at=snapshot_at)
        equivalent = DeliveryBuilder(db, adapter).build(
            version=equivalent_version, snapshot_at=snapshot_at
        )
        with pytest.raises(ValueError, match="cannot be overwritten"):
            DeliveryBuilder(db, adapter).build(version=version, snapshot_at=snapshot_at)

    formats = {item["format"] for item in result.manifest["files"]}
    assert {"duckdb", "parquet", "excel", "markdown"}.issubset(formats)
    assert result.manifest["database_schema_version"] == "0005_concurrency_guards"
    assert result.manifest["build_status"] == "published"
    assert "papers" in result.manifest["record_counts"]
    for item in result.manifest["files"]:
        data = adapter.get_bytes(f"deliveries/{version}/{item['filename']}")
        assert hashlib.sha256(data).hexdigest() == item["sha256"]
        assert len(data) == item["size_bytes"]
    manifest = json.loads(adapter.get_bytes(f"deliveries/{version}/manifest.json"))
    assert manifest == result.manifest
    first_parquet = {
        item["filename"]: item["sha256"] for item in result.manifest["files"] if item["format"] == "parquet"
    }
    second_parquet = {
        item["filename"]: item["sha256"] for item in equivalent.manifest["files"] if item["format"] == "parquet"
    }
    assert first_parquet == second_parquet


def test_delivery_builder_applies_data_scope(tmp_path: Path) -> None:
    create_db_and_tables()
    adapter = LocalStorageAdapter(tmp_path / "objects")
    with SessionLocal() as db:
        included = _paper_with_object(db, adapter)
        included_id = included.id
        _paper_with_object(db, adapter)
        result = DeliveryBuilder(db, adapter).build(
            version=f"scope-{uuid4()}",
            data_scope={"paper_ids": [included_id]},
        )
    assert result.manifest["data_scope"] == {"project_id": 1, "paper_ids": [included_id]}
    assert result.manifest["record_counts"]["papers"] == 1
    with SessionLocal() as db:
        empty = DeliveryBuilder(db, adapter).build(
            version=f"scope-empty-{uuid4()}",
            data_scope={"paper_ids": []},
        )
    assert empty.manifest["record_counts"]["papers"] == 0


def test_alembic_builds_fresh_database_from_zero(tmp_path: Path) -> None:
    database = tmp_path / "fresh.db"
    environment = {
        **os.environ,
        "DATA_DIR": str(tmp_path),
        "DATABASE_URL": f"sqlite:///{database}",
        "STORAGE_LOCAL_ROOT": str(tmp_path / "objects"),
    }
    completed = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=Path(__file__).resolve().parents[1],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    engine = create_engine(f"sqlite:///{database}")
    try:
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        assert {"projects", "storage_objects", "extraction_runs", "structured_results", "run_artifacts", "delivery_versions"} <= tables
        assert "is_active" in {column["name"] for column in inspector.get_columns("paper_assets")}
        assert "mineru_markdown_object_id" in {column["name"] for column in inspector.get_columns("papers")}
        assert "claim_generation" in {column["name"] for column in inspector.get_columns("pending_jobs")}
        assert "uq_papers_project_active_hash" in {index["name"] for index in inspector.get_indexes("papers")}
        assert "ix_paper_assets_asset_type" in {index["name"] for index in inspector.get_indexes("paper_assets")}
        with engine.connect() as connection:
            trigger_names = {
                row[0]
                for row in connection.execute(
                    text("SELECT name FROM sqlite_master WHERE type = 'trigger'")
                )
            }
        assert "trg_guard_terminal_extraction_run_update" in trigger_names
        for table in tables:
            assert not any(isinstance(column["type"], LargeBinary) for column in inspector.get_columns(table))
    finally:
        engine.dispose()


@pytest.mark.postgresql
def test_postgresql_repository_integration() -> None:
    database_url = os.getenv("TEST_POSTGRES_URL")
    if not database_url:
        pytest.skip("TEST_POSTGRES_URL is required for the PostgreSQL integration test")
    environment = {**os.environ, "DATABASE_URL": database_url}
    completed = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=Path(__file__).resolve().parents[1],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    engine = create_engine(database_url)
    assert {"extraction_runs", "structured_results", "delivery_versions"} <= set(inspect(engine).get_table_names())
    session_factory = sessionmaker(bind=engine)
    with session_factory() as db:
        paper = Paper(
            project_id=1,
            title=f"PostgreSQL {uuid4()}",
            original_filename="postgres.pdf",
            file_path=f"postgres/{uuid4()}.pdf",
            file_size=1,
            file_hash=uuid4().hex + uuid4().hex,
            status="pending",
        )
        db.add(paper)
        db.flush()
        first, created = JobRepository(db).get_or_create(
            paper_id=paper.id, task_type="paper_parse", idempotency_key=f"postgres:{paper.id}"
        )
        second, created_again = JobRepository(db).get_or_create(
            paper_id=paper.id, task_type="paper_parse", idempotency_key=f"postgres:{paper.id}"
        )
        assert created and not created_again and first.id == second.id
        db.commit()
        claimed = JobRepository(db).claim(first.id, worker_id="postgres-worker-a")
        assert claimed is not None
        with session_factory() as competing_db:
            assert JobRepository(competing_db).claim(first.id, worker_id="postgres-worker-b") is None
            competing_db.rollback()
        stored = StorageObject(
            object_key=f"postgres/{uuid4()}.pdf",
            uri=f"s3://postgres-test/{uuid4()}.pdf",
            sha256="1" * 64,
            size_bytes=1,
            media_type="application/pdf",
            metadata_json={},
        )
        db.add(stored)
        db.flush()
        run = create_extraction_run(
            db,
            job=claimed,
            paper=paper,
            input_object=stored,
            config_snapshot={"postgres": True},
        )
        run.status = "succeeded"
        run.completed_at = datetime.now(timezone.utc)
        JobRepository(db).complete(claimed)
        db.commit()
        with pytest.raises(DBAPIError):
            db.execute(
                text("UPDATE extraction_runs SET error_message = 'bulk overwrite' WHERE id = :id"),
                {"id": run.id},
            )
            db.commit()
        db.rollback()
    engine.dispose()


def _paper_with_object(db, adapter: LocalStorageAdapter) -> Paper:
    if db.get(Project, 1) is None:
        db.add(Project(id=1, slug="default", name="Default Project"))
        db.flush()
    unique = uuid4().hex
    paper = Paper(
        project_id=1,
        title=f"Paper {unique}",
        original_filename="paper.pdf",
        file_path="pending",
        file_size=9,
        file_hash=(unique * 2)[:64],
        status="pending",
    )
    db.add(paper)
    db.flush()
    stored = ObjectStore(db, adapter).put_bytes(
        key=f"papers/{paper.id}/source/{unique}.pdf",
        data=b"%PDF-test",
        media_type="application/pdf",
    )
    paper.file_path = stored.object_key
    paper.pdf_object_id = stored.id
    db.commit()
    db.refresh(paper)
    return paper

from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Barrier, Event, Lock
from types import SimpleNamespace
from uuid import uuid4

import app.services.pdf.dispatcher as dispatcher
import pytest
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.engine import Inspector, make_url
from sqlalchemy.sql.sqltypes import LargeBinary
from sqlalchemy.orm import sessionmaker

from app.db import SessionLocal, create_db_and_tables
from app.delivery import DeliveryBuilder
from app.models import (
    BatchEvent,
    BatchItem,
    BatchRun,
    ExtractionRun,
    ImmutableRecordError,
    Paper,
    PendingJob,
    Project,
    RunArtifact,
    StorageObject,
    StructuredResult,
)
from app.repositories import BatchRepository, JobClaim, JobRepository, LostJobLease
from app.services.extraction_runs import (
    create_extraction_run,
    fail_extraction_run,
    finalize_extraction_run,
    stage_raw_responses,
)
from app.services.object_store import ObjectStore
from app.services.batches import BatchLifecycleService, BatchScheduler, BatchSubmissionService, result_config_hash
from app.services.pdf.parse_service import PaperParseService
from app.services.storage import LocalStorageAdapter, S3StorageAdapter, StorageService
from app.worker import _resolve_job


def _foreign_key_contract(inspector: Inspector, table_name: str) -> set[tuple[tuple[str, ...], str, tuple[str, ...], str | None]]:
    return {
        (
            tuple(foreign_key["constrained_columns"]),
            foreign_key["referred_table"],
            tuple(foreign_key["referred_columns"]),
            foreign_key.get("options", {}).get("ondelete"),
        )
        for foreign_key in inspector.get_foreign_keys(table_name)
    }


def _assert_batch_schema_contract(inspector: Inspector) -> None:
    assert {
        (("project_id",), "projects", ("id",), "RESTRICT"),
    } <= _foreign_key_contract(inspector, "batch_runs")
    assert {
        (("batch_run_id",), "batch_runs", ("id",), "RESTRICT"),
        (("paper_id",), "papers", ("id",), "RESTRICT"),
        (("resolved_extraction_run_id",), "extraction_runs", ("id",), "RESTRICT"),
    } <= _foreign_key_contract(inspector, "batch_items")
    assert {
        (("batch_run_id",), "batch_runs", ("id",), "RESTRICT"),
        (("batch_item_id",), "batch_items", ("id",), "RESTRICT"),
    } <= _foreign_key_contract(inspector, "batch_events")
    assert {
        (("batch_item_id",), "batch_items", ("id",), "RESTRICT"),
        (("retry_of_job_id",), "pending_jobs", ("id",), "RESTRICT"),
    } <= _foreign_key_contract(inspector, "pending_jobs")
    assert {
        "ck_batch_runs_concurrency_positive",
        "ck_batch_runs_status",
    } <= {constraint["name"] for constraint in inspector.get_check_constraints("batch_runs")}
    assert "ck_batch_items_status" in {constraint["name"] for constraint in inspector.get_check_constraints("batch_items")}
    assert {
        "uq_batch_runs_project_submission_key",
    } <= {constraint["name"] for constraint in inspector.get_unique_constraints("batch_runs")}
    assert {
        "uq_batch_items_run_ordinal",
        "uq_batch_items_run_relative_path",
    } <= {constraint["name"] for constraint in inspector.get_unique_constraints("batch_items")}
    assert {
        "ix_batch_runs_project_status_updated",
    } <= {index["name"] for index in inspector.get_indexes("batch_runs")}
    assert {
        "ix_batch_items_run_status",
        "ix_batch_items_source_sha256",
        "ix_batch_items_paper_id",
    } <= {index["name"] for index in inspector.get_indexes("batch_items")}
    assert {
        "ix_batch_events_run_id",
        "ix_batch_events_item_id",
    } <= {index["name"] for index in inspector.get_indexes("batch_events")}


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


def test_staged_bytes_are_not_registered_until_terminal_fence(tmp_path: Path) -> None:
    create_db_and_tables()
    adapter = LocalStorageAdapter(tmp_path / "objects")
    with SessionLocal() as db:
        before = db.query(StorageObject).count()
        info = ObjectStore(db, adapter).stage_bytes(
            run_id="run-1",
            claim_generation=3,
            role="pipeline_output",
            filename="audit/result.json",
            data=b"{}",
            media_type="application/json",
        )
        assert adapter.exists(info.key)
        assert "/staging/3/pipeline_output/" in info.key
        assert db.query(StorageObject).count() == before
        assert not any(isinstance(record, StorageObject) for record in db.new)


def test_transport_recovery_registers_only_current_claim_generation(tmp_path: Path) -> None:
    create_db_and_tables()
    adapter = LocalStorageAdapter(tmp_path / "objects")
    with SessionLocal() as db:
        paper = _paper_with_object(db, adapter)
        repository = JobRepository(db)
        job, _ = repository.get_or_create(
            paper_id=paper.id,
            task_type="chart_only_run",
            idempotency_key=f"staging-recovery:{paper.id}",
        )
        first_job = repository.claim(job.id, worker_id="worker-a")
        assert first_job is not None
        first_claim = JobClaim.from_job(first_job)
        run = create_extraction_run(
            db,
            job=first_claim,
            paper=paper,
            input_object=paper.pdf_object,
            config_snapshot={"test": True},
        )
        db.commit()

        first_staged = stage_raw_responses(
            ObjectStore(db, adapter),
            run=run,
            job=first_claim,
            raw_responses=[{"response": "first worker"}],
        )
        db.execute(
            text(
                "UPDATE pending_jobs SET status = 'retry', lease_owner = NULL, "
                "lease_expires_at = NULL WHERE id = :id"
            ),
            {"id": job.id},
        )
        db.commit()
        second_job = repository.claim(job.id, worker_id="worker-b")
        assert second_job is not None
        second_claim = JobClaim.from_job(second_job)
        db.commit()
        second_staged = stage_raw_responses(
            ObjectStore(db, adapter),
            run=run,
            job=second_claim,
            raw_responses=[{"response": "second worker"}],
        )

        result = SimpleNamespace(status="succeeded", errors=[])
        with pytest.raises(LostJobLease):
            finalize_extraction_run(
                db,
                run=run,
                job=first_claim,
                raw_responses=[],
                result=result,
                storage_adapter=adapter,
                staged_raw_object=first_staged,
            )
        db.rollback()
        assert db.query(StorageObject).filter(StorageObject.object_key == first_staged.key).count() == 0

        durable_run = db.get(ExtractionRun, run.id)
        assert durable_run is not None
        finalize_extraction_run(
            db,
            run=durable_run,
            job=second_claim,
            raw_responses=[],
            result=result,
            storage_adapter=adapter,
            staged_raw_object=second_staged,
        )
        db.commit()

        assert durable_run.status == "succeeded"
        assert durable_run.raw_output_object.object_key == second_staged.key
        assert "/staging/2/model_raw_responses/" in second_staged.key
        assert adapter.exists(first_staged.key)
        assert db.query(StorageObject).filter(StorageObject.object_key == first_staged.key).count() == 0


def test_stale_dispatch_and_worker_resume_same_job_and_run_without_mineru(tmp_path: Path, monkeypatch) -> None:
    create_db_and_tables()
    adapter = LocalStorageAdapter(tmp_path / "objects")
    queued: list[dict] = []
    monkeypatch.setattr("app.services.pdf.dispatcher.RedisQueue.enqueue", lambda _queue, payload: queued.append(payload))
    with SessionLocal() as db:
        paper = _paper_with_object(db, adapter)
        paper.mineru_content_object_id = paper.pdf_object_id
        paper.mineru_content_list_path = paper.file_path
        repository = JobRepository(db)
        job, _ = repository.get_or_create(
            paper_id=paper.id,
            task_type="paper_parse",
            idempotency_key=f"production-resume:{paper.id}",
        )
        first_claimed = repository.claim(job.id, worker_id="crashed-worker")
        assert first_claimed is not None
        first_claim = JobClaim.from_job(first_claimed)
        run = create_extraction_run(
            db,
            job=first_claim,
            paper=paper,
            input_object=paper.pdf_object,
            config_snapshot={"transport_recovery": True},
        )
        db.commit()
        db.execute(
            text(
                "UPDATE pending_jobs SET lease_expires_at = :expired, updated_at = :expired "
                "WHERE id = :id"
            ),
            {"id": job.id, "expired": datetime.now(timezone.utc) - timedelta(hours=1)},
        )
        db.commit()

        assert dispatcher.dispatch_stale_pending_jobs(db, job_ids=[job.id]) == 1
        assert queued == [{"schema_version": 2, "task_type": "paper_parse", "job_id": job.id}]
        redispatched = _resolve_job(db, queued[0])
        assert redispatched is not None
        second_claimed = repository.claim(redispatched.id, worker_id="recovery-worker")
        assert second_claimed is not None
        second_claim = JobClaim.from_job(second_claimed)
        db.commit()

        def resume_pipeline(_paper, *, job):
            durable_run = db.get(ExtractionRun, run.id)
            assert durable_run is not None
            staged = stage_raw_responses(
                ObjectStore(db, adapter),
                run=durable_run,
                job=job,
                raw_responses=[{"response": "recovered"}],
            )
            finalize_extraction_run(
                db,
                run=durable_run,
                job=job,
                raw_responses=[{"response": "recovered"}],
                result=SimpleNamespace(status="succeeded", errors=[]),
                storage_adapter=adapter,
                staged_raw_object=staged,
            )
            assert durable_run.status == "succeeded"
            return {"status": "succeeded"}

        monkeypatch.setattr("app.services.pdf.parse_service.run_chart_only_for_paper", resume_pipeline)
        monkeypatch.setattr(
            "app.services.pdf.parse_service.MinerUParserService.parse_pdf_file",
            lambda *_args, **_kwargs: pytest.fail("transport resume reran MinerU"),
        )
        result = PaperParseService(db, StorageService(adapter=adapter)).parse_or_fail(
            paper.id,
            job=second_claim,
        )

        assert result is not None and str(result.status) == "done"
        assert db.query(PendingJob).filter(PendingJob.id == job.id).count() == 1
        assert db.query(ExtractionRun).filter(ExtractionRun.id == run.id).count() == 1
        db.refresh(job)
        db.refresh(run)
        assert job.status == "done"
        assert job.claim_generation == 2
        assert run.status == "succeeded"

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
        payload = json.loads(adapter.get_bytes(run.raw_output_object.object_key))
        assert "/staging/1/model_raw_responses/" in run.raw_output_object.object_key
        assert payload["responses"][0]["response"] == {"partial": True}
        assert run.status == "failed"


def test_batch_failure_terminal_facts_commit_atomically_once(tmp_path: Path) -> None:
    create_db_and_tables()
    adapter = LocalStorageAdapter(tmp_path / "objects")
    with SessionLocal() as db:
        paper = _paper_with_object(db, adapter)
        batch = BatchRun(
            project_id=paper.project_id,
            submission_key=f"atomic-failure-{uuid4()}",
            source_root="/test",
            status="running",
            batch_concurrency=1,
            config_snapshot={"test": True},
            result_config_hash="a" * 64,
        )
        db.add(batch)
        db.flush()
        item = BatchItem(
            batch_run_id=batch.id,
            ordinal=0,
            source_relative_path="paper.pdf",
            source_sha256=paper.file_hash,
            source_size_bytes=paper.file_size,
            paper_id=paper.id,
            status="queued",
        )
        db.add(item)
        db.flush()
        job = PendingJob(
            paper_id=paper.id,
            task_type="paper_parse",
            idempotency_key=f"atomic-failure-job-{uuid4()}",
            batch_item_id=item.id,
            status="pending",
        )
        db.add(job)
        db.commit()
        claimed = JobRepository(db).claim(job.id, worker_id="worker-a")
        assert claimed is not None
        claim = JobClaim.from_job(claimed)
        run = create_extraction_run(
            db,
            job=claim,
            paper=paper,
            input_object=paper.pdf_object,
            config_snapshot={"test": True},
        )
        db.commit()
        run_id, job_id, item_id, batch_id, paper_id = run.id, job.id, item.id, batch.id, paper.id

        fail_extraction_run(
            db,
            run=run,
            job=claim,
            exc=RuntimeError("provider failed"),
            raw_responses=[{"response": "partial"}],
            storage_adapter=adapter,
        )
        db.rollback()
        assert db.get(ExtractionRun, run_id).status == "running"
        assert db.get(PendingJob, job_id).status == "processing"
        assert db.get(BatchItem, item_id).status == "processing"
        assert db.get(Paper, paper_id).status == "pending"
        assert db.query(BatchEvent).filter(
            BatchEvent.batch_run_id == batch_id,
            BatchEvent.event_type == "item_failed",
        ).count() == 0

        fail_extraction_run(
            db,
            run=db.get(ExtractionRun, run_id),
            job=claim,
            exc=RuntimeError("provider failed"),
            raw_responses=[{"response": "partial"}],
            storage_adapter=adapter,
        )
        db.commit()
        assert db.get(ExtractionRun, run_id).status == "failed"
        assert db.get(PendingJob, job_id).status == "failed"
        assert db.get(BatchItem, item_id).status == "failed"
        assert db.get(BatchRun, batch_id).status == "failed"
        assert db.get(Paper, paper_id).status == "failed"
        assert db.query(BatchEvent).filter(
            BatchEvent.batch_run_id == batch_id,
            BatchEvent.event_type == "item_failed",
        ).count() == 1


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
    assert result.manifest["database_schema_version"] == "0006_batch_processing"
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
        assert {
            "projects",
            "storage_objects",
            "extraction_runs",
            "structured_results",
            "run_artifacts",
            "delivery_versions",
            "batch_runs",
            "batch_items",
            "batch_events",
        } <= tables
        assert "is_active" in {column["name"] for column in inspector.get_columns("paper_assets")}
        assert "mineru_markdown_object_id" in {column["name"] for column in inspector.get_columns("papers")}
        assert "claim_generation" in {column["name"] for column in inspector.get_columns("pending_jobs")}
        assert {"batch_item_id", "retry_of_job_id"} <= {
            column["name"] for column in inspector.get_columns("pending_jobs")
        }
        assert "uq_papers_project_active_hash" in {index["name"] for index in inspector.get_indexes("papers")}
        assert "ix_paper_assets_asset_type" in {index["name"] for index in inspector.get_indexes("paper_assets")}
        _assert_batch_schema_contract(inspector)
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
def test_postgresql_alembic_builds_batch_schema_in_isolated_fresh_schema() -> None:
    database_url = os.getenv("TEST_POSTGRES_URL")
    if not database_url:
        pytest.skip("TEST_POSTGRES_URL is required for the PostgreSQL integration test")
    schema = f"batch_fresh_{uuid4().hex}"
    base_url = make_url(database_url)
    existing_options = str(base_url.query.get("options") or "")
    isolated_url = base_url.update_query_dict(
        {"options": f"{existing_options} -csearch_path={schema}".strip()}
    ).render_as_string(hide_password=False)
    admin_engine = create_engine(database_url, isolation_level="AUTOCOMMIT")
    fresh_engine = None
    try:
        with admin_engine.connect() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        completed = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=Path(__file__).resolve().parents[1],
            env={**os.environ, "DATABASE_URL": isolated_url},
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr
        fresh_engine = create_engine(isolated_url)
        inspector = inspect(fresh_engine)
        assert {"batch_events", "batch_items", "batch_runs", "pending_jobs"} <= set(inspector.get_table_names())
        _assert_batch_schema_contract(inspector)
    finally:
        if fresh_engine is not None:
            fresh_engine.dispose()
        with admin_engine.connect() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin_engine.dispose()


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
    assert {
        "batch_events",
        "batch_items",
        "batch_runs",
        "extraction_runs",
        "structured_results",
        "delivery_versions",
    } <= set(inspect(engine).get_table_names())
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
        db.flush()
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


@pytest.mark.postgresql
def test_postgresql_paper_parse_admission_has_one_active_job() -> None:
    database_url = os.getenv("TEST_POSTGRES_URL")
    if not database_url:
        pytest.skip("TEST_POSTGRES_URL is required for the PostgreSQL integration test")
    completed = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "DATABASE_URL": database_url},
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    engine = create_engine(database_url)
    session_factory = sessionmaker(bind=engine)
    try:
        with session_factory() as db:
            project = Project(slug=f"admission-project-{uuid4()}", name="Admission Project")
            db.add(project)
            db.flush()
            paper = Paper(
                project_id=project.id,
                title=f"Concurrent PostgreSQL {uuid4()}",
                original_filename="concurrent.pdf",
                file_path=f"postgres/{uuid4()}.pdf",
                file_size=1,
                file_hash=uuid4().hex + uuid4().hex,
                status="pending",
            )
            db.add(paper)
            db.commit()
            paper_id = paper.id

        barrier = Barrier(2)

        def admit() -> tuple[int, bool]:
            with session_factory() as db:
                barrier.wait(timeout=5)
                job, created = JobRepository(db).admit_paper_parse(paper_id=paper_id)
                db.commit()
                return job.id, created

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = [future.result(timeout=10) for future in [executor.submit(admit), executor.submit(admit)]]

        assert sum(created for _, created in results) == 1
        assert len({job_id for job_id, _ in results}) == 1
    finally:
        engine.dispose()


@pytest.mark.postgresql
def test_postgresql_concurrent_submission_key_registers_each_item_once(tmp_path: Path) -> None:
    database_url = os.getenv("TEST_POSTGRES_URL")
    if not database_url:
        pytest.skip("TEST_POSTGRES_URL is required for the PostgreSQL integration test")
    completed = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "DATABASE_URL": database_url},
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    source_root = tmp_path / "source"
    source_root.mkdir()
    content = (
        b"%PDF-1.4\n"
        b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Count 1/Kids[3 0 R]>>endobj\n"
        b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]>>endobj\n"
        b"trailer<</Root 1 0 R>>\n%%EOF\n"
    )
    (source_root / "concurrent.pdf").write_bytes(content)
    submission_key = f"concurrent-submission-{uuid4()}"
    config_snapshot = {"result_semantics": {"model": "concurrent-test"}}
    engine = create_engine(database_url)
    session_factory = sessionmaker(bind=engine)
    try:
        with session_factory() as db:
            project = Project(slug=f"submission-project-{uuid4()}", name="Submission Project")
            db.add(project)
            db.commit()
            project_id = project.id
        barrier = Barrier(2)

        def submit() -> str:
            with session_factory() as db:
                barrier.wait(timeout=5)
                batch = BatchSubmissionService(db, StorageService(root=tmp_path / "objects")).submit(
                    project_id=project_id,
                    source_root=source_root,
                    submission_key=submission_key,
                    batch_concurrency=1,
                    config_snapshot=config_snapshot,
                )
                return batch.id

        with ThreadPoolExecutor(max_workers=2) as executor:
            batch_ids = [future.result(timeout=15) for future in [executor.submit(submit), executor.submit(submit)]]

        assert len(set(batch_ids)) == 1
        with session_factory() as db:
            batch = db.query(BatchRun).filter(BatchRun.submission_key == submission_key).one()
            item = db.query(BatchItem).filter(BatchItem.batch_run_id == batch.id).one()
            events = (
                db.query(BatchEvent)
                .filter(BatchEvent.batch_item_id == item.id, BatchEvent.event_type.in_(("item_registered", "item_reused")))
                .all()
            )
            assert item.paper_id is not None
            assert len(events) == 1
            assert batch.result_config_hash == result_config_hash(config_snapshot)
    finally:
        engine.dispose()


@pytest.mark.postgresql
def test_postgresql_concurrent_batch_schedulers_respect_the_job_window(monkeypatch) -> None:
    database_url = os.getenv("TEST_POSTGRES_URL")
    if not database_url:
        pytest.skip("TEST_POSTGRES_URL is required for the PostgreSQL integration test")
    completed = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "DATABASE_URL": database_url},
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    monkeypatch.setattr("app.queue.redis_queue.RedisQueue.enqueue", lambda _queue, _payload: None)
    engine = create_engine(database_url)
    session_factory = sessionmaker(bind=engine)
    try:
        with session_factory() as db:
            project = Project(slug=f"scheduler-window-{uuid4()}", name="Scheduler Window")
            db.add(project)
            db.flush()
            config_snapshot = {"result_semantics": {"model": "scheduler-test"}}
            batch = BatchRun(
                project_id=project.id,
                submission_key=f"scheduler-window-{uuid4()}",
                source_root="/postgres",
                batch_concurrency=2,
                config_snapshot=config_snapshot,
                result_config_hash=result_config_hash(config_snapshot),
            )
            db.add(batch)
            db.flush()
            for ordinal in range(4):
                paper = Paper(
                    project_id=project.id,
                    title=f"Scheduled {ordinal}",
                    original_filename=f"scheduled-{ordinal}.pdf",
                    file_path=f"postgres/scheduled-{uuid4()}.pdf",
                    file_size=1,
                    file_hash=(uuid4().hex + uuid4().hex)[:64],
                    status="pending",
                )
                db.add(paper)
                db.flush()
                db.add(
                    BatchItem(
                        batch_run_id=batch.id,
                        ordinal=ordinal,
                        source_relative_path=f"scheduled-{ordinal}.pdf",
                        source_sha256=paper.file_hash,
                        source_size_bytes=1,
                        paper_id=paper.id,
                    )
                )
            db.commit()
            batch_id = batch.id

        barrier = Barrier(2)
        first_lock_held = Event()
        second_lock_attempted = Event()
        second_lock_returned = Event()
        release_first_lock = Event()
        call_counter = 0
        counter_lock = Lock()
        original_lock_run = BatchRepository.lock_run

        def lock_run_with_contention(self, batch_run_id: str, *, skip_locked: bool = False):
            nonlocal call_counter
            with counter_lock:
                call_counter += 1
                call_number = call_counter
            if call_number == 2:
                second_lock_attempted.set()
            locked_batch = original_lock_run(self, batch_run_id, skip_locked=skip_locked)
            if call_number == 1:
                first_lock_held.set()
                assert release_first_lock.wait(timeout=5)
            elif call_number == 2:
                second_lock_returned.set()
            return locked_batch

        monkeypatch.setattr(BatchRepository, "lock_run", lock_run_with_contention)

        def schedule() -> list[int]:
            with session_factory() as db:
                barrier.wait(timeout=5)
                return BatchScheduler(db).schedule(batch_id)

        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(schedule)
            second = executor.submit(schedule)
            assert first_lock_held.wait(timeout=5)
            assert second_lock_attempted.wait(timeout=5)
            assert not second_lock_returned.wait(timeout=0.2)
            release_first_lock.set()
            outcomes = [future.result(timeout=15) for future in (first, second)]

        assert sum(len(outcome) for outcome in outcomes) == 2
        with session_factory() as db:
            jobs = (
                db.query(PendingJob)
                .join(BatchItem, PendingJob.batch_item_id == BatchItem.id)
                .filter(BatchItem.batch_run_id == batch_id)
                .all()
            )
            items = db.query(BatchItem).filter(BatchItem.batch_run_id == batch_id).all()
            assert len(jobs) == 2
            assert {job.status for job in jobs} == {"redis_dispatched"}
            assert sum(item.status == "queued" for item in items) == 2
            assert sum(item.status == "pending" for item in items) == 2
    finally:
        engine.dispose()


@pytest.mark.postgresql
def test_postgresql_scheduler_does_not_wait_on_active_job_while_holding_shared_paper_lock(monkeypatch) -> None:
    database_url = os.getenv("TEST_POSTGRES_URL")
    if not database_url:
        pytest.skip("TEST_POSTGRES_URL is required for the PostgreSQL integration test")
    completed = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "DATABASE_URL": database_url},
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    monkeypatch.setattr("app.queue.redis_queue.RedisQueue.enqueue", lambda _queue, _payload: None)
    engine = create_engine(database_url)
    session_factory = sessionmaker(bind=engine)
    try:
        with session_factory() as db:
            project = Project(slug=f"paper-lock-{uuid4()}", name="Paper Lock")
            db.add(project)
            db.flush()
            paper = Paper(
                project_id=project.id,
                title="Shared Paper",
                original_filename="shared.pdf",
                file_path=f"postgres/{uuid4()}.pdf",
                file_size=1,
                file_hash=(uuid4().hex + uuid4().hex)[:64],
                status="processing",
            )
            config_snapshot = {"result_semantics": {"model": "paper-lock"}}
            first_batch = BatchRun(
                project_id=project.id,
                submission_key=f"paper-lock-first-{uuid4()}",
                source_root="/postgres",
                status="running",
                batch_concurrency=1,
                config_snapshot=config_snapshot,
                result_config_hash=result_config_hash(config_snapshot),
            )
            waiting_batch = BatchRun(
                project_id=project.id,
                submission_key=f"paper-lock-waiting-{uuid4()}",
                source_root="/postgres",
                batch_concurrency=1,
                config_snapshot=config_snapshot,
                result_config_hash=result_config_hash(config_snapshot),
            )
            db.add_all([paper, first_batch, waiting_batch])
            db.flush()
            first_item = BatchItem(
                batch_run_id=first_batch.id,
                ordinal=0,
                source_relative_path="first/shared.pdf",
                source_sha256=paper.file_hash,
                source_size_bytes=1,
                paper_id=paper.id,
                status="processing",
            )
            waiting_item = BatchItem(
                batch_run_id=waiting_batch.id,
                ordinal=0,
                source_relative_path="waiting/shared.pdf",
                source_sha256=paper.file_hash,
                source_size_bytes=1,
                paper_id=paper.id,
            )
            db.add_all([first_item, waiting_item])
            db.flush()
            job = PendingJob(
                paper_id=paper.id,
                task_type="paper_parse",
                idempotency_key=f"paper-lock-{uuid4()}",
                batch_item_id=first_item.id,
                status="processing",
                lease_owner="worker-a",
            )
            db.add(job)
            db.commit()
            first_batch_id, first_item_id, paper_id, waiting_batch_id = (
                first_batch.id,
                first_item.id,
                paper.id,
                waiting_batch.id,
            )

        terminal_has_job_lock = Event()
        release_terminal = Event()
        scheduler_finished = Event()

        def hold_terminal_locks() -> None:
            with session_factory() as db:
                db.execute(select(BatchRun).where(BatchRun.id == first_batch_id).with_for_update()).scalar_one()
                db.execute(select(PendingJob).where(PendingJob.batch_item_id == first_item_id).with_for_update()).scalar_one()
                db.execute(select(BatchItem).where(BatchItem.id == first_item_id).with_for_update()).scalar_one()
                terminal_has_job_lock.set()
                assert release_terminal.wait(timeout=5)
                db.execute(select(Paper).where(Paper.id == paper_id).with_for_update()).scalar_one()
                db.commit()

        def schedule_waiting_batch() -> list[int]:
            with session_factory() as db:
                result = BatchScheduler(db).schedule(waiting_batch_id)
                scheduler_finished.set()
                return result

        with ThreadPoolExecutor(max_workers=2) as executor:
            terminal = executor.submit(hold_terminal_locks)
            assert terminal_has_job_lock.wait(timeout=5)
            scheduler = executor.submit(schedule_waiting_batch)
            try:
                assert scheduler_finished.wait(timeout=2)
            finally:
                release_terminal.set()
            assert scheduler.result(timeout=10) == []
            terminal.result(timeout=10)
    finally:
        engine.dispose()


@pytest.mark.postgresql
def test_postgresql_batch_claim_and_cancellation_never_split_job_and_item_state() -> None:
    database_url = os.getenv("TEST_POSTGRES_URL")
    if not database_url:
        pytest.skip("TEST_POSTGRES_URL is required for the PostgreSQL integration test")
    completed = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "DATABASE_URL": database_url},
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    engine = create_engine(database_url)
    session_factory = sessionmaker(bind=engine)
    try:
        with session_factory() as db:
            project = Project(slug=f"claim-cancel-{uuid4()}", name="Claim Cancel")
            db.add(project)
            db.flush()
            paper = Paper(
                project_id=project.id,
                title="Claim Cancel",
                original_filename="claim-cancel.pdf",
                file_path=f"postgres/{uuid4()}.pdf",
                file_size=1,
                file_hash=(uuid4().hex + uuid4().hex)[:64],
                status="pending",
            )
            batch = BatchRun(
                project_id=project.id,
                submission_key=f"claim-cancel-{uuid4()}",
                source_root="/postgres",
                status="running",
                batch_concurrency=1,
                config_snapshot={"result_semantics": {"model": "claim-cancel-test"}},
                result_config_hash=result_config_hash({"result_semantics": {"model": "claim-cancel-test"}}),
            )
            db.add_all([paper, batch])
            db.flush()
            item = BatchItem(
                batch_run_id=batch.id,
                ordinal=0,
                source_relative_path="claim-cancel.pdf",
                source_sha256=paper.file_hash,
                source_size_bytes=1,
                paper_id=paper.id,
                status="queued",
            )
            db.add(item)
            db.flush()
            job = PendingJob(
                paper_id=paper.id,
                task_type="paper_parse",
                idempotency_key=f"claim-cancel-{uuid4()}",
                batch_item_id=item.id,
                status="pending",
            )
            db.add(job)
            db.commit()
            batch_id, item_id, job_id = batch.id, item.id, job.id

        barrier = Barrier(2)

        def claim() -> bool:
            with session_factory() as db:
                barrier.wait(timeout=5)
                claimed = JobRepository(db).claim(job_id, worker_id="claim-worker")
                db.commit()
                return claimed is not None

        def cancel() -> None:
            with session_factory() as db:
                barrier.wait(timeout=5)
                BatchLifecycleService(db).cancel(batch_id)

        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(claim)
            second = executor.submit(cancel)
            claim_succeeded = first.result(timeout=15)
            second.result(timeout=15)

        with session_factory() as db:
            job = db.get(PendingJob, job_id)
            item = db.get(BatchItem, item_id)
            batch = db.get(BatchRun, batch_id)
            assert job is not None and item is not None and batch is not None
            if claim_succeeded:
                assert job.status == "processing"
                assert item.status == "processing"
                assert batch.status == "cancelling"
            else:
                assert job.status == "cancelled"
                assert item.status == "cancelled"
                assert batch.status == "cancelled"
    finally:
        engine.dispose()


@pytest.mark.postgresql
def test_postgresql_stale_recovery_rechecks_a_job_renewed_by_another_session(monkeypatch) -> None:
    database_url = os.getenv("TEST_POSTGRES_URL")
    if not database_url:
        pytest.skip("TEST_POSTGRES_URL is required for the PostgreSQL integration test")
    completed = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "DATABASE_URL": database_url},
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    monkeypatch.setattr("app.queue.redis_queue.RedisQueue.enqueue", lambda _queue, _payload: pytest.fail("must not redispatch"))
    engine = create_engine(database_url)
    session_factory = sessionmaker(bind=engine)
    try:
        with session_factory() as db:
            project = Project(slug=f"recovery-race-{uuid4()}", name="Recovery Race")
            db.add(project)
            db.flush()
            paper = Paper(
                project_id=project.id,
                title="Recovery Race",
                original_filename="recovery-race.pdf",
                file_path=f"postgres/{uuid4()}.pdf",
                file_size=1,
                file_hash=(uuid4().hex + uuid4().hex)[:64],
                status="processing",
            )
            batch = BatchRun(
                project_id=project.id,
                submission_key=f"recovery-race-{uuid4()}",
                source_root="/postgres",
                status="running",
                batch_concurrency=1,
                config_snapshot={"result_semantics": {"model": "recovery-race"}},
                result_config_hash=result_config_hash({"result_semantics": {"model": "recovery-race"}}),
            )
            db.add_all([paper, batch])
            db.flush()
            item = BatchItem(
                batch_run_id=batch.id,
                ordinal=0,
                source_relative_path="recovery-race.pdf",
                source_sha256=paper.file_hash,
                source_size_bytes=1,
                paper_id=paper.id,
                status="processing",
                current_stage="parsing",
            )
            db.add(item)
            db.flush()
            job = PendingJob(
                paper_id=paper.id,
                task_type="paper_parse",
                idempotency_key=f"recovery-race-{uuid4()}",
                batch_item_id=item.id,
                status="processing",
                lease_owner="expired-worker",
                lease_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
            )
            db.add(job)
            db.commit()
            job_id = job.id

        original_lock = dispatcher._lock_job_for_recovery

        def renew_before_lock(db, stale_job_id: int):
            with session_factory() as competing_db:
                competing_job = competing_db.get(PendingJob, stale_job_id)
                assert competing_job is not None
                competing_job.lease_owner = "active-worker"
                competing_job.lease_expires_at = datetime.now(timezone.utc) + timedelta(minutes=15)
                competing_db.commit()
            return original_lock(db, stale_job_id)

        monkeypatch.setattr(dispatcher, "_lock_job_for_recovery", renew_before_lock)
        with session_factory() as db:
            assert dispatcher.dispatch_stale_pending_jobs(db, job_ids=[job_id]) == 0
            db.rollback()

        with session_factory() as db:
            job = db.get(PendingJob, job_id)
            assert job is not None
            assert job.status == "processing"
            assert job.lease_owner == "active-worker"
    finally:
        engine.dispose()


@pytest.mark.postgresql
def test_postgresql_cancelling_batch_never_redispatches_an_expired_processing_job(monkeypatch) -> None:
    database_url = os.getenv("TEST_POSTGRES_URL")
    if not database_url:
        pytest.skip("TEST_POSTGRES_URL is required for the PostgreSQL integration test")
    completed = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "DATABASE_URL": database_url},
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    queued: list[dict] = []
    monkeypatch.setattr("app.queue.redis_queue.RedisQueue.enqueue", lambda _queue, payload: queued.append(payload))
    engine = create_engine(database_url)
    session_factory = sessionmaker(bind=engine)
    try:
        with session_factory() as db:
            project = Project(slug=f"recovery-cancel-{uuid4()}", name="Recovery Cancel")
            db.add(project)
            db.flush()
            paper = Paper(
                project_id=project.id,
                title="Recovery Cancel",
                original_filename="recovery-cancel.pdf",
                file_path=f"postgres/{uuid4()}.pdf",
                file_size=1,
                file_hash=(uuid4().hex + uuid4().hex)[:64],
                status="processing",
            )
            config_snapshot = {"result_semantics": {"model": "recovery-cancel"}}
            batch = BatchRun(
                project_id=project.id,
                submission_key=f"recovery-cancel-{uuid4()}",
                source_root="/postgres",
                status="cancelling",
                batch_concurrency=1,
                config_snapshot=config_snapshot,
                result_config_hash=result_config_hash(config_snapshot),
            )
            db.add_all([paper, batch])
            db.flush()
            item = BatchItem(
                batch_run_id=batch.id,
                ordinal=0,
                source_relative_path="recovery-cancel.pdf",
                source_sha256=paper.file_hash,
                source_size_bytes=1,
                paper_id=paper.id,
                status="processing",
            )
            db.add(item)
            db.flush()
            job = PendingJob(
                paper_id=paper.id,
                task_type="paper_parse",
                idempotency_key=f"recovery-cancel-{uuid4()}",
                batch_item_id=item.id,
                status="processing",
                lease_owner="lost-worker",
                lease_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
            )
            db.add(job)
            db.commit()
            batch_id, item_id, job_id = batch.id, item.id, job.id

        with session_factory() as db:
            assert dispatcher.dispatch_stale_pending_jobs(db, job_ids=[job_id]) == 0

        with session_factory() as db:
            job = db.get(PendingJob, job_id)
            item = db.get(BatchItem, item_id)
            batch = db.get(BatchRun, batch_id)
            assert job is not None and item is not None and batch is not None
            assert queued == []
            assert job.status == "cancelled"
            assert item.status == "cancelled"
            assert batch.status == "cancelled"
    finally:
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

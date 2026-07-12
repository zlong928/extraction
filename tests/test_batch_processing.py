from __future__ import annotations

import hashlib
import json
import app.services.pdf.dispatcher as dispatcher
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.db import SessionLocal, create_db_and_tables
from app.models import (
    BatchEvent,
    BatchItem,
    BatchRun,
    ExtractionRun,
    Paper,
    PendingJob,
    Project,
    RunArtifact,
    StorageObject,
    StructuredResult,
)
from app.repositories import BatchRepository, JobClaim, JobRepository, LostJobLease
from app.services.batches import BatchLifecycleService, BatchScheduler, BatchSubmissionService, discover_pdfs, result_config_hash
from app.services.object_store import ObjectStore
from app.services.pdf.dispatcher import dispatch_stale_pending_jobs
from app.services.pdf.parse_service import PaperParseService
from app.services.pdf.pipeline import _parent_run_id
from app.services.pdf.upload_service import PaperUploadService
from app.services.storage import StorageService
from app.services.extraction_runs import create_extraction_run


def _sample_pdf(label: str) -> bytes:
    return (
        b"%PDF-1.4\n"
        + label.encode("utf-8")
        + b"\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Count 1/Kids[3 0 R]>>endobj\n"
        b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]>>endobj\n"
        b"trailer<</Root 1 0 R>>\n%%EOF\n"
    )


def _batch_config(**result_semantics: object) -> dict[str, object]:
    return {"result_semantics": result_semantics}


def _persist_raw_response_artifact(
    db,
    storage: StorageService,
    run: ExtractionRun,
    *,
    role: str = "model_raw_responses",
) -> StorageObject:
    raw_object = ObjectStore(db, storage.adapter).put_json(
        key=f"tests/runs/{run.id}/model-raw-responses.json",
        payload={"run_id": run.id, "responses": []},
        metadata={"role": role, "run_id": run.id},
    )
    db.add(
        RunArtifact(
            run_id=run.id,
            object_id=raw_object.id,
            role=role,
            filename="model-raw-responses.json",
        )
    )
    return raw_object


def test_batch_facts_enforce_statuses_lineage_and_restrict_deletes() -> None:
    create_db_and_tables()
    with SessionLocal() as db:
        project = Project(slug="batch-test", name="Batch Test")
        db.add(project)
        db.flush()
        batch = BatchRun(
            project_id=project.id,
            submission_key="submission-1",
            source_root="/incoming",
            batch_concurrency=2,
            config_snapshot={"result_config_hash": "a" * 64},
            result_config_hash="a" * 64,
        )
        db.add(batch)
        db.flush()
        item = BatchItem(
            batch_run_id=batch.id,
            ordinal=0,
            source_relative_path="paper.pdf",
            source_sha256="b" * 64,
            source_size_bytes=9,
        )
        db.add(item)
        db.flush()
        paper = Paper(
            project_id=project.id,
            title="Batch lineage",
            original_filename="lineage.pdf",
            file_path="papers/lineage.pdf",
            file_size=9,
            file_hash="c" * 64,
            status="pending",
        )
        db.add(paper)
        db.flush()
        first = PendingJob(
            paper_id=paper.id,
            task_type="paper_parse",
            idempotency_key="batch:1",
            batch_item_id=item.id,
        )
        db.add(first)
        db.flush()
        second = PendingJob(
            paper_id=paper.id,
            task_type="paper_parse",
            idempotency_key="batch:2",
            batch_item_id=item.id,
            retry_of_job_id=first.id,
        )
        db.add(second)
        db.add(BatchEvent(batch_run_id=batch.id, batch_item_id=item.id, event_type="item_registered", data={}))
        db.commit()

        assert first.batch_item_id == second.batch_item_id == item.id
        assert second.retry_of_job_id == first.id

        with pytest.raises(IntegrityError):
            db.execute(text("DELETE FROM batch_runs WHERE id = :id"), {"id": batch.id})
        db.rollback()

        with pytest.raises(IntegrityError):
            db.execute(text("DELETE FROM batch_items WHERE id = :id"), {"id": item.id})
        db.rollback()


@pytest.mark.parametrize(
    ("factory", "expected"),
    [
        (
            lambda project_id: BatchRun(
                project_id=project_id,
                submission_key="invalid-concurrency",
                source_root="/incoming",
                batch_concurrency=0,
                config_snapshot={},
                result_config_hash="a" * 64,
            ),
            "ck_batch_runs_concurrency_positive",
        ),
        (
            lambda project_id: BatchRun(
                project_id=project_id,
                submission_key="invalid-status",
                source_root="/incoming",
                status="unknown",
                batch_concurrency=1,
                config_snapshot={},
                result_config_hash="a" * 64,
            ),
            "ck_batch_runs_status",
        ),
    ],
)
def test_batch_run_database_constraints(factory, expected: str) -> None:
    create_db_and_tables()
    with SessionLocal() as db:
        project = Project(slug=f"batch-{expected}", name="Batch Test")
        db.add(project)
        db.flush()
        db.add(factory(project.id))
        with pytest.raises(IntegrityError, match=expected):
            db.commit()
        db.rollback()


def test_batch_item_database_status_constraint() -> None:
    create_db_and_tables()
    with SessionLocal() as db:
        project = Project(slug="batch-invalid-item", name="Batch Test")
        db.add(project)
        db.flush()
        batch = BatchRun(
            project_id=project.id,
            submission_key="invalid-item-status",
            source_root="/incoming",
            batch_concurrency=1,
            config_snapshot={},
            result_config_hash="a" * 64,
        )
        db.add(batch)
        db.flush()
        db.add(
            BatchItem(
                batch_run_id=batch.id,
                ordinal=0,
                source_relative_path="invalid.pdf",
                source_sha256="b" * 64,
                source_size_bytes=1,
                status="unknown",
            )
        )
        with pytest.raises(IntegrityError, match="ck_batch_items_status"):
            db.commit()
        db.rollback()


def test_discover_pdfs_orders_relative_paths_and_applies_limit(tmp_path: Path) -> None:
    (tmp_path / "z.pdf").write_bytes(_sample_pdf("z"))
    nested = tmp_path / "a"
    nested.mkdir()
    (nested / "b.PDF").write_bytes(_sample_pdf("b"))
    (nested / "ignored.txt").write_text("not a PDF", encoding="utf-8")

    first = discover_pdfs(tmp_path, limit=1)
    second = discover_pdfs(tmp_path, limit=1)

    assert [(item.ordinal, item.relative_path, item.sha256) for item in first] == [
        (0, "a/b.PDF", hashlib.sha256(_sample_pdf("b")).hexdigest())
    ]
    assert [(item.ordinal, item.relative_path, item.sha256) for item in second] == [
        (item.ordinal, item.relative_path, item.sha256) for item in first
    ]


def test_batch_scheduler_commits_only_the_available_job_window(monkeypatch) -> None:
    queued: list[dict] = []
    monkeypatch.setattr("app.queue.redis_queue.RedisQueue.enqueue", lambda _queue, payload: queued.append(payload))
    create_db_and_tables()
    with SessionLocal() as db:
        project = Project(slug="scheduler-window", name="Scheduler Window")
        db.add(project)
        db.flush()
        batch = BatchRun(
            project_id=project.id,
            submission_key="scheduler-window",
            source_root="/incoming",
            batch_concurrency=2,
            config_snapshot=_batch_config(model="test-model"),
            result_config_hash=result_config_hash(_batch_config(model="test-model")),
        )
        db.add(batch)
        db.flush()
        for ordinal in range(4):
            paper = Paper(
                project_id=project.id,
                title=f"Scheduled {ordinal}",
                original_filename=f"scheduled-{ordinal}.pdf",
                file_path=f"papers/scheduled-{ordinal}.pdf",
                file_size=1,
                file_hash=f"{ordinal + 10:064x}",
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

        scheduled = BatchScheduler(db).schedule(batch.id)
        db.refresh(batch)
        jobs = (
            db.query(PendingJob)
            .join(BatchItem, PendingJob.batch_item_id == BatchItem.id)
            .filter(BatchItem.batch_run_id == batch.id)
            .all()
        )
        items = db.query(BatchItem).filter(BatchItem.batch_run_id == batch.id).order_by(BatchItem.ordinal).all()

        assert len(scheduled) == 2
        assert len(jobs) == 2
        assert {job.status for job in jobs} == {"redis_dispatched"}
        assert [item.status for item in items] == ["queued", "queued", "pending", "pending"]
        assert batch.status == "running"
        assert {payload["job_id"] for payload in queued} == {job.id for job in jobs}


def test_batch_redis_push_failure_keeps_the_committed_job_for_stale_recovery(monkeypatch) -> None:
    create_db_and_tables()
    with SessionLocal() as db:
        project = Project(slug="scheduler-recovery", name="Scheduler Recovery")
        db.add(project)
        db.flush()
        paper = Paper(
            project_id=project.id,
            title="Recoverable",
            original_filename="recoverable.pdf",
            file_path="papers/recoverable.pdf",
            file_size=1,
            file_hash="d" * 64,
            status="pending",
        )
        db.add(paper)
        db.flush()
        batch = BatchRun(
            project_id=project.id,
            submission_key="scheduler-recovery",
            source_root="/incoming",
            batch_concurrency=1,
            config_snapshot=_batch_config(model="test-model"),
            result_config_hash=result_config_hash(_batch_config(model="test-model")),
        )
        db.add(batch)
        db.flush()
        item = BatchItem(
            batch_run_id=batch.id,
            ordinal=0,
            source_relative_path="recoverable.pdf",
            source_sha256=paper.file_hash,
            source_size_bytes=1,
            paper_id=paper.id,
        )
        db.add(item)
        db.commit()
        monkeypatch.setattr(
            "app.queue.redis_queue.RedisQueue.enqueue",
            lambda _queue, _payload: (_ for _ in ()).throw(RuntimeError("redis unavailable")),
        )

        scheduled = BatchScheduler(db).schedule(batch.id)
        job = db.get(PendingJob, scheduled[0])
        assert job is not None and job.status == "pending"
        assert db.get(BatchItem, item.id).status == "queued"
        job.updated_at = datetime.now(timezone.utc) - timedelta(minutes=16)
        db.commit()

        redispatched: list[dict] = []
        monkeypatch.setattr("app.queue.redis_queue.RedisQueue.enqueue", lambda _queue, payload: redispatched.append(payload))
        assert dispatch_stale_pending_jobs(db) == 1
        db.refresh(job)

        assert job.status == "redis_dispatched"
        assert redispatched == [{"schema_version": 2, "task_type": "paper_parse", "job_id": job.id}]
        assert db.query(PendingJob).filter(PendingJob.batch_item_id == item.id).count() == 1


def test_expired_batch_job_commits_retry_and_item_queue_before_redis_push(monkeypatch) -> None:
    create_db_and_tables()
    with SessionLocal() as db:
        project = Project(slug="scheduler-lease", name="Scheduler Lease")
        db.add(project)
        db.flush()
        paper = Paper(
            project_id=project.id,
            title="Lease",
            original_filename="lease.pdf",
            file_path="papers/lease.pdf",
            file_size=1,
            file_hash="e" * 64,
            status="processing",
        )
        batch = BatchRun(
            project_id=project.id,
            submission_key="scheduler-lease",
            source_root="/incoming",
            status="running",
            batch_concurrency=1,
            config_snapshot=_batch_config(model="test-model"),
            result_config_hash=result_config_hash(_batch_config(model="test-model")),
        )
        db.add_all([paper, batch])
        db.flush()
        item = BatchItem(
            batch_run_id=batch.id,
            ordinal=0,
            source_relative_path="lease.pdf",
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
            idempotency_key="lease-expired",
            batch_item_id=item.id,
            status="processing",
            lease_owner="worker",
            lease_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        )
        db.add(job)
        db.commit()

        observed: list[tuple[str, str]] = []

        def assert_committed_before_push(_queue, _payload) -> None:
            with SessionLocal() as observer:
                persisted_job = observer.get(PendingJob, job.id)
                persisted_item = observer.get(BatchItem, item.id)
                assert persisted_job is not None and persisted_item is not None
                observed.append((persisted_job.status, persisted_item.status))

        monkeypatch.setattr("app.queue.redis_queue.RedisQueue.enqueue", assert_committed_before_push)

        assert dispatch_stale_pending_jobs(db) == 1
        assert observed == [("retry", "queued")]
        db.refresh(job)
        assert job.status == "redis_dispatched"


def test_stale_recovery_does_not_reassign_processing_job_owned_by_current_worker(monkeypatch) -> None:
    create_db_and_tables()
    queued: list[dict] = []
    monkeypatch.setattr("app.queue.redis_queue.RedisQueue.enqueue", lambda _queue, payload: queued.append(payload))
    with SessionLocal() as db:
        paper = Paper(
            title="Current worker",
            original_filename="current-worker.pdf",
            file_path="papers/current-worker.pdf",
            file_size=1,
            file_hash="1" * 64,
            status="processing",
        )
        db.add(paper)
        db.flush()
        job = PendingJob(
            paper_id=paper.id,
            task_type="paper_parse",
            idempotency_key="current-worker-processing",
            status="processing",
            lease_owner="host:123",
            lease_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        )
        db.add(job)
        db.commit()

        assert dispatch_stale_pending_jobs(
            db,
            job_ids=[job.id],
            protected_processing_owners={"host:123"},
        ) == 0
        db.refresh(job)

        assert job.status == "processing"
        assert job.lease_owner == "host:123"
        assert queued == []


def test_stale_recovery_rechecks_a_job_that_was_claimed_and_renewed(monkeypatch) -> None:
    create_db_and_tables()
    with SessionLocal() as db:
        paper = Paper(
            title="Fresh claim",
            original_filename="fresh-claim.pdf",
            file_path="papers/fresh-claim.pdf",
            file_size=1,
            file_hash="9" * 64,
            status="processing",
        )
        db.add(paper)
        db.flush()
        job = PendingJob(
            paper_id=paper.id,
            task_type="paper_parse",
            idempotency_key="fresh-claim",
            status="pending",
        )
        db.add(job)
        db.commit()
        job.updated_at = datetime.now(timezone.utc) - timedelta(minutes=16)
        db.commit()

        original_lock = dispatcher._lock_job_for_recovery

        def claim_and_renew_before_lock(current_db, job_id: int):
            with SessionLocal() as competing_db:
                fresh = competing_db.get(PendingJob, job_id)
                assert fresh is not None
                fresh.status = "processing"
                fresh.lease_owner = "competing-worker"
                fresh.lease_expires_at = datetime.now(timezone.utc) + timedelta(minutes=15)
                competing_db.commit()
            return original_lock(current_db, job_id)

        queued: list[dict] = []
        monkeypatch.setattr(dispatcher, "_lock_job_for_recovery", claim_and_renew_before_lock)
        monkeypatch.setattr("app.queue.redis_queue.RedisQueue.enqueue", lambda _queue, payload: queued.append(payload))

        assert dispatch_stale_pending_jobs(db) == 0
        db.refresh(job)
        assert job.status == "processing"
        assert queued == []


def test_stale_recovery_cancels_an_expired_job_when_its_batch_is_cancelling(monkeypatch) -> None:
    queued: list[dict] = []
    monkeypatch.setattr("app.queue.redis_queue.RedisQueue.enqueue", lambda _queue, payload: queued.append(payload))
    create_db_and_tables()
    with SessionLocal() as db:
        project = Project(slug="recovery-cancelling", name="Recovery Cancelling")
        db.add(project)
        db.flush()
        paper = Paper(
            project_id=project.id,
            title="Cancelling",
            original_filename="cancelling.pdf",
            file_path="papers/cancelling.pdf",
            file_size=1,
            file_hash="8" * 64,
            status="processing",
        )
        batch = BatchRun(
            project_id=project.id,
            submission_key="recovery-cancelling",
            source_root="/incoming",
            status="cancelling",
            batch_concurrency=1,
            config_snapshot=_batch_config(model="test-model"),
            result_config_hash=result_config_hash(_batch_config(model="test-model")),
        )
        db.add_all([paper, batch])
        db.flush()
        item = BatchItem(
            batch_run_id=batch.id,
            ordinal=0,
            source_relative_path="cancelling.pdf",
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
            idempotency_key="recovery-cancelling-job",
            batch_item_id=item.id,
            status="processing",
            lease_owner="lost-worker",
            lease_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        )
        db.add(job)
        db.commit()

        assert dispatch_stale_pending_jobs(db) == 0
        db.refresh(job)
        db.refresh(item)
        db.refresh(batch)

        assert queued == []
        assert job.status == "cancelled"
        assert item.status == "cancelled"
        assert batch.status == "cancelled"


def test_stale_recovery_cancellation_of_deleted_batch_paper_refreshes_aggregate() -> None:
    create_db_and_tables()
    with SessionLocal() as db:
        project = Project(slug="recovery-deleted", name="Recovery Deleted")
        db.add(project)
        db.flush()
        paper = Paper(
            project_id=project.id,
            title="Deleted",
            original_filename="deleted.pdf",
            file_path="papers/deleted.pdf",
            file_size=1,
            file_hash="7" * 64,
            status="deleted",
        )
        batch = BatchRun(
            project_id=project.id,
            submission_key="recovery-deleted",
            source_root="/incoming",
            status="running",
            batch_concurrency=1,
            config_snapshot=_batch_config(model="test-model"),
            result_config_hash=result_config_hash(_batch_config(model="test-model")),
        )
        db.add_all([paper, batch])
        db.flush()
        item = BatchItem(
            batch_run_id=batch.id,
            ordinal=0,
            source_relative_path="deleted.pdf",
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
            idempotency_key="recovery-deleted-job",
            batch_item_id=item.id,
            status="pending",
        )
        db.add(job)
        db.commit()
        job.updated_at = datetime.now(timezone.utc) - timedelta(minutes=16)
        db.commit()

        assert dispatch_stale_pending_jobs(db) == 0
        db.refresh(job)
        db.refresh(item)
        db.refresh(batch)

        assert job.status == "cancelled"
        assert job.completed_at is not None
        assert item.status == "cancelled"
        assert batch.status == "cancelled"


def test_stale_claim_token_cannot_finalize_a_new_worker_lease() -> None:
    create_db_and_tables()
    with SessionLocal() as db:
        project = Project(slug="claim-token", name="Claim Token")
        db.add(project)
        db.flush()
        paper = Paper(
            project_id=project.id,
            title="Token",
            original_filename="token.pdf",
            file_path="papers/token.pdf",
            file_size=1,
            file_hash="6" * 64,
            status="processing",
        )
        batch = BatchRun(
            project_id=project.id,
            submission_key="claim-token",
            source_root="/incoming",
            status="running",
            batch_concurrency=1,
            config_snapshot=_batch_config(model="test-model"),
            result_config_hash=result_config_hash(_batch_config(model="test-model")),
        )
        db.add_all([paper, batch])
        db.flush()
        item = BatchItem(
            batch_run_id=batch.id,
            ordinal=0,
            source_relative_path="token.pdf",
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
            idempotency_key="claim-token-job",
            batch_item_id=item.id,
            status="pending",
        )
        db.add(job)
        db.commit()

        first = JobRepository(db).claim(job.id, worker_id="worker-a")
        assert first is not None
        stale_claim = JobClaim.from_job(first)
        db.commit()
        with SessionLocal() as competing_db:
            replacement = competing_db.get(PendingJob, job.id)
            replacement_item = competing_db.get(BatchItem, item.id)
            assert replacement is not None and replacement_item is not None
            replacement.status = "retry"
            replacement.lease_owner = None
            replacement.lease_expires_at = None
            replacement_item.status = "queued"
            competing_db.commit()
            second = JobRepository(competing_db).claim(job.id, worker_id="worker-b")
            assert second is not None
            competing_db.commit()

        with pytest.raises(LostJobLease):
            JobRepository(db).complete(stale_claim)
        db.rollback()
        db.refresh(job)
        db.refresh(item)
        assert job.status == "processing"
        assert job.lease_owner == "worker-b"
        assert item.status == "processing"


@pytest.mark.parametrize(("terminal", "expected_status"), [("complete", "done"), ("fail", "failed")])
def test_frozen_claim_finalizes_non_batch_job(terminal: str, expected_status: str) -> None:
    create_db_and_tables()
    with SessionLocal() as db:
        paper = Paper(
            title="Non batch token",
            original_filename="non-batch-token.pdf",
            file_path="papers/non-batch-token.pdf",
            file_size=1,
            file_hash=("4" if terminal == "complete" else "3") * 64,
            status="processing",
        )
        db.add(paper)
        db.flush()
        job = PendingJob(
            paper_id=paper.id,
            task_type="paper_parse",
            idempotency_key=f"non-batch-token-{terminal}",
            status="pending",
        )
        db.add(job)
        db.commit()

        claimed = JobRepository(db).claim(job.id, worker_id="worker")
        assert claimed is not None
        claim = JobClaim.from_job(claimed)
        db.commit()
        if terminal == "complete":
            JobRepository(db).complete(claim)
        else:
            JobRepository(db).fail(claim, "failed")
        db.commit()
        db.refresh(job)

        assert job.status == expected_status


def test_scheduler_propagates_compatible_failure_to_waiting_same_pdf(monkeypatch) -> None:
    monkeypatch.setattr("app.queue.redis_queue.RedisQueue.enqueue", lambda _queue, _payload: None)
    create_db_and_tables()
    with SessionLocal() as db:
        project = Project(slug="compatible-failure", name="Compatible Failure")
        config_snapshot = _batch_config(model="failure-model")
        db.add(project)
        db.flush()
        paper = Paper(
            project_id=project.id,
            title="Shared",
            original_filename="shared.pdf",
            file_path="papers/shared.pdf",
            file_size=1,
            file_hash="b" * 64,
            status="failed",
        )
        batch = BatchRun(
            project_id=project.id,
            submission_key="compatible-failure",
            source_root="/incoming",
            status="running",
            batch_concurrency=1,
            config_snapshot=config_snapshot,
            result_config_hash=result_config_hash(config_snapshot),
        )
        db.add_all([paper, batch])
        db.flush()
        failed_item = BatchItem(
            batch_run_id=batch.id,
            ordinal=0,
            source_relative_path="first/shared.pdf",
            source_sha256=paper.file_hash,
            source_size_bytes=1,
            paper_id=paper.id,
            status="failed",
            current_stage="failed",
            error_message="MinerU unavailable",
        )
        waiting_item = BatchItem(
            batch_run_id=batch.id,
            ordinal=1,
            source_relative_path="second/shared.pdf",
            source_sha256=paper.file_hash,
            source_size_bytes=1,
            paper_id=paper.id,
        )
        db.add_all([failed_item, waiting_item])
        db.flush()
        failed_job = PendingJob(
            paper_id=paper.id,
            task_type="paper_parse",
            idempotency_key="compatible-failure-first",
            batch_item_id=failed_item.id,
            status="failed",
            error_message="MinerU unavailable",
            completed_at=datetime.now(timezone.utc),
        )
        db.add(failed_job)
        db.commit()

        assert BatchScheduler(db).schedule(batch.id) == []
        db.refresh(waiting_item)
        db.refresh(batch)

        assert waiting_item.status == "failed"
        assert waiting_item.current_stage == "compatible_failure"
        assert waiting_item.error_message == "MinerU unavailable"
        assert batch.status == "failed"
        event = db.query(BatchEvent).filter(BatchEvent.batch_item_id == waiting_item.id).one()
        assert event.data["reason"] == "compatible_execution_failed"

        scheduled = BatchLifecycleService(db).retry_failed_items(batch.id, [waiting_item.id])
        retry_job = db.get(PendingJob, scheduled[0])
        assert retry_job is not None
        assert retry_job.retry_of_job_id == failed_job.id


def test_parent_run_uses_nearest_ancestor_when_intermediate_retry_has_no_run() -> None:
    create_db_and_tables()
    with SessionLocal() as db:
        project = Project(slug="retry-lineage", name="Retry Lineage")
        paper = Paper(
            project=project,
            title="Retry lineage",
            original_filename="retry-lineage.pdf",
            file_path="papers/retry-lineage.pdf",
            file_size=1,
            file_hash="9" * 64,
        )
        stored_input = StorageObject(
            object_key="tests/retry-lineage.pdf",
            uri="file:///tests/retry-lineage.pdf",
            sha256=paper.file_hash,
            size_bytes=1,
            media_type="application/pdf",
        )
        db.add_all([paper, stored_input])
        db.flush()
        first = PendingJob(
            paper_id=paper.id,
            task_type="paper_parse",
            idempotency_key="retry-lineage-first",
            status="failed",
        )
        db.add(first)
        db.flush()
        first_run = ExtractionRun(
            task_id=first.id,
            paper_id=paper.id,
            input_object_id=stored_input.id,
            attempt=1,
            model_provider="test",
            model_name="test",
            model_version="test",
            prompt_version="test",
            pipeline_version="test",
            config_snapshot={},
            status="failed",
        )
        db.add(first_run)
        db.flush()
        intermediate = PendingJob(
            paper_id=paper.id,
            task_type="paper_parse",
            idempotency_key="retry-lineage-intermediate",
            status="failed",
            retry_of_job_id=first.id,
        )
        db.add(intermediate)
        db.flush()
        latest = PendingJob(
            paper_id=paper.id,
            task_type="paper_parse",
            idempotency_key="retry-lineage-latest",
            status="pending",
            retry_of_job_id=intermediate.id,
        )
        db.add(latest)
        db.flush()

        assert _parent_run_id(db, latest) == first_run.id


def test_failed_pipeline_summary_commits_run_and_batch_terminal_facts(monkeypatch) -> None:
    create_db_and_tables()
    with SessionLocal() as db:
        project = Project(slug="failed-summary", name="Failed Summary")
        paper = Paper(
            project=project,
            title="Failed summary",
            original_filename="failed-summary.pdf",
            file_path="papers/failed-summary.pdf",
            file_size=1,
            file_hash="8" * 64,
            status="processing",
            mineru_content_list_path="papers/failed-summary/content_list.json",
        )
        stored_input = StorageObject(
            object_key="papers/failed-summary/content_list.json",
            uri="file:///papers/failed-summary/content_list.json",
            sha256="8" * 64,
            size_bytes=1,
            media_type="application/json",
        )
        batch = BatchRun(
            project=project,
            submission_key="failed-summary",
            source_root="/incoming",
            status="running",
            batch_concurrency=1,
            config_snapshot=_batch_config(model="test-model"),
            result_config_hash=result_config_hash(_batch_config(model="test-model")),
        )
        db.add_all([paper, stored_input, batch])
        db.flush()
        paper.mineru_content_object_id = stored_input.id
        item = BatchItem(
            batch_run_id=batch.id,
            ordinal=0,
            source_relative_path="failed-summary.pdf",
            source_sha256=paper.file_hash,
            source_size_bytes=1,
            paper_id=paper.id,
            status="queued",
        )
        db.add(item)
        db.flush()
        pending = PendingJob(
            paper_id=paper.id,
            task_type="paper_parse",
            idempotency_key="failed-summary",
            batch_item_id=item.id,
        )
        db.add(pending)
        db.commit()
        claimed = JobRepository(db).claim(pending.id, worker_id="failed-summary-worker")
        assert claimed is not None
        claim = JobClaim.from_job(claimed)
        run = create_extraction_run(
            db,
            job=claim,
            paper=paper,
            input_object=stored_input,
            config_snapshot={"result_config_hash": batch.result_config_hash},
        )
        db.commit()

        def fail_pipeline(_paper, *, job):
            repository = JobRepository(db)
            context = repository.lock_terminal_context(job)
            assert context.run is not None
            context.run.status = "failed"
            context.run.error_message = "No publishable result"
            context.run.completed_at = datetime.now(timezone.utc)
            repository.fail_terminal(context, "No publishable result")
            return {"status": "failed"}

        monkeypatch.setattr("app.services.pdf.parse_service.run_chart_only_for_paper", fail_pipeline)
        result = PaperParseService(db)._resume_running_extraction(paper, claim)

        db.refresh(run)
        db.refresh(pending)
        db.refresh(item)
        db.refresh(batch)
        assert result.status == "failed"
        assert run.status == "failed"
        assert pending.status == "failed"
        assert item.status == "failed"
        assert batch.status == "failed"


def test_open_batch_scan_skips_full_windows_so_later_batches_are_not_starved() -> None:
    create_db_and_tables()
    with SessionLocal() as db:
        project = Project(slug="scheduler-fairness", name="Scheduler Fairness")
        config_snapshot = _batch_config(model="fairness-model")
        db.add(project)
        db.flush()
        full_batch = BatchRun(
            project_id=project.id,
            submission_key="scheduler-full",
            source_root="/incoming",
            batch_concurrency=1,
            config_snapshot=config_snapshot,
            result_config_hash=result_config_hash(config_snapshot),
        )
        available_batch = BatchRun(
            project_id=project.id,
            submission_key="scheduler-available",
            source_root="/incoming",
            batch_concurrency=1,
            config_snapshot=config_snapshot,
            result_config_hash=result_config_hash(config_snapshot),
        )
        paper = Paper(
            project_id=project.id,
            title="Full",
            original_filename="full.pdf",
            file_path="papers/full.pdf",
            file_size=1,
            file_hash="d" * 64,
            status="pending",
        )
        db.add_all([full_batch, available_batch, paper])
        db.flush()
        item = BatchItem(
            batch_run_id=full_batch.id,
            ordinal=0,
            source_relative_path="full.pdf",
            source_sha256=paper.file_hash,
            source_size_bytes=1,
            paper_id=paper.id,
            status="queued",
        )
        db.add(item)
        db.flush()
        db.add(
            PendingJob(
                paper_id=paper.id,
                task_type="paper_parse",
                idempotency_key="scheduler-full-job",
                batch_item_id=item.id,
                status="redis_dispatched",
            )
        )
        db.commit()

        assert BatchRepository(db).active_batch_run_ids() == [available_batch.id]


@pytest.mark.parametrize(
    ("terminal", "expected_item", "expected_batch"),
    [("complete", "succeeded", "succeeded"), ("partial_failure", "failed", "failed"), ("fail", "failed", "failed")],
)
def test_batch_job_claim_and_terminal_transition_update_batch_facts(
    terminal: str, expected_item: str, expected_batch: str
) -> None:
    create_db_and_tables()
    with SessionLocal() as db:
        project = Project(slug=f"batch-terminal-{terminal}", name="Batch Terminal")
        db.add(project)
        db.flush()
        paper = Paper(
            project_id=project.id,
            title="Terminal",
            original_filename="terminal.pdf",
            file_path="papers/terminal.pdf",
            file_size=1,
            file_hash=("f" if terminal == "complete" else "a") * 64,
            status="pending",
        )
        batch = BatchRun(
            project_id=project.id,
            submission_key=f"batch-terminal-{terminal}",
            source_root="/incoming",
            status="running",
            batch_concurrency=1,
            config_snapshot=_batch_config(model="test-model"),
            result_config_hash=result_config_hash(_batch_config(model="test-model")),
        )
        db.add_all([paper, batch])
        db.flush()
        item = BatchItem(
            batch_run_id=batch.id,
            ordinal=0,
            source_relative_path="terminal.pdf",
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
            idempotency_key=f"batch-terminal-{terminal}",
            batch_item_id=item.id,
            status="pending",
        )
        db.add(job)
        db.commit()

        repository = JobRepository(db)
        claimed = repository.claim(job.id, worker_id="batch-worker")
        assert claimed is not None
        db.commit()
        db.refresh(item)
        assert item.status == "processing"
        assert item.current_stage == "parsing"

        if terminal != "fail":
            stored_input = StorageObject(
                object_key=f"tests/batch-terminal-{terminal}.pdf",
                uri=f"file:///tests/batch-terminal-{terminal}.pdf",
                sha256=paper.file_hash,
                size_bytes=1,
                media_type="application/pdf",
            )
            db.add(stored_input)
            db.flush()
            run = create_extraction_run(
                db,
                job=claimed,
                paper=paper,
                input_object=stored_input,
                config_snapshot={"result_config_hash": batch.result_config_hash},
            )
            run.status = "succeeded" if terminal == "complete" else "partial_failure"
            run.completed_at = datetime.now(timezone.utc)
            db.flush()
            repository.complete(claimed)
        else:
            repository.fail(claimed, "mineru failed")
        db.commit()
        db.refresh(item)
        db.refresh(batch)
        db.refresh(job)

        assert item.status == expected_item
        assert batch.status == expected_batch
        assert job.status == ("failed" if terminal == "fail" else "done")
        assert db.query(BatchEvent).filter(BatchEvent.batch_item_id == item.id).count() == 2
        if terminal == "partial_failure":
            scheduled = BatchLifecycleService(db).retry_failed_items(batch.id, [item.id])
            retry_job = db.get(PendingJob, scheduled[0])
            assert retry_job is not None
            assert retry_job.retry_of_job_id == job.id


def test_batch_job_cannot_complete_before_extraction_run_exists() -> None:
    create_db_and_tables()
    with SessionLocal() as db:
        project = Project(slug="batch-no-run", name="Batch No Run")
        paper = Paper(
            project=project,
            title="No Run",
            original_filename="no-run.pdf",
            file_path="papers/no-run.pdf",
            file_size=1,
            file_hash="7" * 64,
        )
        batch = BatchRun(
            project=project,
            submission_key="batch-no-run",
            source_root="/incoming",
            status="running",
            batch_concurrency=1,
            config_snapshot=_batch_config(model="test-model"),
            result_config_hash=result_config_hash(_batch_config(model="test-model")),
        )
        db.add_all([paper, batch])
        db.flush()
        item = BatchItem(
            batch_run_id=batch.id,
            ordinal=0,
            source_relative_path="no-run.pdf",
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
            idempotency_key="batch-no-run",
            batch_item_id=item.id,
        )
        db.add(job)
        db.commit()
        claimed = JobRepository(db).claim(job.id, worker_id="batch-worker")
        assert claimed is not None
        db.commit()

        with pytest.raises(ValueError, match="ExtractionRun"):
            JobRepository(db).complete(claimed)


def test_batch_cancellation_stops_unclaimed_jobs_and_unscheduled_items(monkeypatch) -> None:
    monkeypatch.setattr("app.queue.redis_queue.RedisQueue.enqueue", lambda _queue, _payload: None)
    create_db_and_tables()
    with SessionLocal() as db:
        project = Project(slug="batch-cancel", name="Batch Cancel")
        db.add(project)
        db.flush()
        batch = BatchRun(
            project_id=project.id,
            submission_key="batch-cancel",
            source_root="/incoming",
            status="running",
            batch_concurrency=1,
            config_snapshot=_batch_config(model="test-model"),
            result_config_hash=result_config_hash(_batch_config(model="test-model")),
        )
        db.add(batch)
        db.flush()
        paper_ids: list[int] = []
        for ordinal in range(2):
            paper = Paper(
                project_id=project.id,
                title=f"Cancel {ordinal}",
                original_filename=f"cancel-{ordinal}.pdf",
                file_path=f"papers/cancel-{ordinal}.pdf",
                file_size=1,
                file_hash=f"{ordinal + 70:064x}",
                status="pending",
            )
            db.add(paper)
            db.flush()
            paper_ids.append(paper.id)
            db.add(
                BatchItem(
                    batch_run_id=batch.id,
                    ordinal=ordinal,
                    source_relative_path=f"cancel-{ordinal}.pdf",
                    source_sha256=paper.file_hash,
                    source_size_bytes=1,
                    paper_id=paper.id,
                    status="queued" if ordinal == 0 else "pending",
                )
            )
        db.flush()
        queued_item = db.query(BatchItem).filter(BatchItem.batch_run_id == batch.id, BatchItem.ordinal == 0).one()
        db.add(
            PendingJob(
                paper_id=paper_ids[0],
                task_type="paper_parse",
                idempotency_key="batch-cancel-job",
                batch_item_id=queued_item.id,
                status="pending",
            )
        )
        db.commit()

        cancelled = BatchLifecycleService(db).cancel(batch.id)
        items = db.query(BatchItem).filter(BatchItem.batch_run_id == batch.id).all()
        job = db.query(PendingJob).filter(PendingJob.batch_item_id == queued_item.id).one()

        assert cancelled.status == "cancelled"
        assert {item.status for item in items} == {"cancelled"}
        assert job.status == "cancelled"
        assert BatchScheduler(db).schedule(batch.id) == []


def test_batch_retry_creates_lineage_job_for_a_failed_item(monkeypatch) -> None:
    monkeypatch.setattr("app.queue.redis_queue.RedisQueue.enqueue", lambda _queue, _payload: None)
    create_db_and_tables()
    with SessionLocal() as db:
        project = Project(slug="batch-retry", name="Batch Retry")
        db.add(project)
        db.flush()
        paper = Paper(
            project_id=project.id,
            title="Retry",
            original_filename="retry.pdf",
            file_path="papers/retry.pdf",
            file_size=1,
            file_hash="b" * 64,
            status="failed",
        )
        batch = BatchRun(
            project_id=project.id,
            submission_key="batch-retry",
            source_root="/incoming",
            status="failed",
            batch_concurrency=1,
            config_snapshot=_batch_config(model="test-model"),
            result_config_hash=result_config_hash(_batch_config(model="test-model")),
        )
        db.add_all([paper, batch])
        db.flush()
        item = BatchItem(
            batch_run_id=batch.id,
            ordinal=0,
            source_relative_path="retry.pdf",
            source_sha256=paper.file_hash,
            source_size_bytes=1,
            paper_id=paper.id,
            status="failed",
            current_stage="failed",
            error_message="MinerU failed",
        )
        db.add(item)
        db.flush()
        first = PendingJob(
            paper_id=paper.id,
            task_type="paper_parse",
            idempotency_key="batch-retry-first",
            batch_item_id=item.id,
            status="failed",
        )
        db.add(first)
        db.commit()
        assert db.query(ExtractionRun).filter(ExtractionRun.task_id == first.id).count() == 0

        scheduled = BatchLifecycleService(db).retry_failed_items(batch.id, [item.id])
        db.refresh(item)
        db.refresh(batch)
        second = db.get(PendingJob, scheduled[0])

        assert second is not None and second.retry_of_job_id == first.id
        assert db.query(ExtractionRun).filter(ExtractionRun.task_id == second.id).count() == 0
        assert second.attempt == 2
        assert item.status == "queued"
        assert batch.status == "running"


def test_retry_waiting_for_another_active_job_preserves_explicit_retry_lineage(monkeypatch) -> None:
    monkeypatch.setattr("app.queue.redis_queue.RedisQueue.enqueue", lambda _queue, _payload: None)
    create_db_and_tables()
    with SessionLocal() as db:
        project = Project(slug="retry-waiting", name="Retry Waiting")
        config_snapshot = _batch_config(model="retry-model")
        db.add(project)
        db.flush()
        paper = Paper(
            project_id=project.id,
            title="Retry",
            original_filename="retry.pdf",
            file_path="papers/retry-waiting.pdf",
            file_size=1,
            file_hash="5" * 64,
            status="failed",
        )
        batch = BatchRun(
            project_id=project.id,
            submission_key="retry-waiting",
            source_root="/incoming",
            status="failed",
            batch_concurrency=1,
            config_snapshot=config_snapshot,
            result_config_hash=result_config_hash(config_snapshot),
        )
        db.add_all([paper, batch])
        db.flush()
        item = BatchItem(
            batch_run_id=batch.id,
            ordinal=0,
            source_relative_path="retry.pdf",
            source_sha256=paper.file_hash,
            source_size_bytes=1,
            paper_id=paper.id,
            status="failed",
            current_stage="failed",
        )
        db.add(item)
        db.flush()
        old_job = PendingJob(
            paper_id=paper.id,
            task_type="paper_parse",
            idempotency_key="retry-waiting-old",
            batch_item_id=item.id,
            status="failed",
        )
        blocking_job = PendingJob(
            paper_id=paper.id,
            task_type="paper_parse",
            idempotency_key="retry-waiting-blocking",
            status="pending",
        )
        db.add_all([old_job, blocking_job])
        db.commit()

        assert BatchLifecycleService(db).retry_failed_items(batch.id, [item.id]) == []
        db.refresh(item)
        db.refresh(batch)
        assert item.current_stage == "retry_waiting_for_active_parse"
        assert batch.status == "running"

        stored_input = StorageObject(
            object_key="tests/retry-waiting.pdf",
            uri="file:///tests/retry-waiting.pdf",
            sha256=paper.file_hash,
            size_bytes=paper.file_size,
            media_type="application/pdf",
        )
        db.add(stored_input)
        db.flush()
        old_failed_run = ExtractionRun(
            task_id=old_job.id,
            paper_id=paper.id,
            input_object_id=stored_input.id,
            attempt=1,
            model_provider="test",
            model_name="test",
            model_version="test",
            prompt_version="test",
            pipeline_version="test",
            config_snapshot={"result_config_hash": batch.result_config_hash},
            status="failed",
            error_message="MinerU failed after extraction began",
            completed_at=datetime.now(timezone.utc),
        )
        db.add(old_failed_run)
        db.flush()
        successful_run = ExtractionRun(
            task_id=blocking_job.id,
            paper_id=paper.id,
            input_object_id=stored_input.id,
            attempt=1,
            model_provider="test",
            model_name="test",
            model_version="test",
            prompt_version="test",
            pipeline_version="test",
            config_snapshot={"result_config_hash": batch.result_config_hash},
            status="running",
        )
        db.add(successful_run)
        db.flush()
        db.add_all(
            [
                StructuredResult(
                    run_id=successful_run.id,
                    paper_id=paper.id,
                    result_type="chart_fact",
                    natural_key="successful-blocker",
                    schema_version="normalized-result.v1",
                    content_hash="a" * 64,
                    payload={"value": 1},
                ),
                RunArtifact(
                    run_id=successful_run.id,
                    object_id=stored_input.id,
                    role="model_raw_responses",
                    filename="successful-blocker.json",
                ),
            ]
        )
        db.flush()
        successful_run.status = "succeeded"
        successful_run.completed_at = datetime.now(timezone.utc)
        blocking_job.status = "done"
        db.commit()
        scheduled = BatchScheduler(db).schedule(batch.id)
        retry_job = db.get(PendingJob, scheduled[0])

        assert retry_job is not None
        assert retry_job.retry_of_job_id == old_job.id
        assert item.status == "queued"
        claimed_retry = JobRepository(db).claim(retry_job.id, worker_id="explicit-retry-worker")
        assert claimed_retry is not None
        retry_claim = JobClaim.from_job(claimed_retry)
        db.commit()
        retry_run = create_extraction_run(
            db,
            job=retry_claim,
            paper=paper,
            input_object=stored_input,
            parent_run_id=_parent_run_id(db, retry_claim),
            config_snapshot={"result_config_hash": batch.result_config_hash},
        )
        db.flush()

        assert retry_run.parent_run_id == old_failed_run.id
        assert retry_run.id != old_failed_run.id
        assert db.get(ExtractionRun, old_failed_run.id).status == "failed"


def test_waiting_item_ignores_historical_failure_while_explicit_retry_is_active(monkeypatch) -> None:
    monkeypatch.setattr("app.queue.redis_queue.RedisQueue.enqueue", lambda _queue, _payload: None)
    create_db_and_tables()
    with SessionLocal() as db:
        project = Project(slug="active-retry", name="Active Retry")
        config_snapshot = _batch_config(model="retry-model")
        db.add(project)
        db.flush()
        paper = Paper(
            project_id=project.id,
            title="Shared retry",
            original_filename="shared-retry.pdf",
            file_path="papers/shared-retry.pdf",
            file_size=1,
            file_hash="6" * 64,
            status="failed",
        )
        retry_batch = BatchRun(
            project_id=project.id,
            submission_key="active-retry-owner",
            source_root="/incoming",
            status="failed",
            batch_concurrency=1,
            config_snapshot=config_snapshot,
            result_config_hash=result_config_hash(config_snapshot),
        )
        waiting_batch = BatchRun(
            project_id=project.id,
            submission_key="active-retry-waiter",
            source_root="/incoming",
            status="pending",
            batch_concurrency=1,
            config_snapshot=config_snapshot,
            result_config_hash=result_config_hash(config_snapshot),
        )
        db.add_all([paper, retry_batch, waiting_batch])
        db.flush()
        retry_item = BatchItem(
            batch_run_id=retry_batch.id,
            ordinal=0,
            source_relative_path="retry/shared.pdf",
            source_sha256=paper.file_hash,
            source_size_bytes=1,
            paper_id=paper.id,
            status="failed",
            current_stage="failed",
            error_message="MinerU unavailable",
        )
        waiting_item = BatchItem(
            batch_run_id=waiting_batch.id,
            ordinal=0,
            source_relative_path="waiting/shared.pdf",
            source_sha256=paper.file_hash,
            source_size_bytes=1,
            paper_id=paper.id,
        )
        db.add_all([retry_item, waiting_item])
        db.flush()
        failed_job = PendingJob(
            paper_id=paper.id,
            task_type="paper_parse",
            idempotency_key="active-retry-failed",
            batch_item_id=retry_item.id,
            status="failed",
            error_message="MinerU unavailable",
            completed_at=datetime.now(timezone.utc),
        )
        db.add(failed_job)
        db.commit()

        scheduled = BatchLifecycleService(db).retry_failed_items(retry_batch.id, [retry_item.id])
        assert len(scheduled) == 1
        active_retry = db.get(PendingJob, scheduled[0])
        assert active_retry is not None
        assert active_retry.retry_of_job_id == failed_job.id

        assert BatchScheduler(db).schedule(waiting_batch.id) == []
        db.refresh(waiting_item)

        assert waiting_item.status == "pending"
        assert waiting_item.current_stage == "waiting_for_active_parse"
        assert waiting_item.error_message is None


def test_cancelling_batch_allows_processing_job_to_finish_then_becomes_cancelled() -> None:
    create_db_and_tables()
    with SessionLocal() as db:
        project = Project(slug="batch-cancelling", name="Batch Cancelling")
        db.add(project)
        db.flush()
        paper = Paper(
            project_id=project.id,
            title="Cancelling",
            original_filename="cancelling.pdf",
            file_path="papers/cancelling.pdf",
            file_size=1,
            file_hash="c" * 64,
            status="processing",
        )
        batch = BatchRun(
            project_id=project.id,
            submission_key="batch-cancelling",
            source_root="/incoming",
            status="running",
            batch_concurrency=1,
            config_snapshot=_batch_config(model="test-model"),
            result_config_hash=result_config_hash(_batch_config(model="test-model")),
        )
        db.add_all([paper, batch])
        db.flush()
        item = BatchItem(
            batch_run_id=batch.id,
            ordinal=0,
            source_relative_path="cancelling.pdf",
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
            idempotency_key="batch-cancelling-job",
            batch_item_id=item.id,
            status="pending",
        )
        db.add(job)
        db.commit()

        claimed = JobRepository(db).claim(job.id, worker_id="batch-worker")
        assert claimed is not None
        db.commit()
        cancelling = BatchLifecycleService(db).cancel(batch.id)
        assert cancelling.status == "cancelling"
        db.refresh(item)
        assert item.status == "processing"

        JobRepository(db).fail(claimed, "parse failed")
        db.commit()
        db.refresh(batch)
        db.refresh(item)

        assert item.status == "failed"
        assert batch.status == "cancelled"


def test_batch_submission_is_idempotent_and_handles_new_reused_and_invalid_pdfs(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    source_root.mkdir()
    new_pdf = source_root / "new.pdf"
    reused_pdf = source_root / "reused.pdf"
    invalid_pdf = source_root / "invalid.pdf"
    new_pdf.write_bytes(_sample_pdf("new"))
    reused_pdf.write_bytes(_sample_pdf("reused"))
    invalid_pdf.write_bytes(b"not a PDF")
    create_db_and_tables()
    storage = StorageService(root=tmp_path / "objects")
    config_snapshot = _batch_config(model="test-model")
    config_hash = result_config_hash(config_snapshot)
    with SessionLocal() as db:
        registered = PaperUploadService(db, storage).register_pdf(
            filename=reused_pdf.name,
            content=reused_pdf.read_bytes(),
        )
        paper = registered.paper
        job, _ = JobRepository(db).get_or_create(
            paper_id=paper.id,
            task_type="paper_parse",
            idempotency_key=f"existing-success:{paper.id}",
        )
        run = ExtractionRun(
            task_id=job.id,
            paper_id=paper.id,
            input_object_id=paper.pdf_object_id,
            attempt=1,
            model_provider="test",
            model_name="test",
            model_version="test",
            prompt_version="test",
            pipeline_version="test",
            config_snapshot={"result_config_hash": config_hash},
            status="running",
        )
        db.add(run)
        db.flush()
        db.add(
            StructuredResult(
                run_id=run.id,
                paper_id=paper.id,
                result_type="chart_fact",
                natural_key="result",
                schema_version="normalized-result.v1",
                content_hash="a" * 64,
                payload={"value": 1},
            )
        )
        _persist_raw_response_artifact(db, storage, run)
        db.flush()
        run.status = "succeeded"
        db.commit()

        service = BatchSubmissionService(db, storage)
        batch = service.submit(
            project_id=1,
            source_root=source_root,
            submission_key="mixed-batch",
            batch_concurrency=2,
            config_snapshot=config_snapshot,
        )
        db.refresh(batch)
        assert batch.status == "running"
        items = {item.source_relative_path: item for item in batch.items}
        assert items["reused.pdf"].status == "reused"
        assert items["reused.pdf"].resolved_extraction_run_id == run.id
        assert items["new.pdf"].status == "queued"
        assert items["invalid.pdf"].status == "failed"
        assert db.query(PendingJob).filter(PendingJob.batch_item_id == items["new.pdf"].id).count() == 1

        repeated = service.submit(
            project_id=1,
            source_root=source_root,
            submission_key="mixed-batch",
            batch_concurrency=2,
            config_snapshot=config_snapshot,
        )
        assert repeated.id == batch.id
        assert db.query(BatchItem).filter(BatchItem.batch_run_id == batch.id).count() == 3


@pytest.mark.parametrize(
    ("case", "run_config", "persist_result", "artifact_role", "delete_artifact_bytes"),
    [
        ("different-hash", {"result_config_hash": "different"}, True, "model_raw_responses", False),
        ("legacy-hash", {}, True, "model_raw_responses", False),
        ("missing-result", None, False, "model_raw_responses", False),
        ("missing-artifact", None, True, None, False),
        ("wrong-artifact-role", None, True, "pipeline_output", False),
        ("missing-artifact-bytes", None, True, "model_raw_responses", True),
    ],
)
def test_batch_submission_does_not_reuse_incompatible_or_incomplete_runs(
    tmp_path: Path,
    case: str,
    run_config: dict[str, str] | None,
    persist_result: bool,
    artifact_role: str | None,
    delete_artifact_bytes: bool,
) -> None:
    source_root = tmp_path / "source"
    source_root.mkdir()
    source_pdf = source_root / "candidate.pdf"
    content = _sample_pdf(f"candidate-{case}")
    source_pdf.write_bytes(content)
    create_db_and_tables()
    storage = StorageService(root=tmp_path / "objects")
    config_snapshot = _batch_config(model="test-model")
    config_hash = result_config_hash(config_snapshot)
    with SessionLocal() as db:
        paper = PaperUploadService(db, storage).register_pdf(filename=source_pdf.name, content=content).paper
        job, _ = JobRepository(db).get_or_create(
            paper_id=paper.id,
            task_type="paper_parse",
            idempotency_key=f"candidate-run:{paper.id}",
        )
        run = ExtractionRun(
            task_id=job.id,
            paper_id=paper.id,
            input_object_id=paper.pdf_object_id,
            attempt=1,
            model_provider="test",
            model_name="test",
            model_version="test",
            prompt_version="test",
            pipeline_version="test",
            config_snapshot=run_config if run_config is not None else {"result_config_hash": config_hash},
            status="running",
        )
        db.add(run)
        db.flush()
        if persist_result:
            db.add(
                StructuredResult(
                    run_id=run.id,
                    paper_id=paper.id,
                    result_type="chart_fact",
                    natural_key="candidate",
                    schema_version="normalized-result.v1",
                    content_hash="a" * 64,
                    payload={"value": 1},
                )
            )
        artifact_object = None
        if artifact_role is not None:
            artifact_object = _persist_raw_response_artifact(db, storage, run, role=artifact_role)
        db.flush()
        run.status = "succeeded"
        db.commit()
        if delete_artifact_bytes:
            assert artifact_object is not None
            storage.adapter.delete(artifact_object.object_key)

        batch = BatchSubmissionService(db, storage).submit(
            project_id=1,
            source_root=source_root,
            submission_key=f"incompatible-{case}-{run.id}",
            batch_concurrency=1,
            config_snapshot=config_snapshot,
        )
        item = db.query(BatchItem).filter(BatchItem.batch_run_id == batch.id).one()

        assert item.status == "pending"
        assert item.resolved_extraction_run_id is None


@pytest.mark.parametrize("probe_failure", [False, True])
def test_scheduler_does_not_reuse_run_when_required_artifact_is_unavailable(
    tmp_path: Path,
    monkeypatch,
    probe_failure: bool,
) -> None:
    monkeypatch.setattr("app.queue.redis_queue.RedisQueue.enqueue", lambda _queue, _payload: None)
    create_db_and_tables()
    storage = StorageService(root=tmp_path / "objects")
    config_snapshot = _batch_config(model="test-model")
    config_hash = result_config_hash(config_snapshot)
    with SessionLocal() as db:
        suffix = "probe-error" if probe_failure else "missing"
        project = Project(slug=f"missing-reuse-bytes-{suffix}", name="Missing Reuse Bytes")
        db.add(project)
        db.flush()
        content = _sample_pdf("missing-reuse-bytes")
        paper = PaperUploadService(db, storage).register_pdf(
            project_id=project.id,
            filename="candidate.pdf",
            content=content,
        ).paper
        completed_job, _ = JobRepository(db).get_or_create(
            paper_id=paper.id,
            task_type="paper_parse",
            idempotency_key=f"completed-candidate:{paper.id}",
        )
        completed_job.status = "done"
        run = ExtractionRun(
            task_id=completed_job.id,
            paper_id=paper.id,
            input_object_id=paper.pdf_object_id,
            attempt=1,
            model_provider="test",
            model_name="test",
            model_version="test",
            prompt_version="test",
            pipeline_version="test",
            config_snapshot={"result_config_hash": config_hash},
            status="running",
        )
        db.add(run)
        db.flush()
        db.add(
            StructuredResult(
                run_id=run.id,
                paper_id=paper.id,
                result_type="chart_fact",
                natural_key="candidate",
                schema_version="normalized-result.v1",
                content_hash="a" * 64,
                payload={"value": 1},
            )
        )
        raw_object = _persist_raw_response_artifact(db, storage, run)
        db.flush()
        run.status = "succeeded"
        run.completed_at = datetime.now(timezone.utc)
        batch = BatchRun(
            project_id=project.id,
            submission_key=f"missing-reuse-bytes-{suffix}",
            source_root="/incoming",
            batch_concurrency=1,
            config_snapshot=config_snapshot,
            result_config_hash=config_hash,
        )
        db.add(batch)
        db.flush()
        item = BatchItem(
            batch_run_id=batch.id,
            ordinal=0,
            source_relative_path="candidate.pdf",
            source_sha256=paper.file_hash,
            source_size_bytes=paper.file_size,
            paper_id=paper.id,
            status="pending",
            current_stage="registered",
        )
        db.add(item)
        db.commit()
        if probe_failure:
            monkeypatch.setattr(storage, "exists", lambda _key: (_ for _ in ()).throw(TimeoutError("S3 timeout")))
        else:
            storage.adapter.delete(raw_object.object_key)

        scheduled = BatchScheduler(db, storage).schedule(batch.id)
        db.refresh(item)

        assert len(scheduled) == (0 if probe_failure else 1)
        assert item.status == ("pending" if probe_failure else "queued")
        assert item.current_stage == ("reuse_availability_unknown" if probe_failure else "queued")
        assert item.resolved_extraction_run_id is None


def test_result_config_hash_changes_only_with_semantic_configuration() -> None:
    baseline = result_config_hash(_batch_config(model="model-a", temperature=0))

    assert baseline == result_config_hash(
        {"result_semantics": {"model": "model-a", "temperature": 0}, "batch_concurrency": 9}
    )
    assert baseline == result_config_hash(
        {
            "result_semantics": {"model": "model-a", "temperature": 0},
            "llm_workers": 9,
            "max_concurrency": 12,
            "http_retries": 5,
            "retry_backoff_seconds": 3,
        }
    )
    assert baseline != result_config_hash(_batch_config(model="model-b", temperature=0))


def test_batch_submission_freezes_non_secret_execution_inputs(tmp_path: Path, monkeypatch) -> None:
    source_root = tmp_path / "source"
    source_root.mkdir()
    (source_root / "frozen.pdf").write_bytes(_sample_pdf("frozen"))
    monkeypatch.setattr("app.queue.redis_queue.RedisQueue.enqueue", lambda _queue, _payload: None)
    create_db_and_tables()
    with SessionLocal() as db:
        batch = BatchSubmissionService(db, StorageService(root=tmp_path / "objects")).submit(
            project_id=1,
            source_root=source_root,
            submission_key="frozen-execution",
            batch_concurrency=1,
            config_snapshot={
                "result_semantics": {
                    "model": "frozen-vlm",
                    "mineru": {"model_version": "frozen-mineru", "enable_table": False},
                },
                "llm_workers": 7,
            },
        )

        execution = batch.config_snapshot["execution"]
        assert execution["vlm"]["model"] == "frozen-vlm"
        assert "api_key" not in execution["vlm"]
        assert execution["mineru"]["model_version"] == "frozen-mineru"
        assert execution["mineru"]["enable_table"] is False
        assert execution["llm_workers"] == 7


def test_batch_parse_uses_frozen_mineru_configuration(monkeypatch) -> None:
    create_db_and_tables()
    captured: dict[str, object] = {}

    class FakeMinerUParser:
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)

    monkeypatch.setattr("app.services.pdf.parse_service.MinerUParserService", FakeMinerUParser)
    with SessionLocal() as db:
        project = Project(slug="frozen-mineru", name="Frozen MinerU")
        db.add(project)
        db.flush()
        batch = BatchRun(
            project_id=project.id,
            submission_key="frozen-mineru",
            source_root="/incoming",
            batch_concurrency=1,
            config_snapshot={
                "result_semantics": {"model": "frozen-vlm"},
                "execution": {
                    "mineru": {
                        "base_url": "https://mineru.example",
                        "model_version": "frozen-v1",
                        "language": "zh",
                        "timeout_seconds": 111,
                        "poll_interval_seconds": 2.5,
                        "is_ocr": True,
                        "enable_formula": False,
                        "enable_table": False,
                    }
                },
            },
            result_config_hash="a" * 64,
        )
        paper = Paper(
            project_id=project.id,
            title="Frozen",
            original_filename="frozen.pdf",
            file_path="papers/frozen.pdf",
            file_size=1,
            file_hash="c" * 64,
            status="pending",
        )
        db.add_all([batch, paper])
        db.flush()
        item = BatchItem(
            batch_run_id=batch.id,
            ordinal=0,
            source_relative_path="frozen.pdf",
            source_sha256=paper.file_hash,
            source_size_bytes=1,
            paper_id=paper.id,
        )
        db.add(item)
        db.flush()
        job = PendingJob(
            paper_id=paper.id,
            task_type="paper_parse",
            idempotency_key="frozen-mineru-job",
            batch_item_id=item.id,
        )
        db.add(job)
        db.flush()

        _parser, options = PaperParseService(db)._mineru_parser_for_job(job)

        assert captured == {
            "base_url": "https://mineru.example",
            "model_version": "frozen-v1",
            "language": "zh",
            "timeout_seconds": 111,
            "poll_interval_seconds": 2.5,
        }
        assert options == {"is_ocr": True, "enable_formula": False, "enable_table": False}


def test_batch_submission_rejects_a_hash_without_semantic_configuration(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    source_root.mkdir()
    create_db_and_tables()
    with SessionLocal() as db:
        service = BatchSubmissionService(db, StorageService(root=tmp_path / "objects"))
        with pytest.raises(ValueError, match="result_semantics"):
            service.submit(
                project_id=1,
                source_root=source_root,
                submission_key="hash-only",
                batch_concurrency=1,
                config_snapshot={"result_config_hash": "a" * 64},
            )


@pytest.mark.parametrize(
    ("item_statuses", "expected_status"),
    [
        (["succeeded", "reused"], "succeeded"),
        (["succeeded", "failed"], "partial_failed"),
        (["failed"], "failed"),
        (["cancelled"], "cancelled"),
    ],
)
def test_batch_status_derives_terminal_outcome_from_item_states(
    item_statuses: list[str], expected_status: str
) -> None:
    create_db_and_tables()
    with SessionLocal() as db:
        project = Project(slug=f"aggregate-{expected_status}-{len(item_statuses)}", name="Aggregate Test")
        db.add(project)
        db.flush()
        batch = BatchRun(
            project_id=project.id,
            submission_key=f"aggregate-{expected_status}",
            source_root="/aggregate",
            batch_concurrency=1,
            config_snapshot=_batch_config(model="test-model"),
            result_config_hash=result_config_hash(_batch_config(model="test-model")),
        )
        db.add(batch)
        db.flush()
        db.add_all(
            [
                BatchItem(
                    batch_run_id=batch.id,
                    ordinal=ordinal,
                    source_relative_path=f"{ordinal}.pdf",
                    source_sha256=f"{ordinal:064x}",
                    source_size_bytes=1,
                    status=item_status,
                )
                for ordinal, item_status in enumerate(item_statuses)
            ]
        )
        db.flush()

        BatchRepository(db).refresh_run_status(batch)

        assert batch.status == expected_status
        assert batch.completed_at is not None


def test_repeated_idempotent_status_refresh_preserves_terminal_completion_time() -> None:
    create_db_and_tables()
    completed_at = datetime(2026, 7, 12, tzinfo=timezone.utc)
    with SessionLocal() as db:
        project = Project(slug="aggregate-idempotent", name="Aggregate Idempotent")
        db.add(project)
        db.flush()
        batch = BatchRun(
            project_id=project.id,
            submission_key="aggregate-idempotent",
            source_root="/aggregate",
            status="succeeded",
            batch_concurrency=1,
            config_snapshot=_batch_config(model="test-model"),
            result_config_hash=result_config_hash(_batch_config(model="test-model")),
            completed_at=completed_at,
        )
        db.add(batch)
        db.flush()
        db.add(
            BatchItem(
                batch_run_id=batch.id,
                ordinal=0,
                source_relative_path="reused.pdf",
                source_sha256="a" * 64,
                source_size_bytes=1,
                status="reused",
            )
        )
        db.flush()

        BatchRepository(db).refresh_run_status(batch)

        assert batch.status == "succeeded"
        assert batch.completed_at == completed_at


def test_batch_exports_are_deterministic_rebuilds_of_database_facts(tmp_path: Path) -> None:
    from app.services.batches import BatchOperationsService

    create_db_and_tables()
    with SessionLocal() as db:
        project = Project(slug="batch-export", name="Batch Export")
        db.add(project)
        db.flush()
        config = _batch_config(model="export-model")
        batch = BatchRun(
            project_id=project.id,
            submission_key="batch-export",
            source_root="/incoming",
            status="succeeded",
            batch_concurrency=1,
            config_snapshot=config,
            result_config_hash=result_config_hash(config),
        )
        db.add(batch)
        db.flush()
        second = BatchItem(
            batch_run_id=batch.id,
            ordinal=1,
            source_relative_path="b.pdf",
            source_sha256="b" * 64,
            source_size_bytes=2,
            status="reused",
        )
        first = BatchItem(
            batch_run_id=batch.id,
            ordinal=0,
            source_relative_path="a.pdf",
            source_sha256="a" * 64,
            source_size_bytes=1,
            status="succeeded",
        )
        db.add_all([second, first])
        db.flush()
        db.add_all([
            BatchEvent(batch_run_id=batch.id, batch_item_id=second.id, event_type="item_reused", data={"n": 2}),
            BatchEvent(batch_run_id=batch.id, batch_item_id=first.id, event_type="item_succeeded", data={"n": 1}),
        ])
        db.commit()

        service = BatchOperationsService(db)
        first_manifest, first_events = service.export(batch.id, tmp_path / "first")
        second_manifest, second_events = service.export(batch.id, tmp_path / "second")

    assert first_manifest.read_bytes() == second_manifest.read_bytes()
    assert first_events.read_bytes() == second_events.read_bytes()
    manifest = json.loads(first_manifest.read_text(encoding="utf-8"))
    assert [item["source_relative_path"] for item in manifest["items"]] == ["a.pdf", "b.pdf"]
    events = [json.loads(line) for line in first_events.read_text(encoding="utf-8").splitlines()]
    assert [event["id"] for event in events] == sorted(event["id"] for event in events)


def test_non_batch_duplicate_upload_keeps_one_active_parse_job(tmp_path: Path) -> None:
    create_db_and_tables()
    storage = StorageService(root=tmp_path / "objects")
    content = _sample_pdf("one-active-job")
    with SessionLocal() as db:
        service = PaperUploadService(db, storage)
        first = service.register_pdf(filename="first.pdf", content=content)
        job, created = JobRepository(db).admit_paper_parse(paper_id=first.paper.id)
        db.commit()
        duplicate = service.register_pdf(filename="duplicate.pdf", content=content)
        same_job, created_again = JobRepository(db).admit_paper_parse(paper_id=duplicate.paper.id)

        assert created is True
        assert created_again is False
        assert duplicate.paper.id == first.paper.id
        assert same_job.id == job.id


def test_single_upload_enqueues_a_pdf_previously_registered_by_batch(tmp_path: Path, monkeypatch) -> None:
    source_root = tmp_path / "source"
    source_root.mkdir()
    source_pdf = source_root / "batch-registered.pdf"
    content = _sample_pdf("batch-registered")
    source_pdf.write_bytes(content)
    queued: list[dict] = []
    monkeypatch.setattr("app.queue.redis_queue.RedisQueue.enqueue", lambda _queue, payload: queued.append(payload))

    create_db_and_tables()
    storage = StorageService(root=tmp_path / "objects")
    with SessionLocal() as db:
        batch = BatchSubmissionService(db, storage).submit(
            project_id=1,
            source_root=source_root,
            submission_key="batch-then-single",
            batch_concurrency=1,
            config_snapshot=_batch_config(model="test-model"),
        )
        item = db.query(BatchItem).filter(BatchItem.batch_run_id == batch.id).one()
        assert item.status == "queued"
        assert item.paper_id is not None
        assert db.query(PendingJob).filter(PendingJob.paper_id == item.paper_id).count() == 1

        uploaded = PaperUploadService(db, storage).create_from_upload(
            filename="single-upload.pdf",
            content=content,
        )

        jobs = (
            db.query(PendingJob)
            .filter(PendingJob.paper_id == uploaded.id, PendingJob.task_type == "paper_parse")
            .all()
        )
        assert len(jobs) == 1
        assert queued == [{"schema_version": 2, "task_type": "paper_parse", "job_id": jobs[0].id}]


def test_submission_key_resumes_an_item_interrupted_after_paper_registration(tmp_path: Path, monkeypatch) -> None:
    source_root = tmp_path / "source"
    source_root.mkdir()
    source_pdf = source_root / "interrupted.pdf"
    content = _sample_pdf("interrupted")
    source_pdf.write_bytes(content)
    create_db_and_tables()
    storage = StorageService(root=tmp_path / "objects")

    with SessionLocal() as db:
        service = BatchSubmissionService(db, storage)
        monkeypatch.setattr(
            service.repository,
            "compatible_successful_runs",
            lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("simulated interruption")),
        )
        with pytest.raises(RuntimeError, match="simulated interruption"):
            service.submit(
                project_id=1,
                source_root=source_root,
                submission_key="resumable-submission",
                batch_concurrency=1,
                config_snapshot=_batch_config(model="test-model"),
            )
        db.rollback()

    with SessionLocal() as db:
        resumed = BatchSubmissionService(db, storage).submit(
            project_id=1,
            source_root=source_root,
            submission_key="resumable-submission",
            batch_concurrency=1,
            config_snapshot=_batch_config(model="test-model"),
        )
        item = db.query(BatchItem).filter(BatchItem.batch_run_id == resumed.id).one()

        assert item.paper_id is not None
        assert item.status == "queued"
        assert db.query(BatchRun).filter(BatchRun.submission_key == "resumable-submission").count() == 1
        assert db.query(BatchItem).filter(BatchItem.batch_run_id == resumed.id).count() == 1

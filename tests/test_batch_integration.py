from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
import redis

from app.db import SessionLocal, create_db_and_tables
from app.models import BatchItem, BatchRun, Paper, PendingJob, Project
from app.queue.contracts import queue_payload
from app.queue.redis_queue import RedisQueue
from app.repositories.jobs import JobRepository
from app.services.batches import BatchScheduler, result_config_hash
from app.services.pdf.dispatcher import dispatch_stale_pending_jobs


@pytest.fixture
def real_redis_queue(monkeypatch) -> RedisQueue:
    redis_url = os.getenv("TEST_REDIS_URL")
    if not redis_url:
        pytest.skip("TEST_REDIS_URL is required for the Redis integration test")
    client = redis.Redis.from_url(redis_url, decode_responses=True)
    try:
        client.ping()
    except Exception as exc:
        pytest.fail(f"Configured TEST_REDIS_URL is unavailable: {exc}")

    queue_name = f"paper_parse_queue:test:{uuid4()}"
    monkeypatch.setattr("app.queue.redis_queue.REDIS_URL", redis_url)
    monkeypatch.setattr("app.services.batches.PAPER_PARSE_QUEUE_NAME", queue_name)
    monkeypatch.setattr("app.services.pdf.dispatcher.PAPER_PARSE_QUEUE_NAME", queue_name)
    queue = RedisQueue(queue_name)
    yield queue
    client.delete(queue_name, f"{queue_name}:dead_letter")


@pytest.mark.redis
def test_real_redis_loss_and_destructive_dequeue_redispatch_the_same_job(
    monkeypatch,
    real_redis_queue: RedisQueue,
) -> None:
    create_db_and_tables()
    with SessionLocal() as db:
        project = Project(slug=f"redis-recovery-{uuid4()}", name="Redis Recovery")
        db.add(project)
        db.flush()
        paper = Paper(
            project_id=project.id,
            title="Redis recovery",
            original_filename="redis-recovery.pdf",
            file_path=f"papers/{uuid4()}.pdf",
            file_size=1,
            file_hash=(uuid4().hex + uuid4().hex)[:64],
            status="pending",
        )
        config = {"result_semantics": {"model": "redis-integration"}}
        batch = BatchRun(
            project_id=project.id,
            submission_key=f"redis-recovery-{uuid4()}",
            source_root="/integration",
            batch_concurrency=1,
            config_snapshot=config,
            result_config_hash=result_config_hash(config),
        )
        db.add_all([paper, batch])
        db.flush()
        item = BatchItem(
            batch_run_id=batch.id,
            ordinal=0,
            source_relative_path="redis-recovery.pdf",
            source_sha256=paper.file_hash,
            source_size_bytes=1,
            paper_id=paper.id,
        )
        db.add(item)
        db.commit()

        real_url = os.environ["TEST_REDIS_URL"]
        unavailable_url = "redis://127.0.0.1:1/15?socket_connect_timeout=0.1&socket_timeout=0.1"
        monkeypatch.setattr("app.queue.redis_queue.REDIS_URL", unavailable_url)
        scheduled = BatchScheduler(db).schedule(batch.id)

        assert len(scheduled) == 1
        job = db.get(PendingJob, scheduled[0])
        assert job is not None and job.status == "pending"
        assert db.get(BatchItem, item.id).status == "queued"
        assert db.query(PendingJob).filter(PendingJob.batch_item_id == item.id).count() == 1

        monkeypatch.setattr("app.queue.redis_queue.REDIS_URL", real_url)
        job.updated_at = datetime.now(timezone.utc) - timedelta(minutes=16)
        db.commit()
        assert dispatch_stale_pending_jobs(db, job_ids=[job.id]) == 1

        first_delivery = real_redis_queue.dequeue(timeout=1)
        assert first_delivery is not None and first_delivery["job_id"] == job.id

        db.refresh(job)
        job.updated_at = datetime.now(timezone.utc) - timedelta(minutes=16)
        db.commit()
        assert dispatch_stale_pending_jobs(db, job_ids=[job.id]) == 1

        second_delivery = real_redis_queue.dequeue(timeout=1)
        assert second_delivery is not None and second_delivery["job_id"] == job.id
        assert db.query(PendingJob).filter(PendingJob.batch_item_id == item.id).count() == 1

        duplicate = queue_payload("paper_parse", job.id)
        real_redis_queue.enqueue(duplicate)
        real_redis_queue.enqueue(duplicate)
        first_duplicate = real_redis_queue.dequeue(timeout=1)
        second_duplicate = real_redis_queue.dequeue(timeout=1)
        assert first_duplicate is not None and second_duplicate is not None

    with SessionLocal() as first_worker:
        claimed = JobRepository(first_worker).claim(job.id, worker_id="redis-worker-a")
        assert claimed is not None
        first_worker.commit()
    with SessionLocal() as second_worker:
        assert JobRepository(second_worker).claim(job.id, worker_id="redis-worker-b") is None
        second_worker.rollback()

    with SessionLocal() as observer:
        persisted = observer.get(PendingJob, job.id)
        assert persisted is not None and persisted.status == "processing"
        assert persisted.claim_generation == 1

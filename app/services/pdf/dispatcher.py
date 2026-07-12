from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.config import PAPER_PARSE_QUEUE_NAME
from app.models import BatchEvent, BatchItem, BatchRun, Paper, PaperStatus
from app.models.job import PendingJob
from app.queue.contracts import queue_payload
from app.queue.redis_queue import RedisQueue
from app.repositories import BatchRepository

logger = logging.getLogger(__name__)

_STALE_THRESHOLD_MINUTES = 15


def dispatch_stale_pending_jobs(
    db: Session,
    *,
    job_ids: list[int] | None = None,
    protected_processing_owners: set[str] | None = None,
) -> int:
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=_STALE_THRESHOLD_MINUTES)
    stale_ids = list(
        db.execute(
            select(PendingJob.id).where(
                or_(
                    (PendingJob.status.in_(["pending", "redis_dispatched", "retry"]))
                    & (PendingJob.updated_at < cutoff),
                    (PendingJob.status == "processing") & (PendingJob.lease_expires_at < now),
                )
            )
            .where(PendingJob.id.in_(job_ids) if job_ids is not None else True)
            .order_by(PendingJob.created_at.asc())
            .limit(20)
        ).scalars()
    )
    if not stale_ids:
        return 0

    redispatch_jobs: list[tuple[int, str]] = []
    affected_batches: dict[str, BatchRun] = {}
    for job_id in stale_ids:
        job, item, batch = _lock_job_for_recovery(db, job_id)
        if job is None or not _is_stale_job(
            job,
            now=now,
            cutoff=cutoff,
            protected_processing_owners=protected_processing_owners,
        ):
            continue
        if batch is not None and batch.status in {"cancelling", "cancelled"}:
            _cancel_recovery_job(
                db,
                job=job,
                item=item,
                reason="Batch cancellation requested during stale recovery.",
            )
            affected_batches[batch.id] = batch
            continue
        paper = db.get(Paper, job.paper_id)
        if paper is None or str(paper.status) == PaperStatus.DELETED.value:
            _cancel_recovery_job(db, job=job, item=item, reason="paper not found or deleted")
            if batch is not None:
                affected_batches[batch.id] = batch
            continue
        if (
            job.batch_item_id is None
            and str(paper.status) == PaperStatus.DONE.value
            and job.task_type == "paper_parse"
        ):
            job.status = "cancelled"
            job.error_message = f"paper status is {paper.status}, no longer needs dispatch"
            continue
        if job.status == "processing":
            job.status = "retry"
            job.lease_owner = None
            job.lease_expires_at = None
            if item is not None:
                item.status = "queued"
                item.current_stage = "queued"
                db.add(
                    BatchEvent(
                        batch_run_id=item.batch_run_id,
                        batch_item_id=item.id,
                        event_type="item_lease_expired",
                        data={"job_id": job.id},
                    )
                )
        redispatch_jobs.append((job.id, job.task_type))

    # PostgreSQL is the source of truth: record any retry/queue transition before Redis sees the Job ID.
    for batch in affected_batches.values():
        BatchRepository(db).refresh_run_status(batch)
    db.commit()
    dispatched = 0
    for job_id, task_type in redispatch_jobs:
        try:
            RedisQueue(PAPER_PARSE_QUEUE_NAME).enqueue(queue_payload(task_type, job_id))
        except Exception as exc:
            logger.warning("redis-dispatch failed for job_id=%s: %s", job_id, exc)
            continue
        job = db.execute(
            select(PendingJob).where(PendingJob.id == job_id).with_for_update().execution_options(populate_existing=True)
        ).scalar_one_or_none()
        if job is None:
            db.rollback()
            continue
        if job.status in {"pending", "retry"}:
            job.status = "redis_dispatched"
        db.commit()
        dispatched += 1
        logger.info("redis-dispatched stale job paper_id=%s task=%s", job.paper_id, job.task_type)
    return dispatched


def _lock_job_for_recovery(db: Session, job_id: int) -> tuple[PendingJob | None, BatchItem | None, BatchRun | None]:
    batch_item_id = db.execute(select(PendingJob.batch_item_id).where(PendingJob.id == job_id)).scalar_one_or_none()
    if batch_item_id is None:
        job = db.execute(
            select(PendingJob)
            .where(PendingJob.id == job_id)
            .with_for_update(skip_locked=True)
            .execution_options(populate_existing=True)
        ).scalar_one_or_none()
        return job, None, None
    batch_run_id = db.execute(select(BatchItem.batch_run_id).where(BatchItem.id == batch_item_id)).scalar_one_or_none()
    if batch_run_id is None:
        return None, None, None
    batch = db.execute(select(BatchRun).where(BatchRun.id == batch_run_id).with_for_update()).scalar_one()
    job = db.execute(
        select(PendingJob)
        .where(PendingJob.id == job_id)
        .with_for_update(skip_locked=True)
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if job is None:
        return None, None, batch
    item = db.execute(
        select(BatchItem)
        .where(BatchItem.id == batch_item_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one()
    return job, item, batch


def _cancel_recovery_job(db: Session, *, job: PendingJob, item: BatchItem | None, reason: str) -> None:
    job.status = "cancelled"
    job.error_message = reason
    job.completed_at = datetime.now(timezone.utc)
    job.lease_owner = None
    job.lease_expires_at = None
    if item is None:
        return
    item.status = "cancelled"
    item.current_stage = "cancelled"
    item.error_message = reason
    db.add(
        BatchEvent(
            batch_run_id=item.batch_run_id,
            batch_item_id=item.id,
            event_type="item_cancelled",
            data={"job_id": job.id, "reason": reason},
        )
    )


def _is_stale_job(
    job: PendingJob,
    *,
    now: datetime,
    cutoff: datetime,
    protected_processing_owners: set[str] | None = None,
) -> bool:
    if job.status in {"pending", "redis_dispatched", "retry"}:
        return _as_utc(job.updated_at) < cutoff
    if job.status == "processing" and job.lease_expires_at is not None:
        if job.lease_owner and job.lease_owner in (protected_processing_owners or set()):
            return False
        return _as_utc(job.lease_expires_at) < now
    return False


def _as_utc(value: datetime) -> datetime:
    """Normalize SQLite's naive timestamp round-trip without changing PostgreSQL values."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def recover_stuck_processing_papers(db: Session) -> int:
    stale_cutoff = datetime.now(timezone.utc) - timedelta(hours=2)
    has_live_job = db.query(PendingJob.id).filter(
        PendingJob.paper_id == Paper.id,
        PendingJob.status.in_(("pending", "redis_dispatched", "retry", "processing")),
    ).exists()
    stuck = (
        db.query(Paper)
        .filter(
            Paper.status == PaperStatus.PROCESSING.value,
            Paper.updated_at < stale_cutoff,
            ~has_live_job,
        )
        .limit(20)
        .all()
    )
    if not stuck:
        return 0

    recovered = 0
    for paper in stuck:
        paper.status = PaperStatus.FAILED.value
        paper.error_message = (
            f"Recovered from stuck PROCESSING state (last update: {paper.updated_at}). "
            "Retry via POST /papers/{id}/retry"
        )
        recovered += 1
        logger.info("recovered stuck paper id=%s title=%s", paper.id, paper.title)
    db.commit()
    return recovered

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.config import PAPER_PARSE_QUEUE_NAME
from app.models import Paper, PaperStatus
from app.models.job import PendingJob
from app.queue.redis_queue import RedisQueue

logger = logging.getLogger(__name__)

_STALE_THRESHOLD_MINUTES = 15


def dispatch_stale_pending_jobs(db: Session) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=_STALE_THRESHOLD_MINUTES)
    stale = (
        db.query(PendingJob)
        .filter(PendingJob.status == "pending", PendingJob.created_at < cutoff)
        .order_by(PendingJob.created_at.asc())
        .limit(20)
        .all()
    )
    if not stale:
        return 0

    dispatched = 0
    for job in stale:
        paper = db.get(Paper, job.paper_id)
        if paper is None or str(paper.status) == PaperStatus.DELETED.value:
            job.status = "cancelled"
            job.error_message = "paper not found or deleted"
            continue
        if str(paper.status) in {PaperStatus.DONE.value, PaperStatus.PENDING.value}:
            job.status = "cancelled"
            job.error_message = f"paper status is {paper.status}, no longer needs dispatch"
            continue
        try:
            RedisQueue(PAPER_PARSE_QUEUE_NAME).enqueue({
                "task_type": job.task_type,
                "paper_id": job.paper_id,
            })
            job.status = "redis_dispatched"
            dispatched += 1
            logger.info("redis-dispatched stale job paper_id=%s task=%s", job.paper_id, job.task_type)
        except Exception as exc:
            logger.warning("redis-dispatch failed for paper_id=%s: %s", job.paper_id, exc)
    db.commit()
    return dispatched


def recover_stuck_processing_papers(db: Session) -> int:
    stale_cutoff = datetime.now(timezone.utc) - timedelta(hours=2)
    stuck = (
        db.query(Paper)
        .filter(
            Paper.status == PaperStatus.PROCESSING.value,
            Paper.updated_at < stale_cutoff,
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

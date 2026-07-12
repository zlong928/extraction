from __future__ import annotations

import logging
import signal
import os
import socket
import threading
from contextlib import contextmanager

from app.config import PAPER_PARSE_QUEUE_NAME
from app.db import SessionLocal, create_db_and_tables
from app.queue.redis_queue import RedisQueue
from app.models import Paper, PaperStatus, PendingJob
from app.repositories import JobRepository, LostJobLease
from app.services.pdf.dispatcher import dispatch_stale_pending_jobs, recover_stuck_processing_papers
from app.services.pdf import (
    ChartOnlyRunAlreadyActive,
    PaperParseService,
    check_content_pipeline_llm_preflight,
    chart_only_run_lock,
    run_chart_only_for_paper,
)
from sqlalchemy.orm import Session

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("extraction_worker")

shutdown = False


def _handle_signal(_signum, _frame) -> None:
    global shutdown
    shutdown = True


signal.signal(signal.SIGTERM, _handle_signal)
signal.signal(signal.SIGINT, _handle_signal)


def run_content_pipeline_job(db: Session, job: PendingJob) -> None:
    paper_id = job.paper_id
    paper = db.get(Paper, paper_id)
    if paper is None:
        JobRepository(db).fail(job, "paper not found")
        db.commit()
        return
    if str(paper.status) == PaperStatus.DELETED.value:
        JobRepository(db).assert_ownership(job, for_update=True)
        job.status = "cancelled"
        job.lease_owner = None
        job.lease_expires_at = None
        job.completed_at = None
        db.commit()
        return

    try:
        logger.info("starting chart-only extraction paper_id=%s", paper_id)
        check_content_pipeline_llm_preflight()
        with chart_only_run_lock(paper_id, blocking=False):
            summary = run_chart_only_for_paper(paper, job=job)
        if summary.get("status") == "failed":
            paper.status = PaperStatus.FAILED.value
            paper.error_message = "chart-only extraction completed without a publishable result"
        else:
            paper.status = PaperStatus.DONE.value
            if summary.get("status") == "succeeded":
                paper.error_message = None
    except ChartOnlyRunAlreadyActive:
        logger.info("chart-only extraction already running paper_id=%s", paper_id)
        JobRepository(db).assert_ownership(job, for_update=True)
        job.status = "retry"
        job.lease_owner = None
        job.lease_expires_at = None
        db.commit()
        return
    except LostJobLease:
        db.rollback()
        logger.warning("discarding stale chart-only worker result paper_id=%s job_id=%s", paper_id, job.id)
        return
    except Exception as exc:
        logger.exception("chart-only extraction failed paper_id=%s: %s", paper_id, exc)
        paper.status = PaperStatus.FAILED.value
        paper.error_message = str(exc)
    db.commit()


def _resolve_job(db: Session, payload: dict) -> PendingJob | None:
    job_id = int(payload.get("job_id") or 0)
    if job_id > 0:
        return db.get(PendingJob, job_id)
    paper_id = int(payload.get("paper_id") or 0)
    if paper_id <= 0:
        return None
    return (
        db.query(PendingJob)
        .filter(PendingJob.paper_id == paper_id, PendingJob.task_type == payload.get("task_type"))
        .order_by(PendingJob.id.desc())
        .first()
    )


@contextmanager
def _job_lease_heartbeat(job: PendingJob, *, interval_seconds: int = 60):
    stop = threading.Event()
    job_id = job.id
    worker_id = str(job.lease_owner)
    claim_generation = job.claim_generation

    def heartbeat() -> None:
        while not stop.wait(interval_seconds):
            try:
                with SessionLocal() as heartbeat_db:
                    renewed = JobRepository(heartbeat_db).renew(
                        job_id,
                        worker_id=worker_id,
                        claim_generation=claim_generation,
                    )
                    heartbeat_db.commit()
                    if not renewed:
                        stop.set()
                        return
            except Exception:
                logger.exception("job lease heartbeat failed job_id=%s", job_id)
                stop.set()
                return

    thread = threading.Thread(target=heartbeat, name=f"job-heartbeat-{job_id}", daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=5)


def run_recovery_once() -> None:
    with SessionLocal() as db:
        stuck = recover_stuck_processing_papers(db)
        stale = dispatch_stale_pending_jobs(db)
        if stuck or stale:
            logger.info("recovery: %s stuck papers recovered, %s stale jobs redispatched", stuck, stale)


def run_parse_loop() -> None:
    queue = RedisQueue(PAPER_PARSE_QUEUE_NAME)
    logger.info("paper/chart-only worker listening on %s", PAPER_PARSE_QUEUE_NAME)
    run_recovery_once()
    recovery_counter = 0
    while not shutdown:
        recovery_counter += 1
        if recovery_counter % 300 == 0:
            run_recovery_once()
        payload = queue.dequeue(timeout=2)
        if not payload:
            continue
        task_type = payload.get("task_type")
        if task_type not in {"paper_parse", "chart_only_run"}:
            continue
        with SessionLocal() as db:
            job = _resolve_job(db, payload)
            if job is None:
                continue
            worker_id = f"{socket.gethostname()}:{os.getpid()}"
            claimed = JobRepository(db).claim(job.id, worker_id=worker_id)
            if claimed is None:
                db.rollback()
                continue
            db.commit()
            db.refresh(claimed)
            paper_id = claimed.paper_id
            with _job_lease_heartbeat(claimed):
                if task_type == "paper_parse":
                    logger.info("starting MinerU parse paper_id=%s", paper_id)
                    PaperParseService(db).parse_or_fail(paper_id, job=claimed)
                    logger.info("finished MinerU parse paper_id=%s", paper_id)
                elif task_type == "chart_only_run":
                    run_content_pipeline_job(db, claimed)
                    logger.info("finished chart-only extraction paper_id=%s", paper_id)
                else:
                    logger.warning("unknown task_type=%s paper_id=%s", task_type, paper_id)


def main() -> None:
    create_db_and_tables()
    logger.info("starting chart-only worker...")
    run_parse_loop()


if __name__ == "__main__":
    main()

from __future__ import annotations

import logging
import signal

from app.config import PAPER_PARSE_QUEUE_NAME
from app.db import SessionLocal, create_db_and_tables
from app.queue.redis_queue import RedisQueue
from app.models import Paper, PaperStatus
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


def run_content_pipeline_job(db: Session, paper_id: int) -> None:
    paper = db.get(Paper, paper_id)
    if paper is None:
        return
    if str(paper.status) == PaperStatus.DELETED.value:
        return

    try:
        logger.info("starting chart-only extraction paper_id=%s", paper_id)
        check_content_pipeline_llm_preflight()
        with chart_only_run_lock(paper_id, blocking=False):
            run_chart_only_for_paper(paper)
        paper.status = PaperStatus.DONE.value
        paper.error_message = None
    except ChartOnlyRunAlreadyActive:
        logger.info("chart-only extraction already running paper_id=%s", paper_id)
        return
    except Exception as exc:
        logger.exception("chart-only extraction failed paper_id=%s: %s", paper_id, exc)
        paper.status = PaperStatus.FAILED.value
        paper.error_message = str(exc)
    db.commit()


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
        paper_id = int(payload.get("paper_id") or 0)
        if paper_id <= 0:
            continue
        with SessionLocal() as db:
            if task_type == "paper_parse":
                logger.info("starting MinerU parse paper_id=%s", paper_id)
                PaperParseService(db).parse_or_fail(paper_id)
                logger.info("finished MinerU parse paper_id=%s", paper_id)
            elif task_type == "chart_only_run":
                run_content_pipeline_job(db, paper_id)
                logger.info("finished chart-only extraction paper_id=%s", paper_id)
            else:
                logger.warning("unknown task_type=%s paper_id=%s", task_type, paper_id)


def main() -> None:
    create_db_and_tables()
    logger.info("starting chart-only worker...")
    run_parse_loop()


if __name__ == "__main__":
    main()

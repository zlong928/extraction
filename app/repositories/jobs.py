from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import BatchEvent, BatchItem, BatchRun, ExtractionRun, Paper, PendingJob
from app.repositories.batches import ACTIVE_BATCH_JOB_STATUSES, BatchRepository


class LostJobLease(RuntimeError):
    pass


@dataclass(frozen=True)
class JobClaim:
    """Immutable worker lease evidence that survives ORM expiration after commits."""

    id: int
    paper_id: int
    task_type: str
    batch_item_id: str | None
    retry_of_job_id: int | None
    attempt: int
    lease_owner: str | None
    claim_generation: int

    @classmethod
    def from_job(cls, job: PendingJob) -> JobClaim:
        return cls(
            id=job.id,
            paper_id=job.paper_id,
            task_type=job.task_type,
            batch_item_id=job.batch_item_id,
            retry_of_job_id=job.retry_of_job_id,
            attempt=job.attempt,
            lease_owner=job.lease_owner,
            claim_generation=job.claim_generation,
        )


@dataclass(frozen=True)
class TerminalJobContext:
    job: PendingJob
    paper: Paper
    run: ExtractionRun | None
    batch: BatchRun | None = None
    item: BatchItem | None = None


class JobRepository:
    """Transactional task creation and claim logic shared by API and workers."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def get_or_create(
        self,
        *,
        paper_id: int,
        task_type: str,
        idempotency_key: str,
        attempt: int = 1,
        batch_item_id: str | None = None,
        retry_of_job_id: int | None = None,
    ) -> tuple[PendingJob, bool]:
        existing = self._by_key(idempotency_key)
        if existing is not None:
            return existing, False
        job = PendingJob(
            paper_id=paper_id,
            task_type=task_type,
            idempotency_key=idempotency_key,
            attempt=attempt,
            batch_item_id=batch_item_id,
            retry_of_job_id=retry_of_job_id,
            status="pending",
        )
        try:
            with self.db.begin_nested():
                self.db.add(job)
                self.db.flush()
        except IntegrityError:
            existing = self._by_key(idempotency_key)
            if existing is None:
                raise
            return existing, False
        return job, True

    def claim(self, job_id: int, *, worker_id: str, lease_seconds: int = 900) -> PendingJob | None:
        now = datetime.now(timezone.utc)
        job = self.db.execute(
            select(PendingJob)
            .where(PendingJob.id == job_id)
            .with_for_update(skip_locked=True)
            .execution_options(populate_existing=True)
        ).scalar_one_or_none()
        if job is None:
            return None
        if job.status not in {"pending", "redis_dispatched", "retry"}:
            return None
        if job.lease_expires_at is not None:
            lease_expires_at = job.lease_expires_at
            if lease_expires_at.tzinfo is None:
                lease_expires_at = lease_expires_at.replace(tzinfo=timezone.utc)
            if lease_expires_at > now:
                return None
        if job.batch_item_id is not None:
            item = self.db.execute(
                select(BatchItem)
                .where(BatchItem.id == job.batch_item_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            ).scalar_one()
            if item.status != "queued":
                return None
            batch_status = self.db.execute(
                select(BatchRun.status).where(BatchRun.id == item.batch_run_id)
            ).scalar_one()
            if batch_status not in {"pending", "running"}:
                return None
            item.status = "processing"
            item.current_stage = "parsing"
            self.db.add(
                BatchEvent(
                    batch_run_id=item.batch_run_id,
                    batch_item_id=item.id,
                    event_type="item_processing",
                    data={"job_id": job.id},
                )
            )
        job.status = "processing"
        job.lease_owner = worker_id
        job.claim_generation += 1
        job.lease_expires_at = now + timedelta(seconds=lease_seconds)
        job.started_at = job.started_at or now
        self.db.flush()
        return job

    def admit_paper_parse(self, *, paper_id: int) -> tuple[PendingJob, bool]:
        """Create at most one active parse Job while holding the Paper row lock."""
        self.lock_paper(paper_id)
        active = self._active_paper_parse(paper_id)
        if active is not None:
            return active, False
        attempt = self.db.query(PendingJob).filter(
            PendingJob.paper_id == paper_id, PendingJob.task_type == "paper_parse"
        ).count() + 1
        return self.get_or_create(
            paper_id=paper_id,
            task_type="paper_parse",
            idempotency_key=f"paper-parse:{paper_id}:attempt:{attempt}",
            attempt=attempt,
        )

    def create_batch_paper_parse_under_lock(
        self,
        *,
        paper_id: int,
        batch_item_id: str,
        retry_of_job_id: int | None = None,
    ) -> tuple[PendingJob, bool]:
        """Create a batch parse Job after the caller locked Paper and found no active attempt."""
        attempt = (
            self.db.query(PendingJob)
            .filter(PendingJob.batch_item_id == batch_item_id, PendingJob.task_type == "paper_parse")
            .count()
            + 1
        )
        return self.get_or_create(
            paper_id=paper_id,
            task_type="paper_parse",
            idempotency_key=f"batch-parse:{batch_item_id}:attempt:{attempt}",
            attempt=attempt,
            batch_item_id=batch_item_id,
            retry_of_job_id=retry_of_job_id,
        )

    def complete(
        self,
        job: PendingJob | JobClaim,
    ) -> None:
        self.complete_terminal(self.lock_terminal_context(job))

    def complete_terminal(self, context: TerminalJobContext) -> None:
        if context.job.batch_item_id is not None:
            self._finalize_batch_job(context, error=None)
            return
        current = context.job
        current.status = "done"
        current.completed_at = datetime.now(timezone.utc)
        current.lease_owner = None
        current.lease_expires_at = None

    def fail(
        self,
        job: PendingJob | JobClaim,
        error: str,
    ) -> None:
        self.fail_terminal(self.lock_terminal_context(job), error)

    def fail_terminal(self, context: TerminalJobContext, error: str) -> None:
        if context.job.batch_item_id is not None:
            self._finalize_batch_job(context, error=error)
            return
        current = context.job
        current.status = "failed"
        current.error_message = error
        current.completed_at = datetime.now(timezone.utc)
        current.lease_owner = None
        current.lease_expires_at = None

    def renew(
        self,
        job_id: int,
        *,
        worker_id: str,
        claim_generation: int,
        lease_seconds: int = 900,
    ) -> bool:
        result = self.db.execute(
            update(PendingJob)
            .where(
                PendingJob.id == job_id,
                PendingJob.status == "processing",
                PendingJob.lease_owner == worker_id,
                PendingJob.claim_generation == claim_generation,
            )
            .values(lease_expires_at=datetime.now(timezone.utc) + timedelta(seconds=lease_seconds))
        )
        return bool(result.rowcount)

    def assert_ownership(self, job: PendingJob | JobClaim, *, for_update: bool = False) -> PendingJob:
        expected_owner = job.lease_owner
        expected_generation = job.claim_generation
        statement = (
            select(PendingJob)
            .where(PendingJob.id == job.id)
            .execution_options(populate_existing=True)
        )
        if for_update:
            statement = statement.with_for_update()
        current = self.db.execute(statement).scalar_one()
        if (
            current.status != "processing"
            or current.lease_owner != expected_owner
            or current.claim_generation != expected_generation
        ):
            raise LostJobLease(f"Job {job.id} lease is no longer owned by this worker")
        return current

    def lock_terminal_context(self, job: PendingJob | JobClaim) -> TerminalJobContext:
        """Fence a terminal write while acquiring rows in one documented order.

        Pending ORM changes are intentionally not flushed until all coordination
        rows are locked. This prevents a dirty Paper from reversing the batch
        scheduler's BatchRun -> Job -> BatchItem -> Paper lock order.
        """
        with self.db.no_autoflush:
            batch = None
            item = None
            if job.batch_item_id is not None:
                batch_run_id = self.db.execute(
                    select(BatchItem.batch_run_id).where(BatchItem.id == job.batch_item_id)
                ).scalar_one()
                batch = self.db.execute(
                    select(BatchRun)
                    .where(BatchRun.id == batch_run_id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                ).scalar_one()
            current = self.assert_ownership(job, for_update=True)
            if job.batch_item_id is not None:
                item = self.db.execute(
                    select(BatchItem)
                    .where(BatchItem.id == job.batch_item_id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                ).scalar_one()
            self.db.execute(
                select(Paper.id)
                .where(Paper.id == current.paper_id)
                .with_for_update()
            ).scalar_one()
            paper = self.db.get(Paper, current.paper_id)
            run = self.db.execute(
                select(ExtractionRun)
                .where(ExtractionRun.task_id == current.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            ).scalar_one_or_none()
        return TerminalJobContext(job=current, paper=paper, run=run, batch=batch, item=item)

    def _by_key(self, idempotency_key: str) -> PendingJob | None:
        return self.db.query(PendingJob).filter(PendingJob.idempotency_key == idempotency_key).one_or_none()

    def lock_paper(self, paper_id: int) -> Paper:
        return self.db.execute(select(Paper).where(Paper.id == paper_id).with_for_update()).scalar_one()

    def active_paper_parse(self, paper_id: int) -> PendingJob | None:
        """Return the active parse attempt after the caller has serialized on Paper."""
        return self._active_paper_parse(paper_id)

    def _active_paper_parse(self, paper_id: int) -> PendingJob | None:
        return self.db.execute(
            select(PendingJob)
            .where(
                PendingJob.paper_id == paper_id,
                PendingJob.task_type == "paper_parse",
                PendingJob.status.in_(ACTIVE_BATCH_JOB_STATUSES),
            )
            .order_by(PendingJob.id)
            .limit(1)
        ).scalar_one_or_none()

    def _finalize_batch_job(
        self,
        context: TerminalJobContext,
        *,
        error: str | None,
    ) -> None:
        if context.job.batch_item_id is None:
            raise ValueError("Batch finalization requires batch_item_id")
        batch = context.batch
        item = context.item
        current = context.job
        run = context.run
        if batch is None or item is None:
            raise ValueError("Batch finalization context is incomplete")
        if error is None and run is None:
            raise ValueError("Batch Job cannot complete without an ExtractionRun")
        if error is None and run.status not in {"succeeded", "partial_failure"}:
            raise ValueError("Batch Job cannot complete before its ExtractionRun is terminal")
        is_partial_failure = run is not None and run.status == "partial_failure"
        if error is None:
            current.status = "done"
            current.completed_at = datetime.now(timezone.utc)
            current.lease_owner = None
            current.lease_expires_at = None
            if is_partial_failure:
                item.status = "failed"
                item.current_stage = "extraction"
                item.error_message = "Extraction completed with partial_failure"
                event_type = "item_failed"
            else:
                item.status = "succeeded"
                item.current_stage = "persisted"
                item.resolved_extraction_run_id = run.id if run is not None else None
                item.error_message = None
                event_type = "item_succeeded"
        else:
            current.status = "failed"
            current.error_message = error
            current.completed_at = datetime.now(timezone.utc)
            current.lease_owner = None
            current.lease_expires_at = None
            item.status = "failed"
            item.current_stage = "failed"
            item.error_message = error
            event_type = "item_failed"
        self.db.add(
            BatchEvent(
                batch_run_id=batch.id,
                batch_item_id=item.id,
                event_type=event_type,
                data={
                    "job_id": current.id,
                    **({"extraction_run_id": run.id} if run is not None else {}),
                    **({"error": error} if error is not None else {}),
                },
            )
        )
        BatchRepository(self.db).refresh_run_status(batch)

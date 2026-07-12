from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import PendingJob


class LostJobLease(RuntimeError):
    pass


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
    ) -> tuple[PendingJob, bool]:
        existing = self._by_key(idempotency_key)
        if existing is not None:
            return existing, False
        job = PendingJob(
            paper_id=paper_id,
            task_type=task_type,
            idempotency_key=idempotency_key,
            attempt=attempt,
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
        job.status = "processing"
        job.lease_owner = worker_id
        job.claim_generation += 1
        job.lease_expires_at = now + timedelta(seconds=lease_seconds)
        job.started_at = job.started_at or now
        self.db.flush()
        return job

    def complete(self, job: PendingJob) -> None:
        self.assert_ownership(job, for_update=True)
        job.status = "done"
        job.completed_at = datetime.now(timezone.utc)
        job.lease_owner = None
        job.lease_expires_at = None

    def fail(self, job: PendingJob, error: str) -> None:
        self.assert_ownership(job, for_update=True)
        job.status = "failed"
        job.error_message = error
        job.completed_at = datetime.now(timezone.utc)
        job.lease_owner = None
        job.lease_expires_at = None

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

    def assert_ownership(self, job: PendingJob, *, for_update: bool = False) -> PendingJob:
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

    def _by_key(self, idempotency_key: str) -> PendingJob | None:
        return self.db.query(PendingJob).filter(PendingJob.idempotency_key == idempotency_key).one_or_none()

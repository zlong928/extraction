from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.models import BatchItem, BatchRun, ExtractionRun, Paper, PendingJob, RunArtifact, StructuredResult


ACTIVE_BATCH_JOB_STATUSES = ("pending", "redis_dispatched", "retry", "processing")
TERMINAL_BATCH_STATUSES = {"succeeded", "partial_failed", "failed", "cancelled"}


class BatchRepository:
    """Database queries for immutable batch manifests and compatible result reuse."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def get_by_submission_key(self, *, project_id: int, submission_key: str) -> BatchRun | None:
        return self.db.execute(
            select(BatchRun)
            .where(BatchRun.project_id == project_id, BatchRun.submission_key == submission_key)
            .execution_options(populate_existing=True)
        ).scalar_one_or_none()

    def get_by_id(self, batch_run_id: str) -> BatchRun:
        return self.db.execute(
            select(BatchRun).where(BatchRun.id == batch_run_id).execution_options(populate_existing=True)
        ).scalar_one()

    def lock_run(self, batch_run_id: str, *, skip_locked: bool = False) -> BatchRun | None:
        result = self.db.execute(
            select(BatchRun)
            .where(BatchRun.id == batch_run_id)
            .with_for_update(skip_locked=skip_locked)
            .execution_options(populate_existing=True)
        )
        if skip_locked:
            return result.scalar_one_or_none()
        return result.scalar_one()

    def active_job_count(self, batch_run_id: str) -> int:
        return int(
            self.db.execute(
                select(func.count(PendingJob.id))
                .join(BatchItem, PendingJob.batch_item_id == BatchItem.id)
                .where(
                    BatchItem.batch_run_id == batch_run_id,
                    PendingJob.status.in_(ACTIVE_BATCH_JOB_STATUSES),
                )
            ).scalar_one()
        )

    def pending_registered_item_candidates(
        self,
        batch_run_id: str,
        *,
        after: tuple[int, int] | None = None,
        limit: int = 64,
    ) -> list[tuple[str, int, int]]:
        statement = select(BatchItem.id, BatchItem.paper_id, BatchItem.ordinal).where(
            BatchItem.batch_run_id == batch_run_id,
            BatchItem.status == "pending",
            BatchItem.paper_id.is_not(None),
        )
        if after is not None:
            paper_id, ordinal = after
            statement = statement.where(
                or_(
                    BatchItem.paper_id > paper_id,
                    and_(BatchItem.paper_id == paper_id, BatchItem.ordinal > ordinal),
                )
            )
        return list(
            self.db.execute(
                statement.order_by(BatchItem.paper_id, BatchItem.ordinal).limit(limit)
            ).tuples()
        )

    def latest_job_for_item(self, batch_item_id: str) -> PendingJob | None:
        return self.db.execute(
            select(PendingJob)
            .where(PendingJob.batch_item_id == batch_item_id)
            .order_by(PendingJob.created_at.desc(), PendingJob.id.desc())
            .limit(1)
        ).scalar_one_or_none()

    def active_batch_run_ids(self, *, limit: int = 64) -> list[str]:
        active_jobs = (
            select(func.count(PendingJob.id))
            .join(BatchItem, PendingJob.batch_item_id == BatchItem.id)
            .where(
                BatchItem.batch_run_id == BatchRun.id,
                PendingJob.status.in_(ACTIVE_BATCH_JOB_STATUSES),
            )
            .correlate(BatchRun)
            .scalar_subquery()
        )
        return list(
            self.db.execute(
                select(BatchRun.id)
                .where(
                    BatchRun.status.in_(("pending", "running")),
                    BatchRun.batch_concurrency > active_jobs,
                )
                .order_by(BatchRun.updated_at, BatchRun.id)
                .limit(limit)
            ).scalars()
        )

    def compatible_successful_runs(
        self,
        *,
        project_id: int,
        source_sha256: str,
        result_config_hash: str,
    ) -> list[ExtractionRun]:
        has_normalized_result = exists(
            select(StructuredResult.id).where(StructuredResult.run_id == ExtractionRun.id)
        )
        has_raw_responses = exists(
            select(RunArtifact.id).where(
                RunArtifact.run_id == ExtractionRun.id,
                RunArtifact.role == "model_raw_responses",
            )
        )
        return list(
            self.db.execute(
                select(ExtractionRun)
                .join(Paper, ExtractionRun.paper_id == Paper.id)
                .where(
                    Paper.project_id == project_id,
                    Paper.file_hash == source_sha256,
                    ExtractionRun.status == "succeeded",
                    ExtractionRun.config_snapshot["result_config_hash"].as_string() == result_config_hash,
                    has_normalized_result,
                    has_raw_responses,
                )
                .options(selectinload(ExtractionRun.artifacts).selectinload(RunArtifact.object))
                .order_by(ExtractionRun.completed_at.desc(), ExtractionRun.created_at.desc())
            ).scalars()
        )

    def compatible_failed_job(self, *, paper_id: int, result_config_hash: str) -> PendingJob | None:
        """Return a terminal batch failure under the same semantic configuration.

        A matching failure resolves every waiting copy of the same PDF. Jobs without a
        batch snapshot remain deliberately unknown, so a non-batch outcome never
        decides a batch item's result.
        """
        return self.db.execute(
            select(PendingJob)
            .join(BatchItem, PendingJob.batch_item_id == BatchItem.id)
            .join(BatchRun, BatchItem.batch_run_id == BatchRun.id)
            .outerjoin(ExtractionRun, ExtractionRun.task_id == PendingJob.id)
            .where(
                PendingJob.paper_id == paper_id,
                BatchRun.result_config_hash == result_config_hash,
                or_(
                    PendingJob.status == "failed",
                    and_(
                        PendingJob.status == "done",
                        ExtractionRun.status.in_(("failed", "partial_failure")),
                    ),
                ),
            )
            .order_by(PendingJob.completed_at.desc(), PendingJob.id.desc())
            .limit(1)
        ).scalar_one_or_none()

    def unregistered_item_ids_for_run(self, batch_run_id: str) -> list[str]:
        return list(
            self.db.execute(
                select(BatchItem.id)
                .where(
                    BatchItem.batch_run_id == batch_run_id,
                    BatchItem.status == "pending",
                    BatchItem.paper_id.is_(None),
                )
                .order_by(BatchItem.ordinal)
            ).scalars()
        )

    def lock_item(self, item_id: str) -> BatchItem:
        return self.db.execute(
            select(BatchItem)
            .where(BatchItem.id == item_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).scalar_one()

    def status_counts_for_run(self, batch_run_id: str) -> dict[str, int]:
        return dict(
            self.db.execute(
                select(BatchItem.status, func.count())
                .where(BatchItem.batch_run_id == batch_run_id)
                .group_by(BatchItem.status)
            ).all()
        )

    def refresh_run_status(self, batch: BatchRun) -> None:
        self.db.flush()
        was_terminal = batch.status in TERMINAL_BATCH_STATUSES
        status_counts = self.status_counts_for_run(batch.id)
        if batch.status == "cancelling":
            if any(status in status_counts for status in {"queued", "processing"}):
                return
            batch.status = "cancelled"
        elif not status_counts:
            batch.status = "succeeded"
        elif any(status in status_counts for status in {"pending", "queued", "processing"}):
            batch.status = "running" if batch.status == "running" or self.active_job_count(batch.id) else "pending"
            batch.completed_at = None
            return
        elif set(status_counts) <= {"succeeded", "reused"}:
            batch.status = "succeeded"
        elif any(status in status_counts for status in {"succeeded", "reused"}) and "failed" in status_counts:
            batch.status = "partial_failed"
        elif set(status_counts) == {"cancelled"}:
            batch.status = "cancelled"
        else:
            batch.status = "failed"
        if not was_terminal or batch.completed_at is None:
            batch.completed_at = datetime.now(timezone.utc)

    def items_for_run(self, batch_run_id: str) -> list[BatchItem]:
        return (
            self.db.query(BatchItem)
            .filter(BatchItem.batch_run_id == batch_run_id)
            .order_by(BatchItem.ordinal)
            .all()
        )

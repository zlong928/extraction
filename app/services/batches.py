from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import BatchEvent, BatchItem, BatchRun, ExtractionRun, PendingJob
from app.config import (
    MINERU_API_BASE_URL,
    MINERU_LANGUAGE,
    MINERU_MODEL_VERSION,
    MINERU_POLL_INTERVAL_SECONDS,
    MINERU_TIMEOUT_SECONDS,
)
from app.config import PAPER_PARSE_QUEUE_NAME
from app.queue.contracts import queue_payload
from app.queue.redis_queue import RedisQueue
from app.repositories import BatchRepository
from app.services.extraction.llm_config import build_vlm_config
from app.repositories.jobs import JobRepository
from app.services.pdf.upload_service import PaperUploadService
from app.services.storage import StorageService, file_digest


_RESULT_SEMANTICS_KEY = "result_semantics"
_SCHEDULABLE_BATCH_STATUSES = {"pending", "running"}
_EXPLICIT_RETRY_STAGES = frozenset({"retry_requested", "retry_waiting_for_active_parse"})

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DiscoveredPdf:
    ordinal: int
    relative_path: str
    path: Path
    sha256: str
    size_bytes: int


@dataclass(frozen=True)
class _BatchRegistrationContext:
    id: str
    project_id: int
    source_root: str
    result_config_hash: str

    @classmethod
    def from_batch(cls, batch: BatchRun) -> _BatchRegistrationContext:
        return cls(
            id=batch.id,
            project_id=batch.project_id,
            source_root=batch.source_root,
            result_config_hash=batch.result_config_hash,
        )


@dataclass(frozen=True)
class _ReuseProbe:
    run_id: str | None = None
    unavailable: bool = False
    observed_run_ids: tuple[str, ...] = ()


def discover_pdfs(source_root: str | Path, *, limit: int | None = None) -> list[DiscoveredPdf]:
    root = Path(source_root).resolve()
    if not root.is_dir():
        raise ValueError(f"Batch source root is not a directory: {root}")
    if limit is not None and limit < 1:
        raise ValueError("Batch limit must be positive when provided.")
    paths = sorted(
        (path for path in root.rglob("*") if path.is_file() and path.suffix.lower() == ".pdf"),
        key=lambda path: path.relative_to(root).as_posix(),
    )
    if limit is not None:
        paths = paths[:limit]
    discovered: list[DiscoveredPdf] = []
    for ordinal, path in enumerate(paths):
        sha256, size_bytes = file_digest(path)
        discovered.append(
            DiscoveredPdf(
                ordinal=ordinal,
                relative_path=path.relative_to(root).as_posix(),
                path=path,
                sha256=sha256,
                size_bytes=size_bytes,
            )
        )
    return discovered


def result_config_hash(config_snapshot: dict[str, Any]) -> str:
    semantic_config = _semantic_config(config_snapshot)
    canonical = json.dumps(semantic_config, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _semantic_config(config_snapshot: dict[str, Any]) -> dict[str, Any]:
    semantic_config = config_snapshot.get(_RESULT_SEMANTICS_KEY)
    if not isinstance(semantic_config, dict) or not semantic_config:
        raise ValueError(f"Batch result config requires a non-empty {_RESULT_SEMANTICS_KEY} object.")
    return semantic_config


def _resolved_execution_snapshot(config_snapshot: dict[str, Any]) -> dict[str, Any]:
    """Freeze non-secret execution inputs while keeping the result hash semantic-only."""
    semantic_config = _semantic_config(config_snapshot)
    mineru_overrides = semantic_config.get("mineru", {})
    if not isinstance(mineru_overrides, dict):
        raise ValueError("result_semantics.mineru must be an object when provided.")
    allowed_mineru_keys = {
        "base_url",
        "model_version",
        "language",
        "timeout_seconds",
        "poll_interval_seconds",
        "is_ocr",
        "enable_formula",
        "enable_table",
    }
    unsupported_mineru_keys = set(mineru_overrides) - allowed_mineru_keys
    if unsupported_mineru_keys:
        keys = ", ".join(sorted(unsupported_mineru_keys))
        raise ValueError(f"Unsupported result_semantics.mineru fields: {keys}")
    mineru = {
        "base_url": MINERU_API_BASE_URL,
        "model_version": MINERU_MODEL_VERSION,
        "language": MINERU_LANGUAGE,
        "timeout_seconds": MINERU_TIMEOUT_SECONDS,
        "poll_interval_seconds": MINERU_POLL_INTERVAL_SECONDS,
        "is_ocr": False,
        "enable_formula": True,
        "enable_table": True,
    }
    mineru.update(mineru_overrides)

    vlm = build_vlm_config()
    vlm.update({key: value for key, value in semantic_config.items() if key not in {"mineru", "chart_only"}})
    vlm.pop("api_key", None)
    return {
        "mineru": mineru,
        "vlm": vlm,
        "llm_workers": max(1, int(config_snapshot.get("llm_workers", os.getenv("CONTENT_PIPELINE_LLM_WORKERS", "3")))),
    }


def _available_compatible_successful_run(
    repository: BatchRepository,
    storage: StorageService,
    *,
    project_id: int,
    source_sha256: str,
    result_config_hash: str,
) -> _ReuseProbe:
    candidates = repository.compatible_successful_runs(
        project_id=project_id,
        source_sha256=source_sha256,
        result_config_hash=result_config_hash,
    )
    observed_run_ids = tuple(candidate.id for candidate in candidates)
    probe_failed = False
    for candidate in candidates:
        raw_artifacts = [artifact for artifact in candidate.artifacts if artifact.role == "model_raw_responses"]
        for artifact in raw_artifacts:
            try:
                if storage.exists(artifact.object.object_key):
                    return _ReuseProbe(run_id=candidate.id, observed_run_ids=observed_run_ids)
            except Exception as exc:
                probe_failed = True
                logger.warning("compatible result availability probe failed object_key=%s: %s", artifact.object.object_key, exc)
    return _ReuseProbe(unavailable=probe_failed, observed_run_ids=observed_run_ids)


class BatchSubmissionService:
    def __init__(self, db: Session, storage: StorageService | None = None) -> None:
        self.db = db
        self.storage = storage or StorageService()
        self.repository = BatchRepository(db)

    def submit(
        self,
        *,
        project_id: int,
        source_root: str | Path,
        submission_key: str,
        batch_concurrency: int,
        config_snapshot: dict[str, Any],
        limit: int | None = None,
    ) -> BatchRun:
        if not submission_key:
            raise ValueError("Batch submission key is required.")
        if batch_concurrency < 1:
            raise ValueError("Batch concurrency must be positive.")
        resolved_config = dict(config_snapshot)
        configured_hash = resolved_config.get("result_config_hash")
        expected_hash = result_config_hash(resolved_config)
        if configured_hash is not None and configured_hash != expected_hash:
            raise ValueError("result_config_hash does not match the semantic configuration.")
        resolved_config["result_config_hash"] = configured_hash or expected_hash
        resolved_config["execution"] = _resolved_execution_snapshot(resolved_config)

        existing = self.repository.get_by_submission_key(project_id=project_id, submission_key=submission_key)
        if existing is not None:
            batch = self._resume_registration(existing)
            BatchScheduler(self.db, self.storage).schedule(batch.id)
            return self.repository.get_by_id(batch.id)
        discovered = discover_pdfs(source_root, limit=limit)
        batch = BatchRun(
            project_id=project_id,
            submission_key=submission_key,
            source_root=str(Path(source_root).resolve()),
            batch_concurrency=batch_concurrency,
            config_snapshot=resolved_config,
            result_config_hash=resolved_config["result_config_hash"],
        )
        try:
            with self.db.begin_nested():
                self.db.add(batch)
                self.db.flush()
                self.db.add_all(
                    [
                        BatchItem(
                            batch_run_id=batch.id,
                            ordinal=item.ordinal,
                            source_relative_path=item.relative_path,
                            source_sha256=item.sha256,
                            source_size_bytes=item.size_bytes,
                        )
                        for item in discovered
                    ]
                )
                self.db.flush()
        except IntegrityError:
            existing = self.repository.get_by_submission_key(project_id=project_id, submission_key=submission_key)
            if existing is None:
                raise
            batch = self._resume_registration(existing)
            BatchScheduler(self.db, self.storage).schedule(batch.id)
            return self.repository.get_by_id(batch.id)
        self.db.commit()
        self.db.refresh(batch)

        batch = self._resume_registration(batch)
        BatchScheduler(self.db, self.storage).schedule(batch.id)
        return self.repository.get_by_id(batch.id)

    def _resume_registration(self, batch: BatchRun) -> BatchRun:
        context = _BatchRegistrationContext.from_batch(batch)
        for item_id in self.repository.unregistered_item_ids_for_run(context.id):
            item = self.repository.lock_item(item_id)
            if item.status != "pending" or item.paper_id is not None:
                continue
            self._register_and_resolve_item(context, item)
        batch = self.repository.get_by_id(context.id)
        self.repository.refresh_run_status(batch)
        self.db.commit()
        self.db.refresh(batch)
        return batch

    def _register_and_resolve_item(self, context: _BatchRegistrationContext, item: BatchItem) -> None:
        source_path = Path(context.source_root) / item.source_relative_path
        try:
            content = source_path.read_bytes()
            if hashlib.sha256(content).hexdigest() != item.source_sha256:
                raise ValueError("Source PDF changed after batch discovery.")
            with self.db.begin_nested():
                registration = PaperUploadService(self.db, self.storage).register_pdf(
                    filename=item.source_relative_path,
                    content=content,
                    project_id=context.project_id,
                    commit=False,
                )
                item.paper_id = registration.paper.id
                probe = _available_compatible_successful_run(
                    self.repository,
                    self.storage,
                    project_id=context.project_id,
                    source_sha256=item.source_sha256,
                    result_config_hash=context.result_config_hash,
                )
                if probe.run_id is not None:
                    item.status = "reused"
                    item.current_stage = "reused"
                    item.resolved_extraction_run_id = probe.run_id
                    self.db.add(
                        BatchEvent(
                            batch_run_id=context.id,
                            batch_item_id=item.id,
                            event_type="item_reused",
                            data={"extraction_run_id": probe.run_id},
                        )
                    )
                else:
                    item.status = "pending"
                    item.current_stage = "reuse_availability_unknown" if probe.unavailable else "registered"
                    self.db.add(BatchEvent(batch_run_id=context.id, batch_item_id=item.id, event_type="item_registered", data={}))
        except (OSError, ValueError) as exc:
            item.status = "failed"
            item.current_stage = "registration"
            item.error_message = str(exc)
            self.db.add(
                BatchEvent(
                    batch_run_id=context.id,
                    batch_item_id=item.id,
                    event_type="item_failed",
                    data={"error": str(exc)},
                )
            )
            self.db.commit()
            return
        self.db.commit()

class BatchScheduler:
    """Creates durable batch Jobs inside a bounded per-BatchRun window."""

    def __init__(self, db: Session, storage: StorageService | None = None) -> None:
        self.db = db
        self.storage = storage or StorageService()
        self.repository = BatchRepository(db)
        self.jobs = JobRepository(db)

    def schedule(self, batch_run_id: str) -> list[int]:
        probes = self._probe_reuse_candidates(batch_run_id)
        job_ids = self._schedule_transaction(batch_run_id, reuse_probes=probes)
        for job_id in job_ids:
            self.dispatch_committed_job(job_id)
        return job_ids

    def schedule_open_batches(self) -> list[int]:
        scheduled: list[int] = []
        for batch_run_id in self.repository.active_batch_run_ids():
            try:
                probes = self._probe_reuse_candidates(batch_run_id)
                job_ids = self._schedule_transaction(batch_run_id, skip_locked=True, reuse_probes=probes)
            except Exception:
                self.db.rollback()
                logger.exception("batch scheduling recovery failed batch_run_id=%s", batch_run_id)
                continue
            for job_id in job_ids:
                self.dispatch_committed_job(job_id)
            scheduled.extend(job_ids)
        return scheduled

    def dispatch_committed_job(self, job_id: int) -> bool:
        try:
            RedisQueue(PAPER_PARSE_QUEUE_NAME).enqueue(queue_payload("paper_parse", job_id))
        except Exception as exc:
            logger.warning("batch redis dispatch failed job_id=%s: %s", job_id, exc)
            return False
        job = self.db.get(PendingJob, job_id, with_for_update=True)
        if job is not None and job.status in {"pending", "retry"}:
            job.status = "redis_dispatched"
            self.db.commit()
        elif job is not None:
            self.db.rollback()
        return True

    def _probe_reuse_candidates(self, batch_run_id: str) -> dict[str, _ReuseProbe]:
        batch = self.db.get(BatchRun, batch_run_id)
        if batch is None:
            self.db.rollback()
            return {}
        source_hashes = self.db.execute(
            select(BatchItem.source_sha256)
            .where(
                BatchItem.batch_run_id == batch_run_id,
                BatchItem.status == "pending",
                BatchItem.paper_id.is_not(None),
                or_(BatchItem.current_stage.is_(None), BatchItem.current_stage.not_in(_EXPLICIT_RETRY_STAGES)),
            )
            .distinct()
        ).scalars()
        probes = {
            source_sha256: _available_compatible_successful_run(
                self.repository,
                self.storage,
                project_id=batch.project_id,
                source_sha256=source_sha256,
                result_config_hash=batch.result_config_hash,
            )
            for source_sha256 in source_hashes
        }
        self.db.rollback()
        return probes

    def _schedule_transaction(
        self,
        batch_run_id: str,
        *,
        skip_locked: bool = False,
        reuse_probes: dict[str, _ReuseProbe] | None = None,
    ) -> list[int]:
        batch = self.repository.lock_run(batch_run_id, skip_locked=skip_locked)
        if batch is None:
            self.db.rollback()
            return []
        if batch.status not in _SCHEDULABLE_BATCH_STATUSES:
            self.db.commit()
            return []
        remaining = batch.batch_concurrency - self.repository.active_job_count(batch.id)
        if remaining <= 0:
            self.db.commit()
            return []

        created_job_ids: list[int] = []
        cursor: tuple[int, int] | None = None
        while remaining > 0:
            candidates = self.repository.pending_registered_item_candidates(batch.id, after=cursor)
            if not candidates:
                break
            for item_id, paper_id, ordinal in candidates:
                cursor = (paper_id, ordinal)
                if remaining <= 0:
                    break
                item = self.repository.lock_item(item_id)
                if item.status != "pending" or item.paper_id is None:
                    continue
                retry_requested = item.current_stage in _EXPLICIT_RETRY_STAGES
                self.jobs.lock_paper(item.paper_id)
                if self.jobs.active_paper_parse(item.paper_id) is not None:
                    item.current_stage = "retry_waiting_for_active_parse" if retry_requested else "waiting_for_active_parse"
                    continue

                if not retry_requested:
                    probe = (reuse_probes or {}).get(item.source_sha256, _ReuseProbe(unavailable=True))
                    if probe.unavailable:
                        item.current_stage = "reuse_availability_unknown"
                        continue
                    compatible = self.db.get(ExtractionRun, probe.run_id) if probe.run_id is not None else None
                    if compatible is not None:
                        item.status = "reused"
                        item.current_stage = "reused"
                        item.resolved_extraction_run_id = compatible.id
                        self.db.add(
                            BatchEvent(
                                batch_run_id=batch.id,
                                batch_item_id=item.id,
                                event_type="item_reused",
                                data={"extraction_run_id": compatible.id},
                            )
                        )
                        continue

                    current_candidates = self.repository.compatible_successful_runs(
                        project_id=batch.project_id,
                        source_sha256=item.source_sha256,
                        result_config_hash=batch.result_config_hash,
                    )
                    if any(candidate.id not in probe.observed_run_ids for candidate in current_candidates):
                        item.current_stage = "reuse_probe_stale"
                        continue

                    failed_job = self.repository.compatible_failed_job(
                        paper_id=item.paper_id,
                        result_config_hash=batch.result_config_hash,
                    )
                    if failed_job is not None:
                        item.status = "failed"
                        item.current_stage = "compatible_failure"
                        item.error_message = failed_job.error_message or "Compatible batch execution failed."
                        self.db.add(
                            BatchEvent(
                                batch_run_id=batch.id,
                                batch_item_id=item.id,
                                event_type="item_failed",
                                data={"job_id": failed_job.id, "reason": "compatible_execution_failed"},
                            )
                        )
                        continue

                previous = self.repository.latest_job_for_item(item.id)
                retry_source = previous
                if retry_requested and retry_source is None:
                    retry_source = self.repository.compatible_failed_job(
                        paper_id=item.paper_id,
                        result_config_hash=batch.result_config_hash,
                    )
                retry_of_job_id = retry_source.id if retry_requested and retry_source is not None else None
                job, created = self.jobs.create_batch_paper_parse_under_lock(
                    paper_id=item.paper_id,
                    batch_item_id=item.id,
                    retry_of_job_id=retry_of_job_id,
                )
                if not created:
                    item.current_stage = "retry_waiting_for_active_parse" if retry_requested else "waiting_for_active_parse"
                    continue
                item.status = "queued"
                item.current_stage = "queued"
                item.error_message = None
                self.db.add(
                    BatchEvent(
                        batch_run_id=batch.id,
                        batch_item_id=item.id,
                        event_type="item_queued",
                        data={"job_id": job.id},
                    )
                )
                created_job_ids.append(job.id)
                remaining -= 1

        self.repository.refresh_run_status(batch)
        self.db.commit()
        return created_job_ids


class BatchLifecycleService:
    """Owns explicit Batch cancellation and retry intent transitions."""

    def __init__(self, db: Session) -> None:
        self.db = db
        self.repository = BatchRepository(db)

    def cancel(self, batch_run_id: str) -> BatchRun:
        batch = self.repository.lock_run(batch_run_id)
        if batch.status in {"succeeded", "partial_failed", "failed", "cancelled"}:
            raise ValueError(f"Batch {batch.id} is already terminal and cannot be cancelled.")
        batch.status = "cancelling"
        job_ids = list(
            self.db.execute(
                select(PendingJob.id)
                .join(BatchItem, PendingJob.batch_item_id == BatchItem.id)
                .where(
                    BatchItem.batch_run_id == batch.id,
                    PendingJob.status.in_(("pending", "redis_dispatched", "retry")),
                )
                .order_by(PendingJob.id)
            ).scalars()
        )
        cancelled_item_ids: set[str] = set()
        for job_id in job_ids:
            job = self.db.execute(
                select(PendingJob).where(PendingJob.id == job_id).with_for_update().execution_options(populate_existing=True)
            ).scalar_one()
            if job.status not in {"pending", "redis_dispatched", "retry"}:
                continue
            item = self.repository.lock_item(job.batch_item_id or "")
            if item.status != "queued":
                continue
            job.status = "cancelled"
            job.completed_at = datetime.now(timezone.utc)
            job.lease_owner = None
            job.lease_expires_at = None
            item.status = "cancelled"
            item.current_stage = "cancelled"
            item.error_message = "Batch cancellation requested before processing."
            cancelled_item_ids.add(item.id)
            self.db.add(
                BatchEvent(
                    batch_run_id=batch.id,
                    batch_item_id=item.id,
                    event_type="item_cancelled",
                    data={"job_id": job.id},
                )
            )
        pending_item_ids = list(
            self.db.execute(
                select(BatchItem.id)
                .where(BatchItem.batch_run_id == batch.id, BatchItem.status == "pending")
                .order_by(BatchItem.ordinal)
            ).scalars()
        )
        for item_id in pending_item_ids:
            if item_id in cancelled_item_ids:
                continue
            item = self.repository.lock_item(item_id)
            item.status = "cancelled"
            item.current_stage = "cancelled"
            item.error_message = "Batch cancellation requested before scheduling."
            self.db.add(
                BatchEvent(
                    batch_run_id=batch.id,
                    batch_item_id=item.id,
                    event_type="item_cancelled",
                    data={},
                )
            )
        self.repository.refresh_run_status(batch)
        self.db.commit()
        return self.repository.get_by_id(batch.id)

    def retry_failed_items(self, batch_run_id: str, item_ids: list[str]) -> list[int]:
        if not item_ids:
            raise ValueError("At least one failed BatchItem is required for retry.")
        batch = self.repository.lock_run(batch_run_id)
        if batch.status not in {"failed", "partial_failed"}:
            raise ValueError("Only failed or partial-failed batches can be retried.")
        selected_ids = list(
            self.db.execute(
                select(BatchItem.id)
                .where(BatchItem.batch_run_id == batch.id, BatchItem.id.in_(item_ids))
                .order_by(BatchItem.ordinal)
            ).scalars()
        )
        if set(selected_ids) != set(item_ids):
            raise ValueError("Every retried item must belong to the BatchRun.")
        for item_id in selected_ids:
            item = self.repository.lock_item(item_id)
            if item.status != "failed":
                raise ValueError(f"BatchItem {item.id} is not failed and cannot be retried.")
            item.status = "pending"
            item.current_stage = "retry_requested"
            item.error_message = None
            item.resolved_extraction_run_id = None
            self.db.add(
                BatchEvent(
                    batch_run_id=batch.id,
                    batch_item_id=item.id,
                    event_type="item_retry_requested",
                    data={},
                )
            )
        batch.status = "running"
        batch.completed_at = None
        self.db.commit()
        return BatchScheduler(self.db).schedule(batch.id)


class BatchOperationsService:
    """Read durable batch progress and rebuild deterministic audit exports."""

    def __init__(self, db: Session) -> None:
        self.db = db
        self.repository = BatchRepository(db)

    def snapshot(self, batch_run_id: str) -> dict[str, Any]:
        batch = self.repository.get_by_id(batch_run_id)
        counts = self.repository.status_counts_for_run(batch.id)
        items = self.repository.items_for_run(batch.id)
        return {
            "id": batch.id,
            "project_id": batch.project_id,
            "submission_key": batch.submission_key,
            "status": batch.status,
            "batch_concurrency": batch.batch_concurrency,
            "result_config_hash": batch.result_config_hash,
            "counts": {status: counts.get(status, 0) for status in (
                "pending", "queued", "processing", "succeeded", "failed", "reused", "cancelled"
            )},
            "total": len(items),
            "completed_at": batch.completed_at.isoformat() if batch.completed_at else None,
        }

    def export(self, batch_run_id: str, output_dir: str | Path) -> tuple[Path, Path]:
        batch = self.repository.get_by_id(batch_run_id)
        items = self.repository.items_for_run(batch.id)
        events = list(
            self.db.execute(
                select(BatchEvent)
                .where(BatchEvent.batch_run_id == batch.id)
                .order_by(BatchEvent.created_at, BatchEvent.id)
            ).scalars()
        )
        root = Path(output_dir)
        root.mkdir(parents=True, exist_ok=True)
        manifest_path = root / "manifest.json"
        events_path = root / "events.jsonl"
        manifest = {
            "schema_version": "batch-manifest.v1",
            "batch_run_id": batch.id,
            "project_id": batch.project_id,
            "submission_key": batch.submission_key,
            "result_config_hash": batch.result_config_hash,
            "items": [
                {
                    "ordinal": item.ordinal,
                    "source_relative_path": item.source_relative_path,
                    "source_sha256": item.source_sha256,
                    "source_size_bytes": item.source_size_bytes,
                    "status": item.status,
                    "paper_id": item.paper_id,
                    "resolved_extraction_run_id": item.resolved_extraction_run_id,
                }
                for item in items
            ],
        }
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        event_lines = [
            json.dumps(
                {
                    "id": event.id,
                    "batch_run_id": event.batch_run_id,
                    "batch_item_id": event.batch_item_id,
                    "event_type": event.event_type,
                    "data": event.data,
                    "created_at": event.created_at.isoformat(),
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            for event in events
        ]
        events_path.write_text("\n".join(event_lines) + ("\n" if event_lines else ""), encoding="utf-8")
        return manifest_path, events_path

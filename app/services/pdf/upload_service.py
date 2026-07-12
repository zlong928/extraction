from __future__ import annotations

import hashlib
import os
from pathlib import Path

from redis.exceptions import RedisError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import MAX_UPLOAD_SIZE_BYTES, PAPER_PARSE_QUEUE_NAME
from app.models import Paper, PaperStatus
from app.repositories import JobRepository
from app.queue.contracts import queue_payload
from app.queue.redis_queue import RedisQueue
from app.services.storage import StorageService
from app.services.object_store import ObjectStore
from app.services.pdf.locks import ChartOnlyRunAlreadyActive, chart_only_run_lock
from app.services.pdf.pipeline import prepare_chart_only_run_for_paper, run_chart_only_for_paper
from app.models.job import PendingJob
from app.services.pdf.validation import PdfValidationError, validate_pdf_file


class PaperUploadService:
    def __init__(self, db: Session, storage: StorageService | None = None) -> None:
        self.db = db
        self.storage = storage or StorageService()

    def create_from_upload(self, *, filename: str, content: bytes, title: str | None = None) -> Paper:
        if len(content) > MAX_UPLOAD_SIZE_BYTES:
            raise ValueError(f"File is too large. Maximum size is {MAX_UPLOAD_SIZE_BYTES} bytes.")
        if not content:
            raise ValueError("File is empty.")
        if not content.startswith(b"%PDF-"):
            raise ValueError("Only PDF files are supported in the slim extraction service.")

        safe_name = self.storage.safe_filename(filename)
        file_hash = hashlib.sha256(content).hexdigest()

        _validate_pdf_content_sync(content, safe_name)
        project_id = 1
        existing = (
            self.db.query(Paper)
            .filter(
                Paper.project_id == project_id,
                Paper.file_hash == file_hash,
                Paper.status != PaperStatus.DELETED,
            )
            .first()
        )
        if existing is not None:
            if existing.status == PaperStatus.FAILED:
                stored = ObjectStore(self.db, self.storage.adapter).put_bytes(
                    key=f"papers/{existing.id}/source/{file_hash}.pdf",
                    data=content,
                    media_type="application/pdf",
                    metadata={"role": "source_pdf", "original_filename": safe_name},
                )
                existing.title = title or existing.title or Path(safe_name).stem
                existing.original_filename = safe_name
                existing.file_path = stored.object_key
                existing.pdf_object_id = stored.id
                existing.file_size = len(content)
                existing.mime_type = "application/pdf"
                self.enqueue_parse(existing, reset=True)
                return existing
            return existing

        paper = Paper(
            project_id=project_id,
            title=title or Path(safe_name).stem,
            original_filename=safe_name,
            file_path="pending",
            file_size=len(content),
            file_hash=file_hash,
            status=PaperStatus.PENDING,
        )
        try:
            with self.db.begin_nested():
                self.db.add(paper)
                self.db.flush()
        except IntegrityError:
            winner = (
                self.db.query(Paper)
                .filter(
                    Paper.project_id == project_id,
                    Paper.file_hash == file_hash,
                    Paper.status != PaperStatus.DELETED,
                )
                .one_or_none()
            )
            if winner is None:
                raise
            return winner

        stored = ObjectStore(self.db, self.storage.adapter).put_bytes(
            key=f"papers/{paper.id}/source/{file_hash}.pdf",
            data=content,
            media_type="application/pdf",
            metadata={"role": "source_pdf", "original_filename": safe_name},
        )
        paper.file_path = stored.object_key
        paper.pdf_object_id = stored.id
        self.db.commit()
        self.db.refresh(paper)
        self._enqueue_or_parse_sync(paper)
        return paper

    def _enqueue_or_parse_sync(self, paper: Paper) -> None:
        from app.services.pdf.parse_service import PaperParseService

        attempt = self.db.query(PendingJob).filter(
            PendingJob.paper_id == paper.id, PendingJob.task_type == "paper_parse"
        ).count() + 1
        job, created = JobRepository(self.db).get_or_create(
            paper_id=paper.id,
            task_type="paper_parse",
            idempotency_key=f"paper-parse:{paper.id}:{paper.file_hash}:attempt:{attempt}",
            attempt=attempt,
        )
        self.db.commit()
        try:
            if created or job.status in {"pending", "redis_dispatched"}:
                RedisQueue(PAPER_PARSE_QUEUE_NAME).enqueue(queue_payload("paper_parse", job.id))
        except RedisError:
            self.db.refresh(paper)
            claimed = JobRepository(self.db).claim(job.id, worker_id=f"sync:{os.getpid()}")
            if claimed is None:
                return
            self.db.commit()
            self.db.refresh(claimed)
            PaperParseService(self.db, self.storage).parse_or_fail(paper.id, job=claimed)

    def enqueue_parse(self, paper: Paper, *, reset: bool = False) -> Paper:
        if paper.status == PaperStatus.DELETED:
            raise ValueError("Deleted papers cannot be parsed.")
        if paper.status in {PaperStatus.PENDING, PaperStatus.PROCESSING} and not reset:
            return paper
        if reset:
            paper.status = PaperStatus.PENDING
            paper.error_message = None
            paper.text_content = None
            paper.mineru_markdown = None
            paper.mineru_artifact_dir = None
            paper.mineru_extract_dir = None
            paper.mineru_content_list_path = None
            paper.mineru_content_object_id = None
            paper.mineru_layout_object_id = None
            paper.mineru_markdown_object_id = None
            paper.page_count = None
            from app.services.pdf.parse_service import _clear_parse_outputs
            _clear_parse_outputs(self.db, paper)
            self.db.commit()
            self.db.refresh(paper)
        self._enqueue_or_parse_sync(paper)
        return paper

    def enqueue_chart_only_run(self, paper: Paper) -> Paper:
        if paper.status == PaperStatus.DELETED:
            raise ValueError("Deleted papers cannot run chart-only extraction.")
        if str(paper.status) == PaperStatus.PROCESSING.value:
            return paper
        if not paper.mineru_content_list_path:
            raise ValueError("Paper has no MinerU content_list path.")
        if not self.storage.exists(paper.mineru_content_list_path):
            raise ValueError("MinerU content_list file not found.")
        attempt = self.db.query(PendingJob).filter(
            PendingJob.paper_id == paper.id, PendingJob.task_type == "chart_only_run"
        ).count() + 1
        job, _ = JobRepository(self.db).get_or_create(
            paper_id=paper.id,
            task_type="chart_only_run",
            idempotency_key=f"chart-only:{paper.id}:{paper.mineru_content_object_id or paper.file_hash}:attempt:{attempt}",
            attempt=attempt,
        )
        queue = RedisQueue(PAPER_PARSE_QUEUE_NAME)
        try:
            queue.ping()
            with chart_only_run_lock(paper.id, blocking=False):
                prepare_chart_only_run_for_paper(paper)
                queue.enqueue(queue_payload("chart_only_run", job.id))
                self.db.commit()
            self.db.refresh(paper)
            return paper
        except RedisError:
            claimed = JobRepository(self.db).claim(job.id, worker_id=f"sync:{os.getpid()}")
            if claimed is None:
                return paper
            self.db.commit()
            self.db.refresh(claimed)
            with chart_only_run_lock(paper.id, blocking=False):
                prepare_chart_only_run_for_paper(paper)
                try:
                    run_chart_only_for_paper(paper, job=claimed)
                    paper.status = PaperStatus.DONE.value
                    paper.error_message = None
                    self.db.commit()
                    self.db.refresh(paper)
                    return paper
                except Exception as exc:
                    paper.status = PaperStatus.FAILED.value
                    paper.error_message = f"chart-only extraction fallback failed: {exc}"
                    self.db.commit()
                    raise
        except ChartOnlyRunAlreadyActive:
            return paper


def _validate_pdf_content_sync(content: bytes, filename: str) -> None:
    import tempfile
    path = Path(tempfile.gettempdir()) / f"__validate_{abs(hash(content))}_{filename}"
    try:
        path.write_bytes(content)
        validate_pdf_file(str(path))
    except PdfValidationError:
        raise
    except Exception:
        pass
    finally:
        path.unlink(missing_ok=True)

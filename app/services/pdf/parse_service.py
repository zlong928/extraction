from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

from sqlalchemy.orm import Session

from app.models import Paper, PaperStatus, PendingJob
from app.repositories import JobRepository, LostJobLease
from app.services.object_store import ObjectStore
from app.services.mineru_asset_builder import MinerUAssetBuilder
from app.services.mineru_parser import MinerUParserService
from app.services.storage import StorageService
from app.services.pdf.locks import chart_only_run_lock
from app.services.pdf.pipeline import run_chart_only_for_paper
from app.services.pdf.validation import PdfValidationError


class PaperParseService:
    def __init__(self, db: Session, storage: StorageService | None = None) -> None:
        self.db = db
        self.storage = storage or StorageService()

    def parse(self, paper: Paper, *, job: PendingJob | None = None) -> Paper:
        if paper.status == PaperStatus.DELETED:
            raise ValueError("Deleted papers cannot be parsed.")
        paper.status = PaperStatus.PROCESSING
        paper.error_message = None
        _clear_parse_outputs(self.db, paper)
        self.db.flush()

        with self.storage.materialize(paper.file_path, suffix=".pdf") as pdf_path:
            with tempfile.TemporaryDirectory(prefix=f"mineru-paper-{paper.id}-") as temp_dir:
                mineru = MinerUParserService().parse_pdf_file(
                    pdf_path,
                    data_id=f"paper-{paper.id}",
                    output_root=Path(temp_dir),
                )
                raw_markdown = mineru.original_markdown or "\n\n".join(mineru.parsed_document.text_pages).strip()
                paper.text_content = "\n\n".join(mineru.parsed_document.text_pages).strip()[:20000]
                paper.mineru_markdown = None
                paper.page_count = len(mineru.parsed_document.pages) or None
                paper.status = PaperStatus.PROCESSING

                if not raw_markdown.strip() and not mineru.content_list_path:
                    raise PdfValidationError(
                        f"MinerU returned no content for '{paper.original_filename}': "
                        f"empty markdown and no content_list"
                    )

                store = ObjectStore(self.db, self.storage.adapter)
                run_key = str(mineru.batch_id or "mineru")
                if raw_markdown:
                    markdown_object = store.put_bytes(
                        key=f"papers/{paper.id}/mineru/{run_key}/document.md",
                        data=raw_markdown.encode("utf-8"),
                        media_type="text/markdown",
                        metadata={"role": "mineru_markdown", "batch_id": run_key},
                    )
                    paper.mineru_markdown_object_id = markdown_object.id
                if mineru.content_list_path and Path(mineru.content_list_path).is_file():
                    content_object = store.put_file(
                        key=f"papers/{paper.id}/mineru/{run_key}/content_list.json",
                        source=mineru.content_list_path,
                        media_type="application/json",
                        metadata={"role": "mineru_content_list", "batch_id": run_key},
                    )
                    paper.mineru_content_object_id = content_object.id
                    paper.mineru_content_list_path = content_object.object_key
                if mineru.layout_path and Path(mineru.layout_path).is_file():
                    layout_object = store.put_file(
                        key=f"papers/{paper.id}/mineru/{run_key}/layout.json",
                        source=mineru.layout_path,
                        media_type="application/json",
                        metadata={"role": "mineru_layout", "batch_id": run_key},
                    )
                    paper.mineru_layout_object_id = layout_object.id
                    self._store_layout_data(paper, mineru.layout_path)
                if mineru.extract_dir and Path(mineru.extract_dir).is_dir():
                    archive_base = Path(temp_dir) / "mineru-raw"
                    archive_path = Path(shutil.make_archive(str(archive_base), "zip", mineru.extract_dir))
                    raw_object = store.put_file(
                        key=f"papers/{paper.id}/mineru/{run_key}/raw.zip",
                        source=archive_path,
                        media_type="application/zip",
                        metadata={"role": "mineru_raw_output", "batch_id": run_key},
                    )
                    paper.mineru_artifact_dir = raw_object.object_key
                    paper.mineru_extract_dir = f"papers/{paper.id}/mineru/{run_key}"

                MinerUAssetBuilder(self.db, self.storage).ingest(
                    paper,
                    raw_markdown,
                    mineru.extract_dir,
                    content_list_path=mineru.content_list_path,
                    layout_path=mineru.layout_path,
                )
        if job is not None:
            JobRepository(self.db).assert_ownership(job, for_update=True)
        self.db.commit()
        self.db.refresh(paper)
        if paper.mineru_content_list_path:
            with chart_only_run_lock(paper.id, blocking=True):
                summary = run_chart_only_for_paper(paper, job=job)
                if summary.get("status") == "failed":
                    raise RuntimeError("Content extraction completed without a publishable result")
        paper.status = PaperStatus.DONE
        if job is not None and job.status == "processing":
            JobRepository(self.db).complete(job)
        self.db.commit()
        self.db.refresh(paper)
        return paper

    @staticmethod
    def _store_layout_data(paper: Paper, layout_path: str) -> None:
        try:
            path = Path(layout_path)
            if path.is_file() and path.suffix.lower() == ".json":
                data = json.loads(path.read_text(encoding="utf-8"))
                paper.layout_data = json.dumps({"page_count": len(data.get("pages", []))} if isinstance(data, dict) else {}, ensure_ascii=False)
        except Exception:
            pass

    def parse_or_fail(self, paper_id: int, *, job: PendingJob | None = None) -> Paper | None:
        paper = self.db.get(Paper, paper_id)
        if paper is None:
            return None
        try:
            return self.parse(paper, job=job)
        except Exception as exc:
            self.db.rollback()
            paper = self.db.get(Paper, paper_id)
            if paper is None:
                return None
            if job is not None:
                try:
                    JobRepository(self.db).assert_ownership(job, for_update=True)
                except LostJobLease:
                    self.db.rollback()
                    return paper
            paper.status = PaperStatus.FAILED
            paper.error_message = str(exc)
            if job is not None and job.status not in {"done", "failed"}:
                JobRepository(self.db).fail(job, str(exc))
        self.db.commit()
        return paper


def _clear_parse_outputs(db: Session, paper: Paper) -> None:
    from app.models import Figure, ImageExtraction, Panel, PaperAsset

    assets = db.query(PaperAsset).filter(
        PaperAsset.paper_id == paper.id, PaperAsset.is_active.is_(True)
    ).all()
    figure_ids = [row[0] for row in db.query(Figure.id).filter(Figure.paper_id == paper.id).all()]
    for asset in assets:
        asset.is_active = False
        asset.figure_id = None
    db.flush()
    if figure_ids:
        db.query(ImageExtraction).filter(ImageExtraction.figure_id.in_(figure_ids)).update(
            {ImageExtraction.figure_id: None}, synchronize_session=False
        )
        db.query(Panel).filter(Panel.figure_id.in_(figure_ids)).delete(synchronize_session=False)
    db.query(Figure).filter(Figure.paper_id == paper.id).delete(synchronize_session=False)

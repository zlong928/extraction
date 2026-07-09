from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import RESULT_DIR
from app.models import Paper, PaperStatus
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

    def parse(self, paper: Paper) -> Paper:
        if paper.status == PaperStatus.DELETED:
            raise ValueError("Deleted papers cannot be parsed.")
        pdf_path = self.storage.absolute_path(paper.file_path)
        paper.status = PaperStatus.PROCESSING
        paper.error_message = None
        _clear_parse_outputs(self.db, paper)
        self.db.flush()

        mineru = MinerUParserService().parse_pdf_file(
            pdf_path,
            data_id=f"paper-{paper.id}",
            output_root=RESULT_DIR / "mineru",
        )
        raw_markdown = mineru.original_markdown or "\n\n".join(mineru.parsed_document.text_pages).strip()
        paper.text_content = "\n\n".join(mineru.parsed_document.text_pages).strip()
        paper.mineru_markdown = raw_markdown
        paper.mineru_artifact_dir = mineru.artifact_dir
        paper.mineru_extract_dir = mineru.extract_dir
        paper.mineru_content_list_path = mineru.content_list_path
        paper.page_count = len(mineru.parsed_document.pages) or None

        if mineru.layout_path:
            self._store_layout_data(paper, mineru.layout_path)

        paper.status = PaperStatus.PROCESSING

        if not raw_markdown.strip() and not mineru.content_list_path:
            raise PdfValidationError(
                f"MinerU returned no content for '{paper.original_filename}': "
                f"empty markdown and no content_list"
            )

        MinerUAssetBuilder(self.db, self.storage).ingest(
            paper,
            raw_markdown,
            mineru.extract_dir,
            content_list_path=mineru.content_list_path,
            layout_path=mineru.layout_path,
        )
        self.db.commit()
        self.db.refresh(paper)
        if paper.mineru_content_list_path:
            with chart_only_run_lock(paper.id, blocking=True):
                run_chart_only_for_paper(paper)
        paper.status = PaperStatus.DONE
        self.db.commit()
        self.db.refresh(paper)
        return paper

    @staticmethod
    def _store_layout_data(paper: Paper, layout_path: str) -> None:
        try:
            path = Path(layout_path)
            if path.is_file() and path.suffix.lower() == ".json":
                data = json.loads(path.read_text(encoding="utf-8"))
                paper.layout_data = json.dumps(data, ensure_ascii=False)
        except Exception:
            pass

    def parse_or_fail(self, paper_id: int) -> Paper | None:
        paper = self.db.get(Paper, paper_id)
        if paper is None:
            return None
        try:
            return self.parse(paper)
        except Exception as exc:
            paper.status = PaperStatus.FAILED
            paper.error_message = str(exc)
        self.db.commit()
        return paper


def _clear_parse_outputs(db: Session, paper: Paper) -> None:
    from sqlalchemy import or_

    from app.models import Figure, ImageExtraction, Panel, PaperAsset

    asset_ids = [row[0] for row in db.query(PaperAsset.id).filter(PaperAsset.paper_id == paper.id).all()]
    figure_ids = [row[0] for row in db.query(Figure.id).filter(Figure.paper_id == paper.id).all()]

    extraction_filters = []
    if asset_ids:
        extraction_filters.append(ImageExtraction.asset_id.in_(asset_ids))
    if figure_ids:
        extraction_filters.append(ImageExtraction.figure_id.in_(figure_ids))
    if extraction_filters:
        db.query(ImageExtraction).filter(or_(*extraction_filters)).delete(synchronize_session=False)
    if figure_ids:
        db.query(Panel).filter(Panel.figure_id.in_(figure_ids)).delete(synchronize_session=False)
    db.query(PaperAsset).filter(PaperAsset.paper_id == paper.id).delete(synchronize_session=False)
    db.query(Figure).filter(Figure.paper_id == paper.id).delete(synchronize_session=False)

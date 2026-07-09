from __future__ import annotations

import logging
import shutil
from pathlib import Path

from sqlalchemy.orm import Session

from app.models import Paper
from app.services.storage import StorageService

logger = logging.getLogger(__name__)


class PaperCleanupService:
    def __init__(self, db: Session, storage: StorageService | None = None) -> None:
        self.db = db
        self.storage = storage or StorageService()
        self._paper_id: int | None = None

    def delete_paper(self, paper_id: int) -> bool:
        paper = self.db.get(Paper, paper_id)
        if paper is None:
            return False

        self._paper_id = paper_id

        if paper.mineru_artifact_dir:
            self._safe_remove_path(paper.mineru_artifact_dir)
        if paper.mineru_extract_dir:
            self._safe_remove_path(paper.mineru_extract_dir)
        if paper.mineru_content_list_path:
            self._safe_remove_path(paper.mineru_content_list_path)
        if paper.file_path:
            self._safe_remove_upload_relative_path(paper.file_path)

        self.storage.remove_paper_tree(paper.id)

        for asset in list(paper.assets):
            for extraction in asset.extractions:
                if extraction.csv_path:
                    self._safe_remove_upload_relative_path(extraction.csv_path)

        self.db.delete(paper)
        self.db.commit()
        return True

    def _safe_remove_upload_relative_path(self, relative_path: str) -> None:
        try:
            self.storage.remove_path(relative_path)
        except Exception:
            logger.exception("failed to remove upload path for paper %s: %s", self._paper_id_for_log(), relative_path)

    def _safe_remove_path(self, path: str) -> None:
        try:
            candidate = Path(path)
            if candidate.exists():
                if candidate.is_dir():
                    shutil.rmtree(candidate)
                else:
                    candidate.unlink(missing_ok=True)
        except Exception:
            logger.exception("failed to remove result path for paper %s: %s", self._paper_id_for_log(), path)

    def _paper_id_for_log(self) -> int:
        return self._paper_id if self._paper_id is not None else -1

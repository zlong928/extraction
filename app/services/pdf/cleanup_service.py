from __future__ import annotations

import logging
from sqlalchemy.orm import Session

from app.models import Paper, PaperStatus
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

        # A paper is a business fact. Deletion hides it from active APIs while
        # preserving object references and immutable extraction history.
        paper.status = PaperStatus.DELETED.value
        paper.error_message = None
        self.db.commit()
        return True

    def _safe_remove_upload_relative_path(self, relative_path: str) -> None:
        try:
            self.storage.remove_path(relative_path)
        except Exception:
            logger.exception("failed to remove upload path for paper %s: %s", self._paper_id_for_log(), relative_path)

    def _paper_id_for_log(self) -> int:
        return self._paper_id if self._paper_id is not None else -1

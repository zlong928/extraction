from __future__ import annotations

import re
import shutil
from pathlib import Path

from app.config import UPLOAD_DIR


SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


class StorageService:
    def __init__(self, root: Path = UPLOAD_DIR) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def safe_filename(self, filename: str) -> str:
        name = Path(filename).name.strip() or "upload.pdf"
        return SAFE_NAME_RE.sub("_", name)[:180]

    def relative_path(self, path: Path) -> str:
        return path.resolve().relative_to(self.root.resolve()).as_posix()

    def absolute_path(self, relative_path: str) -> Path:
        candidate = (self.root / relative_path).resolve()
        candidate.relative_to(self.root.resolve())
        return candidate

    def paper_dir(self, paper_id: int) -> Path:
        path = self.root / "papers" / str(paper_id)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def asset_dir(self, paper_id: int) -> Path:
        path = self.paper_dir(paper_id) / "assets"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def result_dir(self, paper_id: int, asset_id: int) -> Path:
        path = self.paper_dir(paper_id) / "results" / str(asset_id)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def remove_paper_tree(self, paper_id: int) -> None:
        paper_path = self.paper_dir(paper_id)
        if paper_path.exists():
            shutil.rmtree(paper_path)

    def remove_path(self, path: str) -> None:
        target = (self.root / path).resolve()
        try:
            target.relative_to(self.root.resolve())
        except ValueError:
            return
        if not target.exists():
            return
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink(missing_ok=True)

from __future__ import annotations

import atexit
import os
import shutil
import tempfile
from pathlib import Path


_TEST_DATA_DIR = Path(tempfile.mkdtemp(prefix="extraction-pytest-"))

os.environ.setdefault("DATA_DIR", str(_TEST_DATA_DIR))
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_TEST_DATA_DIR / 'extraction.db'}")


@atexit.register
def _cleanup_test_data_dir() -> None:
    shutil.rmtree(_TEST_DATA_DIR, ignore_errors=True)

from __future__ import annotations

import sys
from contextlib import contextmanager

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl


class ChartOnlyRunAlreadyActive(RuntimeError):
    pass


_LOCK_DIR_CREATED = False


@contextmanager
def chart_only_run_lock(paper_id: int, *, blocking: bool = True):
    from sqlalchemy import text

    from app.config import DATA_DIR, DATABASE_URL
    from app.db import engine

    if DATABASE_URL.startswith("postgresql"):
        lock_key = 900_000_000 + int(paper_id)
        with engine.connect() as connection:
            if blocking:
                connection.execute(text("SELECT pg_advisory_lock(:key)"), {"key": lock_key})
            else:
                acquired = bool(
                    connection.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": lock_key}).scalar_one()
                )
                if not acquired:
                    raise ChartOnlyRunAlreadyActive(
                        f"chart-only extraction already running for paper {paper_id}"
                    )
            try:
                yield
            finally:
                connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": lock_key})
        return

    global _LOCK_DIR_CREATED
    lock_dir = DATA_DIR / "runtime" / "content_pipeline_locks"
    if not _LOCK_DIR_CREATED:
        lock_dir.mkdir(parents=True, exist_ok=True)
        _LOCK_DIR_CREATED = True
    lock_path = lock_dir / f"paper_{paper_id}.lock"
    with lock_path.open("w", encoding="utf-8") as handle:
        try:
            if sys.platform == "win32":
                while True:
                    try:
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK if blocking else msvcrt.LK_NBLCK, 1)
                        break
                    except OSError as exc:
                        if not blocking:
                            raise ChartOnlyRunAlreadyActive(
                                f"chart-only extraction already running for paper {paper_id}"
                            ) from exc
            else:
                flags = fcntl.LOCK_EX if blocking else fcntl.LOCK_EX | fcntl.LOCK_NB
                try:
                    fcntl.flock(handle.fileno(), flags)
                except BlockingIOError as exc:
                    raise ChartOnlyRunAlreadyActive(
                        f"chart-only extraction already running for paper {paper_id}"
                    ) from exc
        except ImportError:
            pass
        try:
            yield
        finally:
            try:
                if sys.platform == "win32":
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except (OSError, ImportError):
                pass

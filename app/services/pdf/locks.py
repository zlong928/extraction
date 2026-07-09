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
    from app.config import DATA_DIR

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

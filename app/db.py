from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import BASE_DIR, DATA_DIR, DATABASE_URL, ensure_runtime_dirs


connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args)


def enable_sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:
    if not DATABASE_URL.startswith("sqlite"):
        return
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
    finally:
        cursor.close()


if DATABASE_URL.startswith("sqlite"):
    event.listen(engine, "connect", enable_sqlite_foreign_keys)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def create_db_and_tables() -> None:
    ensure_runtime_dirs()
    alembic_config = Config(str(Path(BASE_DIR) / "alembic.ini"))
    alembic_config.set_main_option("sqlalchemy.url", DATABASE_URL.replace("%", "%%"))
    with _migration_lock():
        command.upgrade(alembic_config, "head")


@contextmanager
def _migration_lock():
    if DATABASE_URL.startswith("postgresql"):
        with engine.connect() as connection:
            connection.execute(text("SELECT pg_advisory_lock(784512039)"))
            try:
                yield
            finally:
                connection.execute(text("SELECT pg_advisory_unlock(784512039)"))
        return
    import fcntl

    lock_path = Path(DATA_DIR) / ".alembic-migration.lock"
    with lock_path.open("a+") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

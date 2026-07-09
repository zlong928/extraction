from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import DATABASE_URL, ensure_runtime_dirs


connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def create_db_and_tables() -> None:
    ensure_runtime_dirs()
    import app.models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    _apply_compat_migrations()


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _apply_compat_migrations() -> None:
    with engine.begin() as conn:
        inspector = inspect(conn)
        existing_tables = set(inspector.get_table_names())
        existing_columns: dict[str, set[str]] = {}
        for table_name in existing_tables:
            existing_columns[table_name] = {col["name"] for col in inspector.get_columns(table_name)}

        _migrate_paper_columns(conn, existing_columns.get("papers", set()))
        _migrate_paper_assets_columns(conn, existing_columns.get("paper_assets", set()))
        _migrate_image_extractions_columns(conn, existing_columns.get("image_extractions", set()))


def _migrate_paper_columns(conn, existing: set[str]) -> None:
    wanted: dict[str, str] = {
        "mineru_markdown": "TEXT",
        "mineru_artifact_dir": "VARCHAR(1000)",
        "mineru_extract_dir": "VARCHAR(1000)",
        "mineru_content_list_path": "VARCHAR(1000)",
        "layout_data": "TEXT",
    }
    for column, column_type in wanted.items():
        if column not in existing:
            conn.execute(text(f"ALTER TABLE papers ADD COLUMN {column} {column_type}"))


def _migrate_paper_assets_columns(conn, existing: set[str]) -> None:
    if "figure_id" not in existing:
        conn.execute(text("ALTER TABLE paper_assets ADD COLUMN figure_id INTEGER REFERENCES figures(id)"))


def _migrate_image_extractions_columns(conn, existing: set[str]) -> None:
    if "figure_id" not in existing:
        conn.execute(text("ALTER TABLE image_extractions ADD COLUMN figure_id INTEGER REFERENCES figures(id)"))

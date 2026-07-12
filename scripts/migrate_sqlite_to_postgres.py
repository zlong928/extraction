from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import typer
from sqlalchemy import MetaData, create_engine, select, text
from sqlalchemy.orm import Session


cli = typer.Typer(help="Migrate the legacy SQLite facts and files to PostgreSQL/object storage.")


@cli.command()
def migrate(
    sqlite_path: Path = typer.Option(..., exists=True, dir_okay=False, resolve_path=True),
    postgres_url: str = typer.Option(..., envvar="TARGET_DATABASE_URL"),
    source_storage_root: Path | None = typer.Option(None, resolve_path=True),
) -> None:
    """Copy a quiesced legacy database; run once against an empty target."""
    os.environ["DATABASE_URL"] = postgres_url
    from alembic import command
    from alembic.config import Config

    from app.config import BASE_DIR
    from app.models import Paper, PaperAsset
    from app.services.object_store import ObjectStore
    from app.services.storage import get_storage_adapter

    config = Config(str(BASE_DIR / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", postgres_url.replace("%", "%%"))
    command.upgrade(config, "head")

    source_engine = create_engine(f"sqlite:///{sqlite_path}")
    target_engine = create_engine(postgres_url)
    source_meta = MetaData()
    target_meta = MetaData()
    source_meta.reflect(bind=source_engine)
    target_meta.reflect(bind=target_engine)
    source_root = (source_storage_root or sqlite_path.parent / "uploads").resolve()

    with source_engine.connect() as source:
        _reject_nonlegacy_source(source, source_meta)

    with target_engine.begin() as target:
        target_data_tables = (
            "papers",
            "figures",
            "paper_assets",
            "panels",
            "image_extractions",
            "pending_jobs",
            "storage_objects",
            "extraction_runs",
            "run_artifacts",
            "structured_results",
            "delivery_versions",
            "delivery_artifacts",
        )
        nonempty = [
            table_name
            for table_name in target_data_tables
            if target.execute(text(f"SELECT 1 FROM {table_name} LIMIT 1")).first() is not None
        ]
        if nonempty:
            raise typer.BadParameter(f"Target PostgreSQL database is not empty: {', '.join(nonempty)}")
        with source_engine.connect() as source:
            for table_name in ("papers", "figures", "paper_assets", "panels", "image_extractions", "pending_jobs"):
                if table_name not in source_meta.tables:
                    continue
                source_table = source_meta.tables[table_name]
                target_table = target_meta.tables[table_name]
                target_columns = {column.name for column in target_table.columns}
                rows = []
                for source_row in source.execute(select(source_table)).mappings():
                    row = {key: value for key, value in source_row.items() if key in target_columns}
                    if table_name == "papers":
                        row.setdefault("project_id", 1)
                    if table_name == "pending_jobs":
                        row.setdefault("idempotency_key", f"legacy:{source_row['id']}")
                        row.setdefault("attempt", 1)
                    rows.append(row)
                if rows:
                    target.execute(target_table.insert(), rows)

    adapter = get_storage_adapter()
    with Session(target_engine) as db:
        store = ObjectStore(db, adapter)
        for paper in db.query(Paper).order_by(Paper.id):
            source_pdf = _legacy_path(source_root, paper.file_path)
            if source_pdf and source_pdf.is_file():
                suffix = source_pdf.suffix.lower() or ".bin"
                stored = store.put_file(
                    key=f"papers/{paper.id}/source/{paper.file_hash}{suffix}",
                    source=source_pdf,
                    media_type=paper.mime_type,
                    metadata={"role": "source_pdf", "migrated_from": str(source_pdf)},
                )
                paper.file_path = stored.object_key
                if paper.mime_type == "application/pdf":
                    paper.pdf_object_id = stored.id
            if paper.mineru_markdown:
                markdown = store.put_bytes(
                    key=f"papers/{paper.id}/mineru/legacy/document.md",
                    data=paper.mineru_markdown.encode("utf-8"),
                    media_type="text/markdown",
                    metadata={"role": "mineru_markdown", "migration": "sqlite"},
                )
                paper.mineru_markdown_object_id = markdown.id
                paper.mineru_markdown = None
            legacy_content = _legacy_path(source_root, paper.mineru_content_list_path)
            if legacy_content and legacy_content.is_file():
                stored = store.put_file(
                    key=f"papers/{paper.id}/mineru/legacy/content_list.json",
                    source=legacy_content,
                    media_type="application/json",
                    metadata={"role": "legacy_mineru_content_list", "migrated_from": str(legacy_content)},
                )
                paper.mineru_content_list_path = stored.object_key
                paper.mineru_content_object_id = stored.id
            legacy_extract = _legacy_path(source_root, paper.mineru_extract_dir)
            if legacy_extract and legacy_extract.is_dir():
                with tempfile.TemporaryDirectory(prefix="legacy-mineru-") as temp_dir:
                    archive_path = Path(
                        shutil.make_archive(str(Path(temp_dir) / "raw"), "zip", legacy_extract)
                    )
                    stored = store.put_file(
                        key=f"papers/{paper.id}/mineru/legacy/raw.zip",
                        source=archive_path,
                        media_type="application/zip",
                        metadata={"role": "legacy_mineru_raw_output", "migrated_from": str(legacy_extract)},
                    )
                paper.mineru_artifact_dir = stored.object_key
                paper.mineru_extract_dir = f"papers/{paper.id}/mineru/legacy"
                layout_path = legacy_extract / "layout.json"
                if layout_path.is_file():
                    layout = store.put_file(
                        key=f"papers/{paper.id}/mineru/legacy/layout.json",
                        source=layout_path,
                        media_type="application/json",
                        metadata={"role": "legacy_mineru_layout", "migrated_from": str(layout_path)},
                    )
                    paper.mineru_layout_object_id = layout.id
        for asset in db.query(PaperAsset).order_by(PaperAsset.id):
            source_asset = _legacy_path(source_root, asset.file_path)
            if source_asset and source_asset.is_file():
                stored = store.put_file(
                    key=f"papers/{asset.paper_id}/assets/legacy-{asset.id}{source_asset.suffix}",
                    source=source_asset,
                    media_type=asset.mime_type,
                    metadata={"role": "extracted_image", "migrated_from": str(source_asset)},
                )
                asset.file_path = stored.object_key
                asset.object_id = stored.id
        db.commit()

    with target_engine.begin() as target:
        for table_name in ("papers", "figures", "paper_assets", "panels", "image_extractions", "pending_jobs", "projects"):
            target.execute(
                text(
                    f"SELECT setval(pg_get_serial_sequence('{table_name}', 'id'), "
                    f"COALESCE((SELECT MAX(id) FROM {table_name}), 1), true)"
                )
            )
    typer.echo("Migration complete. Verify counts and object checksums before switching traffic.")


def _legacy_path(root: Path, value: str | None) -> Path | None:
    if not value:
        return None
    path = Path(value)
    if path.is_absolute():
        return path
    candidate = (root / path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


def _reject_nonlegacy_source(source, metadata: MetaData) -> None:
    production_fact_tables = (
        "storage_objects",
        "extraction_runs",
        "run_artifacts",
        "structured_results",
        "delivery_versions",
        "delivery_artifacts",
    )
    populated = []
    for table_name in production_fact_tables:
        table = metadata.tables.get(table_name)
        if table is not None and source.execute(select(table).limit(1)).first() is not None:
            populated.append(table_name)
    if populated:
        raise typer.BadParameter(
            "Source SQLite contains production persistence facts that this legacy-only migrator "
            f"must not silently omit: {', '.join(populated)}. Export/import these immutable tables explicitly."
        )


if __name__ == "__main__":
    cli()

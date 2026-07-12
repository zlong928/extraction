from __future__ import annotations

import json

import typer

from app.db import SessionLocal, create_db_and_tables
from app.delivery import DeliveryBuilder


cli = typer.Typer(help="Build immutable extraction data delivery packages.")


@cli.command("build")
def build(
    version: str = typer.Option(..., "--version"),
    project_id: int = typer.Option(1, "--project-id"),
    paper_id: list[int] = typer.Option([], "--paper-id"),
    paper_status: list[str] = typer.Option([], "--paper-status"),
) -> None:
    create_db_and_tables()
    scope: dict[str, object] = {"project_id": project_id}
    if paper_id:
        scope["paper_ids"] = paper_id
    if paper_status:
        scope["paper_statuses"] = paper_status
    with SessionLocal() as db:
        result = DeliveryBuilder(db).build(version=version, project_id=project_id, data_scope=scope)
    typer.echo(json.dumps({"version": result.version, "manifest_uri": result.manifest_uri}, ensure_ascii=False))


if __name__ == "__main__":
    cli()

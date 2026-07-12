"""Harden asset history and Markdown object references.

Revision ID: 0003_persistence_hardening
Revises: 0002_production_persistence
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "0003_persistence_hardening"
down_revision = "0002_production_persistence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    paper_columns = {column["name"] for column in inspect(connection).get_columns("papers")}
    if "mineru_markdown_object_id" not in paper_columns:
        op.add_column("papers", sa.Column("mineru_markdown_object_id", sa.String(36)))

    asset_columns = {column["name"] for column in inspect(connection).get_columns("paper_assets")}
    if "is_active" not in asset_columns:
        op.add_column(
            "paper_assets",
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        )
    indexes = {index["name"] for index in inspect(connection).get_indexes("paper_assets")}
    if "ix_paper_assets_is_active" not in indexes:
        op.create_index("ix_paper_assets_is_active", "paper_assets", ["is_active"])

    if connection.dialect.name == "postgresql":
        foreign_keys = {fk.get("name") for fk in inspect(connection).get_foreign_keys("papers")}
        if "fk_papers_mineru_markdown_object" not in foreign_keys:
            op.create_foreign_key(
                "fk_papers_mineru_markdown_object",
                "papers",
                "storage_objects",
                ["mineru_markdown_object_id"],
                ["id"],
                ondelete="RESTRICT",
            )


def downgrade() -> None:
    raise RuntimeError("Persistence audit fields are intentionally non-downgradable; restore a backup instead.")

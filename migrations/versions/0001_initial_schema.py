"""Create the extraction service schema.

Revision ID: 0001_initial_schema
Revises:
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def _create_if_missing(table: str, *columns: sa.Column, **kwargs: object) -> None:
    if not inspect(op.get_bind()).has_table(table):
        op.create_table(table, *columns, **kwargs)


def _add_column_if_missing(table: str, column: sa.Column) -> None:
    inspector = inspect(op.get_bind())
    if inspector.has_table(table) and column.name not in {item["name"] for item in inspector.get_columns(table)}:
        op.add_column(table, column)


def upgrade() -> None:
    _create_if_missing(
        "papers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("original_filename", sa.String(length=500), nullable=False),
        sa.Column("file_path", sa.String(length=1000), nullable=False),
        sa.Column("file_size", sa.Integer(), nullable=False),
        sa.Column("file_hash", sa.String(length=64), nullable=False),
        sa.Column("mime_type", sa.String(length=120), nullable=False, server_default="application/pdf"),
        sa.Column("status", sa.String(length=40), nullable=False, server_default="pending"),
        sa.Column("text_content", sa.Text()),
        sa.Column("mineru_markdown", sa.Text()),
        sa.Column("mineru_artifact_dir", sa.String(length=1000)),
        sa.Column("mineru_extract_dir", sa.String(length=1000)),
        sa.Column("mineru_content_list_path", sa.String(length=1000)),
        sa.Column("page_count", sa.Integer()),
        sa.Column("error_message", sa.Text()),
        sa.Column("layout_data", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    _create_if_missing(
        "figures",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("paper_id", sa.Integer(), sa.ForeignKey("papers.id"), nullable=False),
        sa.Column("figure_id", sa.String(length=300), nullable=False),
        sa.Column("caption_text", sa.Text()),
        sa.Column("page_number", sa.Integer()),
        sa.Column("is_multi_panel", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("panel_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("metadata_json", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    _create_if_missing(
        "paper_assets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("paper_id", sa.Integer(), sa.ForeignKey("papers.id"), nullable=False),
        sa.Column("figure_id", sa.Integer(), sa.ForeignKey("figures.id")),
        sa.Column("asset_type", sa.String(length=40), nullable=False, server_default="image"),
        sa.Column("asset_index", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(length=300)),
        sa.Column("page_number", sa.Integer()),
        sa.Column("file_path", sa.String(length=1000), nullable=False),
        sa.Column("mime_type", sa.String(length=120), nullable=False, server_default="image/png"),
        sa.Column("width", sa.Integer()),
        sa.Column("height", sa.Integer()),
        sa.Column("caption", sa.Text()),
        sa.Column("metadata_json", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    _create_if_missing(
        "panels",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("figure_id", sa.Integer(), sa.ForeignKey("figures.id"), nullable=False),
        sa.Column("asset_id", sa.Integer(), sa.ForeignKey("paper_assets.id")),
        sa.Column("panel_id", sa.String(length=300), nullable=False),
        sa.Column("evidence_shape", sa.String(length=80), nullable=False, server_default="unknown"),
        sa.Column("domain_task", sa.String(length=80), nullable=False, server_default="unknown"),
        sa.Column("extractor", sa.String(length=120), nullable=False, server_default="overview_schematic_extractor"),
        sa.Column("extraction_priority", sa.String(length=40), nullable=False, server_default="panel_level"),
        sa.Column("panel_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("metadata_json", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    _create_if_missing(
        "image_extractions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("asset_id", sa.Integer(), sa.ForeignKey("paper_assets.id"), nullable=False),
        sa.Column("figure_id", sa.Integer(), sa.ForeignKey("figures.id")),
        sa.Column("status", sa.String(length=40), nullable=False, server_default="pending"),
        sa.Column("query", sa.Text()),
        sa.Column("csv_path", sa.String(length=1000)),
        sa.Column("result_json", sa.Text()),
        sa.Column("error_message", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
    )
    _create_if_missing(
        "pending_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("paper_id", sa.Integer(), nullable=False),
        sa.Column("task_type", sa.String(length=40), nullable=False),
        sa.Column("payload_schema_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("error_message", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
    )

    _add_column_if_missing("papers", sa.Column("mineru_markdown", sa.Text()))
    _add_column_if_missing("papers", sa.Column("mineru_artifact_dir", sa.String(length=1000)))
    _add_column_if_missing("papers", sa.Column("mineru_extract_dir", sa.String(length=1000)))
    _add_column_if_missing("papers", sa.Column("mineru_content_list_path", sa.String(length=1000)))
    _add_column_if_missing("papers", sa.Column("layout_data", sa.Text()))
    _add_column_if_missing("paper_assets", sa.Column("figure_id", sa.Integer()))
    _add_column_if_missing("image_extractions", sa.Column("figure_id", sa.Integer()))
    _add_column_if_missing(
        "pending_jobs",
        sa.Column("payload_schema_version", sa.Integer(), nullable=False, server_default="1"),
    )

    for table, columns in {
        "papers": ["file_hash", "status"],
        "figures": ["paper_id"],
        "paper_assets": ["paper_id", "figure_id"],
        "panels": ["figure_id", "asset_id"],
        "image_extractions": ["asset_id", "figure_id", "status"],
        "pending_jobs": ["paper_id", "status"],
    }.items():
        inspector = inspect(op.get_bind())
        existing = {item["name"] for item in inspector.get_indexes(table)}
        for column in columns:
            index_name = f"ix_{table}_{column}"
            if index_name not in existing:
                op.create_index(index_name, table, [column])


def downgrade() -> None:
    raise RuntimeError(
        "0001_initial_schema is intentionally non-downgradable; restore a verified database backup instead."
    )

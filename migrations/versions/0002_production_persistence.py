"""Add production persistence, immutable runs, and delivery versions.

Revision ID: 0002_production_persistence
Revises: 0001_initial_schema
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "0002_production_persistence"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None


def _add_columns(table: str, columns: list[sa.Column]) -> None:
    existing = {column["name"] for column in inspect(op.get_bind()).get_columns(table)}
    for column in columns:
        if column.name not in existing:
            op.add_column(table, column)


def _create_index_if_missing(table: str, name: str, columns: list[str], *, unique: bool = False) -> None:
    existing = {index["name"] for index in inspect(op.get_bind()).get_indexes(table)}
    if name not in existing:
        op.create_index(name, table, columns, unique=unique)


def upgrade() -> None:
    connection = op.get_bind()
    dialect = connection.dialect.name
    inspector = inspect(connection)
    if not inspector.has_table("projects"):
        op.create_table(
            "projects",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("slug", sa.String(120), nullable=False, unique=True),
            sa.Column("name", sa.String(300), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )
    op.execute(sa.text(
        "INSERT INTO projects (id, slug, name) SELECT 1, 'default', 'Default Project' "
        "WHERE NOT EXISTS (SELECT 1 FROM projects WHERE id = 1)"
    ))
    if dialect == "postgresql":
        op.execute(sa.text("SELECT setval(pg_get_serial_sequence('projects', 'id'), 1, true)"))

    inspector = inspect(connection)
    if not inspector.has_table("storage_objects"):
        op.create_table(
            "storage_objects",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("object_key", sa.String(1024), nullable=False, unique=True),
            sa.Column("uri", sa.String(2048), nullable=False, unique=True),
            sa.Column("sha256", sa.String(64), nullable=False),
            sa.Column("size_bytes", sa.BigInteger(), nullable=False),
            sa.Column("media_type", sa.String(255), nullable=False),
            sa.Column("etag", sa.String(255)),
            sa.Column("metadata_json", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )
        op.create_index("ix_storage_objects_sha256", "storage_objects", ["sha256"])

    _add_columns("papers", [
        sa.Column("project_id", sa.Integer(), nullable=True, server_default="1"),
        sa.Column("pdf_object_id", sa.String(36)),
        sa.Column("mineru_content_object_id", sa.String(36)),
        sa.Column("mineru_layout_object_id", sa.String(36)),
        sa.Column("mineru_markdown_object_id", sa.String(36)),
        sa.Column("latest_audit_object_id", sa.String(36)),
    ])
    _create_index_if_missing("papers", "ix_papers_project_id", ["project_id"])
    _create_index_if_missing("papers", "uq_papers_pdf_object_id", ["pdf_object_id"], unique=True)

    _add_columns("paper_assets", [
        sa.Column("object_id", sa.String(36)),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
    ])
    _create_index_if_missing("paper_assets", "ix_paper_assets_object_id", ["object_id"])
    _create_index_if_missing("paper_assets", "ix_paper_assets_is_active", ["is_active"])

    _add_columns("pending_jobs", [
        sa.Column("idempotency_key", sa.String(255)),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("lease_owner", sa.String(255)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
    ])

    rows = connection.execute(sa.text("SELECT id FROM pending_jobs WHERE idempotency_key IS NULL")).fetchall()
    for row in rows:
        connection.execute(
            sa.text("UPDATE pending_jobs SET idempotency_key = :key WHERE id = :id"),
            {"key": f"legacy:{row.id}", "id": row.id},
        )

    if dialect == "postgresql":
        op.alter_column("pending_jobs", "idempotency_key", existing_type=sa.String(255), nullable=False)
        op.alter_column("papers", "project_id", existing_type=sa.Integer(), nullable=False)
        op.create_foreign_key("fk_papers_project", "papers", "projects", ["project_id"], ["id"], ondelete="RESTRICT")
        for name, local, remote_table in (
            ("fk_papers_pdf_object", "pdf_object_id", "storage_objects"),
            ("fk_papers_mineru_content_object", "mineru_content_object_id", "storage_objects"),
            ("fk_papers_mineru_layout_object", "mineru_layout_object_id", "storage_objects"),
            ("fk_papers_mineru_markdown_object", "mineru_markdown_object_id", "storage_objects"),
            ("fk_papers_latest_audit_object", "latest_audit_object_id", "storage_objects"),
        ):
            op.create_foreign_key(name, "papers", remote_table, [local], ["id"], ondelete="RESTRICT")
        op.create_foreign_key(
            "fk_paper_assets_object", "paper_assets", "storage_objects", ["object_id"], ["id"], ondelete="RESTRICT"
        )
        op.create_foreign_key(
            "fk_pending_jobs_paper", "pending_jobs", "papers", ["paper_id"], ["id"], ondelete="RESTRICT"
        )
    _create_index_if_missing(
        "pending_jobs", "uq_pending_jobs_idempotency_key", ["idempotency_key"], unique=True
    )
    _create_index_if_missing(
        "pending_jobs", "ix_pending_jobs_claim", ["status", "lease_expires_at", "created_at"]
    )

    op.create_table(
        "extraction_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("task_id", sa.Integer(), sa.ForeignKey("pending_jobs.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("paper_id", sa.Integer(), sa.ForeignKey("papers.id", ondelete="RESTRICT"), nullable=False),
        sa.Column(
            "input_object_id", sa.String(36), sa.ForeignKey("storage_objects.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column("source_asset_id", sa.Integer(), sa.ForeignKey("paper_assets.id", ondelete="RESTRICT")),
        sa.Column("parent_run_id", sa.String(36), sa.ForeignKey("extraction_runs.id", ondelete="RESTRICT")),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("model_provider", sa.String(120), nullable=False),
        sa.Column("model_name", sa.String(255), nullable=False),
        sa.Column("model_version", sa.String(255), nullable=False),
        sa.Column("prompt_version", sa.String(255), nullable=False),
        sa.Column("pipeline_version", sa.String(255), nullable=False),
        sa.Column("config_snapshot", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(40), nullable=False, server_default="running"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("error_type", sa.String(255)),
        sa.Column("error_message", sa.Text()),
        sa.Column("raw_output_object_id", sa.String(36), sa.ForeignKey("storage_objects.id", ondelete="RESTRICT")),
        sa.Column("normalized_schema_version", sa.String(120)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("task_id", name="uq_extraction_runs_task_id"),
    )
    op.create_index("ix_extraction_runs_status", "extraction_runs", ["status"])
    op.create_index(
        "ix_extraction_runs_paper_status_created", "extraction_runs", ["paper_id", "status", "created_at"]
    )
    op.create_index(
        "ix_extraction_runs_input_pipeline", "extraction_runs", ["input_object_id", "pipeline_version"]
    )

    op.create_table(
        "structured_results",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("extraction_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("paper_id", sa.Integer(), sa.ForeignKey("papers.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("source_asset_id", sa.Integer(), sa.ForeignKey("paper_assets.id", ondelete="RESTRICT")),
        sa.Column("result_type", sa.String(120), nullable=False),
        sa.Column("natural_key", sa.String(500), nullable=False),
        sa.Column("schema_version", sa.String(120), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("page_number", sa.Integer()),
        sa.Column("figure_id", sa.String(300)),
        sa.Column("panel_id", sa.String(300)),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("run_id", "result_type", "natural_key", name="uq_structured_result_natural_key"),
    )
    op.create_index("ix_structured_results_run_type", "structured_results", ["run_id", "result_type"])
    op.create_index("ix_structured_results_paper_panel", "structured_results", ["paper_id", "panel_id"])
    op.create_index("ix_structured_results_figure_id", "structured_results", ["figure_id"])
    op.create_index("ix_structured_results_panel_id", "structured_results", ["panel_id"])

    op.create_table(
        "delivery_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("version", sa.String(255), nullable=False, unique=True),
        sa.Column("status", sa.String(40), nullable=False, server_default="building"),
        sa.Column("data_scope", sa.JSON(), nullable=False),
        sa.Column("snapshot_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("database_schema_version", sa.String(120), nullable=False),
        sa.Column("pipeline_version", sa.String(255), nullable=False),
        sa.Column("model_prompt_versions", sa.JSON(), nullable=False),
        sa.Column("config_hash", sa.String(64), nullable=False),
        sa.Column("record_counts", sa.JSON(), nullable=False),
        sa.Column("manifest_object_id", sa.String(36), sa.ForeignKey("storage_objects.id", ondelete="RESTRICT")),
        sa.Column("error_message", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("published_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_delivery_versions_status", "delivery_versions", ["status"])
    op.create_index("ix_delivery_versions_project_status", "delivery_versions", ["project_id", "status"])

    op.create_table(
        "delivery_artifacts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "delivery_version_id",
            sa.String(36),
            sa.ForeignKey("delivery_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("object_id", sa.String(36), sa.ForeignKey("storage_objects.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("format", sa.String(40), nullable=False),
        sa.Column("filename", sa.String(500), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("media_type", sa.String(255), nullable=False),
        sa.Column("row_count", sa.BigInteger()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("delivery_version_id", "filename", name="uq_delivery_artifact_filename"),
    )


def downgrade() -> None:
    op.drop_table("delivery_artifacts")
    op.drop_index("ix_delivery_versions_project_status", table_name="delivery_versions")
    op.drop_index("ix_delivery_versions_status", table_name="delivery_versions")
    op.drop_table("delivery_versions")
    op.drop_index("ix_structured_results_panel_id", table_name="structured_results")
    op.drop_index("ix_structured_results_figure_id", table_name="structured_results")
    op.drop_index("ix_structured_results_paper_panel", table_name="structured_results")
    op.drop_index("ix_structured_results_run_type", table_name="structured_results")
    op.drop_table("structured_results")
    op.drop_index("ix_extraction_runs_input_pipeline", table_name="extraction_runs")
    op.drop_index("ix_extraction_runs_paper_status_created", table_name="extraction_runs")
    op.drop_index("ix_extraction_runs_status", table_name="extraction_runs")
    op.drop_table("extraction_runs")
    with op.batch_alter_table("pending_jobs") as batch:
        batch.drop_index("ix_pending_jobs_claim")
        batch.drop_constraint("fk_pending_jobs_paper", type_="foreignkey")
        batch.drop_constraint("uq_pending_jobs_idempotency_key", type_="unique")
        for column in ("completed_at", "started_at", "lease_expires_at", "lease_owner", "attempt", "idempotency_key"):
            batch.drop_column(column)
    with op.batch_alter_table("paper_assets") as batch:
        batch.drop_index("ix_paper_assets_is_active")
        batch.drop_index("ix_paper_assets_object_id")
        batch.drop_constraint("fk_paper_assets_object", type_="foreignkey")
        batch.drop_column("object_id")
        batch.drop_column("is_active")
    with op.batch_alter_table("papers") as batch:
        batch.drop_constraint("uq_papers_pdf_object_id", type_="unique")
        batch.drop_index("ix_papers_project_id")
        for constraint in (
            "fk_papers_latest_audit_object",
            "fk_papers_mineru_layout_object",
            "fk_papers_mineru_markdown_object",
            "fk_papers_mineru_content_object",
            "fk_papers_pdf_object",
            "fk_papers_project",
        ):
            batch.drop_constraint(constraint, type_="foreignkey")
        for column in (
            "latest_audit_object_id",
            "mineru_layout_object_id",
            "mineru_markdown_object_id",
            "mineru_content_object_id",
            "pdf_object_id",
            "project_id",
        ):
            batch.drop_column(column)
    op.drop_index("ix_storage_objects_sha256", table_name="storage_objects")
    op.drop_table("storage_objects")
    op.drop_table("projects")

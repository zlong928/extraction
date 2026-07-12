"""Add durable batch processing facts and job lineage.

Revision ID: 0006_batch_processing
Revises: 0005_concurrency_guards
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0006_batch_processing"
down_revision = "0005_concurrency_guards"
branch_labels = None
depends_on = None


_BATCH_RUN_STATUSES = "'pending', 'running', 'succeeded', 'partial_failed', 'failed', 'cancelling', 'cancelled'"
_BATCH_ITEM_STATUSES = "'pending', 'queued', 'processing', 'succeeded', 'failed', 'reused', 'cancelled'"


def upgrade() -> None:
    connection = op.get_bind()
    op.create_table(
        "batch_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("submission_key", sa.String(255), nullable=False),
        sa.Column("source_root", sa.String(2048), nullable=False),
        sa.Column("status", sa.String(40), nullable=False, server_default="pending"),
        sa.Column("batch_concurrency", sa.Integer(), nullable=False),
        sa.Column("config_snapshot", sa.JSON(), nullable=False),
        sa.Column("result_config_hash", sa.String(64), nullable=False),
        sa.Column("error_message", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("project_id", "submission_key", name="uq_batch_runs_project_submission_key"),
        sa.CheckConstraint(f"status IN ({_BATCH_RUN_STATUSES})", name="ck_batch_runs_status"),
        sa.CheckConstraint("batch_concurrency > 0", name="ck_batch_runs_concurrency_positive"),
    )
    op.create_index("ix_batch_runs_project_status_updated", "batch_runs", ["project_id", "status", "updated_at"])

    op.create_table(
        "batch_items",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("batch_run_id", sa.String(36), sa.ForeignKey("batch_runs.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("source_relative_path", sa.String(2048), nullable=False),
        sa.Column("source_sha256", sa.String(64), nullable=False),
        sa.Column("source_size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(40), nullable=False, server_default="pending"),
        sa.Column("current_stage", sa.String(80)),
        sa.Column("paper_id", sa.Integer(), sa.ForeignKey("papers.id", ondelete="RESTRICT")),
        sa.Column("resolved_extraction_run_id", sa.String(36), sa.ForeignKey("extraction_runs.id", ondelete="RESTRICT")),
        sa.Column("error_message", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("batch_run_id", "ordinal", name="uq_batch_items_run_ordinal"),
        sa.UniqueConstraint("batch_run_id", "source_relative_path", name="uq_batch_items_run_relative_path"),
        sa.CheckConstraint(f"status IN ({_BATCH_ITEM_STATUSES})", name="ck_batch_items_status"),
    )
    op.create_index("ix_batch_items_run_status", "batch_items", ["batch_run_id", "status"])
    op.create_index("ix_batch_items_source_sha256", "batch_items", ["source_sha256"])
    op.create_index("ix_batch_items_paper_id", "batch_items", ["paper_id"])

    op.create_table(
        "batch_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("batch_run_id", sa.String(36), sa.ForeignKey("batch_runs.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("batch_item_id", sa.String(36), sa.ForeignKey("batch_items.id", ondelete="RESTRICT")),
        sa.Column("event_type", sa.String(120), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_batch_events_run_id", "batch_events", ["batch_run_id", "id"])
    op.create_index("ix_batch_events_item_id", "batch_events", ["batch_item_id", "id"])

    if connection.dialect.name == "postgresql":
        op.add_column("pending_jobs", sa.Column("batch_item_id", sa.String(36), nullable=True))
        op.add_column("pending_jobs", sa.Column("retry_of_job_id", sa.Integer(), nullable=True))
        op.create_foreign_key(
            "fk_pending_jobs_batch_item", "pending_jobs", "batch_items", ["batch_item_id"], ["id"], ondelete="RESTRICT"
        )
        op.create_foreign_key(
            "fk_pending_jobs_retry_of_job", "pending_jobs", "pending_jobs", ["retry_of_job_id"], ["id"], ondelete="RESTRICT"
        )
        op.create_index("ix_pending_jobs_batch_item_id", "pending_jobs", ["batch_item_id"])
        op.create_index("ix_pending_jobs_retry_of_job_id", "pending_jobs", ["retry_of_job_id"])
        return

    with op.batch_alter_table("pending_jobs", recreate="always") as batch_op:
        batch_op.add_column(sa.Column("batch_item_id", sa.String(36), nullable=True))
        batch_op.add_column(sa.Column("retry_of_job_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_pending_jobs_batch_item", "batch_items", ["batch_item_id"], ["id"], ondelete="RESTRICT"
        )
        batch_op.create_foreign_key(
            "fk_pending_jobs_retry_of_job", "pending_jobs", ["retry_of_job_id"], ["id"], ondelete="RESTRICT"
        )
        batch_op.create_index("ix_pending_jobs_batch_item_id", ["batch_item_id"])
        batch_op.create_index("ix_pending_jobs_retry_of_job_id", ["retry_of_job_id"])


def downgrade() -> None:
    raise RuntimeError("Batch processing facts are intentionally forward-only.")

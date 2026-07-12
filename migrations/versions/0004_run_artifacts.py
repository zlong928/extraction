"""Add explicit run-to-object artifact relationships.

Revision ID: 0004_run_artifacts
Revises: 0003_persistence_hardening
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0004_run_artifacts"
down_revision = "0003_persistence_hardening"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "run_artifacts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("extraction_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("object_id", sa.String(36), sa.ForeignKey("storage_objects.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("role", sa.String(120), nullable=False),
        sa.Column("filename", sa.String(500), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("run_id", "role", "filename", name="uq_run_artifact_role_filename"),
    )
    op.create_index("ix_run_artifacts_run_role", "run_artifacts", ["run_id", "role"])


def downgrade() -> None:
    raise RuntimeError("Run artifact audit relationships are intentionally non-downgradable.")

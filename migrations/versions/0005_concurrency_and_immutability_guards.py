"""Add worker fencing, active-paper uniqueness, and database audit guards.

Revision ID: 0005_concurrency_guards
Revises: 0004_run_artifacts
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "0005_concurrency_guards"
down_revision = "0004_run_artifacts"
branch_labels = None
depends_on = None


TERMINAL_RUNS = "'succeeded', 'partial_failure', 'failed', 'cancelled'"
TERMINAL_DELIVERIES = "'published', 'failed'"


def upgrade() -> None:
    connection = op.get_bind()
    columns = {column["name"] for column in inspect(connection).get_columns("pending_jobs")}
    if "claim_generation" not in columns:
        op.add_column(
            "pending_jobs",
            sa.Column("claim_generation", sa.Integer(), nullable=False, server_default="0"),
        )

    indexes = {index["name"] for index in inspect(connection).get_indexes("papers")}
    if "uq_papers_project_active_hash" not in indexes:
        op.create_index(
            "uq_papers_project_active_hash",
            "papers",
            ["project_id", "file_hash"],
            unique=True,
            postgresql_where=sa.text("status <> 'deleted'"),
            sqlite_where=sa.text("status <> 'deleted'"),
        )

    asset_indexes = {index["name"] for index in inspect(connection).get_indexes("paper_assets")}
    if "ix_paper_assets_asset_type" not in asset_indexes:
        op.create_index("ix_paper_assets_asset_type", "paper_assets", ["asset_type"])

    if connection.dialect.name == "postgresql":
        _install_postgresql_guards()
    elif connection.dialect.name == "sqlite":
        _install_sqlite_guards()


def _install_postgresql_guards() -> None:
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION guard_terminal_extraction_run() RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'extraction run % is immutable', OLD.id
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            IF OLD.status IN ({TERMINAL_RUNS}) THEN
                RAISE EXCEPTION 'terminal extraction run % is immutable', OLD.id
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        "CREATE TRIGGER trg_guard_terminal_extraction_run "
        "BEFORE UPDATE OR DELETE ON extraction_runs FOR EACH ROW EXECUTE FUNCTION guard_terminal_extraction_run()"
    )
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION guard_extraction_run_child() RETURNS trigger AS $$
        DECLARE target_run_id text;
        BEGIN
            IF TG_OP <> 'INSERT' THEN
                RAISE EXCEPTION '% rows are immutable', TG_TABLE_NAME
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            target_run_id := NEW.run_id;
            IF EXISTS (
                SELECT 1 FROM extraction_runs
                WHERE id = target_run_id AND status IN ({TERMINAL_RUNS})
            ) THEN
                RAISE EXCEPTION 'terminal extraction run % cannot accept child facts', target_run_id
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    for table in ("structured_results", "run_artifacts"):
        op.execute(
            f"CREATE TRIGGER trg_guard_{table} BEFORE INSERT OR UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION guard_extraction_run_child()"
        )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION reject_immutable_row_change() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION '% rows are immutable', TG_TABLE_NAME
                USING ERRCODE = 'integrity_constraint_violation';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        "CREATE TRIGGER trg_guard_storage_objects BEFORE UPDATE OR DELETE ON storage_objects "
        "FOR EACH ROW EXECUTE FUNCTION reject_immutable_row_change()"
    )
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION guard_terminal_delivery() RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'delivery % is immutable', OLD.id
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            IF OLD.status IN ({TERMINAL_DELIVERIES}) THEN
                RAISE EXCEPTION 'terminal delivery % is immutable', OLD.id
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        "CREATE TRIGGER trg_guard_terminal_delivery BEFORE UPDATE OR DELETE ON delivery_versions "
        "FOR EACH ROW EXECUTE FUNCTION guard_terminal_delivery()"
    )
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION guard_delivery_artifact() RETURNS trigger AS $$
        BEGIN
            IF TG_OP <> 'INSERT' THEN
                RAISE EXCEPTION 'delivery artifact rows are immutable'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            IF EXISTS (
                SELECT 1 FROM delivery_versions
                WHERE id = NEW.delivery_version_id AND status IN ({TERMINAL_DELIVERIES})
            ) THEN
                RAISE EXCEPTION 'terminal delivery % cannot accept artifacts', NEW.delivery_version_id
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        "CREATE TRIGGER trg_guard_delivery_artifacts BEFORE INSERT OR UPDATE OR DELETE ON delivery_artifacts "
        "FOR EACH ROW EXECUTE FUNCTION guard_delivery_artifact()"
    )


def _install_sqlite_guards() -> None:
    statements = [
        f"""CREATE TRIGGER trg_guard_terminal_extraction_run_update
        BEFORE UPDATE ON extraction_runs WHEN OLD.status IN ({TERMINAL_RUNS})
        BEGIN SELECT RAISE(ABORT, 'terminal extraction run is immutable'); END""",
        """CREATE TRIGGER trg_guard_extraction_run_delete
        BEFORE DELETE ON extraction_runs
        BEGIN SELECT RAISE(ABORT, 'extraction run is immutable'); END""",
        """CREATE TRIGGER trg_guard_storage_objects_update
        BEFORE UPDATE ON storage_objects
        BEGIN SELECT RAISE(ABORT, 'storage object is immutable'); END""",
        """CREATE TRIGGER trg_guard_storage_objects_delete
        BEFORE DELETE ON storage_objects
        BEGIN SELECT RAISE(ABORT, 'storage object is immutable'); END""",
        f"""CREATE TRIGGER trg_guard_terminal_delivery_update
        BEFORE UPDATE ON delivery_versions WHEN OLD.status IN ({TERMINAL_DELIVERIES})
        BEGIN SELECT RAISE(ABORT, 'terminal delivery is immutable'); END""",
        """CREATE TRIGGER trg_guard_delivery_delete
        BEFORE DELETE ON delivery_versions
        BEGIN SELECT RAISE(ABORT, 'delivery is immutable'); END""",
    ]
    for table in ("structured_results", "run_artifacts"):
        statements.extend(
            [
                f"""CREATE TRIGGER trg_guard_{table}_insert BEFORE INSERT ON {table}
                WHEN (SELECT status FROM extraction_runs WHERE id = NEW.run_id) IN ({TERMINAL_RUNS})
                BEGIN SELECT RAISE(ABORT, 'terminal run cannot accept child facts'); END""",
                f"""CREATE TRIGGER trg_guard_{table}_update BEFORE UPDATE ON {table}
                BEGIN SELECT RAISE(ABORT, '{table} rows are immutable'); END""",
                f"""CREATE TRIGGER trg_guard_{table}_delete BEFORE DELETE ON {table}
                BEGIN SELECT RAISE(ABORT, '{table} rows are immutable'); END""",
            ]
        )
    statements.extend(
        [
            f"""CREATE TRIGGER trg_guard_delivery_artifacts_insert BEFORE INSERT ON delivery_artifacts
            WHEN (SELECT status FROM delivery_versions WHERE id = NEW.delivery_version_id)
                IN ({TERMINAL_DELIVERIES})
            BEGIN SELECT RAISE(ABORT, 'terminal delivery cannot accept artifacts'); END""",
            """CREATE TRIGGER trg_guard_delivery_artifacts_update BEFORE UPDATE ON delivery_artifacts
            BEGIN SELECT RAISE(ABORT, 'delivery artifact is immutable'); END""",
            """CREATE TRIGGER trg_guard_delivery_artifacts_delete BEFORE DELETE ON delivery_artifacts
            BEGIN SELECT RAISE(ABORT, 'delivery artifact is immutable'); END""",
        ]
    )
    for statement in statements:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("Concurrency fencing and audit immutability are intentionally non-downgradable.")

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from app.db import create_db_and_tables, engine
from app.queue.contracts import QueuePayload, QueuePayloadError
from app.queue.redis_queue import RedisQueue
from content_pipeline.contracts.audit import AUDIT_SCHEMA_VERSION
from content_pipeline.export.csv_contracts import CSV_SCHEMA_VERSION
from content_pipeline.export.audit_exporter import AuditExporter
from content_pipeline.export.csv_contracts import write_chart_fact_csv
from content_pipeline.llm.client import ContentPipelineLLMClient
from content_pipeline.contracts.panel_facts import PanelFactRow
from scripts.check_architecture_boundaries import _imported_modules, check_boundaries


def test_queue_payload_is_versioned_and_rejects_unknown_versions() -> None:
    payload = QueuePayload.paper_parse(42).model_dump(exclude_none=True)

    assert payload == {
        "schema_version": 2,
        "task_type": "paper_parse",
        "job_id": 42,
    }

    with pytest.raises(QueuePayloadError):
        QueuePayload.from_mapping({**payload, "schema_version": 99})


def test_invalid_queue_payload_is_preserved_in_dead_letter_queue() -> None:
    class FakeRedis:
        def __init__(self) -> None:
            self.dead_letters: list[str] = []

        def blpop(self, _queue_name: str, timeout: int):
            return ("queue", '{"schema_version": 99, "task_type": "paper_parse", "paper_id": 1}')

        def rpush(self, queue_name: str, value: str) -> None:
            assert queue_name.endswith(":dead_letter")
            self.dead_letters.append(value)

        def llen(self, _queue_name: str) -> int:
            return len(self.dead_letters)

    queue = RedisQueue("queue")
    fake = FakeRedis()
    queue._redis = fake

    assert queue.dequeue() is None
    assert queue.dead_letter_size() == 1
    assert "Unsupported queue payload" in fake.dead_letters[0]


def test_audit_and_csv_outputs_share_run_metadata(tmp_path: Path) -> None:
    paths = AuditExporter().write_outputs(
        output_dir=tmp_path,
        audit_payload={"evidence_packets": [], "heatmap_candidates": []},
        panel_fact_rows=[],
    )

    audit = json.loads((tmp_path / "extraction_audit.json").read_text(encoding="utf-8"))
    assert audit["schema_version"] == AUDIT_SCHEMA_VERSION
    assert audit["run_metadata"]["run_id"]
    assert audit["run_metadata"]["model_id"]
    assert audit["run_metadata"]["prompt_set_id"]

    header = (tmp_path / "chart_facts.csv").read_text(encoding="utf-8-sig").splitlines()[0].split(",")
    assert header[:4] == ["schema_version", "run_id", "model_id", "prompt_set_id"]
    assert paths["chart_fact_csv"] == str(tmp_path / "chart_facts.csv")
    assert CSV_SCHEMA_VERSION == "csv.v2"


def test_database_startup_records_alembic_revision() -> None:
    create_db_and_tables()

    assert inspect(engine).has_table("alembic_version")


def test_sqlite_foreign_keys_are_enforced() -> None:
    create_db_and_tables()

    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO paper_assets "
                    "(paper_id, asset_index, file_path) VALUES (999999, 0, 'orphan.png')"
                )
            )


def test_architecture_boundaries_are_enforced() -> None:
    root = Path(__file__).resolve().parents[1]

    assert check_boundaries(root) == []


def test_boundary_checker_checks_every_import_in_a_compound_statement() -> None:
    import ast

    node = ast.parse("import os, app.services.agent.llm_client").body[0]

    assert _imported_modules(node) == ["os", "app.services.agent.llm_client"]


def test_model_adapter_sends_evidence_context_to_provider() -> None:
    class CaptureClient:
        def chat_json(self, messages, *, phase):
            self.messages = messages
            return {}

    provider = CaptureClient()
    ContentPipelineLLMClient(provider).call_json(
        prompt="classify this panel",
        inputs={
            "phase_name": "panel_semantic_classifier",
            "panel_id": "fig-1-a",
            "evidence_map": [{"evidence_id": "ev-1", "text": "caption"}],
        },
    )

    outbound_text = json.dumps(provider.messages, ensure_ascii=False)
    assert "fig-1-a" in outbound_text
    assert "evidence_map" in outbound_text
    assert "ev-1" in outbound_text


def test_direct_csv_writer_generates_provenance_when_metadata_is_omitted(tmp_path: Path) -> None:
    path = write_chart_fact_csv(
        tmp_path / "facts.csv",
        [PanelFactRow(fact_id="fact-1", paper_id="paper-1", figure_id="fig-1", panel_id="p1", source_image="p1.png")],
    )
    row = path.read_text(encoding="utf-8-sig").splitlines()[1].split(",")

    assert row[0] == CSV_SCHEMA_VERSION
    assert row[1]
    assert row[2]
    assert row[3]

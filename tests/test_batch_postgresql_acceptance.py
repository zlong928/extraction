from __future__ import annotations

import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from threading import Barrier, Event
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.models import BatchItem, BatchRun, ExtractionRun, Paper, PendingJob, Project, RunArtifact, StructuredResult
from app.repositories import JobClaim, JobRepository
from app.services.batches import BatchScheduler, result_config_hash
from app.services.extraction_runs import create_extraction_run, finalize_extraction_run
from app.services.object_store import ObjectStore
from app.services.storage import LocalStorageAdapter, StorageService
from content_pipeline.contracts.audit import ExtractionRunResult


@pytest.fixture(scope="module")
def postgresql_session_factory():
    database_url = os.getenv("TEST_POSTGRES_URL")
    if not database_url:
        pytest.skip("TEST_POSTGRES_URL is required for PostgreSQL acceptance tests")
    completed = subprocess.run(
        ["uv", "run", "alembic", "upgrade", "head"],
        cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "DATABASE_URL": database_url},
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    engine = create_engine(database_url, pool_pre_ping=True)
    try:
        yield sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    finally:
        engine.dispose()


@dataclass(frozen=True)
class SharedPaperScenario:
    batch_ids: tuple[str, str]
    item_ids: tuple[str, str]
    paper_id: int
    config_hash: str


def _create_two_batch_scenario(
    db: Session,
    *,
    adapter: LocalStorageAdapter,
    name: str,
) -> SharedPaperScenario:
    unique = uuid4().hex
    project = Project(slug=f"{name}-{unique}", name=name)
    db.add(project)
    db.flush()
    pdf_bytes = f"%PDF-1.4\n{name}-{unique}\n%%EOF\n".encode()
    pdf_object = ObjectStore(db, adapter).put_bytes(
        key=f"acceptance/{unique}/source.pdf",
        data=pdf_bytes,
        media_type="application/pdf",
    )
    paper = Paper(
        project_id=project.id,
        pdf_object_id=pdf_object.id,
        title=name,
        original_filename="shared.pdf",
        file_path=pdf_object.object_key,
        file_size=len(pdf_bytes),
        file_hash=pdf_object.sha256,
        status="pending",
    )
    db.add(paper)
    db.flush()
    config_snapshot = {"result_semantics": {"model": "postgres-acceptance"}}
    config_hash = result_config_hash(config_snapshot)
    batches: list[BatchRun] = []
    items: list[BatchItem] = []
    for ordinal in range(2):
        batch = BatchRun(
            project_id=project.id,
            submission_key=f"{name}-{unique}-{ordinal}",
            source_root="/postgres-acceptance",
            batch_concurrency=1,
            config_snapshot=config_snapshot,
            result_config_hash=config_hash,
        )
        db.add(batch)
        db.flush()
        item = BatchItem(
            batch_run_id=batch.id,
            ordinal=0,
            source_relative_path=f"batch-{ordinal}/shared.pdf",
            source_sha256=paper.file_hash,
            source_size_bytes=paper.file_size,
            paper_id=paper.id,
            current_stage="registered",
        )
        db.add(item)
        batches.append(batch)
        items.append(item)
    db.commit()
    return SharedPaperScenario(
        batch_ids=(batches[0].id, batches[1].id),
        item_ids=(items[0].id, items[1].id),
        paper_id=paper.id,
        config_hash=config_hash,
    )


def _claim_and_create_run(
    session_factory,
    *,
    job_id: int,
    config_hash: str,
) -> tuple[JobClaim, str]:
    with session_factory() as db:
        repository = JobRepository(db)
        claimed = repository.claim(job_id, worker_id=f"acceptance-worker-{uuid4().hex}")
        assert claimed is not None
        claim = JobClaim.from_job(claimed)
        paper = db.get(Paper, claim.paper_id)
        assert paper is not None and paper.pdf_object_id is not None
        input_object = paper.pdf_object
        assert input_object is not None
        run = create_extraction_run(
            db,
            job=claim,
            paper=paper,
            input_object=input_object,
            config_snapshot={"result_config_hash": config_hash},
        )
        db.commit()
        return claim, run.id


def _successful_result(label: str) -> ExtractionRunResult:
    return ExtractionRunResult(
        document_graph_summary={},
        figure_panel_graph={},
        chart_facts=[{"fact_id": label, "value": 1}],
        status="succeeded",
    )


@pytest.mark.postgresql
def test_ae3_same_pdf_in_two_batches_executes_once_then_reuses(
    postgresql_session_factory,
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr("app.queue.redis_queue.RedisQueue.enqueue", lambda _queue, _payload: None)
    session_factory = postgresql_session_factory
    adapter = LocalStorageAdapter(tmp_path / "ae3-objects")
    storage = StorageService(adapter=adapter)
    with session_factory() as db:
        scenario = _create_two_batch_scenario(db, adapter=adapter, name="ae3-shared-paper")

    barrier = Barrier(2)

    def schedule(batch_id: str) -> list[int]:
        with session_factory() as db:
            db.execute(text("SET LOCAL lock_timeout = '5s'"))
            barrier.wait(timeout=5)
            return BatchScheduler(db, storage).schedule(batch_id)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(schedule, batch_id) for batch_id in scenario.batch_ids]
        outcomes = [future.result(timeout=15) for future in futures]

    created_job_ids = [job_id for outcome in outcomes for job_id in outcome]
    assert len(created_job_ids) == 1
    job_id = created_job_ids[0]
    with session_factory() as db:
        jobs = db.query(PendingJob).filter(PendingJob.paper_id == scenario.paper_id).all()
        assert [job.id for job in jobs] == [job_id]
        winner = db.get(BatchItem, jobs[0].batch_item_id)
        waiter = db.get(BatchItem, next(item_id for item_id in scenario.item_ids if item_id != winner.id))
        assert winner is not None and winner.status == "queued"
        assert waiter is not None and waiter.status == "pending"
        assert waiter.current_stage == "waiting_for_active_parse"

    claim, run_id = _claim_and_create_run(
        session_factory,
        job_id=job_id,
        config_hash=scenario.config_hash,
    )
    with session_factory() as db:
        run = db.get(ExtractionRun, run_id)
        assert run is not None
        finalize_extraction_run(
            db,
            run=run,
            job=claim,
            raw_responses=[{"provider_request_id": "ae3"}],
            result=_successful_result("ae3-fact"),
            storage_adapter=adapter,
        )
        db.commit()

    with session_factory() as db:
        waiter = db.get(BatchItem, next(item_id for item_id in scenario.item_ids if item_id != claim.batch_item_id))
        assert waiter is not None
        assert BatchScheduler(db, storage).schedule(waiter.batch_run_id) == []

    with session_factory() as db:
        items = db.query(BatchItem).filter(BatchItem.id.in_(scenario.item_ids)).all()
        assert {item.status for item in items} == {"succeeded", "reused"}
        reused = next(item for item in items if item.status == "reused")
        assert reused.resolved_extraction_run_id == run_id
        assert db.query(PendingJob).filter(PendingJob.paper_id == scenario.paper_id).count() == 1
        assert db.query(ExtractionRun).filter(ExtractionRun.paper_id == scenario.paper_id).count() == 1
        assert db.query(StructuredResult).filter(StructuredResult.run_id == run_id).count() == 1
        assert db.query(RunArtifact).filter(
            RunArtifact.run_id == run_id,
            RunArtifact.role == "model_raw_responses",
        ).count() == 1


@pytest.mark.postgresql
def test_terminal_finalizer_and_scheduler_share_one_lock_order_without_deadlock(
    postgresql_session_factory,
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr("app.queue.redis_queue.RedisQueue.enqueue", lambda _queue, _payload: None)
    session_factory = postgresql_session_factory
    adapter = LocalStorageAdapter(tmp_path / "terminal-lock-objects")
    storage = StorageService(adapter=adapter)
    with session_factory() as db:
        scenario = _create_two_batch_scenario(db, adapter=adapter, name="terminal-lock-order")
        first_batch = db.get(BatchRun, scenario.batch_ids[0])
        second_item = db.get(BatchItem, scenario.item_ids[1])
        assert first_batch is not None and second_item is not None
        second_item.batch_run_id = first_batch.id
        second_item.ordinal = 1
        second_item.source_relative_path = "waiting/shared.pdf"
        first_batch.batch_concurrency = 2
        db.commit()
        batch_id = first_batch.id

    with session_factory() as db:
        assert BatchScheduler(db, storage).schedule(batch_id)
        job = db.query(PendingJob).filter(PendingJob.paper_id == scenario.paper_id).one()
        job_id = job.id

    claim, run_id = _claim_and_create_run(
        session_factory,
        job_id=job_id,
        config_hash=scenario.config_hash,
    )
    start = Barrier(2)
    terminal_lock_entered = Event()
    scheduler_entered = Event()
    original_lock_terminal_context = JobRepository.lock_terminal_context

    def observed_lock_terminal_context(self, job):
        terminal_lock_entered.set()
        assert scheduler_entered.wait(timeout=5)
        return original_lock_terminal_context(self, job)

    monkeypatch.setattr(JobRepository, "lock_terminal_context", observed_lock_terminal_context)

    def finalize() -> None:
        with session_factory() as db:
            db.execute(text("SET LOCAL lock_timeout = '5s'"))
            run = db.get(ExtractionRun, run_id)
            paper = db.get(Paper, scenario.paper_id)
            assert run is not None and paper is not None
            paper.status = "completed"
            paper.text_content = "worker output waiting for terminal commit"
            start.wait(timeout=5)
            finalize_extraction_run(
                db,
                run=run,
                job=claim,
                raw_responses=[{"provider_request_id": "terminal-lock"}],
                result=_successful_result("terminal-lock-fact"),
                storage_adapter=adapter,
            )
            db.commit()

    def schedule_waiter() -> list[int]:
        with session_factory() as db:
            db.execute(text("SET LOCAL lock_timeout = '5s'"))
            start.wait(timeout=5)
            scheduler_entered.set()
            assert terminal_lock_entered.wait(timeout=5)
            return BatchScheduler(db, storage).schedule(batch_id)

    with ThreadPoolExecutor(max_workers=2) as executor:
        terminal_future = executor.submit(finalize)
        scheduler_future = executor.submit(schedule_waiter)
        terminal_future.result(timeout=15)
        assert scheduler_future.result(timeout=15) == []

    assert terminal_lock_entered.is_set() and scheduler_entered.is_set()
    with session_factory() as db:
        assert BatchScheduler(db, storage).schedule(batch_id) == []

    with session_factory() as db:
        items = db.query(BatchItem).filter(BatchItem.batch_run_id == batch_id).order_by(BatchItem.ordinal).all()
        job = db.get(PendingJob, job_id)
        run = db.get(ExtractionRun, run_id)
        paper = db.get(Paper, scenario.paper_id)
        assert [item.status for item in items] == ["succeeded", "reused"]
        assert items[1].resolved_extraction_run_id == run_id
        assert job is not None and job.status == "done"
        assert run is not None and run.status == "succeeded"
        assert paper is not None and paper.status == "completed"
        assert db.query(PendingJob).filter(PendingJob.paper_id == scenario.paper_id).count() == 1
        assert db.query(ExtractionRun).filter(ExtractionRun.paper_id == scenario.paper_id).count() == 1

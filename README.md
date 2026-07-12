# Extraction Service

FastAPI + React service for PDF/MinerU ingestion and LLM/VLM scientific image extraction. The online architecture separates three kinds of state:

- PostgreSQL: projects, papers, object metadata, task/lease state, immutable extraction runs, normalized results and delivery versions.
- S3-compatible object storage: PDFs, images, MinerU artifacts, raw model responses, Markdown/Excel and delivery files.
- Redis: lightweight queue messages containing a versioned task ID; it is not a business database.

SQLite and local object storage remain available for development and tests. DuckDB and Parquet are generated only as immutable delivery snapshots.

## Install and verify

```bash
uv sync
uv run alembic upgrade head
uv run pytest
npm --prefix frontend run build
```

Do not use system Python or `pip`; Python dependencies and commands are managed through `uv`.

## Local development

The zero-infrastructure mode uses SQLite plus the local `StorageAdapter`:

```bash
export DATABASE_URL=sqlite:///./data/extraction.db
export STORAGE_BACKEND=local
export STORAGE_LOCAL_ROOT=./data/objects
uv run alembic upgrade head
uv run uvicorn app.main:app --host 0.0.0.0 --port 8001 --reload
npm --prefix frontend run dev
```

Vite proxies `/papers` and `/extractions` to `http://127.0.0.1:8001`. Override with `VITE_API_PROXY_TARGET`; use `VITE_API_BASE_URL` only for direct cross-origin calls and configure `CORS_ALLOWED_ORIGINS` accordingly.

## Production-shaped Docker stack

```bash
cp .env.example .env.local
docker compose --env-file .env.local up --build postgres minio minio-init redis api worker
```

Compose starts PostgreSQL 16, versioned MinIO, Redis, API and worker. API and worker do not share a persistent filesystem volume. Example defaults are for local development only; replace database/object-store credentials in deployed secret management. API and worker serialize startup migrations with a PostgreSQL advisory lock, but production releases should still run an explicit migration job before rolling out application replicas:

```bash
DATABASE_URL='postgresql+psycopg://…' uv run alembic upgrade head
```

## Object storage configuration

Local adapter:

```bash
STORAGE_BACKEND=local
STORAGE_LOCAL_ROOT=./data/objects
```

S3-compatible adapter:

```bash
STORAGE_BACKEND=s3
S3_BUCKET=extraction
S3_PREFIX=production
S3_ENDPOINT_URL=https://s3.example.internal   # omit for AWS S3
S3_REGION=us-east-1
AWS_ACCESS_KEY_ID=…
AWS_SECRET_ACCESS_KEY=…
```

Database rows store object IDs, keys, URIs, SHA-256, byte size and media type—not PDF/image/Excel bytes. Code that needs a path materializes an object into a temporary workspace through the adapter.

## Queue and extraction semantics

Queue schema v2 contains only `schema_version`, `task_type` and `job_id`. A worker loads the paper, input object and configuration from PostgreSQL/object storage, claims the row with a renewable lease and monotonically increasing fencing generation, and creates exactly one `ExtractionRun` per task. Repeated submissions share an idempotency key; an explicit retry creates a new task/run and never overwrites a terminal run. Database triggers enforce terminal-run, result, object and published-delivery immutability even for bulk SQL that bypasses the ORM.

Each completed run retains:

- input object and linked paper/assets/pages;
- model/provider/version, prompt version and pipeline version;
- configuration snapshot and timestamps;
- raw provider responses in object storage;
- normalized `structured_results` rows for query/export;
- audit/CSV/Markdown pipeline outputs in object storage.

## Build an immutable delivery

```bash
uv run python -m app.delivery.cli --version 2026-07-11-v1 --project-id 1
```

Repeat `--paper-id <id>` and/or `--paper-status <status>` to build a validated subset. The command publishes `papers.parquet`, `paper_assets.parquet`, `extraction_runs.parquet`, `structured_results.parquet`, `run_artifacts.parquet`, `snapshot.duckdb`, `snapshot.xlsx`, `README.md` and `manifest.json` below `deliveries/<version>/`. Version names are unique and cannot be overwritten. The manifest records the snapshot boundary, scope, schema/pipeline/model/prompt versions, counts, configuration hash and each data file checksum.

## Migrate legacy SQLite data

1. Stop API/worker writes and back up the SQLite database plus legacy upload/result directories.
2. Configure the target PostgreSQL and object store, then run:

```bash
STORAGE_BACKEND=s3 \
S3_BUCKET=extraction \
uv run python scripts/migrate_sqlite_to_postgres.py \
  --sqlite-path ./data/extraction.db \
  --source-storage-root ./data/uploads \
  --postgres-url 'postgresql+psycopg://user:password@host/db'
```

3. Compare source/target record counts, sample object SHA-256 values, run a delivery build, and only then switch traffic.

The target must be empty. This command is deliberately limited to the legacy SQLite schema; it fails before copying if the source already contains production `storage_objects`, run/result or delivery facts, because those require an explicit immutable-fact export/import. Invalid legacy foreign keys or missing files stop or surface in verification; do not silently discard them. More detail is in [SQLite migration runbook](docs/sqlite-to-postgresql-migration.md).

## API

- `POST /papers/upload` — upload PDF and enqueue parse/extraction.
- `GET /papers`, `GET /papers/{paper_id}` — query active papers.
- `GET /papers/{paper_id}/figures`, `GET /papers/{paper_id}/audit-tables`.
- `GET /papers/assets/{asset_id}` — stream an object through the storage adapter.
- `POST /papers/{paper_id}/chart-only/run` and retry/delete compatibility routes.

Required external service variables include `MINERU_*`, `OPENAI_*`, and optional `LLM_*`/`VLM_*` overrides. `.env.example` contains placeholders only.

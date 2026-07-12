# SQLite → PostgreSQL / object storage migration runbook

## Preconditions

- Put the legacy service in maintenance mode; no API or worker may write during the copy.
- Back up `extraction.db`, uploads and MinerU/result directories and test restoring that backup.
- Provision an empty PostgreSQL database and an empty/versioned object-storage prefix.
- Run `uv sync`; never use `pip` or system Python.
- Confirm the source is the legacy SQLite layout. The migrator intentionally refuses a source containing populated `storage_objects`, extraction run/result, or delivery tables so that current immutable facts cannot be silently omitted.

## Copy

Run the command documented in the README. The script applies Alembic to the target, copies relational rows in foreign-key order, assigns legacy papers to project `1`, creates deterministic legacy job idempotency keys, uploads available PDFs/images/Markdown/content lists, records object metadata, and repairs PostgreSQL sequences.

Legacy absolute paths are accepted only as migration inputs. Relative paths must stay under `--source-storage-root`; traversal outside it is rejected. Missing objects remain visible as rows without an object ID and must be reconciled before cutover.

## Verification

1. Compare counts for `papers`, `figures`, `paper_assets`, `panels`, `image_extractions`, and `pending_jobs`.
2. Confirm every active PDF/image row has a `storage_objects` reference and independently recompute a sample of SHA-256 values.
3. Run `uv run alembic current` against the target and require `0005_concurrency_guards` (or a later reviewed head).
4. Run a parse retry and a chart-only retry; confirm each produces a new run and leaves the old terminal run unchanged.
5. Build a delivery package and validate every manifest checksum.
6. Point a staging API/worker at PostgreSQL + object storage, restart workers, and verify jobs recover from IDs without a shared directory.

## Cutover and rollback

Take a final source backup, rerun the copy into a fresh target/prefix if the verification window discovered changes, then switch application secrets atomically. Keep the SQLite backup and old object tree read-only for the agreed retention period. Rollback changes connection/storage configuration; never copy partially written PostgreSQL facts back into the old SQLite file.

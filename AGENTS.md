# Repository Guidelines

## Project Structure & Module Organization

FastAPI code is in `app/`; extraction stages and contracts are in `content_pipeline/`. Put utilities in `scripts/`, prompts in `prompts/`, docs in `docs/`, and tests in `tests/`. React/Vite code is under `frontend/src/`; generated artifacts belong in ignored `data/` paths.

## Tooling and Development Commands

Use `uv` for all Python work; never invoke system Python or `pip`. Install with `uv sync`, run tools with `uv run`, and commit `pyproject.toml` plus `uv.lock`. `requirements.txt` remains the Docker input.

- `uv sync` — update the project environment.
- `uv run pytest` — run all backend and pipeline tests.
- `uv run pytest tests/test_content_pipeline_e2e.py -q` — run one module.
- `uv run uvicorn app.main:app --host 0.0.0.0 --port 8001 --reload` — start the API.
- `npm --prefix frontend run dev` / `npm --prefix frontend run build` — develop or build the frontend.
- `docker compose up --build api worker redis` — run the complete stack.

Run non-Python tools directly; do not wrap them in `uv run`.

## Coding Style & Naming Conventions

Python uses four spaces, type annotations, `snake_case` functions/files, and `PascalCase` classes. TypeScript uses two spaces, `PascalCase` components, and `camelCase` functions. Prefer small modules and typed contracts. No formatter or linter is configured; match adjacent code.

## Testing Guidelines

Pytest discovers `tests/test_*.py` via `pytest.ini`; name tests `test_<behavior>` and share fixtures through `tests/conftest.py`. Test contract changes at unit level and boundary changes through integration tests. Live credentials require explicit marking and documentation.

## Architecture Documentation

Treat `ARCHITECTURE.md` as authoritative for ownership, boundaries, dependencies, and data flows. Read it before architecture-sensitive work and verify details in code. Update it when responsibilities, directories, entry points, pipeline stages, cross-layer rules, or external boundaries change. Internal fixes need no update.

## Commit & Pull Request Guidelines

History uses Conventional Commits (`chore: initialize extraction project`). Continue with `feat:`, `fix:`, `test:`, `docs:`, or `refactor:` and an imperative summary. PRs must explain the effect, list verification, link issues, include UI screenshots, and identify schema, configuration, migration, or architecture impacts. Never commit secrets or generated data.

## Instruction Scope & Agent Review

Apply repository guidance in this order:

1. Explicit user instructions for the current task.
2. The nearest applicable nested `AGENTS.md`.
3. The repository root `AGENTS.md`.
4. Global instructions from `.codex/AGENTS.md`.

Before delivery, attack logical gaps, factual errors, and complexity. Fix the three to five likeliest failures and report verification evidence. Keep code, rules, and architecture documentation consistent.

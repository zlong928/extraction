# Extraction Service

Slim FastAPI service for two flows only:

- paper PDF upload
- image asset extraction from uploaded papers

Intentionally not migrated from the old project: auth, chat, notes, tags, RAG,
embedding, KG extraction, Obsidian sync, OAuth, and background Redis workers.

## Run

```bash
docker compose up --build api worker redis
```

For local frontend development, keep the API on `8001` and start Vite on `5173`:

```bash
.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8001 --reload
cd frontend && npm run dev
```

The frontend uses same-origin API calls by default. In Vite dev mode, `/papers`
and `/extractions` are proxied to `http://127.0.0.1:8001`. Override with:

```bash
VITE_API_PROXY_TARGET=http://127.0.0.1:8001 npm run dev
```

Use `VITE_API_BASE_URL` only when the browser must call a remote API directly.
If you do that, make sure `CORS_ALLOWED_ORIGINS` on the API includes the exact
frontend origin.

## Debugging "Failed to fetch"

`Failed to fetch` means the browser could not get a usable HTTP response. Check
these in order:

1. API process is running: `curl http://127.0.0.1:8001/`
2. API endpoint works directly: `curl -i http://127.0.0.1:8001/papers`
3. Vite proxy works: `curl -i http://127.0.0.1:5173/papers`
4. Browser origin matches CORS if using `VITE_API_BASE_URL`.
5. Backend logs: a 500 response is not a fetch/CORS problem; fix the Python
   exception first.

## API

- `POST /papers/upload` multipart field `file`, optional form field `title`; creates a paper and queues MinerU parsing
- `GET /papers`
- `GET /papers/{paper_id}`
- `GET /papers/assets/{asset_id}`
- `POST /papers/assets/{asset_id}/extract`; creates an LLM image extraction job and queues the worker
- `GET /extractions/{extraction_id}`
- `GET /extractions/{extraction_id}/csv`

## Required Runtime Config

The service reuses the old project's model/MinerU environment names:

- `MINERU_API_KEY`, `MINERU_API_BASE_URL`, `MINERU_MODEL_VERSION`, `MINERU_LANGUAGE`
- `OPENAI_BASE_URL`, `OPENAI_API_KEY`, `OPENAI_MODEL`
- or `LLM_*` / `VLM_*` overrides for the image agents

The image extraction path is LLM/agent-first. The temporary local OpenCV sampling
implementation was removed from the main flow.

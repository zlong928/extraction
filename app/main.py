from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.papers import router as papers_router
from app.config import CORS_ALLOWED_ORIGINS, ensure_runtime_dirs
from app.db import create_db_and_tables


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_runtime_dirs()
    create_db_and_tables()
    yield


app = FastAPI(title="Extraction Service", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def root() -> dict[str, str]:
    return {"message": "Extraction Service is running"}


app.include_router(papers_router)

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

DATA_DIR = Path(os.getenv("DATA_DIR", BASE_DIR / "data"))
UPLOAD_DIR = DATA_DIR / "uploads"
RESULT_DIR = DATA_DIR / "results"
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DATA_DIR / 'extraction.db'}")
MAX_UPLOAD_SIZE_BYTES = int(os.getenv("MAX_UPLOAD_SIZE_BYTES", str(100 * 1024 * 1024)))
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
PAPER_PARSE_QUEUE_NAME = os.getenv("PAPER_PARSE_QUEUE_NAME", "paper_parse_queue")
MINERU_API_BASE_URL = os.getenv("MINERU_API_BASE_URL", "https://mineru.net")
MINERU_API_KEY = os.getenv("MINERU_API_KEY", "")
MINERU_MODEL_VERSION = os.getenv("MINERU_MODEL_VERSION", "vlm")
MINERU_LANGUAGE = os.getenv("MINERU_LANGUAGE", "en")
MINERU_POLL_INTERVAL_SECONDS = float(os.getenv("MINERU_POLL_INTERVAL_SECONDS", "5"))
MINERU_TIMEOUT_SECONDS = int(os.getenv("MINERU_TIMEOUT_SECONDS", "1800"))
MINERU_SUBMIT_RATE_LIMIT_PER_MINUTE = int(os.getenv("MINERU_SUBMIT_RATE_LIMIT_PER_MINUTE", "50"))
MINERU_RESULT_RATE_LIMIT_PER_MINUTE = int(os.getenv("MINERU_RESULT_RATE_LIMIT_PER_MINUTE", "1000"))

OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", os.getenv("LLM_BASE_URL", "https://api.openai.com/v1"))
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", os.getenv("LLM_API_KEY", ""))
OPENAI_MODEL = os.getenv("OPENAI_MODEL", os.getenv("LLM_MODEL", "gpt-4o-mini"))
CORS_ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        "CORS_ALLOWED_ORIGINS",
        "http://localhost:3000,http://127.0.0.1:3000,http://localhost:5173,http://127.0.0.1:5173",
    ).split(",")
    if origin.strip()
]


def ensure_runtime_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    RESULT_DIR.mkdir(parents=True, exist_ok=True)

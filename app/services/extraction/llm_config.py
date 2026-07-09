from __future__ import annotations

import os

from app import config as app_config


def env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "t", "yes", "y", "on"}


def env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def capped_concurrency(value: int, *, limit_name: str, default_limit: int = 6) -> int:
    limit = env_int(limit_name, env_int("LLM_PROVIDER_CONCURRENCY_LIMIT", default_limit))
    return min(value, limit) if limit > 0 else value


def _lora_headers(prefix: str) -> dict[str, str]:
    lora_id = os.getenv(f"{prefix}_LORA_ID", "").strip()
    return {"lora_id": lora_id} if lora_id else {}


def build_llm_config() -> dict:
    return {
        "base_url": os.getenv("LLM_BASE_URL") or app_config.OPENAI_BASE_URL,
        "api_key": os.getenv("LLM_API_KEY") or app_config.OPENAI_API_KEY,
        "model": os.getenv("LLM_MODEL") or app_config.OPENAI_MODEL,
        "fallback_models": os.getenv("LLM_FALLBACK_MODELS", ""),
        "api_format": os.getenv("LLM_API_FORMAT", "responses"),
        "timeout": float(os.getenv("LLM_TIMEOUT_SECONDS", "120")),
        "stream_max_seconds": float(os.getenv("LLM_STREAM_MAX_SECONDS", "180")),
        "max_concurrency": capped_concurrency(env_int("LLM_MAX_CONCURRENCY", 4), limit_name="LLM_PROVIDER_CONCURRENCY_LIMIT"),
        "http_retries": int(os.getenv("LLM_HTTP_RETRIES", "2")),
        "retry_backoff_seconds": float(os.getenv("LLM_RETRY_BACKOFF_SECONDS", "1.5")),
        "min_request_interval_seconds": float(os.getenv("LLM_MIN_REQUEST_INTERVAL_SECONDS", "0")),
        "allow_root_chat_fallback": env_bool("LLM_ALLOW_ROOT_CHAT_FALLBACK", False),
        "allow_non_stream_fallback": env_bool("LLM_ALLOW_NON_STREAM_FALLBACK", False),
        "stream": env_bool("LLM_STREAM", True),
        "extra_headers": _lora_headers("LLM"),
        "force_jpeg_images": env_bool("LLM_IMAGE_FORCE_JPEG", True),
        "max_image_bytes": int(os.getenv("LLM_IMAGE_MAX_BYTES", "1500000")),
        "max_image_side": int(os.getenv("LLM_IMAGE_MAX_SIDE", "1000")),
        "image_jpeg_quality": int(os.getenv("LLM_IMAGE_JPEG_QUALITY", "75")),
    }


def build_vlm_config() -> dict:
    shared = build_llm_config()
    shared.update(
        {
            "base_url": os.getenv("VLM_BASE_URL") or shared["base_url"],
            "api_key": os.getenv("VLM_API_KEY") or shared["api_key"],
            "model": os.getenv("VLM_MODEL") or shared["model"],
            "fallback_models": os.getenv("VLM_FALLBACK_MODELS", shared["fallback_models"]),
            "api_format": os.getenv("VLM_API_FORMAT") or shared["api_format"],
            "timeout": float(os.getenv("VLM_TIMEOUT_SECONDS", str(shared["timeout"]))),
            "stream_max_seconds": float(os.getenv("VLM_STREAM_MAX_SECONDS", str(shared["stream_max_seconds"]))),
            "max_concurrency": capped_concurrency(
                env_int("VLM_MAX_CONCURRENCY", int(shared["max_concurrency"])),
                limit_name="VLM_PROVIDER_CONCURRENCY_LIMIT",
            ),
            "http_retries": int(os.getenv("VLM_HTTP_RETRIES", str(shared["http_retries"]))),
            "retry_backoff_seconds": float(os.getenv("VLM_RETRY_BACKOFF_SECONDS", str(shared["retry_backoff_seconds"]))),
            "min_request_interval_seconds": float(
                os.getenv("VLM_MIN_REQUEST_INTERVAL_SECONDS", str(shared["min_request_interval_seconds"]))
            ),
            "allow_root_chat_fallback": env_bool(
                "VLM_ALLOW_ROOT_CHAT_FALLBACK",
                bool(shared["allow_root_chat_fallback"]),
            ),
            "allow_non_stream_fallback": env_bool(
                "VLM_ALLOW_NON_STREAM_FALLBACK",
                env_bool("LLM_ALLOW_NON_STREAM_FALLBACK", False),
            ),
            "stream": env_bool("VLM_STREAM", bool(shared["stream"])),
            "extra_headers": _lora_headers("VLM") or shared["extra_headers"],
            "force_jpeg_images": env_bool("VLM_IMAGE_FORCE_JPEG", bool(shared["force_jpeg_images"])),
            "max_image_bytes": int(os.getenv("VLM_IMAGE_MAX_BYTES", str(shared["max_image_bytes"]))),
            "max_image_side": int(os.getenv("VLM_IMAGE_MAX_SIDE", str(shared["max_image_side"]))),
            "image_jpeg_quality": int(os.getenv("VLM_IMAGE_JPEG_QUALITY", str(shared["image_jpeg_quality"]))),
        }
    )
    return shared

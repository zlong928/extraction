from __future__ import annotations

import base64
import copy
import os
import json
import re
import threading
import time
from pathlib import Path

import httpx


_REQUEST_SEMAPHORES: dict[str, threading.BoundedSemaphore] = {}
_REQUEST_SEMAPHORES_LOCK = threading.Lock()


def _shared_request_semaphore(base_url: str, concurrency: int) -> threading.BoundedSemaphore:
    key = base_url.rstrip("/")
    with _REQUEST_SEMAPHORES_LOCK:
        semaphore = _REQUEST_SEMAPHORES.get(key)
        if semaphore is None:
            semaphore = threading.BoundedSemaphore(concurrency)
            _REQUEST_SEMAPHORES[key] = semaphore
        return semaphore



def _extra_headers(value: object) -> dict[str, str]:
    if not value:
        return {}
    if isinstance(value, dict):
        return {
            str(key): str(header_value)
            for key, header_value in value.items()
            if str(key).strip() and header_value is not None
        }
    if not isinstance(value, str):
        return {}
    text = value.strip()
    if not text:
        return {}
    if text.startswith("{"):
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return {}
        return _extra_headers(parsed)
    headers: dict[str, str] = {}
    for item in text.split(","):
        if ":" in item:
            key, header_value = item.split(":", 1)
        elif "=" in item:
            key, header_value = item.split("=", 1)
        else:
            continue
        key = key.strip()
        if key:
            headers[key] = header_value.strip()
    return headers


class LLMClient:
    """Shared LLM call layer for all agents. Thread-safe token tracking."""

    def __init__(self, config: dict) -> None:
        self.base_url = str(config.get("base_url") or "https://api.openai.com/v1").rstrip("/")
        self.api_key = str(config.get("api_key") or "")
        self.model = str(config.get("model") or "gpt-4o-mini")
        self.fallback_models = self._parse_fallback_models(config.get("fallback_models", ""))
        self.api_format = str(config.get("api_format", "responses")).strip().lower()
        self.extra_headers = _extra_headers(config.get("extra_headers") or "")
        self.timeout = float(config.get("timeout") or 60)
        self.stream_max_seconds = max(1.0, float(config.get("stream_max_seconds", 90.0)))
        self.http_retries = max(0, int(config.get("http_retries", 2)))
        self.retry_backoff_seconds = max(0.0, float(config.get("retry_backoff_seconds", 1.2)))
        self.min_request_interval_seconds = max(0.0, float(config.get("min_request_interval_seconds", 0.0)))
        self.allow_root_chat_fallback = bool(config.get("allow_root_chat_fallback", False))
        self.allow_non_stream_fallback = bool(config.get("allow_non_stream_fallback", False))
        self.stream = bool(config.get("stream", True))
        self.force_jpeg_images = bool(config.get("force_jpeg_images", True))
        self.max_image_bytes = max(1, int(config.get("max_image_bytes", 1_500_000)))
        self.max_image_side = max(256, int(config.get("max_image_side", 1600)))
        self.image_jpeg_quality = min(95, max(40, int(config.get("image_jpeg_quality", 75))))
        configured_concurrency = max(1, int(config.get("max_concurrency", 4)))
        provider_limit = int(config.get("provider_concurrency_limit", 6))
        concurrency = min(configured_concurrency, provider_limit) if provider_limit > 0 else configured_concurrency
        self._request_semaphore = _shared_request_semaphore(self.base_url, concurrency)
        self._last_request_at = 0.0
        self._token_stats: dict[str, dict] = {}
        self._lock = threading.Lock()

    @property
    def token_stats(self) -> dict[str, dict]:
        with self._lock:
            return dict(self._token_stats)

    def chat_json(self, messages: list[dict], *, phase: str) -> dict:
        body = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
            "stream": self.stream,
        }
        last_error: Exception | None = None
        for model in self._model_chain():
            request_body = dict(body)
            request_body["model"] = model
            try:
                content = self._chat_content(request_body, phase=phase)
                return self._parse_json(content)
            except (ValueError, json.JSONDecodeError) as exc:
                last_error = exc
                continue
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                continue
        self._merge_usage(phase, {})
        model_candidates = " > ".join(self._model_chain())
        raise RuntimeError(f"LLM JSON 调用失败 (phase={phase}, models={model_candidates}): {last_error}")

    def chat_text(self, messages: list[dict], *, phase: str, max_tokens: int | None = None) -> str:
        body = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.1,
            "stream": self.stream,
        }
        if max_tokens is not None:
            body["max_tokens"] = max_tokens
        last_error: Exception | None = None
        for model in self._model_chain():
            request_body = dict(body)
            request_body["model"] = model
            try:
                return self._chat_content(request_body, phase=phase)
            except Exception as exc:
                last_error = exc
                continue
        model_candidates = " > ".join(self._model_chain())
        raise RuntimeError(f"LLM 文本调用失败 (phase={phase}, models={model_candidates}): {last_error}")

    def _model_chain(self) -> list[str]:
        models: list[str] = [self.model]
        for fallback in self.fallback_models:
            if fallback not in models:
                models.append(fallback)
        return models

    def image_base64(self, image_path: str) -> str | None:
        try:
            path = Path(image_path)
            if not path.exists():
                return None
            raw = path.read_bytes()
            prepared, _mime_type = self._prepare_image_bytes(raw)
            return base64.b64encode(prepared).decode("utf-8")
        except Exception:
            return None

    def image_data_url(self, image_path: str) -> str | None:
        try:
            path = Path(image_path)
            if not path.exists():
                return None
            raw = path.read_bytes()
            prepared, mime_type = self._prepare_image_bytes(raw)
            encoded = base64.b64encode(prepared).decode("utf-8")
            return f"data:{mime_type};base64,{encoded}"
        except Exception:
            return None

    def _chat_content(self, body: dict, *, phase: str) -> str:
        if self.api_format == "responses":
            content, usage = self._responses_content(body)
            self._merge_usage(phase, usage)
            return content
        if self.api_format != "openai_chat":
            raise RuntimeError(f"Unsupported LLM_API_FORMAT={self.api_format}; expected responses or openai_chat.")
        errors: list[str] = []
        for request_body in self._compatible_request_bodies(body):
            for url in self._chat_urls():
                try:
                    content, usage = self._request_content(url, request_body)
                    if content:
                        self._merge_usage(phase, usage)
                        return content
                except Exception as exc:
                    errors.append(self._format_attempt_error(url, request_body, exc))
                    continue
        error_summary = " | ".join(errors[-6:]) if errors else "no response content"
        raise RuntimeError(f"LLM chat failed phase={phase} model={self.model} format={self.api_format}: {error_summary}")

    def _compatible_request_bodies(self, body: dict) -> list[dict]:
        bodies: list[dict] = []
        seen: set[str] = set()
        if body.get("stream"):
            variants = [
                {"stream": True, "response_format": True, "temperature": True},
                {"stream": True, "response_format": False, "temperature": True},
            ]
            if self.allow_non_stream_fallback:
                variants.extend(
                    [
                        {"stream": False, "response_format": True, "temperature": True},
                        {"stream": False, "response_format": False, "temperature": True},
                    ]
                )
        else:
            variants = [
                {"stream": False, "response_format": True, "temperature": True},
                {"stream": False, "response_format": False, "temperature": True},
            ]
        variants = tuple(
            {"stream": item["stream"], "response_format": item["response_format"], "temperature": False}
            if self._is_openai_gpt_model() and not item["response_format"]
            else item
            for item in variants
        )
        for variant in variants:
            candidate = copy.deepcopy(body)
            candidate["stream"] = bool(variant["stream"])
            if not variant["response_format"]:
                candidate.pop("response_format", None)
            if not variant["temperature"]:
                candidate.pop("temperature", None)
            key = json.dumps(candidate, sort_keys=True, ensure_ascii=False, default=str)
            if key not in seen:
                seen.add(key)
                bodies.append(candidate)
        return bodies

    def _is_openai_gpt_model(self) -> bool:
        normalized = self.model.strip().lower()
        return normalized.startswith("gpt-") or normalized.startswith("o")

    def _responses_content(self, chat_body: dict) -> tuple[str, dict]:
        model = str(chat_body.get("model") or self.model)
        body = {
            "model": model,
            "input": self._responses_input(chat_body.get("messages") or []),
            "store": False,
        }
        if chat_body.get("max_tokens") is not None:
            body["max_output_tokens"] = chat_body["max_tokens"]

        errors: list[str] = []
        for url in self._responses_urls():
            try:
                content, usage = self._request_responses_content(url, body)
                if content:
                    return content, usage
            except Exception as exc:
                errors.append(self._format_responses_attempt_error(url, exc))
        error_summary = " | ".join(errors[-3:]) if errors else "no response content"
        raise RuntimeError(f"LLM responses failed model={model} format={self.api_format}: {error_summary}")

    def _responses_input(self, messages: list[dict]) -> list[dict]:
        response_messages = []
        for message in messages:
            role = str(message.get("role") or "user")
            if role == "system":
                role = "developer"
            content = message.get("content") or ""
            if isinstance(content, list):
                parts = []
                for item in content:
                    if not isinstance(item, dict):
                        continue
                    item_type = item.get("type")
                    if item_type == "text":
                        parts.append({"type": "input_text", "text": str(item.get("text") or "")})
                    elif item_type == "image_url":
                        image_url = item.get("image_url") or {}
                        url = image_url.get("url") if isinstance(image_url, dict) else None
                        if url:
                            parts.append({"type": "input_image", "image_url": str(url)})
                response_messages.append({"role": role, "content": parts or [{"type": "input_text", "text": ""}]})
            else:
                response_messages.append({"role": role, "content": [{"type": "input_text", "text": str(content)}]})
        return response_messages

    def _request_responses_content(self, url: str, body: dict) -> tuple[str, dict]:
        attempts = self.http_retries + 1
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                with self._request_semaphore:
                    self._throttle_request_start()
                    response = httpx.post(
                        url,
                        headers={
                            **self._request_headers(),
                        },
                        json=body,
                        timeout=self.timeout,
                    )
                if response.status_code >= 400:
                    raise RuntimeError(self._response_error(response, response.text))
                payload = self._json_response(response)
                content = self._responses_text(payload)
                if not content:
                    raise RuntimeError(self._empty_content_error(url, body, response.text[:300]))
                return content, payload.get("usage") or {}
            except Exception as exc:
                last_error = exc
                if attempt >= attempts - 1 or not self._retryable_error(exc):
                    raise
                time.sleep(self.retry_backoff_seconds * (2 ** attempt))
        raise RuntimeError(str(last_error) if last_error else "request failed")

    def _responses_text(self, payload: dict) -> str:
        output_text = payload.get("output_text")
        if isinstance(output_text, str) and output_text.strip():
            return output_text.strip()
        chunks: list[str] = []
        for output_item in payload.get("output") or []:
            if not isinstance(output_item, dict):
                continue
            for content_item in output_item.get("content") or []:
                if not isinstance(content_item, dict):
                    continue
                text = content_item.get("text")
                if isinstance(text, str) and text:
                    chunks.append(text)
                elif content_item.get("type") in {"output_text", "text"} and isinstance(content_item.get("content"), str):
                    chunks.append(content_item["content"])
        return "".join(chunks).strip()

    def _responses_urls(self) -> list[str]:
        base = self.base_url.rstrip("/")
        if base.endswith("/v1") or base.endswith("/v2"):
            return [f"{base}/responses"]
        return [f"{base}/v1/responses"]

    def _format_responses_attempt_error(self, url: str, exc: Exception) -> str:
        return f"url={url}: {exc}"

    def _request_content(self, url: str, body: dict) -> tuple[str, dict]:
        attempts = self.http_retries + 1
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                with self._request_semaphore:
                    self._throttle_request_start()
                    if body.get("stream"):
                        return self._stream_content(url, body)
                    return self._non_stream_content(url, body)
            except Exception as exc:
                last_error = exc
                if attempt >= attempts - 1 or not self._retryable_error(exc):
                    raise
                time.sleep(self.retry_backoff_seconds * (2 ** attempt))
        raise RuntimeError(str(last_error) if last_error else "request failed")

    def _stream_content(self, url: str, body: dict) -> tuple[str, dict]:
        chunks: list[str] = []
        usage: dict = {}
        raw_lines: list[str] = []
        started = time.monotonic()
        with httpx.stream(
            "POST",
            url,
            headers={
                **self._request_headers(),
            },
            json=body,
            timeout=self.timeout,
        ) as response:
            if response.status_code >= 400:
                error_text = response.read().decode("utf-8", errors="replace")
                raise RuntimeError(self._response_error(response, error_text))
            for line in response.iter_lines():
                if time.monotonic() - started > self.stream_max_seconds:
                    raise TimeoutError(f"stream exceeded {self.stream_max_seconds:.0f}s without completion")
                if line and len(raw_lines) < 3:
                    raw_lines.append(line[:200])
                if not line:
                    continue
                if line.startswith("data:"):
                    line = line[len("data:"):].strip()
                if line == "[DONE]":
                    break
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if payload.get("usage"):
                    usage = payload["usage"]
                choice = (payload.get("choices") or [{}])[0]
                delta = choice.get("delta") or {}
                if delta.get("content"):
                    chunks.append(delta["content"])
                message = choice.get("message") or {}
                if message.get("content"):
                    chunks.append(message["content"])
        content = "".join(chunks).strip()
        if not content:
            raw = " ".join(raw_lines).strip()
            raise RuntimeError(self._empty_content_error(url, body, raw, response.headers.get("content-type")))
        return content, usage

    def _non_stream_content(self, url: str, body: dict) -> tuple[str, dict]:
        response = httpx.post(
            url,
            headers={
                **self._request_headers(),
            },
            json=body,
            timeout=self.timeout,
        )
        if response.status_code >= 400:
            raise RuntimeError(self._response_error(response, response.text))
        payload = self._json_response(response)
        usage = payload.get("usage") or {}
        choice = (payload.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        content = str(message.get("content") or "").strip()
        if not content:
            raise RuntimeError(self._empty_content_error(url, body, response.text[:200], response.headers.get("content-type")))
        return content, usage

    def _chat_urls(self) -> list[str]:
        base = self.base_url.rstrip("/")
        if base.endswith("/v1") or base.endswith("/v2"):
            return [f"{base}/chat/completions"]
        urls = [f"{base}/v1/chat/completions"]
        if self.allow_root_chat_fallback:
            urls.append(f"{base}/chat/completions")
        return urls

    def _request_headers(self) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            **self.extra_headers,
        }
        # 使用标准的API客户端User-Agent，避免被识别为浏览器
        user_agent = os.getenv("LLM_USER_AGENT", "fastapi-llm-client/1.0")
        if user_agent:
            headers["User-Agent"] = user_agent
        return headers

    def _prepare_image_bytes(self, raw: bytes) -> tuple[bytes, str]:
        if raw.startswith(b"\xff\xd8\xff"):
            return raw, "image/jpeg"
        if raw.startswith(b"\x89PNG\r\n\x1a\n"):
            return raw, "image/png"
        if raw.startswith((b"GIF87a", b"GIF89a")):
            return raw, "image/gif"
        if len(raw) >= 12 and raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
            return raw, "image/webp"
        return raw, "image/png"

    def _throttle_request_start(self) -> None:
        if self.min_request_interval_seconds <= 0:
            return
        with self._lock:
            elapsed = time.monotonic() - self._last_request_at
            wait_for = self.min_request_interval_seconds - elapsed
            if wait_for > 0:
                time.sleep(wait_for)
            self._last_request_at = time.monotonic()

    def _retryable_error(self, exc: Exception) -> bool:
        text = str(exc).lower()
        return any(marker in text for marker in ("http 429", "http 502", "http 503", "http 504", "timeout", "temporarily"))
    def _json_response(self, response: httpx.Response) -> dict:
        content_type = response.headers.get("content-type", "")
        if "json" not in content_type.lower():
            snippet = response.text[:200].replace("\n", " ")
            raise RuntimeError(
                f"non-json response status={response.status_code} content_type={content_type or 'unknown'} body={snippet}"
            )
        try:
            payload = response.json()
        except json.JSONDecodeError as exc:
            snippet = response.text[:200].replace("\n", " ")
            raise RuntimeError(f"invalid json response status={response.status_code} body={snippet}") from exc
        if not isinstance(payload, dict):
            raise RuntimeError(f"json response is not an object: {type(payload).__name__}")
        return payload

    def _response_error(self, response: httpx.Response, text: str) -> str:
        snippet = (text or "").strip()[:300].replace("\n", " ")
        return (
            f"http {response.status_code} content_type={response.headers.get('content-type') or 'unknown'} "
            f"body={snippet or '(empty)'}"
        )

    def _empty_content_error(self, url: str, body: dict, raw: str, content_type: str | None = None) -> str:
        snippet = (raw or "").strip()[:200].replace("\n", " ")
        non_json = content_type and "json" not in content_type.lower()
        prefix = f"non-json response content_type={content_type} " if non_json else ""
        return (
            f"{prefix}empty assistant content url={url} stream={bool(body.get('stream'))} "
            f"response_format={'response_format' in body} raw={snippet or '(empty)'}"
        )

    def _format_attempt_error(self, url: str, body: dict, exc: Exception) -> str:
        model = str(body.get("model") or self.model)
        return (
            f"url={url} stream={bool(body.get('stream'))} "
            f"model={model} response_format={'response_format' in body}: {exc}"
        )

    def _merge_usage(self, phase: str, usage: dict) -> None:
        with self._lock:
            phase_usage = self._token_stats.setdefault(phase, {})
            total_usage = self._token_stats.setdefault("total", {})
            for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                value = int(usage.get(key) or 0)
                phase_usage[key] = int(phase_usage.get(key) or 0) + value
                total_usage[key] = int(total_usage.get(key) or 0) + value

    def _parse_json(self, content: str) -> dict:
        text = (content or "").strip()
        match = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, flags=re.DOTALL)
        if match:
            text = match.group(1)
        elif "{" in text and "}" in text:
            text = text[text.find("{"): text.rfind("}") + 1]
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            snippet = text[:200] if text else "(empty)"
            raise ValueError(
                f"LLM 返回非法 JSON，无法解析。原始内容前200字: {snippet}. 错误: {exc}"
            ) from exc
        if not isinstance(parsed, dict):
            raise ValueError(f"LLM 返回的 JSON 不是对象类型，实际类型: {type(parsed).__name__}")
        return parsed

    @staticmethod
    def _parse_fallback_models(value: object) -> list[str]:
        if not value:
            return []
        text = str(value).strip()
        if not text:
            return []
        if text.startswith("[") and text.endswith("]"):
            try:
                loaded = json.loads(text)
            except json.JSONDecodeError:
                loaded = []
            if isinstance(loaded, list):
                return [str(item).strip() for item in loaded if str(item).strip()]
            return []
        return [item.strip() for item in text.split(",") if item.strip()]

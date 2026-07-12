from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from typing import Any

import redis

from app.config import REDIS_URL
from app.queue.contracts import QueuePayload, QueuePayloadError


logger = logging.getLogger(__name__)


class RedisQueue:
    def __init__(self, queue_name: str) -> None:
        self.queue_name = queue_name
        self.dead_letter_queue_name = f"{queue_name}:dead_letter"
        self._redis = redis.Redis.from_url(REDIS_URL, decode_responses=True)

    def enqueue(self, payload: QueuePayload | Mapping[str, Any]) -> None:
        contract = payload if isinstance(payload, QueuePayload) else QueuePayload.from_mapping(payload)
        self._redis.rpush(
            self.queue_name, json.dumps(contract.model_dump(exclude_none=True), ensure_ascii=False)
        )

    def ping(self) -> None:
        self._redis.ping()

    def dequeue(self, timeout: int = 5) -> dict[str, Any] | None:
        result = self._redis.blpop(self.queue_name, timeout=timeout)
        if result is None:
            return None
        _, payload = result
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError:
            self._dead_letter(payload, "invalid_json")
            return None
        if not isinstance(parsed, dict):
            self._dead_letter(payload, "payload_not_object")
            return None
        try:
            return QueuePayload.from_mapping(parsed).model_dump(exclude_none=True)
        except QueuePayloadError as exc:
            self._dead_letter(parsed, str(exc))
            logger.warning("discarding invalid queue payload queue=%s error=%s", self.queue_name, exc)
            return None

    def size(self) -> int:
        return int(self._redis.llen(self.queue_name))

    def snapshot(self) -> list[str]:
        return list(self._redis.lrange(self.queue_name, 0, -1))

    def dead_letter_size(self) -> int:
        return int(self._redis.llen(self.dead_letter_queue_name))

    def _dead_letter(self, payload: Any, reason: str) -> None:
        record = json.dumps({"reason": reason, "payload": payload}, ensure_ascii=False)
        try:
            self._redis.rpush(self.dead_letter_queue_name, record)
        except Exception:
            logger.exception("failed to write dead-letter queue=%s", self.dead_letter_queue_name)

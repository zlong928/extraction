from __future__ import annotations

import json
from typing import Any

import redis

from app.config import REDIS_URL


class RedisQueue:
    def __init__(self, queue_name: str) -> None:
        self.queue_name = queue_name
        self._redis = redis.Redis.from_url(REDIS_URL, decode_responses=True)

    def enqueue(self, payload: dict[str, Any]) -> None:
        self._redis.rpush(self.queue_name, json.dumps(payload, ensure_ascii=False))

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
            return None
        return parsed if isinstance(parsed, dict) else None

    def size(self) -> int:
        return int(self._redis.llen(self.queue_name))

    def snapshot(self) -> list[str]:
        return list(self._redis.lrange(self.queue_name, 0, -1))

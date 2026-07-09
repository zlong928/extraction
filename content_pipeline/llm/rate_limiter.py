from __future__ import annotations

import os
import threading
import time
from collections import deque


class LLMRateLimiter:
    """Thread-safe rate limiter for LLM API calls with circuit breaker.

    Configurable via environment variables:
      - LLM_RATE_LIMIT_RPM: max requests per minute (default: 30)
      - LLM_CIRCUIT_BREAKER_THRESHOLD: consecutive failures before tripping (default: 5)
      - LLM_CIRCUIT_BREAKER_TIMEOUT: seconds to wait before half-open retry (default: 30)
    """

    _instance: LLMRateLimiter | None = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        rpm = int(os.getenv("LLM_RATE_LIMIT_RPM", "30"))
        self.max_per_minute = max(1, rpm)
        self._window_seconds = 60.0
        self._events: deque[float] = deque()
        self._rate_lock = threading.Lock()

        self._circuit_breaker_threshold = int(os.getenv("LLM_CIRCUIT_BREAKER_THRESHOLD", "5"))
        self._circuit_breaker_timeout = float(os.getenv("LLM_CIRCUIT_BREAKER_TIMEOUT", "30"))
        self._consecutive_failures = 0
        self._circuit_open_until: float = 0.0

    @classmethod
    def get_instance(cls) -> LLMRateLimiter:
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def acquire(self) -> None:
        with self._rate_lock:
            now = time.monotonic()
            while self._events and now - self._events[0] >= self._window_seconds:
                self._events.popleft()
            if len(self._events) >= self.max_per_minute:
                sleep_for = max(0.0, self._window_seconds - (now - self._events[0]))
                if sleep_for > 0:
                    time.sleep(sleep_for)
                now = time.monotonic()
                while self._events and now - self._events[0] >= self._window_seconds:
                    self._events.popleft()
            self._events.append(time.monotonic())

    def check_circuit_breaker(self) -> None:
        now = time.monotonic()
        if self._circuit_open_until > now:
            remaining = self._circuit_open_until - now
            raise RuntimeError(
                f"LLM circuit breaker is open. "
                f"Waiting {remaining:.0f}s before retry. "
                f"Threshold: {self._circuit_breaker_threshold} consecutive failures."
            )

    def record_success(self) -> None:
        self._consecutive_failures = 0

    def record_failure(self) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures >= self._circuit_breaker_threshold:
            self._circuit_open_until = time.monotonic() + self._circuit_breaker_timeout
            self._consecutive_failures = 0

    def reset(self) -> None:
        self._consecutive_failures = 0
        self._circuit_open_until = 0.0
        self._events.clear()

"""In-process resource guards for MCP tool calls."""
from __future__ import annotations

import json
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class LimitLease:
    """Resources reserved for one accepted tool call."""

    identity: str
    targets: tuple[str, ...]
    mutating: bool


class ResourceLimiter:
    """Bound request size, rate, target concurrency, and repeated failures."""

    def __init__(
        self,
        *,
        requests_per_minute: int,
        max_argument_bytes: int,
        max_concurrent_per_target: int,
        circuit_failures: int,
        circuit_reset_seconds: float,
        mutation_cooldown_seconds: float,
        clock=time.monotonic,
    ) -> None:
        self.requests_per_minute = max(1, requests_per_minute)
        self.max_argument_bytes = max(1, max_argument_bytes)
        self.max_concurrent_per_target = max(1, max_concurrent_per_target)
        self.circuit_failures = max(1, circuit_failures)
        self.circuit_reset_seconds = max(0.0, circuit_reset_seconds)
        self.mutation_cooldown_seconds = max(0.0, mutation_cooldown_seconds)
        self._clock = clock
        self._lock = threading.Lock()
        self._requests: dict[str, deque[float]] = defaultdict(deque)
        self._active: dict[str, int] = defaultdict(int)
        self._failures: dict[str, int] = defaultdict(int)
        self._circuit_opened: dict[str, float] = {}
        self._last_mutation: dict[str, float] = {}

    def acquire(
        self,
        *,
        identity: str,
        arguments: Any,
        targets: Iterable[str],
        mutating: bool,
    ) -> tuple[LimitLease | None, str | None]:
        """Reserve capacity, returning a user-safe refusal when unavailable."""
        payload_size = len(
            json.dumps(arguments, default=str, separators=(",", ":")).encode("utf-8")
        )
        if payload_size > self.max_argument_bytes:
            return None, (
                f"refused: arguments exceed {self.max_argument_bytes} bytes "
                f"({payload_size} bytes received)"
            )

        now = self._clock()
        normalized_targets = tuple(sorted(set(targets))) or ("hub",)
        with self._lock:
            recent = self._requests[identity]
            while recent and recent[0] <= now - 60:
                recent.popleft()
            if len(recent) >= self.requests_per_minute:
                return None, "refused: token request quota exceeded"

            for target in normalized_targets:
                opened = self._circuit_opened.get(target)
                if opened is not None:
                    retry_after = self.circuit_reset_seconds - (now - opened)
                    if retry_after > 0:
                        return None, (
                            f"refused: circuit breaker is open for {target}; "
                            f"retry in {retry_after:.1f}s"
                        )
                    self._circuit_opened.pop(target, None)
                    self._failures[target] = 0
                if self._active[target] >= self.max_concurrent_per_target:
                    return None, f"refused: concurrency limit reached for {target}"
                if mutating:
                    elapsed = now - self._last_mutation.get(target, float("-inf"))
                    if elapsed < self.mutation_cooldown_seconds:
                        retry_after = self.mutation_cooldown_seconds - elapsed
                        return None, (
                            f"refused: mutation cooldown active for {target}; "
                            f"retry in {retry_after:.1f}s"
                        )

            recent.append(now)
            for target in normalized_targets:
                self._active[target] += 1
                if mutating:
                    self._last_mutation[target] = now

        return LimitLease(identity, normalized_targets, mutating), None

    def release(self, lease: LimitLease, *, succeeded: bool) -> None:
        """Release capacity and update each target circuit breaker."""
        now = self._clock()
        with self._lock:
            for target in lease.targets:
                self._active[target] = max(0, self._active[target] - 1)
                if succeeded:
                    self._failures[target] = 0
                    self._circuit_opened.pop(target, None)
                    continue
                self._failures[target] += 1
                if self._failures[target] >= self.circuit_failures:
                    self._circuit_opened[target] = now

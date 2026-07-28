from __future__ import annotations

import unittest

from core.limits import ResourceLimiter


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class ResourceLimiterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FakeClock()
        self.limiter = ResourceLimiter(
            requests_per_minute=2,
            max_argument_bytes=100,
            max_concurrent_per_target=1,
            circuit_failures=2,
            circuit_reset_seconds=10,
            mutation_cooldown_seconds=5,
            clock=self.clock,
        )

    def test_rejects_oversized_arguments(self) -> None:
        lease, error = self.limiter.acquire(
            identity="token", arguments={"value": "x" * 101}, targets=["host"], mutating=False
        )
        self.assertIsNone(lease)
        self.assertIn("arguments exceed", error)

    def test_enforces_quota_and_recovers_after_window(self) -> None:
        for _ in range(2):
            lease, error = self.limiter.acquire(
                identity="token", arguments={}, targets=["host"], mutating=False
            )
            self.assertIsNone(error)
            self.limiter.release(lease, succeeded=True)
        lease, error = self.limiter.acquire(
            identity="token", arguments={}, targets=["host"], mutating=False
        )
        self.assertIsNone(lease)
        self.assertIn("quota", error)
        self.clock.advance(61)
        lease, error = self.limiter.acquire(
            identity="token", arguments={}, targets=["host"], mutating=False
        )
        self.assertIsNone(error)
        self.limiter.release(lease, succeeded=True)

    def test_limits_concurrency_per_target(self) -> None:
        lease, _ = self.limiter.acquire(
            identity="one", arguments={}, targets=["host"], mutating=False
        )
        refused, error = self.limiter.acquire(
            identity="two", arguments={}, targets=["host"], mutating=False
        )
        self.assertIsNone(refused)
        self.assertIn("concurrency", error)
        self.limiter.release(lease, succeeded=True)

    def test_mutation_cooldown_is_per_target(self) -> None:
        lease, _ = self.limiter.acquire(
            identity="one", arguments={}, targets=["host"], mutating=True
        )
        self.limiter.release(lease, succeeded=True)
        refused, error = self.limiter.acquire(
            identity="two", arguments={}, targets=["host"], mutating=True
        )
        self.assertIsNone(refused)
        self.assertIn("cooldown", error)
        self.clock.advance(5)
        lease, error = self.limiter.acquire(
            identity="two", arguments={}, targets=["host"], mutating=True
        )
        self.assertIsNone(error)
        self.limiter.release(lease, succeeded=True)

    def test_circuit_breaker_opens_and_resets(self) -> None:
        for identity in ("one", "two"):
            lease, _ = self.limiter.acquire(
                identity=identity, arguments={}, targets=["host"], mutating=False
            )
            self.limiter.release(lease, succeeded=False)
        refused, error = self.limiter.acquire(
            identity="three", arguments={}, targets=["host"], mutating=False
        )
        self.assertIsNone(refused)
        self.assertIn("circuit breaker", error)
        self.clock.advance(10)
        lease, error = self.limiter.acquire(
            identity="three", arguments={}, targets=["host"], mutating=False
        )
        self.assertIsNone(error)
        self.limiter.release(lease, succeeded=True)


if __name__ == "__main__":
    unittest.main()

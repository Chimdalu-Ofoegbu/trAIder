"""Wave-0 RED stub — orchestrator.loop.nonce_manager (D-11 nonce discipline).

Implemented in plan 04-05 (supervisor + nonce manager).

D-11 requirement: one shared orchestrator EOA is used by all three model tasks.
The NonceManager must ensure sequential nonce assignment under concurrent submissions:
  - Lock is held only for: assign nonce → sign → broadcast
  - Lock is RELEASED before awaiting confirmation (40-60s confirms run concurrently)
  - concurrent submissions from 3 model tasks must NOT produce nonce collisions

Phase-3 ARCH-X in-flight gate (has_unresolved_pending_order) is per-vault but does NOT
prevent nonce collisions when multiple vaults submit simultaneously. NonceManager is the
dedicated fix: all three tasks share one NonceManager instance, each acquires the async
lock before signing, releases it before awaiting the receipt.

Stuck-tx watchdog: if a nonce's pending timestamp exceeds LATENCY_WATCHDOG_THRESHOLD
(120s), re-sign at the same nonce with gasPrice *= 1.25.
"""

from __future__ import annotations

import pytest

# Guard: skip if nonce_manager not yet implemented.
pytest.importorskip(
    "orchestrator.loop.nonce_manager",
    reason="Wave 0 stub — nonce_manager implemented in 04-05",
)


class TestNonceManagerConcurrency:
    """D-11 nonce sequential assignment under concurrency."""

    async def test_concurrent_submissions(self) -> None:
        """Three concurrent asyncio tasks submit through the same NonceManager.

        Assert:
          - All 3 tasks receive distinct sequential nonces (no collisions)
          - The lock is released before each task awaits its receipt
          - Tasks 2 and 3 are NOT blocked by Task 1's receipt wait

        Acceptance criteria (from 04-VALIDATION.md):
          pytest tests/unit/test_nonce_manager.py::test_concurrent_submissions
          → test collects and runs (not 0 tests)

        Implemented in: 04-05.
        """
        pytest.skip("Wave 0 stub — 04-05 implements")

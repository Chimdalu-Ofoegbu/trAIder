"""Wave-0 RED stub — orchestrator.loop.arb_bot (D-08/D-09/D-10 house-arb bot).

Implemented in plan 04-06 (arb bot + pool seeding).

D-08/D-09: house-arb bot (key #4) polls all 3 pools at 10-15s cadence and fires
arbCloseGap when |gap| > hysteresis threshold (2.5%, per D-05 fallback from Probe 1).

D-10: single process, 3 pools, sequential per-pool firing. Per-pool fault isolation
(exception in one pool's round → log + continue to next pool; never crash the process).
Separate nonce/EOA from orchestrator trade key (key #4 is arb-only).

Key probe result (Probe 1): changeFeeConfiguration is ABSENT on Algebra Integral v1.
Max dynamic fee = 1.49%. Bot hysteresis = 2.5% (above max fee + slippage buffer).
The 2.5% threshold is the constant in ArbBot; the 1% contract-level floor is in
ArbitragePrimitive.GAP_THRESHOLD_BPS = 100.
"""

from __future__ import annotations

import pytest

# Guard: skip if arb_bot not yet implemented.
pytest.importorskip(
    "orchestrator.loop.arb_bot",
    reason="Wave 0 stub — arb_bot implemented in 04-06",
)


class TestArbBotGapFiring:
    """D-08/D-09 arb bot gap-triggered firing."""

    async def test_arb_bot_fires_on_gap_above_hysteresis(self) -> None:
        """ArbBot calls arbCloseGap when pool gap exceeds 2.5% hysteresis.

        Arrange: mock vault.nav() = 1e18, mock pool price = 1.026e18 (2.6% above NAV).
        Assert: ArbBot fires arbCloseGap exactly once on the next poll.

        D-09: fire at 2.5% (not 1.5%) per D-05 fallback from Probe 1.

        Acceptance criteria (from 04-VALIDATION.md):
          pytest tests/unit/test_arb_bot.py
          → test collects and runs (not 0 tests)

        Implemented in: 04-06.
        """
        pytest.skip("Wave 0 stub — 04-06 implements")

    async def test_arb_bot_per_pool_fault_isolation(self) -> None:
        """An exception in one pool's arbCloseGap does NOT stop the other pool loops.

        Arrange: 3 pools; pool #2 raises an exception during arbCloseGap.
        Assert: pool #1 and pool #3 fire correctly; pool #2 logs WARNING and continues.

        D-10: per-pool fault isolation — exception in one pool does NOT crash the process
        or skip the remaining pools in the current poll cycle.

        Implemented in: 04-06.
        """
        pytest.skip("Wave 0 stub — 04-06 implements")

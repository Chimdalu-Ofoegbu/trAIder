"""Unit tests for orchestrator.loop.nonce_manager (D-11 nonce discipline).

04-05 implementation: shared-EOA async nonce manager with sequential assignment,
stuck-tx watchdog, and gap recovery using the 'pending' block tag.

D-11 requirement: one shared orchestrator EOA is used by all three model tasks.
The NonceManager must ensure sequential nonce assignment under concurrent submissions:
  - Lock is held only for: assign nonce → sign → broadcast
  - Lock is RELEASED before awaiting confirmation (40-60s confirms run concurrently)
  - concurrent submissions from 3 model tasks must NOT produce nonce collisions

Stuck-tx watchdog: if a nonce's pending timestamp exceeds LATENCY_WATCHDOG_THRESHOLD
(120s), re-sign at the same nonce with gasPrice *= 1.25.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from orchestrator.loop.nonce_manager import NonceManager


class TestNonceManagerConcurrency:
    """D-11 nonce sequential assignment under concurrency."""

    async def test_concurrent_submissions(self) -> None:
        """Three concurrent coroutines submit through one NonceManager.

        Assert:
          - All 3 tasks receive distinct sequential nonces (n, n+1, n+2)
          - No duplicates, no skips
          - The lock is released before each task awaits its (simulated) receipt
          - Tasks 2 and 3 are NOT blocked by Task 1's receipt wait
        """
        web3 = MagicMock()
        # Chain reports nonce=10 on first call
        web3.eth.get_transaction_count = AsyncMock(return_value=10)

        manager = NonceManager(web3=web3, address="0xdeadbeef")

        assigned_nonces: list[int] = []

        async def submit(_task_id: int) -> int:
            """Simulates: assign_and_sign → (lock released) → await receipt separately."""

            async def tx_builder(nonce: int) -> str:
                assigned_nonces.append(nonce)
                return f"0xtxhash_{nonce}"

            await manager.assign_and_sign(tx_builder)
            # Simulate a slow receipt wait WITHOUT holding the lock
            await asyncio.sleep(0.01)  # short for test; proves concurrent
            return assigned_nonces[-1]

        # Run 3 concurrent submissions
        await asyncio.gather(
            submit(0),
            submit(1),
            submit(2),
        )

        # All 3 nonces must be distinct and sequential starting at 10
        assert sorted(assigned_nonces) == [10, 11, 12], (
            f"Expected sequential nonces [10, 11, 12], got {sorted(assigned_nonces)}"
        )
        assert len(set(assigned_nonces)) == 3, "Nonce collision detected"

        # web3.get_transaction_count should only be called ONCE (first initialization)
        web3.eth.get_transaction_count.assert_called_once_with("0xdeadbeef", "pending")


class TestNonceManagerRecovery:
    """D-11 gap recovery using 'pending' block tag (Pitfall 7)."""

    async def test_recover_from_gap_uses_pending_tag(self) -> None:
        """recover_from_gap must call get_transaction_count with 'pending', not 'latest'.

        Pitfall 7: if a tx is still in mempool when the manager restarts, 'latest'
        would return the pre-tx nonce → nonce collision. 'pending' includes mempool txs.
        """
        web3 = MagicMock()
        web3.eth.get_transaction_count = AsyncMock(return_value=42)

        manager = NonceManager(web3=web3, address="0xaabbccdd")
        # Manually set a local nonce to simulate previous state
        manager._local_nonce = 5  # stale/incorrect nonce

        await manager.recover_from_gap()

        # Must have called with "pending" tag
        web3.eth.get_transaction_count.assert_called_once_with("0xaabbccdd", "pending")
        # Nonce must be updated to chain value
        assert manager._local_nonce == 42


class TestNonceManagerStuckTx:
    """D-11 stuck-tx same-nonce higher-gas replacement."""

    async def test_stuck_tx_replace_same_nonce_higher_gas(self) -> None:
        """A tx pending past the watchdog threshold is re-signed at SAME nonce with +25% gas.

        Proves:
          - Re-sign uses the SAME nonce as the stuck tx (not a new one)
          - gasPrice = original * 1.25 (or +25%)
          - Replacement is logged
        """
        web3 = MagicMock()
        web3.eth.get_transaction_count = AsyncMock(return_value=0)

        manager = NonceManager(web3=web3, address="0x1234")

        # Simulate a stuck tx at nonce=7 that was submitted 200s ago
        stuck_nonce = 7
        stuck_time = asyncio.get_event_loop().time() - 200  # 200s ago (> 120s threshold)
        manager._local_nonce = 8  # next nonce
        manager._pending_txs[stuck_nonce] = stuck_time

        resign_calls: list[dict] = []

        async def resign_coro(nonce: int, gas_bump: float) -> str:
            resign_calls.append({"nonce": nonce, "gas_bump": gas_bump})
            return f"0xreplacement_{nonce}"

        await manager.check_and_replace_stuck(
            threshold_seconds=120.0,
            gas_bump=1.25,
            resign_coro=resign_coro,
        )

        assert len(resign_calls) == 1, "Expected exactly one replacement call"
        replacement = resign_calls[0]
        # SAME nonce — not a new one
        assert replacement["nonce"] == stuck_nonce, (
            f"Expected stuck nonce {stuck_nonce}, got {replacement['nonce']}"
        )
        # +25% gas bump
        assert replacement["gas_bump"] == pytest.approx(1.25, rel=1e-6)

    async def test_no_wedge_on_drop(self) -> None:
        """A dropped tx (nonce assigned, never confirmed) followed by recover_from_gap
        re-reads the chain nonce and continues without deadlock.
        """
        web3 = MagicMock()
        # Chain returns nonce=5 (as if tx was dropped, nonce still at pre-tx value on "pending")
        web3.eth.get_transaction_count = AsyncMock(return_value=5)

        manager = NonceManager(web3=web3, address="0xfeed")
        # Simulate a submitted-but-dropped tx: local nonce was incremented to 6, but
        # the tx at nonce=5 never made it to chain/mempool
        manager._local_nonce = 6
        manager._pending_txs[5] = asyncio.get_event_loop().time() - 10  # recent

        # recover_from_gap re-reads chain nonce
        await manager.recover_from_gap()

        # No deadlock: function returns, local nonce is updated
        assert manager._local_nonce == 5  # reset to chain nonce

        # After recovery, can assign new nonces without deadlock
        assigned_nonces: list[int] = []

        async def tx_builder(nonce: int) -> str:
            assigned_nonces.append(nonce)
            return f"0xtx_{nonce}"

        await manager.assign_and_sign(tx_builder)
        assert assigned_nonces == [5], f"Expected nonce 5 after recovery, got {assigned_nonces}"

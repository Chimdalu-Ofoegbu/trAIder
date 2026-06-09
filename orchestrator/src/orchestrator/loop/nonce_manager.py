"""
NonceManager — async nonce discipline for shared-EOA multi-model trading (D-11).

Lock held only for: assign nonce → sign → broadcast. Released BEFORE await receipt.
Confirmations (40-60s) run CONCURRENTLY across all three model tasks.

Stuck-tx watchdog: detect pending too long → replace at same nonce with higher gas.
recover_from_gap: re-read chain nonce using 'pending' block tag (Pitfall 7).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger(__name__)

LATENCY_WATCHDOG_THRESHOLD: float = 120.0  # seconds (ARCH-X default)


class NonceManager:
    """Shared-EOA nonce manager for concurrent multi-model trade submissions (D-11).

    Design:
      - asyncio.Lock protects the assign-nonce → sign → broadcast window only.
      - Lock is released BEFORE the caller awaits the receipt — so 40-60s confirmation
        waits run concurrently across all three model tasks.
      - `_local_nonce` is incremented locally (no chain re-read per tx) to avoid
        round-trip latency; `recover_from_gap` re-syncs on restart/drop.
      - `_pending_txs` maps nonce → submit_time for the stuck-tx watchdog.

    Usage::

        manager = NonceManager(web3, operator_address)

        # In each model task — lock held only for sign+broadcast:
        tx_hash = await manager.assign_and_sign(lambda nonce: my_contract.transact(nonce=nonce))
        # Lock released here — concurrent with other tasks
        receipt = await web3.eth.wait_for_transaction_receipt(tx_hash)
        manager.mark_confirmed(the_nonce)

    Pitfall 7: always use 'pending' block tag in recover_from_gap so mempool txs are
    included in the nonce count — prevents nonce collision on restart after a
    submit-but-not-yet-mined tx.
    """

    def __init__(self, web3: Any, address: str) -> None:
        self.web3 = web3
        self.address = address
        self._lock = asyncio.Lock()
        self._local_nonce: int | None = None
        self._pending_txs: dict[int, float] = {}  # nonce → submit_time (loop time)

    async def assign_and_sign(
        self,
        tx_builder_coro: Callable[[int], Awaitable[str]],
    ) -> str:
        """Assign the next nonce, build+sign+broadcast the tx, return tx_hash.

        Lock is held for the assign→sign→broadcast window and released BEFORE
        this coroutine returns.  The caller MUST await the receipt OUTSIDE this
        call (i.e. after assign_and_sign returns) so confirmation waits run
        concurrently with other tasks' assign_and_sign calls.

        Args:
            tx_builder_coro: An async callable that accepts (nonce: int) and
                returns a tx_hash string.  It must include both the sign and
                broadcast steps (using web3 SignAndSendRaw middleware or similar).

        Returns:
            tx_hash: Hex string of the broadcast transaction hash.
        """
        async with self._lock:
            if self._local_nonce is None:
                # First call: seed local nonce from chain (using 'pending' to capture
                # any in-flight txs from a previous run — Pitfall 7).
                self._local_nonce = await self.web3.eth.get_transaction_count(
                    self.address, "pending"
                )
                logger.debug(
                    "NonceManager: seeded local_nonce=%d for address=%s",
                    self._local_nonce,
                    self.address[:10],
                )

            nonce = self._local_nonce
            tx_hash = await tx_builder_coro(nonce)
            self._local_nonce += 1
            # Record submit time for stuck-tx watchdog
            loop = asyncio.get_event_loop()
            self._pending_txs[nonce] = loop.time()
            logger.debug(
                "NonceManager: assigned nonce=%d tx_hash=%s",
                nonce,
                str(tx_hash)[:12],
            )
        # Lock released here — caller awaits receipt concurrently
        return tx_hash

    async def recover_from_gap(self) -> None:
        """Re-sync local nonce from chain after a dropped/missed tx (Pitfall 7).

        MUST use 'pending' block tag (not 'latest') to include any transactions
        that are still in the mempool — avoiding nonce collision on restart.

        Call this:
          - On supervisor restart before resuming the model loop (D-12).
          - When a nonce gap is detected (e.g. tx dropped from mempool).
        """
        async with self._lock:
            self._local_nonce = await self.web3.eth.get_transaction_count(
                self.address,
                "pending",  # MUST be 'pending' — Pitfall 7
            )
            logger.info(
                "NonceManager: recovered local_nonce=%d for address=%s",
                self._local_nonce,
                self.address[:10],
            )

    async def check_and_replace_stuck(
        self,
        threshold_seconds: float = LATENCY_WATCHDOG_THRESHOLD,
        gas_bump: float = 1.25,
        resign_coro: Callable[[int, float], Awaitable[str]] | None = None,
    ) -> None:
        """Replace any pending tx that has been waiting longer than threshold_seconds.

        Re-signs at the SAME nonce with gasPrice *= gas_bump (e.g. 1.25 = +25%).
        This is the stuck-tx watchdog (D-11 / ARCH-X pattern).

        Args:
            threshold_seconds: Age in seconds beyond which a pending tx is considered stuck.
            gas_bump: Gas price multiplier for the replacement tx (default 1.25 = +25%).
            resign_coro: Async callable (nonce, gas_bump) → new_tx_hash.
                Must re-sign the same payload at the specified nonce with higher gas.
                If None, only logs the stuck tx (no replacement).
        """
        if not resign_coro:
            return

        loop = asyncio.get_event_loop()
        now = loop.time()

        stuck_nonces = [
            nonce
            for nonce, submit_time in list(self._pending_txs.items())
            if (now - submit_time) > threshold_seconds
        ]

        for nonce in stuck_nonces:
            logger.warning(
                "NonceManager: stuck-tx detected, replacing at nonce=%d (age=%.1fs, gas_bump=%.2f)",
                nonce,
                now - self._pending_txs[nonce],
                gas_bump,
            )
            new_tx_hash = await resign_coro(nonce, gas_bump)
            # Reset the submit timestamp for the replacement tx
            self._pending_txs[nonce] = now
            logger.info(
                "NonceManager: replacement submitted nonce=%d new_tx=%s",
                nonce,
                str(new_tx_hash)[:12],
            )

    def mark_confirmed(self, nonce: int) -> None:
        """Remove a confirmed nonce from the pending tracking dict.

        Call this after a receipt is received to prevent the stuck-tx watchdog
        from falsely replacing a confirmed tx.

        Args:
            nonce: The nonce of the confirmed transaction.
        """
        self._pending_txs.pop(nonce, None)
        logger.debug("NonceManager: confirmed nonce=%d", nonce)

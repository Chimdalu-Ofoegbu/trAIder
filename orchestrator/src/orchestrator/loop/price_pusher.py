"""Seeded replayable price walk + MockChainlinkAggregator push (D-01/D-02/D-04).

Key design decisions:
- PriceWalk uses ``random.Random(seed)`` (CPython Mersenne Twister, stable across
  platforms per assumption A1 in 02-RESEARCH.md).  Same seed → same price sequence,
  enabling full session replay.
- Log-normal step (``price * exp(shock)``) + MIN_PRICE_USD floor (Pitfall 5) ensures
  MockChainlinkAggregator never receives answer <= 0 (which MockPerps would revert on).
- ``push_price`` calls ``MockChainlinkAggregator.setPrice(int256)`` on the SHARED
  aggregator — one call per asset per cycle drives BOTH vault NAV and MockPerps PnL
  consistently (D-02/D-03).  ``setMarkOverride`` is NOT used (D-02).
- All timing is ``asyncio.sleep`` — never ``time.sleep`` (event-loop rule).
"""

from __future__ import annotations

import asyncio
import logging
import math
import random
from typing import Any

logger = logging.getLogger(__name__)

# Pitfall 5: MockPerps requires answer > 0; floor prevents revert.
MIN_PRICE_USD: float = 0.01


class PriceWalk:
    """Seeded random-walk price path for ETH / BTC / SOL (D-01).

    Two instances constructed with the same ``seed`` and ``starting_prices``
    produce identical ``step()`` sequences — this is the core replay primitive.

    The walk is log-normal: each cycle the price moves by ``exp(shock)`` where
    ``shock ~ N(drift, volatility)``.  Log-normal prices are always positive in
    theory; the ``MIN_PRICE_USD`` floor provides an explicit safety net against
    floating-point underflow or extreme shock sequences (Pitfall 5).

    ``funding_rate`` and ``change_24h`` are *derived* deterministically from the
    accumulated price history — they do not consume RNG and are therefore
    stable for any partial-replay prefix of the path.
    """

    def __init__(
        self,
        seed: int,
        starting_prices: dict,
        drift: float,
        volatility: float,
    ) -> None:
        # CPython Mersenne Twister — replay-stable (A1 in 02-RESEARCH.md)
        self._rng = random.Random(seed)
        self.prices: dict[str, float] = dict(starting_prices)
        self.drift = drift
        self.volatility = volatility
        # History includes the starting price as index 0.
        self._history: dict[str, list[float]] = {k: [v] for k, v in starting_prices.items()}

    def step(self) -> dict[str, float]:
        """Advance all three assets one cycle and return the new mark prices.

        Uses a log-normal shock: ``new_price = old * exp(gauss(drift, volatility))``.
        Prices are clamped to ``MIN_PRICE_USD`` to prevent non-positive values
        reaching MockChainlinkAggregator (Pitfall 5).
        """
        for asset in self.prices:
            shock = self._rng.gauss(self.drift, self.volatility)
            # log-normal step + Pitfall-5 floor
            self.prices[asset] = max(
                self.prices[asset] * math.exp(shock),
                MIN_PRICE_USD,
            )
            self._history[asset].append(self.prices[asset])
        return dict(self.prices)

    def funding_rate(self, asset: str) -> float:
        """Synthetic annualised funding proxy (D-04).

        Derived deterministically from the last two prices in the history —
        no new RNG is consumed.  Returns 0.0 before the first ``step()`` call.

        The formula is a simple carry proxy: the sign and magnitude of the
        most-recent cycle return, scaled by 0.1 to keep values in a plausible
        annualised funding range (±10% per step * 0.1 ≈ ±1%).
        """
        hist = self._history[asset]
        if len(hist) < 2:
            return 0.0
        delta = (hist[-1] - hist[-2]) / hist[-2]
        return round(delta * 0.1, 6)

    def change_24h(self, asset: str) -> float:
        """24-hour percentage change (D-04).

        Looks back 1440 cycles (24h at 60s cadence) from the current position
        in the history.  If fewer than 1440 cycles have elapsed, uses the
        starting price as the reference.

        Returns a rounded float (4 decimal places) for terse pipe-table display.
        """
        hist = self._history[asset]
        lookback = hist[-1440] if len(hist) > 1440 else hist[0]
        return round((hist[-1] - lookback) / lookback, 4)


# ---------------------------------------------------------------------------
# On-chain helpers (D-02 — shared aggregator push)
# ---------------------------------------------------------------------------


def to_8dec(price_usd: float) -> int:
    """Convert a float USD price to the 8-decimal integer form used on-chain.

    Example: $3000.00 → 300_000_000_000.

    Uses ``round()`` before truncation so that floating-point representation
    errors (e.g. 3000 * 1e8 = 299999999999.9997) do not produce off-by-one values.
    """
    return int(round(price_usd * 1e8))


async def push_price(
    web3: Any,
    aggregator_contract: Any,
    new_price_8dec: int,
    from_address: str,
) -> None:
    """Push a new price to a MockChainlinkAggregator via setPrice (D-02).

    One call per asset per cycle.  ``setPrice(int256)`` updates ``answer``,
    sets ``updatedAt = block.timestamp``, and increments ``roundId`` —
    keeping the freshness guard in MockPerps happy (Pitfall 7).

    This function drives the SHARED aggregator that BOTH the vault NAV path
    and MockPerps PnL read.  ``setMarkOverride`` is deliberately NOT called
    (D-02/D-03 — secondary/fallback only).

    Parameters
    ----------
    web3:
        ``AsyncWeb3`` instance connected to the local anvil node.
    aggregator_contract:
        ``AsyncWeb3`` contract instance for ``MockChainlinkAggregator``.
    new_price_8dec:
        Price as an 8-decimal integer (e.g. 300_000_000_000 for $3000).
    from_address:
        Sender address (deployer EOA).
    """
    tx_hash = await aggregator_contract.functions.setPrice(new_price_8dec).transact(
        {"from": from_address}
    )
    # GAP-1b fix: use wait_for_transaction_receipt (not get_transaction_receipt) so the price
    # is confirmed on-chain before the driver reads it via _markPrice. get_transaction_receipt
    # races with anvil mining and returns None if the tx isn't yet in a block, which can leave
    # the aggregator with a stale/zero price that triggers MockPerps._markPrice staleness revert.
    await web3.eth.wait_for_transaction_receipt(tx_hash, timeout=30)
    logger.debug(
        "push_price: %s → %d (tx=%s)", aggregator_contract.address, new_price_8dec, tx_hash.hex()
    )


def build_consistent_snapshot(walk: PriceWalk) -> dict[str, dict[str, float]]:
    """Produce a consistent market snapshot after walk.step() has been called.

    Returns a dict keyed by asset with keys ``mark``, ``funding``, ``change_24h``
    all derived from the SAME walk step.  This is the value published to the
    snapshot_queue for driver.build_market_table consumption (CR-03 fix).

    Parameters
    ----------
    walk:
        A ``PriceWalk`` whose ``step()`` has already been called this cycle.
        ``walk.prices`` holds the stepped mark prices; ``funding_rate`` and
        ``change_24h`` read from the same ``_history`` that ``step()`` just updated.

    Returns
    -------
    dict
        Example: ``{"ETH": {"mark": 3001.2, "funding": 0.0001, "change_24h": 0.012}, ...}``
    """
    snapshot: dict[str, dict[str, float]] = {}
    for asset in walk.prices:
        snapshot[asset] = {
            "mark": walk.prices[asset],
            "funding": walk.funding_rate(asset),
            "change_24h": walk.change_24h(asset),
        }
    return snapshot


async def run_price_pusher(
    web3: Any,
    aggregators: dict[str, Any],
    walk: PriceWalk,
    from_address: str,
    cadence_seconds: float,
    stop_event: asyncio.Event,
    snapshot_queue: asyncio.Queue | None = None,
) -> None:
    """Price-pusher coroutine — runs as a separate asyncio.Task alongside loop_driver.

    Each cadence cycle:
    1. Advance the seeded walk by one step (deterministic, log-normal + floor).
    2. Push the new price for each asset to its MockChainlinkAggregator.
    3. (CR-03) If snapshot_queue is provided, publish a consistent snapshot dict
       ``{asset: {mark, funding, change_24h}}`` computed from the SAME step.
       The driver reads this snapshot for build_market_table so mark/funding/24h%
       all come from one consistent step, not an arbitrary concurrent step.

    Stops cleanly when ``stop_event`` is set (D-12 session-end signal).

    Parameters
    ----------
    web3:
        ``AsyncWeb3`` instance.
    aggregators:
        Mapping of ``{"ETH": contract, "BTC": contract, "SOL": contract}``.
    walk:
        ``PriceWalk`` instance (must be the same instance used by ``loop_driver``
        to derive ``market_table`` values).
    from_address:
        Deployer / operator EOA address.
    cadence_seconds:
        Seconds between price pushes (matches ``SessionConfig.cadence_seconds``).
    stop_event:
        ``asyncio.Event`` that signals all coroutines to shut down (D-12).
    snapshot_queue:
        Optional asyncio.Queue for publishing consistent per-step market snapshots
        to the driver (CR-03 fix).  Pass maxsize=1 so the driver always gets the
        latest snapshot (older unread snapshots are discarded).
    """
    logger.info(
        "run_price_pusher: starting (cadence=%.1fs, assets=%s)",
        cadence_seconds,
        list(aggregators.keys()),
    )
    while not stop_event.is_set():
        # Step the walk and publish on-chain atomically — funding/24h derived from
        # this exact step so build_consistent_snapshot is internally consistent (CR-03).
        prices = walk.step()
        for asset, contract in aggregators.items():
            price_8dec = to_8dec(prices[asset])
            await push_price(web3, contract, price_8dec, from_address)
            logger.debug("run_price_pusher: %s → $%.4f (%d 8dec)", asset, prices[asset], price_8dec)

        # CR-03: publish consistent snapshot so driver's market_table uses the same step
        if snapshot_queue is not None:
            snapshot = build_consistent_snapshot(walk)
            # Drain any stale snapshot so the driver always gets the latest step
            if not snapshot_queue.empty():
                try:
                    snapshot_queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            await snapshot_queue.put(snapshot)

        # NEVER time.sleep — must keep the event loop responsive (asyncio rule)
        await asyncio.sleep(cadence_seconds)
    logger.info("run_price_pusher: stop_event set — exiting")

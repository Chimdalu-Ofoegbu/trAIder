"""Market-state utilities: terse market_table + frozen-prompt Jinja2 render (D-04/D-05/D-06).

Key design decisions:
- format_market_table is model-agnostic (D-05): it takes NO model or provider parameter.
  All three models receive the identical table each cycle.
- Terse pipe-table format (D-06): saves tokens vs JSON; readable in verifier replays.
- render_prompt uses jinja2.Template.render() — NOT str.format() (Pitfall 6).
  system.md uses Jinja2 {{placeholder}} syntax; str.format would raise KeyError.
- SYSTEM_MD loaded once at import time from the frozen template path.  If the path
  does not resolve, an ImportError is raised immediately (fail-fast, catches path bugs).
- read_mark_prices reads the SHARED MockChainlinkAggregator (same source as vault NAV
  — D-02).  Uses .call() not .transact() (read-only).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import jinja2

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Frozen template — loaded once at import time
# ---------------------------------------------------------------------------

# market_state.py lives at: src/orchestrator/loop/market_state.py
# Traversal: loop/ -> orchestrator/ -> src/ -> orchestrator/ (project root)
# prompts/system.md is at: orchestrator/prompts/system.md
_PROMPTS_DIR = Path(__file__).parent.parent.parent.parent / "prompts"
_SYSTEM_MD_PATH = _PROMPTS_DIR / "system.md"

if not _SYSTEM_MD_PATH.exists():
    raise ImportError(
        f"Frozen system.md template not found at {_SYSTEM_MD_PATH}. "
        "Verify that market_state.py is at the expected depth inside the source tree."
    )

SYSTEM_MD: str = _SYSTEM_MD_PATH.read_text(encoding="utf-8")
_TEMPLATE: jinja2.Template = jinja2.Template(SYSTEM_MD)

logger.debug("market_state: loaded system.md from %s (%d chars)", _SYSTEM_MD_PATH, len(SYSTEM_MD))


# ---------------------------------------------------------------------------
# Market table formatting (D-04/D-05/D-06)
# ---------------------------------------------------------------------------


def format_market_table(
    prices: dict,
    funding: dict,
    change_24h: dict,
) -> str:
    """Render a terse pipe-table with mark, funding, and 24h % for ETH/BTC/SOL (D-06).

    The table is model-agnostic (D-05) — identical output regardless of which
    model will consume it.  Four lines total: header + separator + 3 asset rows.

    Parameters
    ----------
    prices:
        Current mark prices in USD (``{"ETH": float, "BTC": float, "SOL": float}``).
    funding:
        Synthetic annualised funding rate per asset (derived from PriceWalk.funding_rate).
    change_24h:
        24-hour percentage change per asset (derived from PriceWalk.change_24h).

    Returns
    -------
    str
        Four-line pipe-table.  Example row:
        ``| ETH | $3,000.00 | +0.0001 | +1.20% |``
    """
    lines = [
        "| Asset | Mark | Funding (ann.) | 24h % |",
        "|-------|------|---------------|-------|",
    ]
    for asset in ("ETH", "BTC", "SOL"):
        lines.append(
            f"| {asset} | ${prices[asset]:,.2f} | {funding[asset]:+.4f} | {change_24h[asset]:+.2%} |"
        )
    return "\n".join(lines)


def build_market_table(walk: Any, prices: dict) -> str:
    """Convenience wrapper: derive funding + 24h % from a PriceWalk and format.

    Plan 05 calls this once per cycle to produce the ``{{market_table}}`` value
    for the Jinja2 render.

    Parameters
    ----------
    walk:
        A ``PriceWalk`` instance that has already had ``step()`` called this cycle.
    prices:
        The mark prices returned by ``walk.step()`` this cycle.

    Returns
    -------
    str
        Same four-line pipe-table as ``format_market_table``.
    """
    funding = {a: walk.funding_rate(a) for a in ("ETH", "BTC", "SOL")}
    change = {a: walk.change_24h(a) for a in ("ETH", "BTC", "SOL")}
    return format_market_table(prices, funding, change)


def build_market_table_from_snapshot(snapshot: dict[str, dict[str, float]]) -> str:
    """Build a market_table from a consistent per-step snapshot (CR-03 fix).

    Use this in the driver when consuming snapshots published by run_price_pusher.
    All three values (mark, funding, change_24h) come from the SAME walk step,
    ensuring the prompt table is internally consistent.

    Parameters
    ----------
    snapshot:
        Dict keyed by asset with keys ``mark``, ``funding``, ``change_24h``.
        Produced by ``price_pusher.build_consistent_snapshot(walk)`` immediately
        after ``walk.step()`` — guarantees all three values are from one step.

    Returns
    -------
    str
        Same four-line pipe-table as ``format_market_table``.
    """
    prices = {asset: v["mark"] for asset, v in snapshot.items()}
    funding = {asset: v["funding"] for asset, v in snapshot.items()}
    change = {asset: v["change_24h"] for asset, v in snapshot.items()}
    return format_market_table(prices, funding, change)


# ---------------------------------------------------------------------------
# Frozen-prompt render (Pitfall 6 — Jinja2, not str.format)
# ---------------------------------------------------------------------------


def render_prompt(
    *,
    nav_table: str,
    time_remaining: str,
    positions_table: str,
    available_usdc: float,
    recent_decisions: str,
    market_table: str,
) -> str:
    """Fill the six frozen system.md Jinja2 placeholders and return the full prompt.

    Placeholder mapping (all must be present or Jinja2 raises ``UndefinedError``):
    - ``{{nav_table}}`` — vault NAV table string
    - ``{{time_remaining}}`` — truthful countdown from ``format_time_remaining()`` (D-11)
    - ``{{positions_table}}`` — open positions string
    - ``{{available_usdc}}`` — formatted USDC balance (e.g. ``"10,000.00"``)
    - ``{{recent_decisions}}`` — last-5-cycles decision summary
    - ``{{market_table}}`` — terse pipe-table from ``format_market_table()``

    IMPORTANT: uses ``jinja2.Template.render()`` NOT ``str.format()`` (Pitfall 6).
    The system.md template uses ``{{placeholder}}`` Jinja2 syntax; ``str.format()``
    would raise ``KeyError`` on the first encounter.

    Parameters
    ----------
    nav_table:
        NAV table string.
    time_remaining:
        Truthful countdown string (from ``session.format_time_remaining()``).
    positions_table:
        Open positions pipe-table or ``"No open positions."``.
    available_usdc:
        Available USDC balance as a float; formatted internally as ``{:,.2f}``.
    recent_decisions:
        Summary of last 5 decisions (plain text or mini table).
    market_table:
        Terse market data table (from ``format_market_table()``).

    Returns
    -------
    str
        Fully rendered prompt ready to send to the model.
    """
    return _TEMPLATE.render(
        nav_table=nav_table,
        time_remaining=time_remaining,
        positions_table=positions_table,
        available_usdc=f"{available_usdc:,.2f}",
        recent_decisions=recent_decisions,
        market_table=market_table,
    )


# ---------------------------------------------------------------------------
# Off-chain price read helper (D-02 — reads shared aggregator, same as NAV)
# ---------------------------------------------------------------------------


async def read_mark_prices(aggregators: dict[str, Any]) -> dict[str, float]:
    """Read current mark prices from the shared MockChainlinkAggregator contracts.

    Uses ``.call()`` — read-only, never ``.transact()`` (no on-chain state change).
    The same aggregator addresses are used by the vault NAV path (D-02), so these
    prices are consistent with NAV calculations.

    Parameters
    ----------
    aggregators:
        Mapping ``{"ETH": contract, "BTC": contract, "SOL": contract}`` where each
        value is an ``AsyncWeb3`` contract instance for ``MockChainlinkAggregator``.

    Returns
    -------
    dict[str, float]
        Mark prices in USD (8-decimal integer divided by 1e8).
        Example: ``{"ETH": 3000.0, "BTC": 60000.0, "SOL": 150.0}``.
    """
    prices: dict[str, float] = {}
    for asset, contract in aggregators.items():
        # latestRoundData() returns (roundId, answer, startedAt, updatedAt, answeredInRound)
        round_data = await contract.functions.latestRoundData().call()
        answer: int = round_data[1]  # int256, 8-decimal USD
        prices[asset] = answer / 1e8
    return prices

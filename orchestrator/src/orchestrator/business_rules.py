"""
orchestrator.business_rules — D-09/D-10 capital + exposure gate (Plan 02-02).

Sits above schema validation: the Decision has already passed Decision.model_validate()
before reaching this layer. business_rules enforces RUNTIME constraints that depend on
current state (available USDC, open positions) not expressible in the Pydantic schema.

D-09: Never silent-clamp. Return a human-readable reason string and make NO trade.
D-10: One open position per asset (ETH/BTC/SOL) at any time. Three concurrent max.

The caller (Plan 05 loop driver) journals the returned reason and skips trade submission.
"""

from __future__ import annotations

from typing import Any

from orchestrator.schema import MAX_NOTIONAL_USD, Decision

# ---------------------------------------------------------------------------
# Public constants
# ---------------------------------------------------------------------------

_LEVERAGE_CAP = 3.0
"""Hard leverage ceiling. Already enforced by Decision schema (le=3); re-checked here
as a defensive layer so the loop driver can rely on business_rules as a single gate."""

_FLOAT_EPSILON = 1e-9
"""Floating-point tolerance for the collateral >= available_usdc boundary check."""


# ---------------------------------------------------------------------------
# validate_business_rules — the D-09/D-10 gate
# ---------------------------------------------------------------------------


def validate_business_rules(
    decision: Decision,
    available_usdc: float,
    open_positions: dict[str, Any],
) -> str | None:
    """Return None if the decision passes business rules, else a rejection reason (D-09).

    Rules applied (in order):

    1. action in ("hold", "close")  → None immediately (no capital/exposure check).
       Close needs an existing position but that is the loop driver's concern, not
       a capital rejection.

    2. For action in ("open", "adjust"):
       a. Leverage cap (defensive re-check; schema already enforces le=3):
          if decision.leverage > 3.0 → reject with leverage-cap reason.
       b. Capital check (D-09):
          required_collateral = sizeUsd / leverage
          if required_collateral > available_usdc + epsilon → reject with
          "requested sizeUsd {X}, max allowable {Y} at {L}x leverage on {U} USDC"
       c. One-position-per-asset (D-10, action=="open" only):
          if decision.market in open_positions → reject with
          "already holding a {market} position; one position per asset (D-10) — no trade"

    Never raises. Never mutates decision (D-09 prohibits silent-clamp).

    Args:
        decision:        Pydantic-validated Decision (must have passed model_validate).
        available_usdc:  Undeployed USDC balance available for new collateral.
        open_positions:  Mapping of market → position summary dict. Presence of a key
                         indicates an open position exists for that market.

    Returns:
        None   — decision is valid, proceed to trade submission.
        str    — human-readable rejection reason; caller journals this and skips submit.
    """
    # ── Rule 1: hold / close bypass all capital/exposure checks ─────────────
    if decision.action in ("hold", "close"):
        return None

    # ── Rule 2a: defensive leverage cap ──────────────────────────────────────
    if decision.leverage > _LEVERAGE_CAP:
        return f"leverage {decision.leverage:g}x exceeds 3x cap — no trade"

    # ── Rule 2a′: absolute notional ceiling (SEC backstop) ───────────────────
    # Independent of available_usdc: blocks a hallucinated/oversized sizeUsd even if the
    # relative capital figure is ever stale or large. Schema already enforces le=MAX_NOTIONAL_USD;
    # re-checked here so business_rules remains the single runtime gate (mirrors the leverage cap).
    if decision.sizeUsd > MAX_NOTIONAL_USD:
        return f"sizeUsd {decision.sizeUsd:.0f} exceeds absolute cap {MAX_NOTIONAL_USD:.0f} USD — no trade"

    # ── Rule 2b: capital check (D-09) ────────────────────────────────────────
    required_collateral = decision.sizeUsd / decision.leverage
    if required_collateral > available_usdc + _FLOAT_EPSILON:
        max_size = available_usdc * decision.leverage
        return (
            f"requested sizeUsd {decision.sizeUsd:.0f}, "
            f"max allowable {max_size:.0f} "
            f"at {decision.leverage:g}x leverage on {available_usdc:.0f} USDC"
        )

    # ── Rule 2c: one-position-per-asset (D-10, open only) ────────────────────
    if decision.action == "open" and decision.market in open_positions:
        return (
            f"already holding a {decision.market} position; "
            f"one position per asset (D-10) — no trade"
        )

    return None

"""Deterministic reachable-NAV-range computation for trAIder Phase 4 (D-02).

Computes the LP seed range [LP_RANGE_LOWER_USD, LP_RANGE_UPPER_USD] consumed by
NavStressSim.t.sol (04-02) and the pool seeding script (04-06).

Design constraints (D-02 — PROHIBITED to widen):
  - PRICE_SEED=42 / DRIFT=0.0001 / VOL=0.005 — EXACT values from price_pusher.py
  - Seeded walk replicates price_pusher.PriceWalk.step() log-normal formula
  - Walk runs for GATE_DURATION_CYCLES (default 60 = 3600s / 60s cadence)
  - NAV swing computed with 3x leverage cap (VAULT-04) and one-position-per-asset
  - +25% tail margin applied to each side (TAIL_MARGIN_FACTOR)
  - The printed range IS the LP seed range AND the enforced peg claim
  - If WIDTH_VERDICT=WIDE, the range legitimately routes to V2 (D-03)
  - DO NOT widen a computed BOUNDED range to fake robustness

Usage:
    uv run --project orchestrator python contracts/test/utils/ReachableNavRange.py

Output (3 lines, always in this order):
    LP_RANGE_LOWER_USD=<x>
    LP_RANGE_UPPER_USD=<y>
    WIDTH_VERDICT=BOUNDED|WIDE
"""

from __future__ import annotations

import math
import random
import sys

# ============================================================================
# Locked parameters — MUST match price_pusher.PriceWalk exactly (D-02)
# ============================================================================

PRICE_SEED: int = 42          # RNG seed — same as price_pusher.py
DRIFT: float = 0.0001         # per-cycle log-normal drift (price_pusher.py constant)
VOL: float = 0.005            # per-cycle log-normal volatility (price_pusher.py constant)
MIN_PRICE_USD: float = 0.01   # Pitfall-5 floor — matches price_pusher.MIN_PRICE_USD

# ============================================================================
# Gate-duration cycles — D-17 default gate = ~45–60 min at 60s cadence
# ============================================================================

SESSION_CADENCE_SECONDS: int = 60   # matches orchestrator default cadence
GATE_DURATION_SECONDS: int = 3600   # 60 min default (D-17 upper bound for gate duration)
GATE_DURATION_CYCLES: int = GATE_DURATION_SECONDS // SESSION_CADENCE_SECONDS  # = 60

GATE_DURATION_CYCLES_45MIN: int = 45  # 45-min lower bound check (D-17)

# ============================================================================
# Leverage and position sizing (VAULT-04 — 3x leverage cap)
# ============================================================================

MAX_LEVERAGE: float = 3.0           # VAULT-04 hard cap
MAX_POSITIONS: int = 3              # one-position-per-asset (ETH / BTC / SOL)

# ============================================================================
# Tail margin (D-02 — +25% on each side)
# ============================================================================

TAIL_MARGIN_FACTOR: float = 0.25   # +25% tail margin, D-02 citation

# ============================================================================
# Venue-width threshold (D-03 decision boundary)
# ============================================================================

BOUNDED_LOWER_THRESHOLD: float = 0.5   # BOUNDED if lower_usd >= 0.5
BOUNDED_UPPER_THRESHOLD: float = 2.0   # BOUNDED if upper_usd <= 2.0


# ============================================================================
# Replicates price_pusher.PriceWalk.step() exactly
# ============================================================================

def run_price_walk(seed: int, drift: float, vol: float, cycles: int) -> tuple[float, float]:
    """Run a seeded log-normal price walk and return (min_factor, max_factor).

    Replicates price_pusher.PriceWalk.step() identically:
        shock = rng.gauss(drift, vol)
        price = max(price * exp(shock), MIN_PRICE_USD)

    Returns the min and max observed price as a factor of the starting price 1.0.
    The starting price is normalised to 1.0 (we care about fractional movement).
    """
    rng = random.Random(seed)
    price: float = 1.0
    price_min: float = 1.0
    price_max: float = 1.0

    for _ in range(cycles):
        shock = rng.gauss(drift, vol)
        # log-normal step + Pitfall-5 floor (price_pusher.py lines 67–73)
        price = max(price * math.exp(shock), MIN_PRICE_USD)
        if price < price_min:
            price_min = price
        if price > price_max:
            price_max = price

    return price_min, price_max


def compute_nav_range(
    price_min_factor: float,
    price_max_factor: float,
    leverage: float,
    tail_margin: float,
) -> tuple[float, float]:
    """Map worst-case asset price movement to NAV using leverage cap.

    NAV with a single maximally-leveraged position:
        nav = 1.0 + leverage * (price_factor - 1.0)

    For the worst-case downside (price_min_factor < 1.0):
        nav_low_raw = 1.0 + leverage * (price_min_factor - 1.0)

    For the best-case upside (price_max_factor > 1.0):
        nav_high_raw = 1.0 + leverage * (price_max_factor - 1.0)

    Each bound is then widened by the +TAIL_MARGIN_FACTOR tail margin on its side:
        nav_low  = nav_low_raw  - |nav_low_raw  - 1.0| * tail_margin
        nav_high = nav_high_raw + |nav_high_raw - 1.0| * tail_margin

    The LP range is expressed as USD per mTOKEN at initial NAV = $1.00, so
    the NAV factor directly equals the USD value.

    D-02 note: this uses net-position-value semantics (collateral + pnl), matching
    the vault's totalAssets() computation.
    """
    # Raw NAV swing at full leverage
    nav_low_raw = 1.0 + leverage * (price_min_factor - 1.0)
    nav_high_raw = 1.0 + leverage * (price_max_factor - 1.0)

    # +TAIL_MARGIN_FACTOR on each side (D-02)
    swing_down = abs(nav_low_raw - 1.0)
    swing_up = abs(nav_high_raw - 1.0)

    nav_low = nav_low_raw - swing_down * tail_margin
    nav_high = nav_high_raw + swing_up * tail_margin

    # Floor at a small positive value to avoid negative NAV
    nav_low = max(nav_low, 0.01)

    return nav_low, nav_high


def width_verdict(lower_usd: float, upper_usd: float) -> str:
    """Return BOUNDED or WIDE per D-03.

    BOUNDED: range is within [BOUNDED_LOWER_THRESHOLD, BOUNDED_UPPER_THRESHOLD]
             (~0.5x–2x from initial NAV $1.00).
    WIDE:    range extends outside that window.
    """
    if lower_usd >= BOUNDED_LOWER_THRESHOLD and upper_usd <= BOUNDED_UPPER_THRESHOLD:
        return "BOUNDED"
    return "WIDE"


def main() -> None:
    """Compute and print the reachable NAV range for the default gate duration."""

    # Run the seeded price walk (PRICE_SEED=42, DRIFT=0.0001, VOL=0.005 — D-02)
    price_min_60, price_max_60 = run_price_walk(
        seed=PRICE_SEED,
        drift=DRIFT,
        vol=VOL,
        cycles=GATE_DURATION_CYCLES,
    )

    # Also compute for 45-min gate duration (D-17 lower bound)
    price_min_45, price_max_45 = run_price_walk(
        seed=PRICE_SEED,
        drift=DRIFT,
        vol=VOL,
        cycles=GATE_DURATION_CYCLES_45MIN,
    )

    # Use the worst case across both durations for the conservative range
    # (the 60-cycle walk should dominate, but we document both)
    price_min = min(price_min_60, price_min_45)
    price_max = max(price_max_60, price_max_45)

    # Map to NAV range with 3x leverage cap + 25% tail margin (D-02)
    nav_low, nav_high = compute_nav_range(
        price_min_factor=price_min,
        price_max_factor=price_max,
        leverage=MAX_LEVERAGE,
        tail_margin=TAIL_MARGIN_FACTOR,
    )

    # Round to 4 decimal places for stable deterministic output
    lower_usd = round(nav_low, 4)
    upper_usd = round(nav_high, 4)

    verdict = width_verdict(lower_usd, upper_usd)

    # Diagnostics (to stderr — does not pollute the parseable stdout lines)
    print(
        f"# ReachableNavRange — PRICE_SEED={PRICE_SEED}, DRIFT={DRIFT}, VOL={VOL}",
        file=sys.stderr,
    )
    print(
        f"# 60-cycle walk: price_min={price_min_60:.6f}, price_max={price_max_60:.6f}",
        file=sys.stderr,
    )
    print(
        f"# 45-cycle walk: price_min={price_min_45:.6f}, price_max={price_max_45:.6f}",
        file=sys.stderr,
    )
    print(
        f"# worst-case combined: price_min={price_min:.6f}, price_max={price_max:.6f}",
        file=sys.stderr,
    )
    print(
        f"# nav_low_pre_margin={1.0 + MAX_LEVERAGE * (price_min - 1.0):.6f}, "
        f"nav_high_pre_margin={1.0 + MAX_LEVERAGE * (price_max - 1.0):.6f}",
        file=sys.stderr,
    )
    print(
        f"# tail_margin={TAIL_MARGIN_FACTOR} (+{TAIL_MARGIN_FACTOR * 100:.0f}% each side, D-02)",
        file=sys.stderr,
    )
    print(
        f"# BOUNDED thresholds: lower>={BOUNDED_LOWER_THRESHOLD}, upper<={BOUNDED_UPPER_THRESHOLD}",
        file=sys.stderr,
    )

    # Machine-parseable output (stdout) — consumed by NavStressSim.t.sol and 04-06
    print(f"LP_RANGE_LOWER_USD={lower_usd}")
    print(f"LP_RANGE_UPPER_USD={upper_usd}")
    print(f"WIDTH_VERDICT={verdict}")


if __name__ == "__main__":
    main()

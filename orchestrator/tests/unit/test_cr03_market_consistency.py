"""Unit tests for CR-03: market_table mark/funding/24h must come from one consistent step.

Regression test:
  - Drive a known seeded walk; push step N; assert market_table funding/24h equal the
    values computed at step N's price — not a later step produced by background concurrent
    stepping.
"""

from __future__ import annotations

from orchestrator.loop.market_state import (
    build_market_table_from_snapshot,
)
from orchestrator.loop.price_pusher import PriceWalk, build_consistent_snapshot


def _make_walk(seed: int = 42) -> PriceWalk:
    return PriceWalk(
        seed=seed,
        starting_prices={"ETH": 3000.0, "BTC": 60000.0, "SOL": 150.0},
        drift=0.0001,
        volatility=0.01,
    )


# ---------------------------------------------------------------------------
# build_consistent_snapshot returns all three values from the same step
# ---------------------------------------------------------------------------


def test_consistent_snapshot_values_match_walk_at_same_step() -> None:
    """build_consistent_snapshot must return mark/funding/change_24h from the same step.

    CR-03 regression: previously the driver read mark from the aggregator (latest push)
    but funding/24h from the walk's _history, which price_pusher may have stepped more
    or fewer times than expected.  This test drives a deterministic walk to a known step
    and asserts that the snapshot values exactly match what the walk reports at that step.
    """
    walk = _make_walk(seed=42)

    # Step the walk to a known state
    _ = walk.step()  # step 1
    _ = walk.step()  # step 2
    prices_step3 = walk.step()  # step 3 — capture the deterministic prices

    snapshot = build_consistent_snapshot(walk)

    for asset in ("ETH", "BTC", "SOL"):
        # mark in snapshot must match the price returned by step()
        assert abs(snapshot[asset]["mark"] - prices_step3[asset]) < 1e-10, (
            f"{asset} mark in snapshot ({snapshot[asset]['mark']}) != step() price ({prices_step3[asset]})"
        )
        # funding in snapshot must match walk.funding_rate(asset) at THIS point in history
        expected_funding = walk.funding_rate(asset)
        assert abs(snapshot[asset]["funding"] - expected_funding) < 1e-10, (
            f"{asset} funding in snapshot ({snapshot[asset]['funding']}) != "
            f"walk.funding_rate ({expected_funding})"
        )
        # change_24h in snapshot must match walk.change_24h(asset) at THIS point
        expected_24h = walk.change_24h(asset)
        assert abs(snapshot[asset]["change_24h"] - expected_24h) < 1e-10, (
            f"{asset} change_24h in snapshot ({snapshot[asset]['change_24h']}) != "
            f"walk.change_24h ({expected_24h})"
        )


def test_snapshot_is_internally_consistent_across_steps() -> None:
    """Snapshot from step N must differ from snapshot at step N+1.

    This test explicitly demonstrates the CR-03 problem scenario: if we produced
    a market_table using step-N mark prices but step-(N+1) funding values, the table
    would be internally inconsistent.  Using build_consistent_snapshot ensures all
    three values come from the same step.
    """
    walk = _make_walk(seed=123)
    _ = walk.step()  # step 1

    # Capture snapshot at step 1
    snapshot_step1 = build_consistent_snapshot(walk)
    funding_step1 = {a: snapshot_step1[a]["funding"] for a in ("ETH", "BTC", "SOL")}
    change_step1 = {a: snapshot_step1[a]["change_24h"] for a in ("ETH", "BTC", "SOL")}

    # Advance one more step
    _ = walk.step()  # step 2

    # Snapshot at step 2 should differ from step 1's funding/change
    snapshot_step2 = build_consistent_snapshot(walk)
    funding_step2 = {a: snapshot_step2[a]["funding"] for a in ("ETH", "BTC", "SOL")}

    # The two steps should produce different funding values (deterministic walk with
    # non-zero volatility; the probability of identical funding across steps is negligible)
    for asset in ("ETH", "BTC", "SOL"):
        # If step1 and step2 snapshots are the same, the snapshot is not being updated
        # (This is the CR-03 invariant: each snapshot belongs to ONE step)
        assert (
            funding_step1 != funding_step2
            or change_step1[asset] != snapshot_step2[asset]["change_24h"]
        ), (
            f"Funding/24h at step1 and step2 are identical for {asset} — "
            "snapshot may not be stepping correctly."
        )


def test_build_market_table_from_snapshot_produces_correct_table() -> None:
    """build_market_table_from_snapshot renders a 5-line pipe table from a snapshot."""
    walk = _make_walk(seed=99)
    _ = walk.step()
    snapshot = build_consistent_snapshot(walk)

    table = build_market_table_from_snapshot(snapshot)
    lines = table.split("\n")

    assert len(lines) == 5, f"Expected 5 lines, got {len(lines)}: {lines!r}"
    assert "| Asset | Mark | Funding (ann.) | 24h % |" in lines[0]

    for asset in ("ETH", "BTC", "SOL"):
        assert f"| {asset} |" in table, f"Row for {asset} not found in table:\n{table}"


def test_snapshot_mark_price_matches_table_mark_column() -> None:
    """The mark price in the snapshot must appear in the market_table mark column.

    CR-03 regression: previously the mark column came from on-chain read while
    funding/24h came from a possibly-different walk step.  With snapshot-based
    rendering, the mark column and the funding column are always from the same step.
    """
    walk = _make_walk(seed=77)
    prices = walk.step()
    snapshot = build_consistent_snapshot(walk)

    table = build_market_table_from_snapshot(snapshot)

    # ETH mark from snapshot must be in the table
    eth_mark = snapshot["ETH"]["mark"]
    eth_mark_formatted = f"${eth_mark:,.2f}"
    assert eth_mark_formatted in table, (
        f"ETH mark {eth_mark_formatted} from snapshot not found in table:\n{table}"
    )

    # The snapshot mark must match the walk's current price (same step)
    assert abs(snapshot["ETH"]["mark"] - prices["ETH"]) < 1e-10, (
        f"Snapshot ETH mark ({snapshot['ETH']['mark']}) != step() price ({prices['ETH']})"
    )

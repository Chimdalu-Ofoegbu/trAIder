"""Unit tests for PriceWalk (D-01/D-02/D-04/Pitfall-5).

Test contracts:
  (i)  Two PriceWalk(seed=7) instances produce identical 10-step sequences
       (seeded replay: same seed → same prices every step).
  (ii) After 5000 steps with strongly-negative drift/high volatility, every
       price is still > 0 (log-normal floor guard, Pitfall 5).
  (iii) to_8dec(3000.0) == 300000000000 (8-decimal on-chain conversion).
  (iv)  funding_rate and change_24h return deterministic floats for a fixed seed.
"""

from __future__ import annotations

from orchestrator.loop.price_pusher import PriceWalk, to_8dec

STARTING = {"ETH": 3000.0, "BTC": 60000.0, "SOL": 150.0}


# ---------------------------------------------------------------------------
# (i) Seed replay — two instances with same seed produce identical sequences
# ---------------------------------------------------------------------------


def test_price_walk_seed_replay_identical_10_steps() -> None:
    """D-01: two PriceWalk(seed=7) instances must produce byte-identical price dicts."""
    walk_a = PriceWalk(seed=7, starting_prices=STARTING, drift=0.0001, volatility=0.005)
    walk_b = PriceWalk(seed=7, starting_prices=STARTING, drift=0.0001, volatility=0.005)

    for step_n in range(10):
        prices_a = walk_a.step()
        prices_b = walk_b.step()
        assert prices_a == prices_b, (
            f"Step {step_n}: walk_a={prices_a} != walk_b={prices_b} — seed replay broken"
        )


def test_price_walk_different_seeds_diverge() -> None:
    """Two walks with *different* seeds should produce different sequences."""
    walk_42 = PriceWalk(seed=42, starting_prices=STARTING, drift=0.0, volatility=0.01)
    walk_43 = PriceWalk(seed=43, starting_prices=STARTING, drift=0.0, volatility=0.01)

    prices_42 = [walk_42.step() for _ in range(5)]
    prices_43 = [walk_43.step() for _ in range(5)]

    # At least one step must differ — seeds 42 and 43 should diverge
    assert prices_42 != prices_43, "Different seeds should produce different price paths"


# ---------------------------------------------------------------------------
# (ii) Price floor guard — no non-positive prices after aggressive drift
# ---------------------------------------------------------------------------


def test_price_walk_floor_guard_5000_steps() -> None:
    """Pitfall 5: floor guard ensures prices never go <= 0 even under strong negative drift."""
    # Use a very strong negative drift and high volatility to stress-test the floor
    walk = PriceWalk(
        seed=99,
        starting_prices=STARTING,
        drift=-0.05,  # strongly negative drift (5% per cycle downward)
        volatility=0.1,  # high volatility
    )
    for step_n in range(5000):
        prices = walk.step()
        for asset, price in prices.items():
            assert price > 0, (
                f"Step {step_n}: {asset} price went non-positive ({price}) — "
                "MIN_PRICE_USD floor guard missing or broken"
            )


# ---------------------------------------------------------------------------
# (iii) to_8dec conversion
# ---------------------------------------------------------------------------


def test_to_8dec_3000_usd() -> None:
    """$3000.00 → 300_000_000_000 (8 decimal places, integer)."""
    result = to_8dec(3000.0)
    assert result == 300_000_000_000, f"Expected 300000000000, got {result}"


def test_to_8dec_returns_int() -> None:
    assert isinstance(to_8dec(150.0), int)


def test_to_8dec_60000_usd() -> None:
    """$60,000.00 → 6_000_000_000_000."""
    assert to_8dec(60000.0) == 6_000_000_000_000


def test_to_8dec_fractional() -> None:
    """to_8dec must round correctly for prices with fractional cents."""
    # $0.01 == 1_000_000
    assert to_8dec(0.01) == 1_000_000
    # $1.23456789 rounds to nearest integer at 8 dec
    assert to_8dec(1.23456789) == 123456789


# ---------------------------------------------------------------------------
# (iv) funding_rate and change_24h are deterministic for a fixed seed
# ---------------------------------------------------------------------------


def test_funding_rate_deterministic_for_fixed_seed() -> None:
    """Same seed → same funding_rate after same number of steps."""
    walk_a = PriceWalk(seed=42, starting_prices=STARTING, drift=0.0001, volatility=0.005)
    walk_b = PriceWalk(seed=42, starting_prices=STARTING, drift=0.0001, volatility=0.005)

    for _ in range(20):
        walk_a.step()
        walk_b.step()

    for asset in ("ETH", "BTC", "SOL"):
        rate_a = walk_a.funding_rate(asset)
        rate_b = walk_b.funding_rate(asset)
        assert rate_a == rate_b, (
            f"{asset}: funding_rate differs between identical walks — not deterministic"
        )
        assert isinstance(rate_a, float)


def test_change_24h_deterministic_for_fixed_seed() -> None:
    """Same seed → same change_24h after same number of steps."""
    walk_a = PriceWalk(seed=42, starting_prices=STARTING, drift=0.0001, volatility=0.005)
    walk_b = PriceWalk(seed=42, starting_prices=STARTING, drift=0.0001, volatility=0.005)

    for _ in range(50):
        walk_a.step()
        walk_b.step()

    for asset in ("ETH", "BTC", "SOL"):
        chg_a = walk_a.change_24h(asset)
        chg_b = walk_b.change_24h(asset)
        assert chg_a == chg_b, (
            f"{asset}: change_24h differs between identical walks — not deterministic"
        )
        assert isinstance(chg_a, float)


def test_funding_rate_zero_before_first_step() -> None:
    """funding_rate should return 0.0 before any steps (only starting price in history)."""
    walk = PriceWalk(seed=1, starting_prices=STARTING, drift=0.0, volatility=0.0)
    for asset in ("ETH", "BTC", "SOL"):
        assert walk.funding_rate(asset) == 0.0


def test_change_24h_lookback_shorter_than_1440() -> None:
    """With fewer than 1440 steps in history, change_24h uses the starting price."""
    walk = PriceWalk(
        seed=1,
        starting_prices={"ETH": 100.0, "BTC": 1000.0, "SOL": 10.0},
        drift=0.0,
        volatility=0.0,
    )
    # With drift=0 and volatility=0, gauss(0,0) is 0, prices don't change (exp(0)=1)
    walk.step()
    # change from 100 to 100 == 0.0
    assert walk.change_24h("ETH") == 0.0

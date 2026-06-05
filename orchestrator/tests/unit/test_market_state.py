"""Unit tests for market_state module (D-04/D-05/D-06/ORCH-04/Pitfall-6).

Test contracts:
  (i)   format_market_table produces 4 lines (header + separator + 3 rows) and
        includes ETH, BTC, SOL with the correct pipe-table header.
  (ii)  render_prompt fills all six frozen system.md placeholders without raising
        (no ``{{`` or ``}}`` left in the output).
  (iii) Rendering does not raise on the real frozen system.md (Pitfall 6 guard).
  (iv)  format_market_table and render_prompt take NO model/provider parameter
        (D-05 model-agnostic check).
"""

from __future__ import annotations

import inspect

from orchestrator.loop.market_state import build_market_table, format_market_table, render_prompt

# ---------------------------------------------------------------------------
# (i) format_market_table structure
# ---------------------------------------------------------------------------


def test_format_market_table_line_count() -> None:
    """format_market_table must return exactly 4 lines (header + sep + 3 asset rows)."""
    prices = {"ETH": 3000.0, "BTC": 60000.0, "SOL": 150.0}
    funding = {"ETH": 0.0001, "BTC": -0.0002, "SOL": 0.0}
    change = {"ETH": 0.012, "BTC": -0.005, "SOL": 0.003}

    table = format_market_table(prices, funding, change)
    lines = table.split("\n")
    assert len(lines) == 4, f"Expected 4 lines, got {len(lines)}: {lines!r}"


def test_format_market_table_header() -> None:
    """Table must contain the canonical header line."""
    prices = {"ETH": 3000.0, "BTC": 60000.0, "SOL": 150.0}
    funding = {"ETH": 0.0, "BTC": 0.0, "SOL": 0.0}
    change = {"ETH": 0.0, "BTC": 0.0, "SOL": 0.0}

    table = format_market_table(prices, funding, change)
    assert "| Asset | Mark | Funding (ann.) | 24h % |" in table, (
        f"Expected header not found in:\n{table}"
    )


def test_format_market_table_contains_eth_btc_sol_rows() -> None:
    """One row each for ETH, BTC, SOL must appear in the table."""
    prices = {"ETH": 3000.0, "BTC": 60000.0, "SOL": 150.0}
    funding = {"ETH": 0.0001, "BTC": -0.0002, "SOL": 0.0}
    change = {"ETH": 0.012, "BTC": -0.005, "SOL": 0.003}

    table = format_market_table(prices, funding, change)
    for asset in ("ETH", "BTC", "SOL"):
        assert f"| {asset} |" in table, f"Row for {asset} not found in table:\n{table}"


def test_format_market_table_price_formatting() -> None:
    """Prices should appear with comma-separated thousands and 2 decimal places."""
    prices = {"ETH": 3000.0, "BTC": 60000.0, "SOL": 150.0}
    funding = {"ETH": 0.0, "BTC": 0.0, "SOL": 0.0}
    change = {"ETH": 0.0, "BTC": 0.0, "SOL": 0.0}

    table = format_market_table(prices, funding, change)
    assert "$3,000.00" in table, f"ETH price not formatted correctly in:\n{table}"
    assert "$60,000.00" in table, f"BTC price not formatted correctly in:\n{table}"
    assert "$150.00" in table, f"SOL price not formatted correctly in:\n{table}"


# ---------------------------------------------------------------------------
# (ii) render_prompt fills all six placeholders — no {{ }} left in output
# ---------------------------------------------------------------------------


DUMMY_MARKET_TABLE = (
    "| Asset | Mark | Funding (ann.) | 24h % |\n"
    "|-------|------|---------------|-------|\n"
    "| ETH | $3,000.00 | +0.0001 | +1.20% |\n"
    "| BTC | $60,000.00 | -0.0002 | -0.50% |\n"
    "| SOL | $150.00 | +0.0000 | +0.30% |"
)


def test_render_prompt_no_unfilled_placeholders() -> None:
    """render_prompt must not leave any Jinja2 {{ }} placeholders in the output."""
    rendered = render_prompt(
        nav_table="| Vault | NAV |\n|---|---|\n| mCLA-S1 | $10,000.00 |",
        time_remaining="0h 15m 0s",
        positions_table="No open positions.",
        available_usdc=10000.0,
        recent_decisions="No decisions yet.",
        market_table=DUMMY_MARKET_TABLE,
    )
    assert "{{" not in rendered, "Unfilled Jinja2 opening braces remain in output"
    assert "}}" not in rendered, "Unfilled Jinja2 closing braces remain in output"


def test_render_prompt_contains_market_table_content() -> None:
    """render_prompt output must contain the market_table string passed in."""
    rendered = render_prompt(
        nav_table="| Vault | NAV |",
        time_remaining="1h 0m 0s",
        positions_table="None",
        available_usdc=5000.0,
        recent_decisions="hold cycle 1",
        market_table=DUMMY_MARKET_TABLE,
    )
    assert "$3,000.00" in rendered, "market_table content missing from rendered prompt"


def test_render_prompt_contains_formatted_usdc() -> None:
    """available_usdc should appear formatted as e.g. '10,000.00 USDC' in the output."""
    rendered = render_prompt(
        nav_table="NAV table",
        time_remaining="0h 5m 0s",
        positions_table="None",
        available_usdc=10000.0,
        recent_decisions="",
        market_table=DUMMY_MARKET_TABLE,
    )
    # The render formats available_usdc as f"{available_usdc:,.2f}"
    assert "10,000.00" in rendered, (
        f"Formatted available_usdc '10,000.00' not found in rendered prompt:\n{rendered[:500]}"
    )


def test_render_prompt_on_real_system_md_does_not_raise() -> None:
    """Rendering against the real frozen system.md must not raise (Pitfall 6)."""
    # This test specifically catches Pitfall 6: using str.format() instead of
    # jinja2.Template().render() would raise KeyError on {{ nav_table }}.
    rendered = render_prompt(
        nav_table="| Vault | NAV |",
        time_remaining="0h 15m 0s",
        positions_table="No open positions.",
        available_usdc=10000.0,
        recent_decisions="No decisions yet.",
        market_table=DUMMY_MARKET_TABLE,
    )
    assert isinstance(rendered, str)
    assert len(rendered) > 100, "Rendered prompt suspiciously short"


# ---------------------------------------------------------------------------
# (iii) model-agnostic check — D-05
# ---------------------------------------------------------------------------


def test_format_market_table_has_no_model_parameter() -> None:
    """D-05: format_market_table must NOT have a 'model' or 'provider' parameter."""
    sig = inspect.signature(format_market_table)
    param_names = list(sig.parameters.keys())
    assert "model" not in param_names, (
        "format_market_table has 'model' parameter — must be model-agnostic (D-05)"
    )
    assert "provider" not in param_names, (
        "format_market_table has 'provider' parameter — must be model-agnostic (D-05)"
    )


def test_render_prompt_has_no_model_parameter() -> None:
    """D-05: render_prompt must NOT have a 'model' or 'provider' parameter."""
    sig = inspect.signature(render_prompt)
    param_names = list(sig.parameters.keys())
    assert "model" not in param_names, (
        "render_prompt has 'model' parameter — must be model-agnostic (D-05)"
    )
    assert "provider" not in param_names, (
        "render_prompt has 'provider' parameter — must be model-agnostic (D-05)"
    )


# ---------------------------------------------------------------------------
# build_market_table integration with PriceWalk
# ---------------------------------------------------------------------------


def test_build_market_table_uses_walk_funding_and_change() -> None:
    """build_market_table should produce a 4-line table using PriceWalk-derived values."""
    from orchestrator.loop.price_pusher import PriceWalk

    walk = PriceWalk(
        seed=42,
        starting_prices={"ETH": 3000.0, "BTC": 60000.0, "SOL": 150.0},
        drift=0.0001,
        volatility=0.005,
    )
    prices = walk.step()  # advance one step so funding is non-trivial

    table = build_market_table(walk, prices)
    lines = table.split("\n")
    assert len(lines) == 4
    for asset in ("ETH", "BTC", "SOL"):
        assert f"| {asset} |" in table

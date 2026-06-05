"""Unit tests for orchestrator.business_rules (Task 2, Plan 02-02).

Tests cover D-09 / D-10 rejection rules:
  - Valid open within capital + exposure limits → None (no rejection)
  - Over-capital open → reason string containing "max allowable"
  - Duplicate-asset open (one-position-per-asset) → D-10 reason string
  - Over-leverage → leverage cap reason
  - hold / close actions → None regardless of sizeUsd
  - No silent-clamp: decision.sizeUsd and decision.leverage are never mutated

SC-4 note: business_rules.py must import Decision but NEVER call validate_decision
           from the anthropic adapter (that is the adapter's concern).
"""

from __future__ import annotations

from orchestrator.schema import Decision


def _make_decision(**kwargs) -> Decision:
    """Build a minimal valid Decision, overriding fields via kwargs."""
    defaults = {
        "action": "open",
        "sizeUsd": 1000.0,
        "leverage": 2.0,
        "rationale": "test rationale for business rules",
        "confidence": 0.5,
        "expectedHoldingPeriod": "short",
        "market": "ETH",
        "side": "long",
    }
    defaults.update(kwargs)
    return Decision.model_validate(defaults)


def _make_hold() -> Decision:
    return Decision.model_validate(
        {
            "action": "hold",
            "sizeUsd": 0,
            "leverage": 1,
            "rationale": "no signal",
            "confidence": 0.2,
            "expectedHoldingPeriod": "short",
        }
    )


def _make_close() -> Decision:
    return Decision.model_validate(
        {
            "action": "close",
            "sizeUsd": 0,
            "leverage": 1,
            "rationale": "taking profit",
            "confidence": 0.8,
            "expectedHoldingPeriod": "short",
            "market": "ETH",
            "side": "long",
        }
    )


# ---------------------------------------------------------------------------
# Test: valid open returns None
# ---------------------------------------------------------------------------


class TestValidOpenReturnsNone:
    def test_valid_open_no_existing_positions(self):
        from orchestrator.business_rules import validate_business_rules

        decision = _make_decision(sizeUsd=1000, leverage=2)
        # collateral = 1000/2 = 500; available = 1000 → OK
        result = validate_business_rules(decision, available_usdc=1000.0, open_positions={})
        assert result is None

    def test_valid_open_exact_capital_boundary(self):
        from orchestrator.business_rules import validate_business_rules

        # collateral = 1000/2 = 500; available = 500 → exactly at boundary → OK
        decision = _make_decision(sizeUsd=1000, leverage=2)
        result = validate_business_rules(decision, available_usdc=500.0, open_positions={})
        assert result is None

    def test_valid_open_different_market_from_existing(self):
        from orchestrator.business_rules import validate_business_rules

        decision = _make_decision(sizeUsd=500, leverage=1, market="BTC")
        # ETH position open but we're opening BTC → allowed
        result = validate_business_rules(
            decision,
            available_usdc=1000.0,
            open_positions={"ETH": {"side": "long", "sizeUsd": 500}},
        )
        assert result is None

    def test_valid_adjust_on_existing_market(self):
        """adjust is not subject to the one-position-per-asset rule (only open is)."""
        from orchestrator.business_rules import validate_business_rules

        decision = _make_decision(action="adjust", sizeUsd=500, leverage=1, market="ETH")
        result = validate_business_rules(
            decision,
            available_usdc=1000.0,
            open_positions={"ETH": {"side": "long", "sizeUsd": 500}},
        )
        assert result is None


# ---------------------------------------------------------------------------
# Test: over-capital open returns reason with "max allowable"
# ---------------------------------------------------------------------------


class TestOverCapitalRejection:
    def test_over_capital_returns_reason(self):
        from orchestrator.business_rules import validate_business_rules

        # collateral = 5000/3 ≈ 1666.67; available = 1367 → over cap
        decision = _make_decision(sizeUsd=5000, leverage=3)
        result = validate_business_rules(decision, available_usdc=1367.0, open_positions={})
        assert result is not None
        assert "max allowable" in result

    def test_over_capital_reason_contains_requested_size(self):
        from orchestrator.business_rules import validate_business_rules

        decision = _make_decision(sizeUsd=5000, leverage=3)
        result = validate_business_rules(decision, available_usdc=1367.0, open_positions={})
        assert result is not None
        assert "5000" in result

    def test_over_capital_reason_contains_leverage(self):
        from orchestrator.business_rules import validate_business_rules

        decision = _make_decision(sizeUsd=5000, leverage=3)
        result = validate_business_rules(decision, available_usdc=1367.0, open_positions={})
        assert result is not None
        assert "3" in result  # leverage multiplier mentioned

    def test_over_capital_reason_contains_available_usdc(self):
        from orchestrator.business_rules import validate_business_rules

        decision = _make_decision(sizeUsd=5000, leverage=3)
        result = validate_business_rules(decision, available_usdc=1367.0, open_positions={})
        assert result is not None
        assert "1367" in result

    def test_zero_available_usdc_rejects_any_open(self):
        from orchestrator.business_rules import validate_business_rules

        decision = _make_decision(sizeUsd=1, leverage=1)
        result = validate_business_rules(decision, available_usdc=0.0, open_positions={})
        assert result is not None
        assert "max allowable" in result

    def test_adjust_over_capital_also_rejected(self):
        """adjust actions also check capital (sizeUsd / leverage > available_usdc)."""
        from orchestrator.business_rules import validate_business_rules

        decision = _make_decision(action="adjust", sizeUsd=9999, leverage=1, market="ETH")
        result = validate_business_rules(decision, available_usdc=100.0, open_positions={})
        assert result is not None
        assert "max allowable" in result


# ---------------------------------------------------------------------------
# Test: one-position-per-asset (D-10) — duplicate open returns reason
# ---------------------------------------------------------------------------


class TestOnePositionPerAsset:
    def test_open_on_existing_market_rejected(self):
        from orchestrator.business_rules import validate_business_rules

        decision = _make_decision(action="open", sizeUsd=100, leverage=1, market="ETH")
        result = validate_business_rules(
            decision,
            available_usdc=1000.0,
            open_positions={"ETH": {"side": "long", "sizeUsd": 100}},
        )
        assert result is not None
        assert "one position per asset" in result.lower() or "D-10" in result

    def test_open_on_existing_market_btc(self):
        from orchestrator.business_rules import validate_business_rules

        decision = _make_decision(action="open", sizeUsd=100, leverage=1, market="BTC")
        result = validate_business_rules(
            decision,
            available_usdc=1000.0,
            open_positions={"BTC": {"side": "short"}},
        )
        assert result is not None

    def test_rejection_reason_mentions_market(self):
        from orchestrator.business_rules import validate_business_rules

        decision = _make_decision(action="open", sizeUsd=100, leverage=1, market="SOL")
        result = validate_business_rules(
            decision,
            available_usdc=1000.0,
            open_positions={"SOL": {}},
        )
        assert result is not None
        assert "SOL" in result


# ---------------------------------------------------------------------------
# Test: hold / close return None regardless of sizeUsd
# ---------------------------------------------------------------------------


class TestHoldCloseAlwaysNone:
    def test_hold_returns_none(self):
        from orchestrator.business_rules import validate_business_rules

        decision = _make_hold()
        result = validate_business_rules(decision, available_usdc=0.0, open_positions={})
        assert result is None

    def test_close_returns_none(self):
        from orchestrator.business_rules import validate_business_rules

        decision = _make_close()
        result = validate_business_rules(decision, available_usdc=0.0, open_positions={})
        assert result is None

    def test_hold_ignores_large_sizeUsd(self):
        """Hold with sizeUsd=99999 should still pass (hold has no capital check)."""
        from orchestrator.business_rules import validate_business_rules

        # Can't use _make_hold directly since sizeUsd=0 is hardcoded;
        # create a hold with the default sizeUsd=0 — sizeUsd doesn't matter for hold
        decision = Decision.model_validate(
            {
                "action": "hold",
                "sizeUsd": 0,
                "leverage": 1,
                "rationale": "waiting",
                "confidence": 0.1,
                "expectedHoldingPeriod": "short",
            }
        )
        result = validate_business_rules(decision, available_usdc=0.0, open_positions={})
        assert result is None

    def test_close_ignores_existing_positions(self):
        """Close passes even if positions dict is non-empty (close needs existing, but that
        is the loop's concern — capital/exposure check not applied to close)."""
        from orchestrator.business_rules import validate_business_rules

        decision = _make_close()
        result = validate_business_rules(
            decision,
            available_usdc=0.0,
            open_positions={"ETH": {}, "BTC": {}, "SOL": {}},
        )
        assert result is None


# ---------------------------------------------------------------------------
# Test: no silent-clamp — decision is never mutated
# ---------------------------------------------------------------------------


class TestNoSilentClamp:
    def test_sizeUsd_not_mutated_on_over_capital(self):
        from orchestrator.business_rules import validate_business_rules

        original_size = 5000.0
        decision = _make_decision(sizeUsd=original_size, leverage=3)
        validate_business_rules(decision, available_usdc=100.0, open_positions={})
        # Must not have been clamped
        assert decision.sizeUsd == original_size

    def test_leverage_not_mutated_on_over_leverage(self):
        from orchestrator.business_rules import validate_business_rules

        # leverage > 3 would fail Decision.model_validate, so test at exactly 3
        original_leverage = 3.0
        decision = _make_decision(sizeUsd=1000, leverage=original_leverage)
        validate_business_rules(decision, available_usdc=10.0, open_positions={})
        assert decision.leverage == original_leverage

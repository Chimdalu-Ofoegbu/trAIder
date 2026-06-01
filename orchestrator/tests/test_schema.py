"""
Validation gate for the frozen decision schema (IFACE-03).

Tests:
  1. good.json        → validates and parses cleanly
  2. missing_action   → rejected with ValidationError (required field absent)
  3. extra_field      → accepted; extra stored in model_extra, NOT in model_fields
  4. strict_provider_schema() → additionalProperties:false, all property keys in required
  --- CR-02 regression tests ---
  5. open_missing_market  → rejected (market required for open)
  6. open_missing_side    → rejected (side required for open; must NOT silently short)
  7. adjust_missing_market → rejected (market required for adjust)
  8. close without market/side → valid (close may omit both)
  9. hold without market/side  → valid (hold may omit both)
  10. run_cycle with null-side open fixture → status="malformed" (ORCH-05 path, no crash)
"""

from pathlib import Path

import pytest
from pydantic import ValidationError

from orchestrator.schema import Decision, strict_provider_schema

FIXTURES = Path(__file__).parent / "fixtures" / "decisions"


def load(name: str) -> dict:
    import json

    return json.loads((FIXTURES / name).read_text())


# ---------------------------------------------------------------------------
# Test 1: good fixture validates and parses
# ---------------------------------------------------------------------------


def test_good_decision_validates():
    data = load("good.json")
    decision = Decision.model_validate(data)

    assert decision.action == "open"
    assert decision.market == "ETH"
    assert decision.side == "long"
    assert decision.sizeUsd == 5000.0
    assert decision.leverage == 2.0
    assert decision.confidence == 0.7
    assert decision.expectedHoldingPeriod == "short"
    assert len(decision.rationale) >= 1


# ---------------------------------------------------------------------------
# Test 2: missing required field `action` is REJECTED
# ---------------------------------------------------------------------------


def test_missing_action_rejected():
    data = load("missing_action.json")
    with pytest.raises(ValidationError) as exc_info:
        Decision.model_validate(data)
    errors = exc_info.value.errors()
    fields_with_errors = {e["loc"][0] for e in errors}
    assert "action" in fields_with_errors, f"Expected 'action' in errors, got: {fields_with_errors}"


# ---------------------------------------------------------------------------
# Test 3: extra field accepted, stored in model_extra, NOT in model_fields
# ---------------------------------------------------------------------------


def test_extra_field_accepted_but_ignored_for_execution():
    data = load("extra_field.json")
    decision = Decision.model_validate(data)

    # Extra field is preserved in model_extra (stored verbatim in journal per D-08)
    assert "moonPhase" in decision.model_extra, "Extra field must be stored in model_extra"
    assert decision.model_extra["moonPhase"] == "waxing"

    # Extra field is NOT a declared execution field
    assert "moonPhase" not in Decision.model_fields, (
        "moonPhase must NOT be a declared field (ignored for execution)"
    )

    # Core decision still parses correctly
    assert decision.action == "open"
    assert decision.market == "BTC"


# ---------------------------------------------------------------------------
# Test 4: strict_provider_schema() derives provider-strict variant
# ---------------------------------------------------------------------------


def test_strict_provider_schema_derives_correctly():
    strict = strict_provider_schema()

    # Must have additionalProperties:false
    assert strict["additionalProperties"] is False, (
        "Provider-strict schema must have additionalProperties:false"
    )

    # All property keys must appear in required
    all_property_keys = set(strict["properties"].keys())
    required_set = set(strict["required"])
    assert all_property_keys == required_set, (
        f"required must equal all property keys.\n"
        f"  Properties: {sorted(all_property_keys)}\n"
        f"  Required:   {sorted(required_set)}"
    )

    # Canonical schema (additionalProperties:true) must be unchanged
    from orchestrator.schema import CANONICAL_SCHEMA

    assert CANONICAL_SCHEMA["additionalProperties"] is True, (
        "strict_provider_schema() must not mutate CANONICAL_SCHEMA"
    )


# ---------------------------------------------------------------------------
# CR-02 regression tests — cross-field validator: open/adjust require market+side
# ---------------------------------------------------------------------------


def test_open_missing_market_rejected():
    """open action with no market must raise ValidationError (CR-02)."""
    data = load("open_missing_market.json")
    assert data.get("action") == "open"
    assert "market" not in data or data.get("market") is None

    with pytest.raises(ValidationError) as exc_info:
        Decision.model_validate(data)

    error_msgs = " ".join(e["msg"] for e in exc_info.value.errors())
    assert "market" in error_msgs, (
        f"Expected 'market' mentioned in error messages, got: {error_msgs}"
    )


def test_open_missing_side_rejected():
    """
    open action with no side must raise ValidationError (CR-02).

    CRITICAL: Before the fix, None side silently fell into the else branch and
    opened a SHORT — a directional-integrity bug for the fairness seam.
    This test explicitly asserts that behaviour is GONE.
    """
    data = load("open_missing_side.json")
    assert data.get("action") == "open"
    assert data.get("market") == "ETH", "Fixture must have a market to isolate the side check"
    assert "side" not in data or data.get("side") is None

    with pytest.raises(ValidationError) as exc_info:
        Decision.model_validate(data)

    error_msgs = " ".join(e["msg"] for e in exc_info.value.errors())
    assert "side" in error_msgs, (
        f"Expected 'side' mentioned in error messages, got: {error_msgs}\n"
        "A None side on an open must be REJECTED — it must NOT silently open a short."
    )


def test_adjust_missing_market_rejected():
    """adjust action with no market must raise ValidationError (CR-02)."""
    data = load("adjust_missing_market.json")
    assert data.get("action") == "adjust"
    assert "market" not in data or data.get("market") is None

    with pytest.raises(ValidationError) as exc_info:
        Decision.model_validate(data)

    error_msgs = " ".join(e["msg"] for e in exc_info.value.errors())
    assert "market" in error_msgs, (
        f"Expected 'market' mentioned in error messages, got: {error_msgs}"
    )


def test_close_without_market_side_is_valid():
    """close may legitimately omit market and side — must still validate (CR-02 non-regression)."""
    data = load("close_no_market_no_side.json")
    assert data.get("action") == "close"

    decision = Decision.model_validate(data)
    assert decision.action == "close"
    assert decision.market is None
    assert decision.side is None


def test_hold_without_market_side_is_valid():
    """hold may legitimately omit market and side — must still validate (CR-02 non-regression)."""
    data = load("hold_no_market_no_side.json")
    assert data.get("action") == "hold"

    decision = Decision.model_validate(data)
    assert decision.action == "hold"
    assert decision.market is None
    assert decision.side is None


# ---------------------------------------------------------------------------
# CR-02 harness-level regression — null-side open routes to ORCH-05 malformed path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_cycle_null_side_open_routes_to_malformed():
    """
    run_cycle with a null-side open fixture must return status='malformed'
    via the ORCH-05 gate (Decision.model_validate raises ValidationError →
    caught → ModelStatus{malformed}) and must NOT crash or attempt a trade.

    Uses the cr02_test model fixtures (cycle 1 = open with market, no side).
    No Postgres/Redis/Anvil required — db=None, redis=None, web3=None.
    web3 and mock_perps are None because run_cycle must return before reaching
    Step 5 (MockPerps call) when the schema validation fails at Step 3.
    """
    from orchestrator.mock_harness import run_cycle

    result = await run_cycle(
        web3=None,
        mock_perps=None,
        vault="0x000000000000000000000000000000000000dEaD",
        model="cr02_test",
        cycle=1,
        db=None,
        redis=None,
    )

    assert result["status"] == "malformed", (
        f"Expected status='malformed' for null-side open fixture, got: {result}"
    )
    assert "reason" in result, "Malformed result must include a reason string"
    reason = result["reason"]
    assert "side" in reason.lower() or "ValidationError" in reason, (
        f"Reason should mention 'side' or 'ValidationError', got: {reason}"
    )

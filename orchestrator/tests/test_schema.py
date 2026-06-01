"""
Validation gate for the frozen decision schema (IFACE-03).

Tests:
  1. good.json        → validates and parses cleanly
  2. missing_action   → rejected with ValidationError (required field absent)
  3. extra_field      → accepted; extra stored in model_extra, NOT in model_fields
  4. strict_provider_schema() → additionalProperties:false, all property keys in required
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

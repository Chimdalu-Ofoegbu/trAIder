"""SC-4 regression lock: malformed decision fixture fails Decision.model_validate().

Plan 02-02, Task 3.

This test locks the 0002_malformed.json fixture as a regression seed:
  - The fixture is MISSING the required `action` field.
  - Decision.model_validate() MUST raise pydantic.ValidationError.

This is the authoritative SC-4 gate. The live malformed path (Plan 05) must remain
consistent with this test: any response that fails this validation is routed to the
ORCH-05 malformed path (no trade, no journal, malformed_streak++).

References:
  - 02-VALIDATION.md SC-4: pytest tests/unit/test_malformed_no_trade.py
    tests/unit/test_schema_fixtures.py -x
  - orchestrator/tests/fixtures/decisions/claude/0002_malformed.json
    (missing 'action' field — regression seed, do NOT modify)
  - orchestrator/src/orchestrator/schema.py Decision model
"""

from __future__ import annotations

import json
from pathlib import Path

import pydantic
import pytest

from orchestrator.schema import Decision

# ---------------------------------------------------------------------------
# Fixture path — resolved relative to this test file (no hardcoded absolute path)
# ---------------------------------------------------------------------------

FIXTURE = Path(__file__).parent.parent / "fixtures" / "decisions" / "claude" / "0002_malformed.json"


# ---------------------------------------------------------------------------
# SC-4 regression test — malformed fixture MUST fail validation
# ---------------------------------------------------------------------------


def test_malformed_fixture_fails_decision_validation() -> None:
    """SC-4: 0002_malformed.json is missing 'action' and MUST raise pydantic.ValidationError.

    This test is the regression lock for the malformed-response path (ORCH-05).
    If this test fails, the fixture was accidentally fixed and the malformed path
    would have no test coverage.

    Do NOT modify 0002_malformed.json. If this test fails, investigate why the
    fixture now passes validation (it should not).
    """
    assert FIXTURE.exists(), f"Malformed fixture not found at {FIXTURE}"
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))

    with pytest.raises(pydantic.ValidationError):
        Decision.model_validate(raw)


# ---------------------------------------------------------------------------
# Positive control — valid fixture (0001.json) passes validation
# ---------------------------------------------------------------------------

_VALID_FIXTURE = Path(__file__).parent.parent / "fixtures" / "decisions" / "claude" / "0001.json"


def test_valid_fixture_passes_decision_validation() -> None:
    """Positive control: 0001.json is a well-formed decision and MUST pass validation.

    Ensures that the schema itself is not broken in a way that rejects all inputs.
    If this test fails, Decision.model_validate() is broken (not just the fixture).
    """
    if not _VALID_FIXTURE.exists():
        pytest.skip(f"No valid fixture found at {_VALID_FIXTURE} — skipping positive control")

    raw = json.loads(_VALID_FIXTURE.read_text(encoding="utf-8"))
    decision = Decision.model_validate(raw)
    # Minimal sanity — the fixture must have a valid action
    assert decision.action in ("open", "close", "hold", "adjust")

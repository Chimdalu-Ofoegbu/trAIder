"""
orchestrator.tests.integration.test_journal_gating — Journal publish-only-on-OrderExecuted (JOURNAL-01).

STUB — Wave 0 scaffold. All tests are skipped pending Wave 2 (03-06).

Wave 2 will implement the integration test:
  - Run a full mock trade cycle (submit openLong → executeOrder).
  - Assert that publish_journal_entry is called EXACTLY ONCE per OrderExecuted event.
  - Assert that no journal entry is published on the initial openLong submission
    (pre-execution receipt — front-running mitigation per spec §9.1).
  - Assert that if executeOrder emits PositionLiquidated (not OrderExecuted), NO journal
    entry is published.

Requires: anvil (local), MockPerps deployed, PINATA_JWT (or mock pin function).
"""

import pytest


def test_journal_published_only_on_order_executed() -> None:
    """Journal entry is published on OrderExecuted, never on the submission receipt."""
    pytest.skip("Wave 2: 03-06")

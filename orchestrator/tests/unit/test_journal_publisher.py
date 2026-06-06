"""
orchestrator.tests.unit.test_journal_publisher — Unit tests for JournalPublisher (JOURNAL-01).

STUB — Wave 0 scaffold. All tests are skipped pending Wave 2 (03-06).

Wave 2 will implement the full state-machine test covering:
  1. Pinata pin succeeds → Storacha backup succeeds → CIDs match → sign → recordJournal.
  2. Pinata pin fails → alert dispatched → no on-chain call.
  3. Dual-pin CID mismatch → ValueError raised → no on-chain call.
  4. ecrecover gate: sign_journal_entry produces valid EIP-191 signature recoverable to
     OPERATOR_JOURNAL_KEY address.
"""

import pytest


def test_journal_publisher_state_machine() -> None:
    """Full publish pipeline state machine: pin → sign → attest → persist."""
    pytest.skip("Wave 2: 03-06")

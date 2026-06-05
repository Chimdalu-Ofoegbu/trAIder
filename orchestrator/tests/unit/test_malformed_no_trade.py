"""SC-4 stub: malformed response produces no trade, no journal entry (ORCH-05 / JOURNAL-04).

Plan 02/05 (malformed path) + Plan 02/02 (schema-fixtures lock) will fill this test.

When complete, this test verifies:
  - A cycle where call_claude returns a dict that fails Decision.model_validate()
    results in status='malformed'.
  - No 'trades' row is written to Postgres (ORCH-05).
  - No trade-execution journal payload is produced (JOURNAL-04).
  - A model_status_log row with status='malformed' IS written.
  - The Redis ModelStatus envelope with status='malformed' IS published.
"""

from __future__ import annotations

import pytest


@pytest.mark.xfail(reason="Plan 02/05 malformed path + 02/02 schema-fixtures", strict=False)
async def test_malformed_response_no_trade_no_journal() -> None:
    """Malformed LLM response → no trade, no journal; model_status 'malformed' (ORCH-05).

    Implementation notes (for Plan 02/05 / 02/02):
      - Mock call_claude to return a dict that fails Decision.model_validate()
        (e.g., missing required 'action' field, or extra-invalid structure).
      - Run one cycle through the driver (db=None, redis=None for unit isolation).
      - Assert result['status'] == 'malformed'.
      - Assert 'order_key' not in result and 'tx_hash' not in result.
      - With a real db mock: assert no record_trade call was made.
      - With a real redis mock: assert ModelStatus envelope published with
        status='malformed'.
    """
    # Lazy import — driver does not exist yet; import inside test body so the
    # module collects cleanly even when the import would fail at module top.
    from unittest.mock import AsyncMock, patch  # noqa: F401 (used by 02/05 fill-in)

    # Placeholder assertion — replaced by Plan 02/05.
    raise NotImplementedError("Plan 02/05 will implement this test body")

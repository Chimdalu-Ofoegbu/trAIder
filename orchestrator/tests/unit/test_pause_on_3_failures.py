"""SC-3 stub: pause on 3 consecutive API failures (ORCH-06).

Plan 02/05 will implement the FailureTracker + driver and fill this test.

When complete, this test verifies:
  - After 3 consecutive anthropic.APITimeoutError (or other transient errors),
    the driver writes a model_status_log row with status='paused'.
  - api_failure_streak resets to 0 on the next successful cycle.
  - The loop continues (does not crash) after the pause condition is cleared.
"""

from __future__ import annotations

import pytest


@pytest.mark.xfail(reason="Plan 02/05 implement adapter+driver", strict=False)
async def test_pause_on_3_consecutive_api_failures() -> None:
    """After 3 consecutive API failures the driver pauses the model (ORCH-06).

    Implementation notes (for Plan 02/05):
      - Mock call_claude to raise anthropic.APITimeoutError three times.
      - Run 3 cycles through the driver.
      - Assert model_status_log has a row with status='paused' and
        consecutive_failures=3.
      - Assert loop state allows recovery on the 4th cycle (streak resets).
    """
    # Lazy import — driver does not exist yet; import inside test body so the
    # module collects cleanly even when the import would fail at module top.
    from unittest.mock import AsyncMock, patch  # noqa: F401 (used by 02/05 fill-in)

    # Placeholder assertion — replaced by Plan 02/05.
    raise NotImplementedError("Plan 02/05 will implement this test body")

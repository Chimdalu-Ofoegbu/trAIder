"""Wave-0 RED stub — orchestrator.loop.supervisor (D-12 multi-model supervisor).

Implemented in plan 04-05 (supervisor + nonce manager).

D-12 requirement: each model loop runs as an independent asyncio.Task with its own
exception boundary. A crashed model task is restarted with exponential backoff. After
exceeding the auto-restart threshold, the model is set to ModelStatus.AUTO_PAUSED
and an alert is sent. The other two model tasks continue unaffected.

The supervisor is an extension of the existing run_session.py pattern, using
asyncio.gather(return_exceptions=True) so child crashes do NOT propagate to the
supervisor level.
"""

from __future__ import annotations

import pytest

# Guard: skip if supervisor not yet implemented.
pytest.importorskip(
    "orchestrator.loop.supervisor",
    reason="Wave 0 stub — supervisor implemented in 04-05",
)


class TestSupervisorAutoRestart:
    """D-12 supervisor auto-restart with backoff."""

    async def test_supervisor_autorestarts_crashed_model_with_backoff(self) -> None:
        """Supervisor detects a crashed model task and restarts it with exponential backoff.

        When a model task raises an unexpected exception:
          1. Supervisor catches the exception via return_exceptions=True
          2. Increments the crash counter for that model
          3. Applies exponential backoff delay before restart
          4. Restarts the task
          5. The OTHER two model tasks are NOT interrupted

        After crash count exceeds threshold:
          - ModelStatus set to AUTO_PAUSED
          - alert sent (CRITICAL severity)

        Acceptance criteria (from 04-VALIDATION.md):
          pytest tests/unit/test_supervisor.py -x
          → test collects and runs (not 0 tests)

        Implemented in: 04-05.
        """
        pytest.skip("Wave 0 stub — 04-05 implements")

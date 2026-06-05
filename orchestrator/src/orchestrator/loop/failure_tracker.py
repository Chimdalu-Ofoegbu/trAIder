"""
orchestrator.loop.failure_tracker — Two-counter pause/recover state machine (D-15/D-16/D-17).

Tracks two independent failure streaks:

  api_failure_streak : incremented on classify_exception == "api_failure"
                       → pause at API_FAILURE_PAUSE_THRESHOLD (3, D-15)
  malformed_streak   : incremented on extract_tool_input None OR validate_decision None
                       → pause at MALFORMED_PAUSE_THRESHOLD (5, D-17)

Either counter resets to 0 on a successful valid parse (D-17 reset-on-valid-parse rule).

Design notes:
  - Pure state machine — NO db/web3/SDK imports (trivially unit-testable).
  - The driver reads should_pause() each cycle and backs off to
    SessionConfig.paused_poll_interval_seconds when paused (D-16 slow-poll).
  - record_success() returns True when the call recovers from a paused state
    (auto-flip to active signal for the driver — D-16).
  - consecutive() returns max(api_failure_streak, malformed_streak) which is what
    record_model_status.consecutive_failures expects.
"""

from __future__ import annotations

import dataclasses

# ---------------------------------------------------------------------------
# Pause thresholds (D-15 / D-17)
# ---------------------------------------------------------------------------

API_FAILURE_PAUSE_THRESHOLD: int = 3  # D-15: three api_failures → pause
MALFORMED_PAUSE_THRESHOLD: int = 5  # D-17: five malformed → pause


# ---------------------------------------------------------------------------
# FailureTracker
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class FailureTracker:
    """Two-counter pause state machine for the trading loop driver.

    Attributes
    ----------
    api_failure_streak:
        Running count of consecutive API failures (APITimeoutError, RateLimitError,
        InternalServerError, APIConnectionError).  Resets on success.
    malformed_streak:
        Running count of consecutive malformed responses (no ToolUseBlock OR
        Decision.model_validate failure).  Resets on success.
    paused:
        True when either streak has reached its threshold.

    Usage
    -----
    ::

        tracker = FailureTracker()
        ...
        except APIException as exc:
            tracker.record_api_failure()
            if tracker.should_pause():
                await record_model_status(..., status="paused", ...)
        ...
        raw = extract_tool_input(response)
        if raw is None:
            tracker.record_malformed()
            status = "paused" if tracker.should_pause() else "malformed"
            await record_model_status(..., status=status, ...)
        ...
        decision = validate_decision(raw)
        if decision is None:
            tracker.record_malformed()
            ...
        else:
            recovered = tracker.record_success()  # resets both streaks (D-17)
            if recovered:
                await record_model_status(..., status="active", ...)
    """

    api_failure_streak: int = 0
    malformed_streak: int = 0
    paused: bool = False

    def record_api_failure(self) -> None:
        """Increment api_failure_streak.  Sets paused=True at threshold (D-15)."""
        self.api_failure_streak += 1
        if self.api_failure_streak >= API_FAILURE_PAUSE_THRESHOLD:
            self.paused = True

    def record_malformed(self) -> None:
        """Increment malformed_streak.  Sets paused=True at threshold (D-17)."""
        self.malformed_streak += 1
        if self.malformed_streak >= MALFORMED_PAUSE_THRESHOLD:
            self.paused = True

    def record_success(self) -> bool:
        """Reset BOTH streaks to 0 (D-17 reset-on-valid-parse).

        Returns True if this success RECOVERED from a paused state (auto-flip to
        active, D-16); False otherwise.  The driver should publish a ModelStatus
        event with status='active' when this returns True.
        """
        recovered = self.paused
        self.api_failure_streak = 0
        self.malformed_streak = 0
        self.paused = False
        return recovered

    def should_pause(self) -> bool:
        """Return True when either streak has reached its pause threshold."""
        return self.paused

    def consecutive(self) -> int:
        """Return the current consecutive failure count for record_model_status.

        Uses max(api_failure_streak, malformed_streak) — whichever counter is
        higher reflects the severity the operator is most interested in.
        """
        return max(self.api_failure_streak, self.malformed_streak)

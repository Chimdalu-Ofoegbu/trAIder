"""Regression tests for the session-duration parameterization fix (03-08 defect).

Root cause: system.md hardcoded "72 hours" in both the role description and the
time-remaining line, while {{time_remaining}} reflected the ACTUAL configured
session duration.  For any non-72h session (e.g. 30-min gate, 3-4h demo) the
model saw a contradiction like "2m 35s of 72 hours left" and rationally HOLDed
every cycle.

Fix: {{session_duration}} placeholder replaces every hardcoded "72" occurrence in
system.md.  format_session_duration() derives the string from
SessionConfig.session_duration_seconds — the SAME value used by
format_time_remaining() — so the two are always consistent.

Test contracts (success criteria from objective):
  T-1  system.md template contains no hardcoded "72" (grep assertion).
  T-2  format_session_duration(259200) == "72 hours"  (72h config still works).
  T-3  format_session_duration(1800)   contains "30" and "minute" (30-min gate).
  T-4  render_prompt with session_duration_seconds=1800 contains "30 minutes"
       and does NOT contain "72 hours".
  T-5  render_prompt with session_duration_seconds=259200 contains "72 hours".
  T-6  render_prompt with session_duration_seconds=155 does NOT contain "72 hours".
  T-7  Internal consistency: for any total_seconds T, every rendered prompt's
       "of X left" label (session_duration) equals format_session_duration(T).
  T-8  format_session_duration is consistent with format_time_remaining's basis
       (both use the same total_seconds; the total is the ceiling of
       format_time_remaining at elapsed=0).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator.loop.market_state import render_prompt
from orchestrator.loop.session import format_session_duration, format_time_remaining

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SYSTEM_MD_PATH = Path(__file__).parent.parent.parent / "prompts" / "system.md"

_DUMMY_MARKET_TABLE = (
    "| Asset | Mark | Funding (ann.) | 24h % |\n"
    "|-------|------|---------------|-------|\n"
    "| ETH | $3,000.00 | +0.0001 | +1.20% |\n"
    "| BTC | $60,000.00 | -0.0002 | -0.50% |\n"
    "| SOL | $150.00 | +0.0000 | +0.30% |"
)


def _rendered(total_seconds: int, elapsed: float = 0.0) -> str:
    """Helper: render the prompt for a given session length and elapsed time."""
    return render_prompt(
        nav_table="| Vault | NAV |\n|---|---|\n| mock | $10,000 |",
        time_remaining=format_time_remaining(elapsed, total_seconds),
        positions_table="No open positions.",
        available_usdc=10_000.0,
        recent_decisions="No decisions yet.",
        market_table=_DUMMY_MARKET_TABLE,
        session_duration=format_session_duration(total_seconds),
    )


# ---------------------------------------------------------------------------
# T-1: no hardcoded "72" in template
# ---------------------------------------------------------------------------


def test_system_md_has_no_hardcoded_72() -> None:
    """T-1: system.md must not contain a hardcoded '72 hours' literal.

    The root-cause defect was "72 hours" appearing twice in the template as a
    hardcoded constant.  After the fix both occurrences are replaced with
    {{session_duration}}.  We specifically check for "72 hours" (the defect
    pattern) rather than bare "72", because "72" legitimately appears in
    decision-code labels (e.g. D-72) and Jinja comments.
    """
    text = _SYSTEM_MD_PATH.read_text(encoding="utf-8")
    assert "72 hours" not in text, (
        "system.md still contains hardcoded '72 hours'. Replace with {{session_duration}}."
    )


# ---------------------------------------------------------------------------
# T-2: format_session_duration(259200) → "72 hours"
# ---------------------------------------------------------------------------


def test_format_session_duration_72h() -> None:
    """T-2: 259200 seconds (72 h) must produce '72 hours'."""
    result = format_session_duration(259200)
    assert result == "72 hours", f"Expected '72 hours', got {result!r}"


# ---------------------------------------------------------------------------
# T-3: format_session_duration(1800) contains "30" and "minute"
# ---------------------------------------------------------------------------


def test_format_session_duration_30min() -> None:
    """T-3: 1800 seconds (30 min) must mention '30' and 'minute'."""
    result = format_session_duration(1800)
    assert "30" in result, f"'30' not found in {result!r}"
    assert "minute" in result, f"'minute' not found in {result!r}"


# ---------------------------------------------------------------------------
# T-4: 30-min prompt contains "30 minutes", NOT "72 hours"
# ---------------------------------------------------------------------------


def test_rendered_prompt_30min_contains_30_minutes() -> None:
    """T-4a: Prompt rendered for a 30-minute session must contain '30 minutes'."""
    prompt = _rendered(1800)
    assert "30 minutes" in prompt, (
        f"'30 minutes' not found in rendered prompt for 1800s session.\n"
        f"First 600 chars:\n{prompt[:600]}"
    )


def test_rendered_prompt_30min_has_no_72_hours() -> None:
    """T-4b: Prompt rendered for a 30-minute session must NOT contain '72 hours'."""
    prompt = _rendered(1800)
    assert "72 hours" not in prompt, (
        "Rendered 30-min prompt still contains '72 hours' — "
        "session_duration parameterization is broken.\n"
        f"First 600 chars:\n{prompt[:600]}"
    )


# ---------------------------------------------------------------------------
# T-5: 72h prompt contains "72 hours"
# ---------------------------------------------------------------------------


def test_rendered_prompt_72h_contains_72_hours() -> None:
    """T-5: Prompt rendered for a 72-hour session must contain '72 hours'."""
    prompt = _rendered(259200)
    assert "72 hours" in prompt, (
        "'72 hours' not found in rendered 72h prompt — "
        "format_session_duration(259200) must return '72 hours'.\n"
        f"First 600 chars:\n{prompt[:600]}"
    )


# ---------------------------------------------------------------------------
# T-6: short-duration prompt (155s) does NOT contain "72 hours"
# ---------------------------------------------------------------------------


def test_rendered_prompt_155s_has_no_72_hours() -> None:
    """T-6: Prompt rendered for a 155-second session must NOT contain '72 hours'.

    155 seconds is the diagnostic scenario captured live ('2m35s of 72 hours
    left') that caused the model to always HOLD.  After the fix this must be
    gone.
    """
    prompt = _rendered(155)
    assert "72 hours" not in prompt, (
        "Rendered 155s prompt still contains '72 hours' — "
        "root-cause defect is not fixed.\n"
        f"First 600 chars:\n{prompt[:600]}"
    )


# ---------------------------------------------------------------------------
# T-7: internal consistency — "of X left" label matches session_duration
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "total_seconds",
    [155, 900, 1800, 3600, 10800, 14400, 259200],
)
def test_prompt_session_duration_matches_format_session_duration(
    total_seconds: int,
) -> None:
    """T-7: The session_duration rendered in the prompt equals format_session_duration(total).

    Verifies internal consistency: the 'of X left' label in the prompt is always
    derived from the same total_seconds as the time_remaining countdown, never a
    hardcoded constant.
    """
    expected_duration = format_session_duration(total_seconds)
    prompt = _rendered(total_seconds, elapsed=0.0)
    assert expected_duration in prompt, (
        f"Expected session_duration '{expected_duration}' not found in prompt "
        f"for total_seconds={total_seconds}.\n"
        f"First 600 chars:\n{prompt[:600]}"
    )


# ---------------------------------------------------------------------------
# T-8: format_session_duration consistent with format_time_remaining at elapsed=0
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "total_seconds,expected_duration",
    [
        (259200, "72 hours"),
        (10800, "3 hours"),
        (3600, "1 hour"),
        (1800, "30 minutes"),
        (60, "1 minute"),
        (155, "2 minutes 35 seconds"),
        (1, "1 second"),
    ],
)
def test_format_session_duration_values(total_seconds: int, expected_duration: str) -> None:
    """T-8: format_session_duration produces the expected human string."""
    result = format_session_duration(total_seconds)
    assert result == expected_duration, (
        f"format_session_duration({total_seconds}) → {result!r}, expected {expected_duration!r}"
    )

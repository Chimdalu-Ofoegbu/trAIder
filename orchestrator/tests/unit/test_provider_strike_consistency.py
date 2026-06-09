"""Wave-0 RED stub — D-17 provider strike-counter consistency.

Implemented in plan 04-04 (provider adapters).

D-17 requirement: all three adapters (anthropic, openai, gemini) must map the same
transient-error categories to "api_failure" (increments api_failure_streak; pause@3).
A malformed or missing tool input maps to "malformed" (increments malformed_streak; pause@5).

The test asserts:
  - classify_exception(APITimeoutError-equivalent) == "api_failure" for all three adapters
  - classify_exception(RateLimitError-equivalent) == "api_failure" for all three adapters
  - classify_exception(unknown error) == "unknown" for all three adapters
  - The same exception category produces the same counter increment regardless of provider
"""

from __future__ import annotations

import pytest

# Guard: skip if adapters not yet implemented.
pytest.importorskip(
    "orchestrator.providers.anthropic_adapter",
    reason="Wave 0 stub — adapters implemented in 04-04",
)


class TestProviderStrikeConsistency:
    """D-17 cross-adapter strike consistency."""

    def test_all_three_adapters_map_same_exceptions_to_api_failure(self) -> None:
        """All three provider adapters must return 'api_failure' for the same error categories.

        Acceptance criteria (from 04-VALIDATION.md):
          pytest tests/unit/test_provider_strike_consistency.py
          → test collects and runs (not 0 tests)

        Implemented in: 04-04.
        """
        pytest.skip("Wave 0 stub — 04-04 implements")

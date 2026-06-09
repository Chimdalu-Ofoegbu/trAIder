"""Wave-0 RED stub — orchestrator.providers.openai_adapter (D-13 GPT-5.5 adapter).

Implemented in plan 04-04 (provider adapters).

D-14: the ONLY module permitted to import openai is openai_adapter.py itself.
      This test file uses pytest.importorskip to skip cleanly if the module
      does not yet exist, ensuring collectability at all times.

D-17: extract_tool_input returning None increments malformed_streak (pause@5);
      classify_exception returning "api_failure" increments api_failure_streak (pause@3).
      The test_provider_strike_consistency.py file asserts cross-adapter consistency.
"""

from __future__ import annotations

import pytest

# Guard: skip the entire module if the adapter has not been implemented yet.
openai_adapter = pytest.importorskip(
    "orchestrator.providers.openai_adapter",
    reason="Wave 0 stub — openai_adapter implemented in 04-04",
)


class TestOpenAIAdapterExtractToolInput:
    """Tests for extract_tool_input (D-13 structured-output extraction)."""

    def test_extract_tool_input_returns_dict_on_valid_response(self) -> None:
        """extract_tool_input returns a dict when GPT responds with a valid tool call.

        Acceptance criteria (from 04-VALIDATION.md):
          pytest tests/unit/test_openai_adapter.py -x
          → test collects and runs (not 0 tests)

        Implemented in: 04-04.
        """
        pytest.skip("Wave 0 stub — 04-04 implements")

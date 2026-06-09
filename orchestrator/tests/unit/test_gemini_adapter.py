"""Wave-0 RED stub — orchestrator.providers.gemini_adapter (D-13 Gemini adapter).

Implemented in plan 04-04 (provider adapters).

D-14: the ONLY module permitted to import google.genai is gemini_adapter.py itself.

Probe 3 verdict (04-PROBE-RESULTS.md):
  - Async call path: client.aio.models.generate_content(...)
  - Config field for raw dict schema: response_json_schema (NOT response_schema)
  - response_mime_type="application/json" required when response_json_schema is set
  - seed and temperature are supported

D-17: same counter paths as anthropic_adapter and openai_adapter.
"""

from __future__ import annotations

import pytest

# Guard: skip the entire module if the adapter has not been implemented yet.
gemini_adapter = pytest.importorskip(
    "orchestrator.providers.gemini_adapter",
    reason="Wave 0 stub — gemini_adapter implemented in 04-04",
)


class TestGeminiAdapterExtractToolInput:
    """Tests for extract_tool_input (D-13 JSON text extraction)."""

    def test_extract_tool_input_returns_dict_on_valid_response(self) -> None:
        """extract_tool_input returns a dict when Gemini responds with valid JSON text.

        Gemini uses response_mime_type + response_json_schema → raw JSON text response.
        extract_tool_input parses response.text and returns the parsed dict.

        Acceptance criteria (from 04-VALIDATION.md):
          pytest tests/unit/test_gemini_adapter.py -x
          → test collects and runs (not 0 tests)

        Implemented in: 04-04.
        """
        pytest.skip("Wave 0 stub — 04-04 implements")

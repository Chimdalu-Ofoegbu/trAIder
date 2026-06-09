"""Unit tests for orchestrator.providers.gemini_adapter (Plan 04-04, Task 2).

Tests cover:
  - extract_tool_input: returns dict when response.text is valid JSON, None otherwise
  - classify_exception: name-matched transient errors → "api_failure"; generic → "unknown"
  - validate_decision: returns Decision for valid dict, None for bad dict

Probe 3 verdict (04-PROBE-RESULTS.md):
  - Async call path: client.aio.models.generate_content(...)
  - Config field for raw dict schema: response_json_schema (NOT response_schema)
  - response_mime_type="application/json" required when response_json_schema is set
  - seed and temperature are supported config fields

D-14: this test file does NOT import google.genai at module level (allowed for adapter
      module itself). Tests mock the client without importing the SDK directly.
D-17: same counter paths as anthropic_adapter and openai_adapter.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

# Guard: skip the entire module if the adapter has not been implemented yet.
gemini_adapter = pytest.importorskip(
    "orchestrator.providers.gemini_adapter",
    reason="Wave 0 stub — gemini_adapter implemented in 04-04",
)


# ---------------------------------------------------------------------------
# Helpers — fake Gemini SDK response objects
# ---------------------------------------------------------------------------


def _fake_response(text: str | None) -> Any:
    """Build a fake Gemini GenerateContentResponse with a .text attribute."""
    resp = MagicMock()
    resp.text = text
    return resp


# ---------------------------------------------------------------------------
# Test: extract_tool_input
# ---------------------------------------------------------------------------


class TestGeminiAdapterExtractToolInput:
    """Tests for extract_tool_input (D-13 JSON text extraction from Gemini)."""

    def test_extract_tool_input_returns_dict_on_valid_response(self) -> None:
        """extract_tool_input returns a dict when Gemini responds with valid JSON text."""
        from orchestrator.providers.gemini_adapter import extract_tool_input

        payload = {
            "action": "hold",
            "sizeUsd": 0,
            "leverage": 1,
            "rationale": "flat market",
            "confidence": 0.5,
            "expectedHoldingPeriod": "short",
        }
        import json

        resp = _fake_response(json.dumps(payload))
        result = extract_tool_input(resp)
        assert result == payload

    def test_extract_tool_input_returns_none_on_empty_text(self) -> None:
        """extract_tool_input returns None when response.text is None or empty."""
        from orchestrator.providers.gemini_adapter import extract_tool_input

        # None text
        resp_none = _fake_response(None)
        assert extract_tool_input(resp_none) is None

        # Empty string
        resp_empty = _fake_response("")
        assert extract_tool_input(resp_empty) is None

    def test_extract_tool_input_returns_none_on_invalid_json(self) -> None:
        """extract_tool_input returns None when response.text is not valid JSON."""
        from orchestrator.providers.gemini_adapter import extract_tool_input

        resp = _fake_response("not valid json {{{")
        result = extract_tool_input(resp)
        assert result is None

    def test_extract_tool_input_returns_none_on_no_text_attr(self) -> None:
        """extract_tool_input returns None when response has no .text attribute."""
        from orchestrator.providers.gemini_adapter import extract_tool_input

        # A mock with spec that doesn't include 'text'
        resp = MagicMock(spec=[])
        result = extract_tool_input(resp)
        assert result is None


# ---------------------------------------------------------------------------
# Test: classify_exception
# ---------------------------------------------------------------------------


class TestGeminiAdapterClassifyException:
    """Tests for classify_exception (D-17 name-matched error counter mapping)."""

    def test_classify_exception_maps_transient_to_api_failure(self) -> None:
        """Name-matched transient error types map to 'api_failure'.

        Matching spec (04-04 plan action): type.__name__ contains any of
        "Timeout", "RateLimit", "ServerError", "Connection".
        Classes whose names don't include those substrings map to "unknown".
        """
        from orchestrator.providers.gemini_adapter import classify_exception

        # Direct name-match tests per plan spec:
        # "Timeout", "RateLimit", "ServerError", "Connection"
        class SomeTimeoutError(Exception):
            pass

        class SomeRateLimitError(Exception):
            pass

        class SomeServerError(Exception):
            pass

        class SomeConnectionError(Exception):
            pass

        class APITimeoutError(Exception):
            pass

        class InternalServerError(Exception):
            pass

        assert classify_exception(SomeTimeoutError()) == "api_failure"
        assert classify_exception(SomeRateLimitError()) == "api_failure"
        assert classify_exception(SomeServerError()) == "api_failure"
        assert classify_exception(SomeConnectionError()) == "api_failure"
        assert classify_exception(APITimeoutError()) == "api_failure"
        assert classify_exception(InternalServerError()) == "api_failure"

        # Names without matching substrings map to "unknown"
        class DeadlineExceededError(Exception):
            pass

        class ResourceExhaustedError(Exception):
            pass

        assert classify_exception(DeadlineExceededError()) == "unknown"
        assert classify_exception(ResourceExhaustedError()) == "unknown"

    def test_classify_exception_generic_returns_unknown(self) -> None:
        """Generic exceptions map to 'unknown'."""
        from orchestrator.providers.gemini_adapter import classify_exception

        assert classify_exception(ValueError("unexpected")) == "unknown"
        assert classify_exception(RuntimeError("boom")) == "unknown"
        assert classify_exception(Exception("generic")) == "unknown"


# ---------------------------------------------------------------------------
# Test: validate_decision
# ---------------------------------------------------------------------------


class TestGeminiAdapterValidateDecision:
    """Tests for validate_decision (D-17 malformed path via schema validation)."""

    def test_validate_decision_rejects_bad_schema(self) -> None:
        """validate_decision returns None for a dict missing required fields."""
        from orchestrator.providers.gemini_adapter import validate_decision

        # Missing 'action' field — should return None
        raw = {
            "market": "ETH",
            "side": "long",
            "sizeUsd": 2000,
            "leverage": 2.0,
            "rationale": "ETH breakout",
            "confidence": 0.7,
            "expectedHoldingPeriod": "medium",
        }
        result = validate_decision(raw)
        assert result is None

    def test_validate_decision_accepts_valid_hold(self) -> None:
        """validate_decision returns a Decision for a valid hold dict."""
        from orchestrator.providers.gemini_adapter import validate_decision

        raw = {
            "action": "hold",
            "sizeUsd": 0,
            "leverage": 1,
            "rationale": "market unclear",
            "confidence": 0.2,
            "expectedHoldingPeriod": "short",
        }
        result = validate_decision(raw)
        assert result is not None
        assert result.action == "hold"


# ---------------------------------------------------------------------------
# Test: call_gemini — verifies call shape
# ---------------------------------------------------------------------------


class TestCallGemini:
    """Tests for call_gemini (D-13 Gemini forced-JSON call shape via response_json_schema)."""

    @pytest.mark.asyncio
    async def test_call_gemini_passes_correct_config(self) -> None:
        """call_gemini must use aio.models.generate_content with correct config."""
        import json

        from orchestrator.providers.gemini_adapter import call_gemini
        from orchestrator.schema import strict_provider_schema

        payload = {
            "action": "hold",
            "sizeUsd": 0,
            "leverage": 1,
            "rationale": "flat",
            "confidence": 0.4,
            "expectedHoldingPeriod": "short",
        }
        fake_response = _fake_response(json.dumps(payload))

        # Mock the client's aio.models.generate_content path
        mock_aio_models = MagicMock()
        mock_aio_models.generate_content = AsyncMock(return_value=fake_response)

        mock_aio = MagicMock()
        mock_aio.models = mock_aio_models

        mock_client = MagicMock()
        mock_client.aio = mock_aio

        rendered_prompt = "What is the crypto market doing?"
        result = await call_gemini(rendered_prompt, client=mock_client)

        assert mock_aio_models.generate_content.called
        call_args = mock_aio_models.generate_content.call_args

        # Verify the config was passed (GenerateContentConfig object)
        # The config should have temperature=0, seed=42, response_json_schema
        config = call_args.kwargs.get("config") or (
            call_args.args[2] if len(call_args.args) > 2 else None
        )
        assert config is not None, "config must be passed to generate_content"

        # Check config fields (GenerateContentConfig is a pydantic-like object)
        assert config.temperature == 0.0
        assert config.seed == 42
        assert config.response_mime_type == "application/json"
        assert config.response_json_schema == strict_provider_schema()
        assert config.max_output_tokens == 1024

        # Verify result is the fake response
        assert result is fake_response

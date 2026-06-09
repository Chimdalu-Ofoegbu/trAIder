"""Unit tests for orchestrator.providers.openai_adapter (Plan 04-04, Task 1).

Tests cover:
  - extract_tool_input: returns dict on valid tool_calls response, None on non-tool-call
  - classify_exception: transient SDK errors → "api_failure"; generic → "unknown"
  - validate_decision: returns Decision for valid dict, None for bad dict

D-14: this test file imports openai for constructing fake exceptions (allowed in tests).
      The constraint is that only openai_adapter.py may import openai in src/.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

# Guard: skip the entire module if the adapter has not been implemented yet.
openai_adapter = pytest.importorskip(
    "orchestrator.providers.openai_adapter",
    reason="Wave 0 stub — openai_adapter implemented in 04-04",
)


# ---------------------------------------------------------------------------
# Helpers — fake OpenAI SDK response objects
# ---------------------------------------------------------------------------


def _fake_tool_call(name: str, arguments: str) -> Any:
    """Build a fake tool_call object with function.name and function.arguments."""
    func = MagicMock()
    func.name = name
    func.arguments = arguments

    call = MagicMock()
    call.function = func
    return call


def _fake_choice(finish_reason: str, tool_calls: list | None = None) -> Any:
    """Build a fake ChatCompletionChoice with finish_reason and optional tool_calls."""
    msg = MagicMock()
    msg.tool_calls = tool_calls  # None or list

    choice = MagicMock()
    choice.finish_reason = finish_reason
    choice.message = msg
    return choice


def _fake_response(choices: list) -> Any:
    """Build a fake ChatCompletion response with the given choices list."""
    resp = MagicMock()
    resp.choices = choices
    return resp


# ---------------------------------------------------------------------------
# Test: extract_tool_input
# ---------------------------------------------------------------------------


class TestOpenAIAdapterExtractToolInput:
    """Tests for extract_tool_input (D-13 structured-output extraction)."""

    def test_extract_tool_input_returns_dict_on_valid_response(self) -> None:
        """extract_tool_input returns a dict when GPT responds with a valid tool call."""
        from orchestrator.providers.openai_adapter import extract_tool_input

        payload = {
            "action": "hold",
            "sizeUsd": 0,
            "leverage": 1,
            "rationale": "flat market",
            "confidence": 0.5,
            "expectedHoldingPeriod": "short",
        }
        import json

        tool_call = _fake_tool_call("submit_decision", json.dumps(payload))
        choice = _fake_choice("tool_calls", tool_calls=[tool_call])
        resp = _fake_response([choice])

        result = extract_tool_input(resp)
        assert result == payload

    def test_extract_tool_input_returns_none_on_non_toolcall(self) -> None:
        """extract_tool_input returns None when finish_reason is 'stop' (not tool_calls)."""
        from orchestrator.providers.openai_adapter import extract_tool_input

        choice = _fake_choice("stop", tool_calls=None)
        resp = _fake_response([choice])

        result = extract_tool_input(resp)
        assert result is None

    def test_extract_tool_input_returns_none_on_wrong_function_name(self) -> None:
        """extract_tool_input returns None when tool call function name is wrong."""
        import json

        from orchestrator.providers.openai_adapter import extract_tool_input

        tool_call = _fake_tool_call("wrong_function", json.dumps({"action": "hold"}))
        choice = _fake_choice("tool_calls", tool_calls=[tool_call])
        resp = _fake_response([choice])

        result = extract_tool_input(resp)
        assert result is None

    def test_extract_tool_input_returns_none_on_empty_choices(self) -> None:
        """extract_tool_input returns None on empty choices list."""
        from orchestrator.providers.openai_adapter import extract_tool_input

        resp = _fake_response([])
        result = extract_tool_input(resp)
        assert result is None


# ---------------------------------------------------------------------------
# Test: classify_exception
# ---------------------------------------------------------------------------


class TestOpenAIAdapterClassifyException:
    """Tests for classify_exception (D-17 error counter mapping)."""

    def test_classify_exception_maps_transient_to_api_failure(self) -> None:
        """Transient OpenAI errors map to 'api_failure' (increments api_failure_streak)."""
        import openai

        from orchestrator.providers.openai_adapter import classify_exception

        # APITimeoutError
        exc_timeout = openai.APITimeoutError(request=MagicMock())
        assert classify_exception(exc_timeout) == "api_failure"

        # RateLimitError
        exc_rate = openai.RateLimitError(
            message="rate limited",
            response=MagicMock(status_code=429),
            body={},
        )
        assert classify_exception(exc_rate) == "api_failure"

        # InternalServerError
        exc_server = openai.InternalServerError(
            message="internal error",
            response=MagicMock(status_code=500),
            body={},
        )
        assert classify_exception(exc_server) == "api_failure"

        # APIConnectionError
        exc_conn = openai.APIConnectionError(request=MagicMock())
        assert classify_exception(exc_conn) == "api_failure"

    def test_classify_exception_generic_returns_unknown(self) -> None:
        """Generic exceptions map to 'unknown'."""
        from orchestrator.providers.openai_adapter import classify_exception

        assert classify_exception(ValueError("unexpected")) == "unknown"
        assert classify_exception(RuntimeError("boom")) == "unknown"


# ---------------------------------------------------------------------------
# Test: validate_decision
# ---------------------------------------------------------------------------


class TestOpenAIAdapterValidateDecision:
    """Tests for validate_decision (D-17 malformed path via schema validation)."""

    def test_validate_decision_rejects_bad_schema(self) -> None:
        """validate_decision returns None for a dict missing required fields."""
        from orchestrator.providers.openai_adapter import validate_decision

        # Missing 'action' field — should return None
        raw = {
            "market": "BTC",
            "side": "short",
            "sizeUsd": 3000,
            "leverage": 1.5,
            "rationale": "BTC showing weakness",
            "confidence": 0.6,
            "expectedHoldingPeriod": "medium",
        }
        result = validate_decision(raw)
        assert result is None

    def test_validate_decision_accepts_valid_hold(self) -> None:
        """validate_decision returns a Decision for a valid hold dict."""
        from orchestrator.providers.openai_adapter import validate_decision

        raw = {
            "action": "hold",
            "sizeUsd": 0,
            "leverage": 1,
            "rationale": "waiting for signal",
            "confidence": 0.3,
            "expectedHoldingPeriod": "short",
        }
        result = validate_decision(raw)
        assert result is not None
        assert result.action == "hold"


# ---------------------------------------------------------------------------
# Test: call_gpt — verifies call shape
# ---------------------------------------------------------------------------


class TestCallGpt:
    """Tests for call_gpt (D-13 GPT-5.5 forced-tool call shape)."""

    @pytest.mark.asyncio
    async def test_call_gpt_passes_correct_kwargs(self) -> None:
        """call_gpt must call chat.completions.create with correct shape."""
        import json

        from orchestrator.providers.openai_adapter import call_gpt
        from orchestrator.schema import strict_provider_schema

        payload = {
            "action": "hold",
            "sizeUsd": 0,
            "leverage": 1,
            "rationale": "flat",
            "confidence": 0.4,
            "expectedHoldingPeriod": "short",
        }
        tool_call = _fake_tool_call("submit_decision", json.dumps(payload))
        choice = _fake_choice("tool_calls", tool_calls=[tool_call])
        fake_response = _fake_response([choice])

        mock_client = MagicMock()
        mock_client.chat = MagicMock()
        mock_client.chat.completions = MagicMock()
        mock_client.chat.completions.create = AsyncMock(return_value=fake_response)

        rendered_prompt = "What is the market doing?"
        await call_gpt(rendered_prompt, client=mock_client)

        assert mock_client.chat.completions.create.called
        call_kwargs = mock_client.chat.completions.create.call_args

        # temperature=0 must be present
        assert call_kwargs.kwargs.get("temperature") == 0

        # seed=42 must be present
        assert call_kwargs.kwargs.get("seed") == 42

        # tool_choice must force submit_decision
        tool_choice = call_kwargs.kwargs.get("tool_choice", {})
        assert tool_choice.get("type") == "function"
        assert tool_choice.get("name") == "submit_decision"

        # tools must contain submit_decision with strict schema and strict:True
        tools = call_kwargs.kwargs.get("tools", [])
        assert len(tools) == 1
        assert tools[0]["type"] == "function"
        func_def = tools[0]["function"]
        assert func_def["name"] == "submit_decision"
        assert func_def["parameters"] == strict_provider_schema()
        assert func_def["strict"] is True

        # messages must contain the rendered prompt
        messages = call_kwargs.kwargs.get("messages", [])
        assert len(messages) == 1
        assert messages[0]["role"] == "user"
        assert messages[0]["content"] == rendered_prompt

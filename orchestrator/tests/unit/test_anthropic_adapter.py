"""Unit tests for orchestrator.providers.anthropic_adapter (Task 1, Plan 02-02).

Tests cover:
  - extract_tool_input: returns dict for ToolUseBlock, None for TextBlock / empty content
  - classify_exception: APITimeoutError / RateLimitError / InternalServerError /
    APIConnectionError → "api_failure"; unknown → "unknown"
  - validate_decision: returns Decision for valid dict, None for bad dict (missing action)
  - call_claude: passes correct kwargs (model, tools, tool_choice), NO temperature kwarg

D-14: this test file imports anthropic directly (via AsyncMock / fake objects), which is allowed
in tests. The constraint is that mock_harness.py must NOT import anthropic.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import anthropic
import pytest

# ---------------------------------------------------------------------------
# Helpers — fake Anthropic SDK response objects
# ---------------------------------------------------------------------------


def _fake_tool_use_block(input_dict: dict) -> Any:
    """Build a fake ToolUseBlock-shaped object (has .input and .type attrs)."""
    block = MagicMock()
    block.type = "tool_use"
    block.input = input_dict
    return block


def _fake_text_block(text: str = "I cannot help with that.") -> Any:
    """Build a fake TextBlock-shaped object (no .input attr, type='text')."""
    block = MagicMock(spec=["type", "text"])
    block.type = "text"
    block.text = text
    # Ensure .input raises AttributeError (no spec attribute) — MagicMock won't have it
    del block.input
    return block


def _fake_response(content: list) -> Any:
    """Build a fake Anthropic Message response with the given content list."""
    resp = MagicMock()
    resp.content = content
    return resp


# ---------------------------------------------------------------------------
# Test: extract_tool_input
# ---------------------------------------------------------------------------


class TestExtractToolInput:
    def test_returns_dict_for_tool_use_block(self):
        from orchestrator.providers.anthropic_adapter import extract_tool_input

        expected = {
            "action": "hold",
            "sizeUsd": 0,
            "leverage": 1,
            "rationale": "flat market",
            "confidence": 0.5,
            "expectedHoldingPeriod": "short",
        }
        resp = _fake_response([_fake_tool_use_block(expected)])
        result = extract_tool_input(resp)
        assert result == expected

    def test_returns_none_for_text_block_only(self):
        from orchestrator.providers.anthropic_adapter import extract_tool_input

        resp = _fake_response([_fake_text_block()])
        result = extract_tool_input(resp)
        assert result is None

    def test_returns_none_for_empty_content(self):
        from orchestrator.providers.anthropic_adapter import extract_tool_input

        resp = _fake_response([])
        result = extract_tool_input(resp)
        assert result is None

    def test_returns_none_when_content_is_none(self):
        from orchestrator.providers.anthropic_adapter import extract_tool_input

        resp = MagicMock()
        resp.content = None
        result = extract_tool_input(resp)
        assert result is None


# ---------------------------------------------------------------------------
# Test: classify_exception
# ---------------------------------------------------------------------------


class TestClassifyException:
    def test_timeout_error_is_api_failure(self):
        from orchestrator.providers.anthropic_adapter import classify_exception

        exc = anthropic.APITimeoutError(request=MagicMock())
        assert classify_exception(exc) == "api_failure"

    def test_rate_limit_error_is_api_failure(self):
        from orchestrator.providers.anthropic_adapter import classify_exception

        exc = anthropic.RateLimitError(
            message="rate limited",
            response=MagicMock(status_code=429),
            body={},
        )
        assert classify_exception(exc) == "api_failure"

    def test_internal_server_error_is_api_failure(self):
        from orchestrator.providers.anthropic_adapter import classify_exception

        exc = anthropic.InternalServerError(
            message="internal error",
            response=MagicMock(status_code=500),
            body={},
        )
        assert classify_exception(exc) == "api_failure"

    def test_api_connection_error_is_api_failure(self):
        from orchestrator.providers.anthropic_adapter import classify_exception

        exc = anthropic.APIConnectionError(request=MagicMock())
        assert classify_exception(exc) == "api_failure"

    def test_unknown_exception_returns_unknown(self):
        from orchestrator.providers.anthropic_adapter import classify_exception

        exc = ValueError("unexpected")
        assert classify_exception(exc) == "unknown"

    def test_generic_exception_returns_unknown(self):
        from orchestrator.providers.anthropic_adapter import classify_exception

        exc = RuntimeError("boom")
        assert classify_exception(exc) == "unknown"


# ---------------------------------------------------------------------------
# Test: validate_decision
# ---------------------------------------------------------------------------


class TestValidateDecision:
    def test_returns_decision_for_valid_hold(self):
        from orchestrator.providers.anthropic_adapter import validate_decision

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

    def test_returns_decision_for_valid_open(self):
        from orchestrator.providers.anthropic_adapter import validate_decision

        raw = {
            "action": "open",
            "sizeUsd": 1000,
            "leverage": 2,
            "rationale": "bullish ETH signal",
            "confidence": 0.7,
            "expectedHoldingPeriod": "medium",
            "market": "ETH",
            "side": "long",
        }
        result = validate_decision(raw)
        assert result is not None
        assert result.action == "open"
        assert result.market == "ETH"

    def test_returns_none_for_missing_action(self):
        from orchestrator.providers.anthropic_adapter import validate_decision

        raw = {
            # "action" is missing — this is the 0002_malformed.json fixture shape
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

    def test_returns_none_for_invalid_leverage(self):
        from orchestrator.providers.anthropic_adapter import validate_decision

        raw = {
            "action": "open",
            "sizeUsd": 1000,
            "leverage": 10,  # exceeds le=3
            "rationale": "test",
            "confidence": 0.5,
            "expectedHoldingPeriod": "short",
            "market": "ETH",
            "side": "long",
        }
        result = validate_decision(raw)
        assert result is None

    def test_returns_none_for_open_without_market(self):
        from orchestrator.providers.anthropic_adapter import validate_decision

        raw = {
            "action": "open",
            "sizeUsd": 1000,
            "leverage": 1,
            "rationale": "test",
            "confidence": 0.5,
            "expectedHoldingPeriod": "short",
            # market is None / missing — cross-field validator raises
            "side": "long",
        }
        result = validate_decision(raw)
        assert result is None


# ---------------------------------------------------------------------------
# Test: call_claude — verifies call shape (model, tools, tool_choice, NO temperature)
# ---------------------------------------------------------------------------


class TestCallClaude:
    @pytest.mark.asyncio
    async def test_call_claude_passes_correct_kwargs_no_temperature(self):
        """call_claude must call messages.create with the right shape and NO temperature."""
        from orchestrator.providers.anthropic_adapter import call_claude
        from orchestrator.schema import strict_provider_schema

        # Build a fake successful response
        input_dict = {
            "action": "hold",
            "sizeUsd": 0,
            "leverage": 1,
            "rationale": "flat",
            "confidence": 0.4,
            "expectedHoldingPeriod": "short",
        }
        fake_response = _fake_response([_fake_tool_use_block(input_dict)])

        # Mock the Anthropic AsyncAnthropic client
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=fake_response)

        rendered_prompt = "What is the market doing?"
        await call_claude(rendered_prompt, client=mock_client)

        # Verify messages.create was called
        assert mock_client.messages.create.called
        call_kwargs = mock_client.messages.create.call_args

        # model must be claude-opus-4-7
        assert call_kwargs.kwargs.get("model") == "claude-opus-4-7" or (
            call_kwargs.args and call_kwargs.args[0] == "claude-opus-4-7"
        )

        # temperature must NOT be present
        assert "temperature" not in call_kwargs.kwargs, (
            "call_claude must NOT pass temperature= — Opus 4.7 returns HTTP 400"
        )

        # tool_choice must be the forced-tool shape
        tool_choice = call_kwargs.kwargs.get("tool_choice", {})
        assert tool_choice.get("type") == "tool"
        assert tool_choice.get("name") == "submit_decision"

        # tools must contain submit_decision with strict_provider_schema as input_schema
        tools = call_kwargs.kwargs.get("tools", [])
        assert len(tools) == 1
        assert tools[0]["name"] == "submit_decision"
        assert tools[0]["input_schema"] == strict_provider_schema()

        # messages must contain the rendered prompt
        messages = call_kwargs.kwargs.get("messages", [])
        assert len(messages) == 1
        assert messages[0]["role"] == "user"
        assert messages[0]["content"] == rendered_prompt

    @pytest.mark.asyncio
    async def test_call_claude_returns_raw_response(self):
        """call_claude returns the raw response object (not yet classified)."""
        from orchestrator.providers.anthropic_adapter import call_claude

        fake_response = _fake_response([_fake_tool_use_block({"action": "hold"})])
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=fake_response)

        result = await call_claude("prompt text", client=mock_client)
        assert result is fake_response

    @pytest.mark.asyncio
    async def test_call_claude_uses_custom_model_string(self):
        """call_claude accepts an alternate model string."""
        from orchestrator.providers.anthropic_adapter import call_claude

        fake_response = _fake_response([])
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=fake_response)

        await call_claude("prompt", model="claude-opus-4-7", client=mock_client)
        call_kwargs = mock_client.messages.create.call_args
        assert call_kwargs.kwargs.get("model") == "claude-opus-4-7"

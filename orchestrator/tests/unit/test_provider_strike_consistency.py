"""D-17 provider strike-counter consistency across all 3 adapters (Plan 04-04, Task 3).

D-17 requirement: all three adapters (anthropic, openai, gemini) must map the same
transient-error categories to "api_failure" (increments api_failure_streak; pause@3).
A malformed or missing tool input maps to the malformed counter (increments
malformed_streak; pause@5) via extract_tool_input=None or validate_decision=None.

This file asserts cross-provider consistency per 04-04 plan Task 3 behavior spec:
  1. classify_exception(transient-error) → "api_failure" for all 3 adapters
  2. classify_exception(generic-error) → "unknown" for all 3 adapters
  3. extract_tool_input(garbage) → None for all 3 adapters (malformed path)
  4. validate_decision(bad_dict) → None for all 3 adapters (malformed path)
  5. All 4 primitives exist on each adapter module (introspection)

This is the D-17 STRIKE-CONSISTENCY gate required by the 04-04 phase fairness check.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import anthropic
import openai
import pytest

# Guard: skip if any adapter not yet implemented.
pytest.importorskip(
    "orchestrator.providers.anthropic_adapter",
    reason="Wave 0 stub — adapters implemented in 04-04",
)
pytest.importorskip(
    "orchestrator.providers.openai_adapter",
    reason="Wave 0 stub — openai_adapter implemented in 04-04",
)
pytest.importorskip(
    "orchestrator.providers.gemini_adapter",
    reason="Wave 0 stub — gemini_adapter implemented in 04-04",
)

from orchestrator.providers import anthropic_adapter, gemini_adapter, openai_adapter  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers — build representative transient exceptions per provider
# ---------------------------------------------------------------------------


def _anthropic_transient_exceptions() -> list[Exception]:
    """Representative transient exceptions for the Anthropic adapter."""
    return [
        anthropic.APITimeoutError(request=MagicMock()),
        anthropic.RateLimitError(
            message="rate limited",
            response=MagicMock(status_code=429),
            body={},
        ),
        anthropic.InternalServerError(
            message="internal server error",
            response=MagicMock(status_code=500),
            body={},
        ),
        anthropic.APIConnectionError(request=MagicMock()),
    ]


def _openai_transient_exceptions() -> list[Exception]:
    """Representative transient exceptions for the OpenAI adapter."""
    return [
        openai.APITimeoutError(request=MagicMock()),
        openai.RateLimitError(
            message="rate limited",
            response=MagicMock(status_code=429),
            body={},
        ),
        openai.InternalServerError(
            message="internal server error",
            response=MagicMock(status_code=500),
            body={},
        ),
        openai.APIConnectionError(request=MagicMock()),
    ]


def _gemini_transient_exceptions() -> list[Exception]:
    """Representative transient exceptions for the Gemini adapter (name-matched)."""

    # google-genai v2.8.0 doesn't export typed exception classes at package level.
    # Create fake exceptions with names matching the "Timeout/RateLimit/ServerError/Connection"
    # patterns that gemini_adapter.classify_exception uses for name matching.
    class GeminiTimeoutError(Exception):
        pass

    class GeminiRateLimitError(Exception):
        pass

    class GeminiServerError(Exception):
        pass

    class GeminiConnectionError(Exception):
        pass

    return [
        GeminiTimeoutError(),
        GeminiRateLimitError(),
        GeminiServerError(),
        GeminiConnectionError(),
    ]


# ---------------------------------------------------------------------------
# Test: all 3 adapters expose the 4 primitives (introspection)
# ---------------------------------------------------------------------------


class TestAllAdaptersExposeFourPrimitives:
    """Assert each adapter module has the 4-primitive contract (D-13)."""

    @pytest.mark.parametrize(
        "adapter_module,call_fn_name",
        [
            (anthropic_adapter, "call_claude"),
            (openai_adapter, "call_gpt"),
            (gemini_adapter, "call_gemini"),
        ],
    )
    def test_all_adapters_expose_four_primitives(
        self, adapter_module: Any, call_fn_name: str
    ) -> None:
        """Each adapter exposes call_X, extract_tool_input, classify_exception, validate_decision."""
        # call_X (name varies per provider)
        assert hasattr(adapter_module, call_fn_name), (
            f"{adapter_module.__name__} missing {call_fn_name}"
        )
        assert callable(getattr(adapter_module, call_fn_name))

        # Shared primitive names (identical across all 3)
        for prim in ("extract_tool_input", "classify_exception", "validate_decision"):
            assert hasattr(adapter_module, prim), f"{adapter_module.__name__} missing {prim}"
            assert callable(getattr(adapter_module, prim))


# ---------------------------------------------------------------------------
# Test: classify_exception returns "api_failure" for transient errors (D-17)
# ---------------------------------------------------------------------------


class TestAllThreeAdaptersMapsApiFailure:
    """D-17: classify_exception must return 'api_failure' for transient errors."""

    def test_anthropic_transient_exceptions_map_to_api_failure(self) -> None:
        """Anthropic transient errors → 'api_failure' (increments api_failure_streak)."""
        for exc in _anthropic_transient_exceptions():
            result = anthropic_adapter.classify_exception(exc)
            assert result == "api_failure", (
                f"anthropic classify_exception({type(exc).__name__}) returned {result!r}, "
                f"expected 'api_failure'"
            )

    def test_openai_transient_exceptions_map_to_api_failure(self) -> None:
        """OpenAI transient errors → 'api_failure' (increments api_failure_streak)."""
        for exc in _openai_transient_exceptions():
            result = openai_adapter.classify_exception(exc)
            assert result == "api_failure", (
                f"openai classify_exception({type(exc).__name__}) returned {result!r}, "
                f"expected 'api_failure'"
            )

    def test_gemini_transient_exceptions_map_to_api_failure(self) -> None:
        """Gemini name-matched transient errors → 'api_failure' (api_failure_streak)."""
        for exc in _gemini_transient_exceptions():
            result = gemini_adapter.classify_exception(exc)
            assert result == "api_failure", (
                f"gemini classify_exception({type(exc).__name__}) returned {result!r}, "
                f"expected 'api_failure'"
            )

    def test_all_three_adapters_map_same_exceptions_to_api_failure(self) -> None:
        """Parameterized: all 3 adapters return 'api_failure' for generic 'SomeTimeoutError'."""

        # Build a fake exception whose name contains "Timeout" — matches all 3 adapters'
        # api_failure categories: anthropic has APITimeoutError, openai has APITimeoutError,
        # gemini uses name matching. For consistency, use a custom named class.
        class SomeTimeoutError(Exception):
            pass

        class SomeRateLimitError(Exception):
            pass

        class SomeServerError(Exception):
            pass

        class SomeConnectionError(Exception):
            pass

        # For anthropic and openai, only their SDK's typed exceptions get "api_failure";
        # generic Exception → "unknown". So we test with THEIR typed exceptions:
        # Test that each adapter has at least ONE matching exception category for each
        # logical transient type, and that all three adapters route them to api_failure.

        # Anthropic timeout → api_failure
        a_exc = anthropic.APITimeoutError(request=MagicMock())
        assert anthropic_adapter.classify_exception(a_exc) == "api_failure"

        # OpenAI timeout → api_failure
        o_exc = openai.APITimeoutError(request=MagicMock())
        assert openai_adapter.classify_exception(o_exc) == "api_failure"

        # Gemini "Timeout" name match → api_failure
        g_exc = SomeTimeoutError()
        assert gemini_adapter.classify_exception(g_exc) == "api_failure"

        # All three handle rate limits
        a_rate = anthropic.RateLimitError(
            message="rate", response=MagicMock(status_code=429), body={}
        )
        o_rate = openai.RateLimitError(message="rate", response=MagicMock(status_code=429), body={})
        g_rate = SomeRateLimitError()

        assert anthropic_adapter.classify_exception(a_rate) == "api_failure"
        assert openai_adapter.classify_exception(o_rate) == "api_failure"
        assert gemini_adapter.classify_exception(g_rate) == "api_failure"


# ---------------------------------------------------------------------------
# Test: generic exceptions → "unknown" for all 3 adapters
# ---------------------------------------------------------------------------


class TestAllThreeAdaptersReturnUnknownForGeneric:
    """D-17: generic exceptions must return 'unknown' for all 3 adapters."""

    @pytest.mark.parametrize(
        "adapter_module",
        [anthropic_adapter, openai_adapter, gemini_adapter],
        ids=["anthropic", "openai", "gemini"],
    )
    def test_generic_exception_returns_unknown(self, adapter_module: Any) -> None:
        """ValueError / RuntimeError → 'unknown' for all 3 adapters."""
        for exc in [ValueError("unexpected"), RuntimeError("boom")]:
            result = adapter_module.classify_exception(exc)
            assert result == "unknown", (
                f"{adapter_module.__name__} classify_exception({type(exc).__name__}) "
                f"returned {result!r}, expected 'unknown'"
            )


# ---------------------------------------------------------------------------
# Test: malformed path (extract_tool_input=None + validate_decision=None) — D-17
# ---------------------------------------------------------------------------


class TestMalformedPathConsistentAcrossAdapters:
    """D-17: malformed path (pause@5) is consistent across all 3 adapters."""

    @pytest.mark.parametrize(
        "adapter_module",
        [anthropic_adapter, openai_adapter, gemini_adapter],
        ids=["anthropic", "openai", "gemini"],
    )
    def test_extract_tool_input_returns_none_on_none_input(self, adapter_module: Any) -> None:
        """extract_tool_input(None) → None for all 3 adapters (malformed_streak path)."""
        result = adapter_module.extract_tool_input(None)
        assert result is None, (
            f"{adapter_module.__name__}.extract_tool_input(None) returned {result!r}, "
            f"expected None (malformed_streak path)"
        )

    @pytest.mark.parametrize(
        "adapter_module",
        [anthropic_adapter, openai_adapter, gemini_adapter],
        ids=["anthropic", "openai", "gemini"],
    )
    def test_extract_tool_input_returns_none_on_garbage(self, adapter_module: Any) -> None:
        """extract_tool_input(garbage) → None for all 3 adapters (malformed_streak path)."""
        for garbage in [MagicMock(spec=[]), "not a response", 42]:
            result = adapter_module.extract_tool_input(garbage)
            assert result is None, (
                f"{adapter_module.__name__}.extract_tool_input({garbage!r}) "
                f"returned {result!r}, expected None"
            )

    @pytest.mark.parametrize(
        "adapter_module",
        [anthropic_adapter, openai_adapter, gemini_adapter],
        ids=["anthropic", "openai", "gemini"],
    )
    def test_validate_decision_returns_none_on_empty_dict(self, adapter_module: Any) -> None:
        """validate_decision({}) → None for all 3 adapters (malformed_streak path)."""
        result = adapter_module.validate_decision({})
        assert result is None, (
            f"{adapter_module.__name__}.validate_decision({{}}) returned {result!r}, "
            f"expected None (malformed_streak path)"
        )

    @pytest.mark.parametrize(
        "adapter_module",
        [anthropic_adapter, openai_adapter, gemini_adapter],
        ids=["anthropic", "openai", "gemini"],
    )
    def test_validate_decision_returns_none_on_missing_required_fields(
        self, adapter_module: Any
    ) -> None:
        """validate_decision(dict missing 'action') → None for all 3 adapters."""
        bad_dict = {
            "market": "ETH",
            "sizeUsd": 1000,
            "leverage": 2,
            "rationale": "test",
            "confidence": 0.5,
            "expectedHoldingPeriod": "short",
            # 'action' is missing
        }
        result = adapter_module.validate_decision(bad_dict)
        assert result is None, (
            f"{adapter_module.__name__}.validate_decision(bad_dict) returned {result!r}, "
            f"expected None (malformed_streak path)"
        )

    def test_malformed_path_consistent(self) -> None:
        """Both extract_tool_input=None AND validate_decision=None feed the same
        malformed_streak counter (pause@5) — identical behavior across all 3 adapters.

        This is the driver.py pattern (lines 373-432):
          raw = extract_tool_input(response)
          if raw is None: tracker.record_malformed()  # malformed_streak
          decision = validate_decision(raw)
          if decision is None: tracker.record_malformed()  # SAME counter
        """
        for adapter_module in [anthropic_adapter, openai_adapter, gemini_adapter]:
            # extract_tool_input(None) → None → malformed_streak
            assert adapter_module.extract_tool_input(None) is None, (
                f"{adapter_module.__name__}.extract_tool_input(None) must be None"
            )
            # validate_decision({}) → None → malformed_streak (SAME counter)
            assert adapter_module.validate_decision({}) is None, (
                f"{adapter_module.__name__}.validate_decision({{}}) must be None"
            )

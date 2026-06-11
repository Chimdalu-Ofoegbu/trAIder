"""
Seam A.2 — provider-dispatch tests for driver._select_provider_callables.

The live loop routes each vault's `model` string to its OWN frontier-model adapter so a
3-model gate runs Claude / GPT-5.5 / Gemini (not Claude-x3). These tests pin the routing AND
the regression-preservation guarantee: the Claude/default branch returns the driver module-level
call_claude/extract_tool_input/classify_exception so the proven single-model loop stays
byte-identical and the existing tests that patch `orchestrator.loop.driver.call_claude` keep working.
"""

from __future__ import annotations

from unittest.mock import patch

import orchestrator.loop.driver as driver
from orchestrator.providers import anthropic_adapter, gemini_adapter, openai_adapter


def test_dispatch_claude_default() -> None:
    call, extract, classify = driver._select_provider_callables("claude-opus-4-7")
    assert call is anthropic_adapter.call_claude
    assert extract is anthropic_adapter.extract_tool_input
    assert classify is anthropic_adapter.classify_exception


def test_dispatch_gpt() -> None:
    call, extract, classify = driver._select_provider_callables("gpt-5.5-2026-04-23")
    assert call is openai_adapter.call_gpt
    assert extract is openai_adapter.extract_tool_input
    assert classify is openai_adapter.classify_exception


def test_dispatch_gemini() -> None:
    call, extract, classify = driver._select_provider_callables("gemini-3.1-pro-preview")
    assert call is gemini_adapter.call_gemini
    assert extract is gemini_adapter.extract_tool_input
    assert classify is gemini_adapter.classify_exception


def test_dispatch_unknown_and_empty_default_to_claude() -> None:
    assert (
        driver._select_provider_callables("some-unknown-model")[0] is anthropic_adapter.call_claude
    )
    assert driver._select_provider_callables("")[0] is anthropic_adapter.call_claude


def test_dispatch_case_insensitive() -> None:
    assert driver._select_provider_callables("GPT-5.5-2026-04-23")[0] is openai_adapter.call_gpt
    assert (
        driver._select_provider_callables("Gemini-3.1-Pro-Preview")[0] is gemini_adapter.call_gemini
    )


def test_claude_branch_respects_driver_patch() -> None:
    """Regression guarantee: patching driver.call_claude is honored by the claude/default branch.

    This is WHY the default branch returns the module-level names (resolved from driver's globals
    at call time) — the 5+ existing tests that patch orchestrator.loop.driver.call_claude /
    .extract_tool_input must keep working unchanged after the A.2 dispatch refactor.
    """
    sentinel_call = object()
    sentinel_extract = object()
    with (
        patch.object(driver, "call_claude", sentinel_call),
        patch.object(driver, "extract_tool_input", sentinel_extract),
    ):
        call, extract, _classify = driver._select_provider_callables("claude-opus-4-7")
        assert call is sentinel_call, "claude branch must use driver.call_claude (patchable)"
        assert extract is sentinel_extract, (
            "claude branch must use driver.extract_tool_input (patchable)"
        )

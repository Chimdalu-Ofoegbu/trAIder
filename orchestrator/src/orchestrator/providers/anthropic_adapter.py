"""
orchestrator.providers.anthropic_adapter — Claude Opus 4.7 tool-use adapter (Plan 02-02).

# D-14: this is the ONLY module permitted to import the provider SDK.
# mock_harness.py must NEVER import anthropic (deterministic, no live LLM).

Exposes four primitives used by the loop driver (Plan 05):

  call_claude(rendered_prompt, model, *, client) -> Message
      Make the forced-tool API call. Returns the raw Message object.
      CRITICAL: DO NOT pass temperature — Opus 4.7 returns HTTP 400.

  extract_tool_input(response) -> dict | None
      Extract ToolUseBlock.input from the response, or None if no ToolUseBlock
      is present (content-policy refusal / TextBlock-only / empty content).
      None = malformed-received path (D-17).

  classify_exception(exc) -> str
      Map SDK exceptions to "api_failure" (transient, retriable) or "unknown"
      (not silently swallowed). Never returns "malformed" — that is the caller's
      job for the content layer.

  validate_decision(raw) -> Decision | None
      Attempt Decision.model_validate(raw). Return None on pydantic.ValidationError.
      None = malformed-received path (D-17).

D-17 two-counter design:
  api_failure_streak : classify_exception == "api_failure" → pause at 3 (D-15)
  malformed_streak   : extract_tool_input is None, OR validate_decision is None → pause at 5

Either counter resets to 0 on a successful, valid, parseable response.
"""

from __future__ import annotations

from typing import Any

import anthropic
from pydantic import ValidationError

from orchestrator.schema import Decision, strict_provider_schema

# ---------------------------------------------------------------------------
# Public constants
# ---------------------------------------------------------------------------

DEFAULT_MODEL = "claude-opus-4-7"
MAX_TOKENS = 1024

# ---------------------------------------------------------------------------
# call_claude — make the forced-tool API call
# ---------------------------------------------------------------------------


async def call_claude(
    rendered_prompt: str,
    model: str = DEFAULT_MODEL,
    *,
    client: Any = None,
) -> Any:
    """Make the Claude tool-use forced-JSON call.

    Args:
        rendered_prompt: The Jinja2-rendered system/user prompt for this cycle.
        model: Claude model string. Defaults to "claude-opus-4-7".
        client: Optional pre-built AsyncAnthropic client (for testing).
                If None, a new AsyncAnthropic() is created (picks up
                ANTHROPIC_API_KEY from the environment).

    Returns:
        The raw Anthropic Message response object. NOT yet classified.
        Call extract_tool_input() and validate_decision() on the result.

    CRITICAL:
        DO NOT pass temperature= — Anthropic returns HTTP 400 for Opus 4.7.
        See CLAUDE.md "Provider Quirks" and orchestrator/prompts/README.md.
    """
    if client is None:
        client = anthropic.AsyncAnthropic()

    # Forced-tool call: model MUST emit a ToolUseBlock for "submit_decision".
    # input_schema = strict_provider_schema() ensures additionalProperties:false + all required.
    # OMIT temperature entirely (Opus 4.7 adaptive sampling — HTTP 400 if passed).
    response = await client.messages.create(
        model=model,
        max_tokens=MAX_TOKENS,
        tools=[
            {
                "name": "submit_decision",
                "description": "Submit your trading decision for this cycle.",
                "input_schema": strict_provider_schema(),
            }
        ],
        tool_choice={"type": "tool", "name": "submit_decision"},
        messages=[{"role": "user", "content": rendered_prompt}],
    )
    return response


# ---------------------------------------------------------------------------
# extract_tool_input — pull out ToolUseBlock.input (or None for malformed)
# ---------------------------------------------------------------------------


def extract_tool_input(response: Any) -> dict | None:
    """Extract the ToolUseBlock input dict from a Claude response.

    Returns:
        dict: The ToolUseBlock.input dict when response.content[0] is a ToolUseBlock.
        None: When content is empty, None, or content[0] is not a ToolUseBlock
              (e.g. TextBlock from a content-policy refusal or stop_reason != tool_use).
              This is the malformed-received signal for the D-17 two-counter design.
    """
    try:
        content = response.content
        if not content:
            return None
        block = content[0]
        # ToolUseBlock has an .input attribute that is a dict.
        # TextBlock has .text but no .input. Guard with hasattr.
        input_dict = block.input
        # Confirm it's actually a dict (not some other attr)
        if not isinstance(input_dict, dict):
            return None
        return input_dict
    except (AttributeError, IndexError, TypeError):
        return None


# ---------------------------------------------------------------------------
# classify_exception — map SDK exceptions to error-counter category
# ---------------------------------------------------------------------------


def classify_exception(exc: Exception) -> str:
    """Map an SDK exception to an error-counter category.

    Returns:
        "api_failure": transient, retriable — increments api_failure_streak.
            Covers: APITimeoutError, RateLimitError (429), InternalServerError (5xx),
            APIConnectionError (network-level failure).
        "unknown": not a known transient error. Caller should log and decide whether
            to re-raise. Does NOT silently swallow unknown exceptions.

    D-17: "api_failure" drives the api_failure_streak counter (pause at 3, D-15).
    The "malformed" bucket is the CONTENT layer (extract_tool_input / validate_decision)
    and is NOT returned here.
    """
    if isinstance(
        exc,
        anthropic.APITimeoutError
        | anthropic.RateLimitError
        | anthropic.InternalServerError
        | anthropic.APIConnectionError,
    ):
        return "api_failure"
    return "unknown"


# ---------------------------------------------------------------------------
# validate_decision — try Decision.model_validate, return None on ValidationError
# ---------------------------------------------------------------------------


def validate_decision(raw: dict) -> Decision | None:
    """Attempt to parse a raw dict into a validated Decision.

    Returns:
        Decision: When raw passes all schema + cross-field validators.
        None: When raw fails pydantic.ValidationError (missing required field,
              out-of-range value, or cross-field constraint violation such as
              action="open" with market=None). None = malformed-received signal.

    Never raises — the caller checks the return value and increments
    malformed_streak on None (D-17).
    """
    try:
        return Decision.model_validate(raw)
    except ValidationError:
        return None

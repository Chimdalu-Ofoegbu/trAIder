"""
orchestrator.providers.openai_adapter — GPT-5.5 function-calling adapter (D-13, Plan 04-04).

# D-14: the ONLY module permitted to import openai.
# mock_harness.py must NEVER import openai (deterministic, no live LLM).

Exposes four primitives used by the loop driver (Plan 05):

  call_gpt(rendered_prompt, model, *, client) -> Any
      Make the forced-tool API call. Returns the raw ChatCompletion object.
      Passes temperature=0, seed=42, reasoning={"effort":"low"} for inference parity.

  extract_tool_input(response) -> dict | None
      Extract the tool_calls[0].function.arguments dict from the response,
      or None if finish_reason != "tool_calls" or the call is not submit_decision.
      None = malformed-received path (D-17).

  classify_exception(exc) -> str
      Map SDK exceptions to "api_failure" (transient, retriable) or "unknown".
      Never returns "malformed" — that is the caller's job for the content layer.

  validate_decision(raw) -> Decision | None
      Attempt Decision.model_validate(raw). Return None on pydantic.ValidationError.
      None = malformed-received path (D-17).

D-17 two-counter design (identical to anthropic_adapter):
  api_failure_streak : classify_exception == "api_failure" → pause at 3 (D-15)
  malformed_streak   : extract_tool_input is None, OR validate_decision is None → pause at 5

Inference parity (D-13):
  GPT-5.5 → temperature=0 + seed=42 + reasoning={"effort":"low"}
  Reasoning tier: "low" avoids 5-15s latency; fast crypto decisions (CLAUDE.md § Provider Quirks)
  seed=42 for best-effort determinism; temperature=0 for greedy decoding
  Note: reasoning kwarg is a top-level param in openai>=2.0 (verified against openai==2.41.0
  via inspect.getsource — 04-PROBE-RESULTS.md Assumption A1 resolved: reasoning IS top-level).
"""

from __future__ import annotations

import json
from typing import Any

import openai
from pydantic import ValidationError

from orchestrator.schema import Decision, strict_provider_schema

# ---------------------------------------------------------------------------
# Public constants
# ---------------------------------------------------------------------------

DEFAULT_MODEL = "gpt-5.5-2026-04-23"
MAX_TOKENS = 1024
SEED = 42

# ---------------------------------------------------------------------------
# call_gpt — make the forced-tool API call
# ---------------------------------------------------------------------------


async def call_gpt(
    rendered_prompt: str,
    model: str = DEFAULT_MODEL,
    *,
    client: Any = None,
) -> Any:
    """Make the GPT-5.5 forced-function-call API call.

    Args:
        rendered_prompt: The Jinja2-rendered system/user prompt for this cycle.
        model: GPT model string. Defaults to "gpt-5.5-2026-04-23".
        client: Optional pre-built AsyncOpenAI client (for testing).
                If None, a new openai.AsyncOpenAI() is created (picks up
                OPENAI_API_KEY from the environment).

    Returns:
        The raw ChatCompletion response object. NOT yet classified.
        Call extract_tool_input() and validate_decision() on the result.

    Inference parity (D-13):
        temperature=0 + seed=42 for best-effort determinism.
        reasoning={"effort":"low"} to avoid 5-15s reasoning latency (CLAUDE.md).
    """
    if client is None:
        client = openai.AsyncOpenAI()

    # Forced-function call: model MUST emit a tool_call for "submit_decision".
    # parameters = strict_provider_schema() ensures additionalProperties:false + all required.
    # strict:True enables OpenAI's strict structured output mode.
    response = await client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": rendered_prompt}],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "submit_decision",
                    "description": "Submit your trading decision for this cycle.",
                    "parameters": strict_provider_schema(),
                    "strict": True,
                },
            }
        ],
        tool_choice={"type": "function", "name": "submit_decision"},
        temperature=0,
        seed=SEED,
        reasoning={"effort": "low"},
        max_tokens=MAX_TOKENS,
    )
    return response


# ---------------------------------------------------------------------------
# extract_tool_input — pull out tool_calls[0].function.arguments (or None)
# ---------------------------------------------------------------------------


def extract_tool_input(response: Any) -> dict | None:
    """Extract the submit_decision tool call arguments from a GPT response.

    Returns:
        dict: The parsed JSON dict from tool_calls[0].function.arguments when
              finish_reason=="tool_calls" and function.name=="submit_decision".
        None: When finish_reason is not "tool_calls", tool_calls is empty, the
              function name doesn't match, or arguments is not valid JSON.
              This is the malformed-received signal for the D-17 two-counter design.
    """
    try:
        choice = response.choices[0]
        if choice.finish_reason != "tool_calls":
            return None
        call = choice.message.tool_calls[0]
        if call.function.name != "submit_decision":
            return None
        return json.loads(call.function.arguments)
    except (AttributeError, IndexError, TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# classify_exception — map SDK exceptions to error-counter category
# ---------------------------------------------------------------------------


def classify_exception(exc: Exception) -> str:
    """Map an OpenAI SDK exception to an error-counter category.

    Returns:
        "api_failure": transient, retriable — increments api_failure_streak.
            Covers: APITimeoutError, RateLimitError (429), InternalServerError (5xx),
            APIConnectionError (network-level failure).
        "unknown": not a known transient error. Caller should log and decide whether
            to re-raise. Does NOT silently swallow unknown exceptions.

    D-17: "api_failure" drives the api_failure_streak counter (pause at 3, D-15).
    The "malformed" bucket is the CONTENT layer (extract_tool_input / validate_decision)
    and is NOT returned here.

    D-17 strike-consistency: identical category set to anthropic_adapter and gemini_adapter.
    Asserted by test_provider_strike_consistency.py.
    """
    if isinstance(
        exc,
        openai.APITimeoutError
        | openai.RateLimitError
        | openai.InternalServerError
        | openai.APIConnectionError,
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

    Identical implementation across all three provider adapters (D-13 byte-equal contract).
    """
    try:
        return Decision.model_validate(raw)
    except ValidationError:
        return None

"""
orchestrator.providers.gemini_adapter — Gemini 3.1 Pro JSON-schema adapter (D-13, Plan 04-04).

# D-14: the ONLY module permitted to import google.genai.
# mock_harness.py must NEVER import google.genai (deterministic, no live LLM).

Exposes four primitives used by the loop driver (Plan 05):

  call_gemini(rendered_prompt, model, *, client) -> Any
      Make the JSON-schema constrained API call. Returns the raw GenerateContentResponse.
      Uses response_json_schema + response_mime_type="application/json" (Probe 3 VERDICT).

  extract_tool_input(response) -> dict | None
      Parse response.text as JSON, return dict or None if missing/invalid.
      Gemini's structured-output mode returns the JSON directly as text (not tool_calls).
      None = malformed-received path (D-17).

  classify_exception(exc) -> str
      Map SDK exceptions to "api_failure" or "unknown" by type name matching.
      google-genai does not export named exception classes as of v2.8.0;
      name-matching ("Timeout", "RateLimit", "ServerError", "Connection") covers
      the same transient categories as anthropic/openai adapters (D-17 parity).

  validate_decision(raw) -> Decision | None
      Attempt Decision.model_validate(raw). Return None on pydantic.ValidationError.
      Identical to anthropic_adapter and openai_adapter.

D-17 two-counter design (identical to anthropic_adapter):
  api_failure_streak : classify_exception == "api_failure" → pause at 3 (D-15)
  malformed_streak   : extract_tool_input is None, OR validate_decision is None → pause at 5

Inference parity (D-13):
  Gemini 3.1 Pro → temperature=0.0 + seed=42 (no reasoning_effort knob available)
  Reasoning tier mapping: Gemini lacks a reasoning_effort equivalent; temp0+seed is the
  parity lever. This is documented per RESEARCH.md § D3 inference-parity mapping table:
    Claude Opus 4.7:  no temp/seed (adaptive sampling — HTTP 400 if passed)
    GPT-5.5:          temp=0 + seed=42 + reasoning_effort=low
    Gemini 3.1 Pro:   temp=0.0 + seed=42 (no reasoning_effort parameter exposed)
  All three use strict structured output; schema is shared via strict_provider_schema().

Async path (04-PROBE-RESULTS.md Probe 3 VERDICT):
  client.aio.models.generate_content(...) is a native coroutine on AsyncModels
  (google-genai==2.8.0). No asyncio.to_thread wrapper needed.
  Config field for raw dict schema: response_json_schema (NOT response_schema —
  response_schema accepts a Pydantic model; response_json_schema accepts a plain dict).
  response_mime_type="application/json" is required when response_json_schema is set.
"""

from __future__ import annotations

import json
from typing import Any

from google import genai
from google.genai import types
from pydantic import ValidationError

from orchestrator.schema import Decision, strict_provider_schema

# ---------------------------------------------------------------------------
# Public constants
# ---------------------------------------------------------------------------

DEFAULT_MODEL = "gemini-3.1-pro-preview"
MAX_TOKENS = 1024
SEED = 42

# ---------------------------------------------------------------------------
# call_gemini — make the JSON-schema constrained API call
# ---------------------------------------------------------------------------


async def call_gemini(
    rendered_prompt: str,
    model: str = DEFAULT_MODEL,
    *,
    client: Any = None,
) -> Any:
    """Make the Gemini 3.1 Pro JSON-schema constrained API call.

    Args:
        rendered_prompt: The Jinja2-rendered system/user prompt for this cycle.
        model: Gemini model string. Defaults to "gemini-3.1-pro-preview".
        client: Optional pre-built google.genai.Client (for testing).
                If None, a new genai.Client() is created (picks up
                GOOGLE_API_KEY from the environment).

    Returns:
        The raw GenerateContentResponse object. NOT yet classified.
        Call extract_tool_input() and validate_decision() on the result.

    Async path (Probe 3 VERDICT):
        Uses client.aio.models.generate_content — native coroutine on AsyncModels.
        Config field: response_json_schema (accepts plain dict from strict_provider_schema()).
        response_mime_type="application/json" required when response_json_schema is set.

    Inference parity (D-13):
        temperature=0.0 + seed=42. No reasoning_effort knob (Gemini 3.1 Pro).
    """
    if client is None:
        client = genai.Client()

    schema = strict_provider_schema()

    # Probe 3 VERDICT: use response_json_schema (not response_schema) for a plain dict.
    # response_mime_type="application/json" is required when response_json_schema is set.
    # Async path: client.aio.models.generate_content (AsyncModels native coroutine).
    response = await client.aio.models.generate_content(
        model=model,
        contents=rendered_prompt,
        config=types.GenerateContentConfig(
            temperature=0.0,
            seed=SEED,
            max_output_tokens=MAX_TOKENS,
            response_mime_type="application/json",
            response_json_schema=schema,
        ),
    )
    return response


# ---------------------------------------------------------------------------
# extract_tool_input — parse response.text as JSON (or None for malformed)
# ---------------------------------------------------------------------------


def extract_tool_input(response: Any) -> dict | None:
    """Extract the JSON decision dict from a Gemini response.

    Gemini's response_json_schema mode returns the structured JSON directly
    as response.text (not tool_calls like OpenAI/Anthropic).

    Returns:
        dict: The parsed JSON dict when response.text is valid JSON.
        None: When response.text is None, empty, invalid JSON, or
              response has no .text attribute.
              This is the malformed-received signal for the D-17 two-counter design.
    """
    try:
        text = response.text
        if not text:
            return None
        return json.loads(text)
    except (AttributeError, TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# classify_exception — name-match SDK exceptions to error-counter category
# ---------------------------------------------------------------------------


def classify_exception(exc: Exception) -> str:
    """Map a google-genai SDK exception to an error-counter category.

    google-genai v2.8.0 does not export named exception classes at the package
    level (unlike anthropic/openai which have typed APITimeoutError etc.).
    We use type.__name__ matching to cover the same transient categories:
      "Timeout"     → DeadlineExceededError, APITimeoutError, etc.
      "RateLimit"   → RateLimitError, ResourceExhaustedError
      "ServerError" → InternalServerError, ServiceUnavailableError
      "Connection"  → APIConnectionError, ConnectionError

    Returns:
        "api_failure": transient, retriable — increments api_failure_streak.
        "unknown": not a known transient error.

    D-17 strike-consistency: matches the same four logical categories as
    anthropic_adapter (isinstance-based) and openai_adapter (isinstance-based).
    Asserted by test_provider_strike_consistency.py.
    """
    exc_name = type(exc).__name__
    if any(s in exc_name for s in ("Timeout", "RateLimit", "ServerError", "Connection")):
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

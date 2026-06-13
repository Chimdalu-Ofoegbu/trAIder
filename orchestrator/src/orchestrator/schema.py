"""
Decision schema — canonical source of truth for the LLM trading decision contract (IFACE-03).

Two variants derive from ONE canonical source (schema.json):
  1. Validation variant  — additionalProperties:true  (forward-compat, D-08)
  2. Provider-strict     — additionalProperties:false, all properties required (Pitfall 1)

See orchestrator/prompts/README.md for the three provider request shapes (D-09, Phase 2 wiring).
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# ---------------------------------------------------------------------------
# Canonical schema — loaded once at import time
# ---------------------------------------------------------------------------

_PROMPTS_DIR = Path(__file__).parent.parent.parent / "prompts"
_SCHEMA_PATH = _PROMPTS_DIR / "schema.json"

CANONICAL_SCHEMA: dict = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
"""
The frozen canonical Draft 2020-12 validation schema (additionalProperties:true).
Do not mutate. Call strict_provider_schema() for the provider-strict variant.
"""

# ---------------------------------------------------------------------------
# Risk constants (SEC — absolute notional backstop)
# ---------------------------------------------------------------------------

MAX_NOTIONAL_USD = 1_000_000.0
"""Absolute hard ceiling on a single decision's notional `sizeUsd` (USD), independent of
available capital. Backstops against a hallucinated/prompt-injected oversized size slipping
through if the relative capital gate's `available_usdc` is ever stale or large, and prevents
an `int(sizeUsd * 1e30)` overflow downstream. Far above any legitimate single-position
notional for this product (operator capital ≈ $10k/model × ≤3x ≈ $30k). Raise deliberately
if the product scales. Enforced in BOTH the schema (below) and business_rules (single gate)."""


# ---------------------------------------------------------------------------
# Pydantic v2 model (D-05, D-08)
# ---------------------------------------------------------------------------


class Decision(BaseModel):
    """
    Parsed trading decision emitted by an LLM each cycle.

    Validation semantics (D-08):
      - Required fields are strictly validated; missing = ValidationError → no trade this cycle.
      - Extra fields are accepted and stored in model_extra (forward-compat).
      - model_extra fields are stored verbatim in the journal but ignored for trade execution.

    Example usage:
        decision = Decision.model_validate(raw_json_dict)
        # Extra fields stored but not declared:
        print(decision.model_extra)   # {"moonPhase": "waxing"}
    """

    model_config = ConfigDict(
        extra="allow",  # D-08: extras accepted + stored in model_extra, ignored for execution
        populate_by_name=True,
    )

    # --- Required fields (D-05) ---

    action: Literal["open", "close", "hold", "adjust"]
    """Trade action. 'hold' = no position change this cycle."""

    sizeUsd: float = Field(ge=0, le=MAX_NOTIONAL_USD)
    """Notional position size in USD (post-leverage). 0 on hold or close.
    Upper-bounded by MAX_NOTIONAL_USD (SEC backstop — see constant above)."""

    leverage: float = Field(ge=1, le=3)
    """Leverage multiplier. Hard cap: 3x. Use 1 on hold."""

    rationale: str = Field(min_length=1, max_length=2000)
    """Step-by-step reasoning. Stored verbatim in journal for verifier replay."""

    confidence: float = Field(ge=0, le=1)
    """Self-assessed conviction score. 0 = no conviction, 1 = maximum."""

    expectedHoldingPeriod: Literal["short", "medium", "long"]
    """Expected holding duration: short (<4h), medium (4-24h), long (>24h)."""

    # --- Optional fields (D-05) ---

    market: Literal["ETH", "BTC", "SOL"] | None = None
    """Perpetual market. Required for open/close/adjust; omit on hold."""

    side: Literal["long", "short"] | None = None
    """Direction. Required for open/adjust; omit on close and hold."""

    # stopLoss / takeProfit: DEFERRED (D-06) — not part of Phase 0 schema.
    # Do not add until Phase 1 operator decision.

    # --- Cross-field validators (D-05, CR-02) ---

    @model_validator(mode="after")
    def _require_market_and_side_for_open_adjust(self) -> Decision:
        """
        Enforce the cross-field contract documented in schema.json:
          open / adjust  →  market AND side are required.
          close / hold   →  both may be omitted.

        A missing market or side on open/adjust raises ValueError so the harness
        routes the response to the ORCH-05 'malformed' path (no trade, no journal).
        Without this guard, None-market crashes ABI encoding and None-side silently
        opens a SHORT (the directional-integrity bug for the fairness seam, CR-02).
        """
        if self.action in ("open", "adjust"):
            if self.market is None:
                raise ValueError("market is required for open/adjust actions")
            if self.side is None:
                raise ValueError("side is required for open/adjust actions")
        return self


# ---------------------------------------------------------------------------
# Provider-strict schema deriver (Pitfall 1)
# ---------------------------------------------------------------------------


def strict_provider_schema() -> dict:
    """
    Derive the provider-strict variant from the canonical schema.

    Rules (Pitfall 1):
      - Deep copy the canonical schema (never mutate CANONICAL_SCHEMA)
      - Set additionalProperties = False
      - Set required = list of all property keys

    This is the schema fed to provider structured-output APIs (D-09):
      - Anthropic: tool.input_schema
      - OpenAI: response_format.json_schema.schema (strict:true)
      - Google: GenerateContentConfig.response_schema

    Phase 2 wires this into each provider's request shape. See prompts/README.md.
    """
    strict = copy.deepcopy(CANONICAL_SCHEMA)
    strict["additionalProperties"] = False
    strict["required"] = list(strict["properties"].keys())
    return strict

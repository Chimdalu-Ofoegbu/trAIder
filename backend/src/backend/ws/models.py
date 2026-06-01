"""
backend.ws.models — Frozen WS event Pydantic v2 models + standard envelope (IFACE-04).

Source of truth: Pydantic models here -> OpenAPI components -> openapi-typescript ->
committed frontend/types/api.ts -> CI drift gate (D-27).

Standard envelope (D-26):
  {seq, serverTs, chainTs, blockNumber, eventType, payload}
  - chainTs + blockNumber are nullable for purely operational events
  - Purely operational events carry latestBlockNumber + latestBlockTs instead

Channel topology (D-23, frozen):
  ws/vault/{vaultAddress}: NavTick, TradeEvent, JournalEvent, ModelStatus
  ws/global: ArbOpp, SessionEvent

Naming convention: snake_case field names (Pydantic default); serialized as snake_case.
Field names are within Claude's discretion per CONTEXT.md.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class JournalStateEnum(str, Enum):
    """Mirrors the journal_state Postgres ENUM defined in Plan 03 migrations (D-21)."""

    pending_pin = "pending_pin"
    pinned_primary = "pinned_primary"
    pinned_backup = "pinned_backup"
    signed = "signed"
    submitted = "submitted"
    recorded = "recorded"
    failed = "failed"


class ModelStatusEnum(str, Enum):
    """Model operational status — supports ORCH-06 downstream (circuit-breaker)."""

    active = "active"
    paused = "paused"
    malformed = "malformed"


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------


class _WsBase(BaseModel):
    """Shared Pydantic v2 config for all WS event models."""

    model_config = ConfigDict(
        # Pydantic v2 strict = reject extra fields at validation time
        extra="forbid",
        # Freeze instances so they are safely hashable (useful in reducers)
        frozen=True,
        # Populate from field name (snake_case) AND alias (camelCase) if ever needed
        populate_by_name=True,
    )


# ---------------------------------------------------------------------------
# NavTick — ws/vault/{vaultAddress} (D-23)
# ---------------------------------------------------------------------------


class NavTick(_WsBase):
    """
    Per-block NAV snapshot for one vault.

    navPerToken1e18: uint256 NAV-per-share scaled to 1e18 (string for bigint-safety).
    totalAssets: total USDC in the vault (string, 6-decimal USDC units, bigint-safe).
    ammPrice: Camelot AMM price for mTOKEN in the same 1e18 scale; nullable before pool seeded.
    """

    vault_address: str = Field(description="ERC-4626 vault contract address")
    nav_per_token_1e18: str = Field(
        description="NAV per mTOKEN share scaled to 1e18 (uint256 as string)"
    )
    total_assets: str = Field(description="Total USDC in vault, 6-decimal (uint256 as string)")
    block_number: int = Field(description="Block number at which NAV was computed")
    amm_price: str | None = Field(
        default=None, description="Camelot AMM price (1e18 scale); null before pool seeded"
    )


# ---------------------------------------------------------------------------
# TradeEvent — ws/vault/{vaultAddress} (D-23)
# ---------------------------------------------------------------------------


class TradeEvent(_WsBase):
    """
    Emitted when the orchestrator executes (or cancels) a position on GMX / MockPerps.

    action: 'open' | 'close' | 'adjust'  (D-05 action field subset).
    market: 'ETH' | 'BTC' | 'SOL'  (D-10 enumerated markets).
    sizeUsd: notional position size in USD (string for precision).
    leverage: effective leverage (float, 1.0..3.0 per spec 3x cap).
    txHash: the keeper-execution tx hash (from OrderExecuted event), NOT createOrder tx.
    """

    vault_address: str = Field(description="Vault that owns this position")
    order_key: str = Field(description="GMX / MockPerps orderKey (bytes32 as hex string)")
    action: Literal["open", "close", "adjust"] = Field(description="Trade action")
    market: Literal["ETH", "BTC", "SOL"] = Field(description="Perpetual market")
    side: Literal["long", "short"] = Field(description="Position direction")
    size_usd: str = Field(description="Notional size in USD (string for precision)")
    leverage: float = Field(description="Effective leverage (1.0..3.0)")
    tx_hash: str = Field(description="On-chain transaction hash from keeper execution")
    block_number: int = Field(description="Block number of execution")
    trade_hash: str | None = Field(
        default=None,
        description="Journal trade hash (bytes32 as hex); populated after journal pinned",
    )


# ---------------------------------------------------------------------------
# JournalEvent — ws/vault/{vaultAddress} (D-23)
# ---------------------------------------------------------------------------


class JournalEvent(_WsBase):
    """
    Emitted when a journal entry's state transitions (D-21 state machine).

    Carries the current journal_state so the frontend can show pin progress.
    pinata_cid / web3_storage_cid are null until the respective pin completes.
    """

    vault_address: str = Field(description="Vault associated with this journal entry")
    trade_hash: str = Field(description="Journal entry trade hash (bytes32 as hex)")
    pinata_cid: str | None = Field(
        default=None, description="Pinata IPFS CID; null until pinned_primary"
    )
    web3_storage_cid: str | None = Field(
        default=None, description="web3.storage CID; null until pinned_backup"
    )
    journal_state: JournalStateEnum = Field(description="Current state machine state (D-21)")


# ---------------------------------------------------------------------------
# ModelStatus — ws/vault/{vaultAddress} (D-23)
# ---------------------------------------------------------------------------


class ModelStatus(_WsBase):
    """
    Emitted when an LLM model's operational status changes (ORCH-06 circuit-breaker).

    model: the provider model string (e.g. 'claude-opus-4-7').
    status: active | paused | malformed  (maps to ORCH-06 enum).
    consecutiveFailures: counter incremented on validation/timeout failures; resets on success.
    reason: optional human-readable note (e.g. 'malformed JSON response: missing action field').
    """

    vault_address: str = Field(description="Vault managed by this model")
    model: str = Field(description="LLM model identifier (e.g. 'claude-opus-4-7')")
    status: ModelStatusEnum = Field(description="Operational status")
    consecutive_failures: int = Field(
        default=0, description="Consecutive validation/timeout failures"
    )
    reason: str | None = Field(default=None, description="Human-readable status note")


# ---------------------------------------------------------------------------
# ArbOpp — ws/global (D-23)
# ---------------------------------------------------------------------------


class ArbOpp(_WsBase):
    """
    Cross-vault arbitrage opportunity detected by the backend block-watcher (D-74).

    direction: 'mint' = NAV < AMM (arber profits by minting at NAV, selling at AMM premium).
               'burn' = NAV > AMM (arber profits by buying at AMM discount, burning at NAV).
    gapBps: |NAV - AMM| / NAV in basis points (1 bps = 0.01%).
    """

    vault_address: str = Field(description="Vault with the NAV-AMM gap")
    nav_price: str = Field(description="NAV per mTOKEN in 1e18 scale (uint256 as string)")
    amm_price: str = Field(description="Camelot AMM price in 1e18 scale (uint256 as string)")
    gap_bps: int = Field(description="Absolute gap in basis points")
    direction: Literal["mint", "burn"] = Field(description="Arbitrage direction")


# ---------------------------------------------------------------------------
# SessionEvent — ws/global (D-23)
# ---------------------------------------------------------------------------


class SessionEvent(_WsBase):
    """
    Lifecycle events for a trading session.

    kind: 'started' | 'hour_milestone' | 'settling' | 'settled'.
    hour: non-null only for 'hour_milestone' kind (e.g. 24, 48).
    """

    session_id: str = Field(description="Unique session identifier")
    kind: Literal["started", "hour_milestone", "settling", "settled"] = Field(
        description="Session lifecycle event kind"
    )
    hour: int | None = Field(
        default=None, description="Session hour; non-null only for hour_milestone"
    )


# ---------------------------------------------------------------------------
# CurrentState — snapshot-on-subscribe (D-24)
# ---------------------------------------------------------------------------


class CurrentState(_WsBase):
    """
    Snapshot bundle sent on every subscribe (initial + reconnect) for a vault (D-24).

    Read from backend.dashboard_model_state materialized view.
    Carries its own seq so the client baseline is anchored immediately.
    recentTrades: last N trades (typically 10-20 for initial render).
    latestJournal: most recent JournalEvent for this vault (null if no trades yet).
    """

    vault_address: str = Field(description="Vault this snapshot covers")
    latest_nav: NavTick = Field(description="Most recent NavTick for this vault")
    recent_trades: list[TradeEvent] = Field(
        default_factory=list, description="Recent trades (capped array)"
    )
    latest_journal: JournalEvent | None = Field(
        default=None, description="Most recent journal entry; null if none"
    )
    model_status: ModelStatus = Field(description="Current model operational status")
    seq: int = Field(
        description="Snapshot seq — client baseline for per-channel monotonic ordering (D-25)"
    )


# ---------------------------------------------------------------------------
# Envelope — standard wrapper for every WS event (D-26)
# ---------------------------------------------------------------------------


class Envelope(_WsBase):
    """
    Standard WS event envelope (D-26).

    Every event sent over ws/vault/* or ws/global is wrapped in this envelope.

    seq: per-channel monotonic sequence number (D-25). Client drops if seq <= lastAppliedSeq.
    serverTs: ISO 8601 UTC timestamp at event emission (indexer server time).
    chainTs: ISO 8601 UTC block timestamp; null for purely operational events (ModelStatus, SessionEvent).
    blockNumber: block number; null for purely operational events.
    latestBlockNumber / latestBlockTs: filled for purely operational events that carry no chain anchor.
    eventType: discriminator string — matches the class name of the payload model.
    payload: the serialized event model (dict, pre-validated by the emitter).

    Dashboard computes indexer lag as (serverTs - chainTs) with banner thresholds:
      testnet: banner at 15s; mainnet: banner at 5s (D-26).
    """

    seq: int = Field(description="Per-channel monotonic sequence number (D-25)")
    server_ts: str = Field(description="ISO 8601 UTC emission timestamp (server clock)")
    chain_ts: str | None = Field(
        default=None, description="ISO 8601 UTC block timestamp; null for operational events"
    )
    block_number: int | None = Field(
        default=None, description="Block number; null for operational events"
    )
    event_type: str = Field(description="Payload discriminator (class name of the event model)")
    payload: Any = Field(description="Serialized event model payload (dict)")
    # Operational-event anchors (filled when chainTs/blockNumber are null)
    latest_block_number: int | None = Field(
        default=None, description="Latest known block; filled for operational events"
    )
    latest_block_ts: str | None = Field(
        default=None,
        description="Latest known block ts (ISO 8601 UTC); filled for operational events",
    )

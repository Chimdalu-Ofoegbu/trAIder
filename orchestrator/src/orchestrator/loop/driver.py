"""
orchestrator.loop.driver — Per-cycle live ORCH-02 loop (Plan 02-05 / 03-02).

Composes the Wave-2 pieces into the live trade loop:

  run_live_cycle(...)
      Single ORCH-02 cycle: prompt → call_claude → validate → business-rules →
      record-intent (BEFORE submit) → vault.openLong/openShort/closePosition → promote.

  reconcile_pending_orders(...)
      Startup ORCH-08 reconciliation: reads unresolved intent/pending rows and
      checks each against the adapter's pendingOrders to determine if the submit landed.

  run_session(...)
      Session driver: creates session, reconciles, launches price_pusher + keeper
      as asyncio.Tasks, runs the cycle loop until session_duration_seconds, then
      ends the session cleanly (D-12 — positions left open, NO close-all).

SC-2 record-intent-before-submit ordering guarantee
---------------------------------------------------
In run_live_cycle step 8, the order of operations is MANDATORY:
  8a. Compute intent_key (pure, no network).
  8b. record_journal_pending + record_pending_order(status='intent')  ← DB write FIRST
  8c. vault_contract.openLong/openShort/closePosition.transact(...)   ← network call SECOND
  8d. Promote intent row to real order_key + mark_pending_order_reconciled(intent_key)

A SIGKILL between 8b and 8c leaves an 'intent' row with no on-chain order.
On restart, reconcile_pending_orders sees vault==0 for that intent key → safe to
resubmit once.  A SIGKILL between 8c and 8d leaves an 'intent' row AND an on-chain
order.  On restart, reconcile sees vault!=0 → do NOT resubmit (keeper will execute).

D-17 two-counter design
-----------------------
api_failure_streak : pause@3  (APITimeoutError / RateLimitError / 5xx / connection)
malformed_streak   : pause@5  (no ToolUseBlock OR validate_decision None)
Both counters reset to 0 on a successful valid parse.
One malformed → surface "malformed, no trade" + journal the raw response; do NOT pause.

D-12 session end
----------------
stop_event.set() → price_pusher and keeper stop; end_session marks DB row ended;
positions are LEFT OPEN (settlement contract drains them separately).

D-16 REQUIRED-REGARDLESS (03-02): trade submission
---------------------------------------------------
Trades are submitted by calling vault_contract.functions.(openLong|openShort|closePosition)
as the operator-trade EOA — NOT by direct adapter calls with anvil from-impersonation.
The vault's onlyOrchestrator modifier requires msg.sender == operator-trade key.
  - Trade SUBMISSION path: vault_contract.functions.* → operator_trade_address
  - Event/read path: mock_perps / adapter contract object → unchanged (venue-agnostic)

Signing middleware (SignAndSendRawMiddlewareBuilder, web3.py 7.x) is loaded once at
startup in run_session.  On anvil, the operator-trade key is anvil account[N].  On
Sepolia, it is the gitignored OPERATOR_TRADE_KEY from .env (SEC-01).

FORBIDDEN in this module (D-13 / T-02-21):
  adapter.executeOrder — that is the keeper_monitor's job.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from web3.middleware import SignAndSendRawMiddlewareBuilder

from orchestrator.business_rules import validate_business_rules
from orchestrator.loop.failure_tracker import FailureTracker
from orchestrator.loop.keeper_monitor import run_keeper_monitor
from orchestrator.loop.market_state import (
    build_market_table,
    build_market_table_from_snapshot,
    read_mark_prices,
)
from orchestrator.loop.nonce_manager import submit_op_tx
from orchestrator.loop.price_pusher import PriceWalk, run_price_pusher
from orchestrator.loop.session import SessionConfig, format_session_duration, format_time_remaining
from orchestrator.loop.settlement_keeper import run_settlement_keeper
from orchestrator.mock_harness import _make_envelope, _publish
from orchestrator.providers.anthropic_adapter import (
    call_claude,
    classify_exception,
    extract_tool_input,
    validate_decision,
)
from orchestrator.state.db import (
    create_session,
    end_session,
    get_latest_model_status,
    get_unresolved_pending_orders,
    has_unresolved_pending_order,
    mark_pending_order_reconciled,
    record_journal_pending,
    record_model_status,
    record_pending_order,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# _emit_diagnostic — gated observe-only JSONL capture (TEST-03 trade-gap investigation)
#
# Activated ONLY when env var DIAGNOSTIC_CAPTURE is set to a non-empty file path.
# When unset/empty: this function is a pure no-op with zero overhead.
# The try/except ensures a capture failure can NEVER affect the trading cycle.
# ---------------------------------------------------------------------------


def _emit_diagnostic(  # noqa: PLR0913 (many params by design — diagnostic)
    *,
    capture_path: str | None,
    cycle: int,
    prompt: str | None,
    raw_response: Any,
    parsed_decision: dict | None,
    rationale: str | None,
    outcome: str,
    malformed_reason: str | None,
) -> None:
    """Append one JSONL line to DIAGNOSTIC_CAPTURE file (observe-only, TEST-03).

    One line per cycle, covering every branch (hold / open / malformed / rejected).
    No-op when capture_path is None or empty.  Never raises — capture errors are
    logged as warnings so the trading cycle is never affected.
    """
    if not capture_path:
        return
    try:
        record = {
            "cycle": cycle,
            "ts": datetime.now(UTC).isoformat(),
            "prompt": prompt,
            "raw_response": (
                raw_response
                if isinstance(raw_response, dict | list | type(None))
                else str(raw_response)
            ),
            "parsed_decision": parsed_decision,
            "rationale": rationale,
            "outcome": outcome,
            "malformed_reason": malformed_reason,
        }
        with open(capture_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, default=str) + "\n")
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "DIAGNOSTIC_CAPTURE write failed (cycle=%d, path=%s): %s — cycle unaffected",
            cycle,
            capture_path,
            exc,
        )


# ---------------------------------------------------------------------------
# _build_open_positions — on-chain position map (WR-03)
# ---------------------------------------------------------------------------


async def _build_open_positions(mock_perps: Any, vault: str) -> dict[str, Any]:
    """Build a market→position dict from on-chain state for the vault.

    Calls getOpenPositionKeys(vault) and reads each Position struct, producing
    a dict keyed by market string.  This is restart-safe: it reflects actual
    chain state rather than in-memory guesses, so the D-10 one-position-per-asset
    check is authoritative even after a SIGKILL+restart.

    Returns a dict:
        {market: {"position_key": "0x...", "side": "long"|"short", "size_usd": float}}

    Returns an empty dict if no positions are open.
    """
    try:
        keys: list[bytes] = await mock_perps.functions.getOpenPositionKeys(vault).call()
    except Exception as exc:  # noqa: BLE001
        logger.warning("_build_open_positions: getOpenPositionKeys failed: %s", exc)
        return {}

    result: dict[str, Any] = {}
    for key_bytes in keys:
        key_hex = "0x" + key_bytes.hex()
        try:
            # positions(bytes32) returns the Position struct as a tuple:
            # (market, signedSize, entryPrice, collateral, vault, closed)
            pos = await mock_perps.functions.positions(key_bytes).call()
            market: str = pos[0]
            signed_size: int = pos[1]
            closed: bool = pos[5]
            if closed:
                continue
            side = "long" if signed_size > 0 else "short"
            # sizeUsd is stored 1e30-scaled; convert to float USD
            size_usd = abs(signed_size) / 1e30
            result[market] = {
                "position_key": key_hex,
                "side": side,
                "size_usd": size_usd,
            }
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "_build_open_positions: failed to read position %s: %s", key_hex[:10], exc
            )

    return result


# ---------------------------------------------------------------------------
# run_live_cycle — single ORCH-02 cycle
# ---------------------------------------------------------------------------


async def run_live_cycle(
    web3: Any,
    mock_perps: Any,
    vault: str,
    model: str,
    cycle: int,
    *,
    config: SessionConfig,
    walk: Any,
    aggregators: dict[str, Any],
    tracker: FailureTracker,
    db: Any,
    redis: Any | None,
    session_id: str,
    seq: int,
    available_usdc: float,
    open_positions: dict[str, Any],
    nav_table: str,
    positions_table: str,
    recent_decisions: str,
    elapsed_seconds: float,
    market_snapshot: dict[str, dict[str, float]] | None = None,
    vault_contract: Any = None,
    operator_trade_address: str | None = None,
    # D-11 multi-model: shared NonceManager for the operator-trade EOA. None → auto-nonce.
    nonce_manager: Any = None,
) -> dict:
    """Execute one live trading cycle (ORCH-02 sequence).

    Args:
        web3: AsyncWeb3 instance.
        mock_perps: Adapter contract instance (MockPerps or GMXAdapter).
                    Used for EVENT DECODING and READ CALLS only (D-16 split):
                    getOpenPositionKeys, positions, pendingOrders, OrderCreated event.
        vault: Vault address string (checksummed hex).
        model: LLM model identifier (e.g. 'claude-opus-4-7').
        cycle: 1-based cycle number within the session.
        config: SessionConfig for cadence / duration / etc.
        walk: PriceWalk instance (shared with price_pusher).
        aggregators: Mapping of asset → MockChainlinkAggregator contract.
        tracker: FailureTracker (shared across cycles — maintains streaks).
        db: AsyncSession for orchestrator DB writes.
        redis: Optional redis.asyncio client for WS event publishing.
        session_id: Active session UUID string.
        seq: Per-channel sequence number for WS envelopes.
        available_usdc: Undeployed USDC balance for capital check.
        open_positions: Mapping of market → position dict (D-10 check).
        nav_table: Pre-rendered NAV table string for the prompt.
        positions_table: Pre-rendered positions table string for the prompt.
        recent_decisions: Last-N-cycles decision summary for the prompt.
        elapsed_seconds: Seconds elapsed since session start (for time_remaining).
        market_snapshot: Optional consistent per-step snapshot from price_pusher
            (CR-03 fix).  If provided, all market_table values (mark, funding,
            change_24h) come from this snapshot's single step.  When None, falls
            back to reading mark prices from the aggregator and deriving funding/24h
            from the walk (may be one step behind price_pusher — only for backwards
            compat / testing without a snapshot queue).
        vault_contract: MTokenVault contract instance (D-16). TRADE SUBMISSION goes
            through this contract's openLong / openShort / closePosition (onlyOrchestrator
            enforces msg.sender == operator_trade_address). When None (legacy/test),
            falls back to calling the adapter directly with vault from-impersonation
            (anvil-only path — NOT for Sepolia).
        operator_trade_address: Checksummed address of the operator-trade EOA. Signing
            middleware must already be loaded on web3 for this address before the first
            cycle (see run_session). When None, falls back to the legacy vault from-impersonation.

    Returns:
        Result dict with keys:
          status: 'ok' | 'api_failure' | 'malformed' | 'rejected' | 'submitted'
          action: 'hold' | 'open' | 'adjust' | 'close' (on status='ok' or 'submitted')
          order_key: hex bytes32 (on status='submitted')
          reason: human-readable string (on status='rejected' or 'api_failure')
    """
    from backend.ws.channels import channel_for

    vault_channel = channel_for("ModelStatus", vault_address=vault)

    # ── Step 1–3: Build prompt ────────────────────────────────────────────────
    # CR-03: prefer market_snapshot (from price_pusher via snapshot_queue) so mark,
    # funding, and 24h% all come from the same walk step.  Fall back to the legacy
    # on-chain read + walk-derived path when no snapshot is available (backwards compat).
    if market_snapshot is not None:
        market_table = build_market_table_from_snapshot(market_snapshot)
        # Still read mark prices for any downstream callers (unused in this path but
        # kept for consistency with the existing function signature contract)
        prices = {asset: v["mark"] for asset, v in market_snapshot.items()}
    else:
        prices = await read_mark_prices(aggregators)
        market_table = build_market_table(walk, prices)
    time_remaining = format_time_remaining(elapsed_seconds, config.session_duration_seconds)
    session_duration = format_session_duration(config.session_duration_seconds)

    from orchestrator.loop.market_state import render_prompt

    prompt = render_prompt(
        nav_table=nav_table,
        time_remaining=time_remaining,
        positions_table=positions_table,
        available_usdc=available_usdc,
        recent_decisions=recent_decisions,
        market_table=market_table,
        session_duration=session_duration,
    )

    # TEST-03 diagnostic capture — resolved once per cycle; None when env unset/empty.
    _diag_path: str | None = os.environ.get("DIAGNOSTIC_CAPTURE") or None

    # ── Step 4: Call Claude (api_failure path on exception) ───────────────────
    try:
        response = await call_claude(prompt, model=model)
    except Exception as exc:
        kind = classify_exception(exc)
        tracker.record_api_failure()
        paused = tracker.should_pause()
        status_str = "paused" if paused else "active"
        logger.warning(
            "Cycle %d: %s exception (streak=%d paused=%s): %s",
            cycle,
            kind,
            tracker.api_failure_streak,
            paused,
            exc,
        )
        await record_model_status(
            db,
            vault_address=vault,
            session_id=session_id,
            model=model,
            status=status_str,
            consecutive_failures=tracker.consecutive(),
            api_failure_streak=tracker.api_failure_streak,
            malformed_streak=tracker.malformed_streak,
            reason=f"api_failure: {exc}",
            cycle_number=cycle,
        )
        status_payload = {
            "vault_address": vault,
            "model": model,
            "status": status_str,
            "consecutive_failures": tracker.consecutive(),
            "reason": f"api_failure: {exc}",
        }
        envelope = _make_envelope("ModelStatus", status_payload, seq=seq)
        await _publish(redis, vault_channel, envelope)
        # TEST-03 diagnostic capture (DIAGNOSTIC_CAPTURE env — observe-only, no side effects)
        _emit_diagnostic(
            capture_path=_diag_path,
            cycle=cycle,
            prompt=prompt,
            raw_response=None,
            parsed_decision=None,
            rationale=None,
            outcome="api_failure",
            malformed_reason=f"api_failure: {exc}",
        )
        return {"status": "api_failure", "reason": str(exc), "kind": kind}

    # ── Step 5: Extract + validate ────────────────────────────────────────────
    raw = extract_tool_input(response)
    if raw is None:
        tracker.record_malformed()
        paused = tracker.should_pause()
        status_str = "paused" if paused else "malformed"
        reason = "no ToolUseBlock (content-policy/refusal)"
        logger.warning(
            "Cycle %d: malformed — %s (streak=%d paused=%s)",
            cycle,
            reason,
            tracker.malformed_streak,
            paused,
        )
        await record_model_status(
            db,
            vault_address=vault,
            session_id=session_id,
            model=model,
            status=status_str,
            consecutive_failures=tracker.consecutive(),
            api_failure_streak=tracker.api_failure_streak,
            malformed_streak=tracker.malformed_streak,
            reason=reason,
            cycle_number=cycle,
        )
        # D-07/D-08: journal the malformed cycle (raw request + raw response), NO trade fields
        await record_journal_pending(
            db,
            vault_address=vault,
            order_key=f"malformed-{session_id}-{cycle}",
            raw_request={"prompt": prompt},
            raw_response={"_malformed": True, "reason": reason},
        )
        status_payload = {
            "vault_address": vault,
            "model": model,
            "status": status_str,
            "consecutive_failures": tracker.consecutive(),
            "reason": reason,
        }
        envelope = _make_envelope("ModelStatus", status_payload, seq=seq)
        await _publish(redis, vault_channel, envelope)
        # TEST-03 diagnostic capture (DIAGNOSTIC_CAPTURE env — observe-only, no side effects)
        _emit_diagnostic(
            capture_path=_diag_path,
            cycle=cycle,
            prompt=prompt,
            raw_response=None,
            parsed_decision=None,
            rationale=None,
            outcome="malformed",
            malformed_reason=reason,
        )
        return {"status": "malformed", "reason": reason}

    decision = validate_decision(raw)
    if decision is None:
        tracker.record_malformed()
        paused = tracker.should_pause()
        status_str = "paused" if paused else "malformed"
        reason = "Decision.model_validate failed"
        logger.warning(
            "Cycle %d: malformed — %s (streak=%d paused=%s)",
            cycle,
            reason,
            tracker.malformed_streak,
            paused,
        )
        await record_model_status(
            db,
            vault_address=vault,
            session_id=session_id,
            model=model,
            status=status_str,
            consecutive_failures=tracker.consecutive(),
            api_failure_streak=tracker.api_failure_streak,
            malformed_streak=tracker.malformed_streak,
            reason=reason,
            cycle_number=cycle,
        )
        # Journal raw response without trade fields (D-07/D-08)
        await record_journal_pending(
            db,
            vault_address=vault,
            order_key=f"malformed-{session_id}-{cycle}",
            raw_request={"prompt": prompt},
            raw_response={"_malformed": True, "reason": reason, "raw": raw},
        )
        status_payload = {
            "vault_address": vault,
            "model": model,
            "status": status_str,
            "consecutive_failures": tracker.consecutive(),
            "reason": reason,
        }
        envelope = _make_envelope("ModelStatus", status_payload, seq=seq)
        await _publish(redis, vault_channel, envelope)
        # TEST-03 diagnostic capture (DIAGNOSTIC_CAPTURE env — observe-only, no side effects)
        _emit_diagnostic(
            capture_path=_diag_path,
            cycle=cycle,
            prompt=prompt,
            raw_response=raw,
            parsed_decision=None,
            rationale=None,
            outcome="malformed",
            malformed_reason=reason,
        )
        return {"status": "malformed", "reason": reason}

    # ── Step 6: Valid parse — reset both streaks (D-17) ──────────────────────
    recovered = tracker.record_success()
    if recovered:
        logger.info("Cycle %d: recovered from paused state — resetting to active", cycle)
        await record_model_status(
            db,
            vault_address=vault,
            session_id=session_id,
            model=model,
            status="active",
            consecutive_failures=0,
            api_failure_streak=0,
            malformed_streak=0,
            reason="auto-recovered",
            cycle_number=cycle,
        )

    # Hold path — journal the hold (request + response, no trade fields, D-08)
    if decision.action == "hold":
        logger.info("Cycle %d: action=hold — no trade this cycle", cycle)
        await record_journal_pending(
            db,
            vault_address=vault,
            order_key=f"hold-{session_id}-{cycle}",
            raw_request={"prompt": prompt},
            raw_response=raw,
            canonical_decision=decision.model_dump(),
        )
        # TEST-03 diagnostic capture (DIAGNOSTIC_CAPTURE env — observe-only, no side effects)
        _emit_diagnostic(
            capture_path=_diag_path,
            cycle=cycle,
            prompt=prompt,
            raw_response=raw,
            parsed_decision=decision.model_dump(),
            rationale=getattr(decision, "rationale", None),
            outcome="hold",
            malformed_reason=None,
        )
        return {"status": "ok", "action": "hold"}

    # ── Step 7: Business rules gate (D-09/D-10 — reject-as-no-trade) ─────────
    rejection_reason = validate_business_rules(decision, available_usdc, open_positions)
    if rejection_reason is not None:
        logger.warning(
            "Cycle %d: business-rule reject — %s",
            cycle,
            rejection_reason,
        )
        await record_model_status(
            db,
            vault_address=vault,
            session_id=session_id,
            model=model,
            status="active",
            consecutive_failures=0,
            api_failure_streak=0,
            malformed_streak=0,
            reason=f"invalid decision: {rejection_reason}",
            cycle_number=cycle,
        )
        # D-09: journal with reason annotation; NO MockPerps call
        await record_journal_pending(
            db,
            vault_address=vault,
            order_key=f"rejected-{session_id}-{cycle}",
            raw_request={"prompt": prompt},
            raw_response=raw,
            canonical_decision=decision.model_dump(),
        )
        # TEST-03 diagnostic capture (DIAGNOSTIC_CAPTURE env — observe-only, no side effects)
        _emit_diagnostic(
            capture_path=_diag_path,
            cycle=cycle,
            prompt=prompt,
            raw_response=raw,
            parsed_decision=decision.model_dump(),
            rationale=getattr(decision, "rationale", None),
            outcome="rejected",
            malformed_reason=rejection_reason,
        )
        return {"status": "rejected", "reason": rejection_reason}

    # ── Step 8: RECORD-INTENT BEFORE SUBMIT (ORCH-08 / SC-2) ─────────────────
    #
    # ARCH-X submission gate: before writing the intent row, verify that no prior
    # order for this vault is still in-flight (status='intent' or 'pending').
    # On Sepolia, keeper execution takes ~40-60s; without this gate the driver
    # submits a second order over the first, hits "Vault: order in flight" revert,
    # and crashes the session.
    #
    # Single-owner / no TOCTOU rationale:
    #   - The decision loop is the SOLE submitter per vault.
    #   - The keeper only CLEARS (mark_pending_order_executed / reconciled) — never submits.
    #   - The event loop is cooperative: there is NO async yield between this EXISTS
    #     check and record_pending_order(status='intent') two lines below, which
    #     acquires the "lock" by creating the intent row.  No concurrent Task can
    #     interleave a submission for the same vault in this window.
    if await has_unresolved_pending_order(db, vault_address=vault):
        logger.info(
            "Cycle %d vault=%s: order still in-flight — skipping submission this cycle "
            "(ARCH-X gate: prior order pending/intent, will retry next cycle)",
            cycle,
            vault[:10],
        )
        # Journal the skipped cycle as a hold so the audit log is complete.
        await record_journal_pending(
            db,
            vault_address=vault,
            order_key=f"skipped-inflight-{session_id}-{cycle}",
            raw_request={"prompt": prompt},
            raw_response=raw,
            canonical_decision=decision.model_dump(),
        )
        _emit_diagnostic(
            capture_path=_diag_path,
            cycle=cycle,
            prompt=prompt,
            raw_response=raw,
            parsed_decision=decision.model_dump(),
            rationale=getattr(decision, "rationale", None),
            outcome="skipped_inflight",
            malformed_reason="order still in-flight — submission skipped (ARCH-X gate)",
        )
        return {
            "status": "skipped_inflight",
            "action": decision.action,
            "reason": "order pending — skipping submit this cycle",
        }

    # 8a. Compute intent key (deterministic, no network) and execution block.
    current_block = await web3.eth.get_block_number()
    execution_delay = await mock_perps.functions.executionDelay().call()
    execute_after_block = current_block + execution_delay
    # Synthetic intent key — encodes cycle + market so it is unique per submit attempt.
    # This key will NEVER be an on-chain bytes32; it starts with "intent-" to distinguish
    # it from real order keys in reconcile_pending_orders.
    intent_key = f"intent-{session_id}-{cycle}-{decision.market}"

    # 8b. WRITE THE INTENT ROW *FIRST* (before any .transact) — idempotent on
    #     UNIQUE(vault_address, order_key) + ON CONFLICT DO NOTHING (safe to re-run):
    await record_journal_pending(
        db,
        vault_address=vault,
        order_key=intent_key,
        raw_request={"prompt": prompt},
        raw_response=raw,
        canonical_decision=decision.model_dump(),
    )
    await record_pending_order(
        db,
        vault_address=vault,
        order_key=intent_key,
        session_id=session_id,
        execute_after_block=execute_after_block,
        status="intent",
        decision_snapshot=decision.model_dump(),
    )
    # <-- record_journal_pending / record_pending_order appear on EARLIER SOURCE LINES
    #     than the .transact(...) below.  Keep this ordering — it is grep-verifiable
    #     (acceptance_criteria: first record_pending_order( line < first .transact( line).

    # 8b-pre. PRE-TRADE FEED-AGE CHECK (GAP #1/#7): before submitting, verify that the
    # relevant Chainlink aggregator for decision.market is not stale. MockPerps has
    # MAX_STALENESS=3600s; submitting into a stale feed → guaranteed revert "stale price".
    # We use a 3000s conservative buffer (600s headroom under the 3600s limit).
    # If the feed is stale: mark the intent row reconciled, fire WARNING alert, skip trade.
    # Market → aggregator mapping uses the aggregators dict from the session.
    _STALE_THRESHOLD_S = 3000  # conservative buffer under MAX_STALENESS=3600s
    _feed_aggr = aggregators.get(decision.market)
    if _feed_aggr is not None:
        try:
            _block = await web3.eth.get_block("latest")
            _block_ts: int = int(_block["timestamp"])
            # latestRoundData() → (roundId, answer, startedAt, updatedAt, answeredInRound)
            _round_data = await _feed_aggr.functions.latestRoundData().call()
            _feed_updated_at: int = int(_round_data[3])  # index 3 = updatedAt
            _feed_age_s: int = _block_ts - _feed_updated_at
            if _feed_age_s > _STALE_THRESHOLD_S:
                # Feed is too stale — submitting will revert "MockPerps: stale price"
                stale_reason = (
                    f"pre-trade staleness check FAILED: {decision.market} feed age "
                    f"{_feed_age_s}s > threshold {_STALE_THRESHOLD_S}s "
                    f"(MockPerps MAX_STALENESS=3600s) — skipping trade this cycle (GAP #1/#7)"
                )
                logger.warning("Cycle %d: %s", cycle, stale_reason)
                from orchestrator.alerts.sink import AlertSeverity
                from orchestrator.alerts.sink import send_alert as _send_alert

                await _send_alert(
                    stale_reason,
                    AlertSeverity.WARNING,
                    context={
                        "market": decision.market,
                        "feed_age_s": str(_feed_age_s),
                        "threshold_s": str(_STALE_THRESHOLD_S),
                        "vault_address": vault,
                        "cycle": str(cycle),
                    },
                )
                # Clear the intent row — no trade this cycle
                await mark_pending_order_reconciled(db, vault_address=vault, order_key=intent_key)
                _emit_diagnostic(
                    capture_path=_diag_path,
                    cycle=cycle,
                    prompt=prompt,
                    raw_response=raw,
                    parsed_decision=decision.model_dump(),
                    rationale=getattr(decision, "rationale", None),
                    outcome="skipped_stale_feed",
                    malformed_reason=stale_reason,
                )
                return {
                    "status": "skipped_stale_feed",
                    "action": decision.action,
                    "reason": stale_reason,
                    "intent_key": intent_key,
                }
        except Exception as stale_exc:  # noqa: BLE001
            # Staleness check error (e.g. aggregator not wired in tests) — log WARNING
            # and CONTINUE (fail open on the check, not on the trade).
            logger.warning(
                "Cycle %d: pre-trade staleness check raised (non-fatal, submitting anyway): %s",
                cycle,
                stale_exc,
            )
    else:
        logger.debug(
            "Cycle %d: no aggregator for market=%s — skipping pre-trade staleness check",
            cycle,
            decision.market,
        )

    # 8c. ONLY NOW submit to MockPerps (the network call):
    size_usd_1e30 = int(decision.sizeUsd * 1e30)
    leverage_1e4 = int(decision.leverage * 1e4)
    slippage_bps = 50  # 0.5% — ignored by mock

    logger.info(
        "Cycle %d: submitting %s %s %s sizeUsd=%s leverage=%sx",
        cycle,
        decision.action,
        decision.market,
        decision.side,
        decision.sizeUsd,
        decision.leverage,
    )

    # D-16 REQUIRED-REGARDLESS (03-02): determine the trade submission contract.
    # When vault_contract + operator_trade_address are provided (Sepolia-capable path):
    #   → submit via vault_contract.functions.* as the operator-trade EOA
    #     (signing middleware on web3 signs automatically)
    # When not provided (legacy anvil-only from-impersonation fallback):
    #   → submit via adapter directly with {"from": vault}
    #     (only works when anvil has the vault address unlocked)
    _use_vault_submit = vault_contract is not None and operator_trade_address is not None

    # CR-02/WR-03: action-based dispatch — NEVER route close/adjust to openLong/openShort.
    if decision.action == "close":
        # Close requires an existing position; look it up from the on-chain map.
        existing = open_positions.get(decision.market)
        if existing is None:
            reason = f"close requested but no open position for {decision.market}, no trade"
            logger.warning("Cycle %d: %s", cycle, reason)
            await record_journal_pending(
                db,
                vault_address=vault,
                order_key=intent_key,
                raw_request={"prompt": prompt},
                raw_response=raw,
                canonical_decision=decision.model_dump(),
            )
            # Flip the intent row to reconciled (no order created) so it won't linger
            await mark_pending_order_reconciled(db, vault_address=vault, order_key=intent_key)
            # TEST-03 diagnostic capture (DIAGNOSTIC_CAPTURE env — observe-only, no side effects)
            _emit_diagnostic(
                capture_path=_diag_path,
                cycle=cycle,
                prompt=prompt,
                raw_response=raw,
                parsed_decision=decision.model_dump(),
                rationale=getattr(decision, "rationale", None),
                outcome="rejected",
                malformed_reason=reason,
            )
            return {"status": "rejected", "reason": reason}
        pos_key_hex: str = existing["position_key"]
        pos_key_bytes = bytes.fromhex(pos_key_hex.removeprefix("0x"))
        # ARCH-X belt-and-suspenders: wrap .transact() in try/except so that if the
        # in-flight gate above was somehow bypassed (e.g. extreme timing edge case)
        # and the vault reverts "Vault: order in flight", the session NEVER crashes.
        # The except block cleans up the intent row and continues to the next cycle.
        try:
            # D-16: submit via vault_contract as operator-trade EOA (Sepolia-capable path)
            if _use_vault_submit:
                tx = await submit_op_tx(
                    vault_contract.functions.closePosition(pos_key_bytes, size_usd_1e30),
                    operator_trade_address,
                    nonce_manager=nonce_manager,
                )
            else:
                # Legacy anvil from-impersonation (not for Sepolia)
                tx = await mock_perps.functions.closePosition(
                    pos_key_bytes, size_usd_1e30
                ).transact({"from": vault})
        except Exception as exc:  # noqa: BLE001
            reason = f"transact failed (belt-and-suspenders catch): {exc}"
            _is_inflight = (
                "order in flight" in str(exc).lower() or "tradinglock" in str(exc).lower()
            )
            log_fn = logger.warning if _is_inflight else logger.error
            log_fn(
                "Cycle %d: %s — intent cleared, loop continues (ARCH-X graceful-catch)",
                cycle,
                reason,
            )
            await mark_pending_order_reconciled(db, vault_address=vault, order_key=intent_key)
            _emit_diagnostic(
                capture_path=_diag_path,
                cycle=cycle,
                prompt=prompt,
                raw_response=raw,
                parsed_decision=decision.model_dump(),
                rationale=getattr(decision, "rationale", None),
                outcome="error",
                malformed_reason=reason,
            )
            return {"status": "error", "reason": reason, "intent_key": intent_key}
    elif decision.action == "adjust":
        # Adjust is not cleanly supported by MockPerps (no partial-size modification).
        # Reject safely rather than silently opening a new position. (D-09 reject pattern)
        reason = "adjust not supported this cycle, no trade"
        logger.warning("Cycle %d: %s", cycle, reason)
        await record_journal_pending(
            db,
            vault_address=vault,
            order_key=intent_key,
            raw_request={"prompt": prompt},
            raw_response=raw,
            canonical_decision=decision.model_dump(),
        )
        await mark_pending_order_reconciled(db, vault_address=vault, order_key=intent_key)
        # TEST-03 diagnostic capture (DIAGNOSTIC_CAPTURE env — observe-only, no side effects)
        _emit_diagnostic(
            capture_path=_diag_path,
            cycle=cycle,
            prompt=prompt,
            raw_response=raw,
            parsed_decision=decision.model_dump(),
            rationale=getattr(decision, "rationale", None),
            outcome="rejected",
            malformed_reason=reason,
        )
        return {"status": "rejected", "reason": reason}
    else:
        # action == "open": proceed with openLong/openShort.
        # ARCH-X belt-and-suspenders: same transact-level catch as the close path above.
        try:
            # D-16: submit via vault_contract as operator-trade EOA (Sepolia-capable path)
            if _use_vault_submit:
                open_fn = (
                    vault_contract.functions.openLong
                    if decision.side == "long"
                    else vault_contract.functions.openShort
                )
                tx = await submit_op_tx(
                    open_fn(decision.market, size_usd_1e30, leverage_1e4, slippage_bps),
                    operator_trade_address,
                    nonce_manager=nonce_manager,
                )
            else:
                # Legacy anvil from-impersonation (not for Sepolia)
                open_fn = (
                    mock_perps.functions.openLong
                    if decision.side == "long"
                    else mock_perps.functions.openShort
                )
                tx = await open_fn(
                    decision.market, size_usd_1e30, leverage_1e4, slippage_bps
                ).transact({"from": vault})
        except Exception as exc:  # noqa: BLE001
            reason = f"transact failed (belt-and-suspenders catch): {exc}"
            _is_inflight = (
                "order in flight" in str(exc).lower() or "tradinglock" in str(exc).lower()
            )
            log_fn = logger.warning if _is_inflight else logger.error
            log_fn(
                "Cycle %d: %s — intent cleared, loop continues (ARCH-X graceful-catch)",
                cycle,
                reason,
            )
            await mark_pending_order_reconciled(db, vault_address=vault, order_key=intent_key)
            _emit_diagnostic(
                capture_path=_diag_path,
                cycle=cycle,
                prompt=prompt,
                raw_response=raw,
                parsed_decision=decision.model_dump(),
                rationale=getattr(decision, "rationale", None),
                outcome="error",
                malformed_reason=reason,
            )
            return {"status": "error", "reason": reason, "intent_key": intent_key}

    # GAP-1a fix: use wait_for_transaction_receipt (not get_transaction_receipt) to avoid
    # TransactionNotFound race on anvil, and wrap the entire receipt + event-recovery block
    # in try/except so ANY on-chain revert (status==0) or timeout is journaled as a cycle
    # error instead of crashing the session loop (SC-1 requires loop survives bad trades).
    try:
        receipt = await web3.eth.wait_for_transaction_receipt(tx, timeout=30)
    except Exception as exc:  # noqa: BLE001
        # TransactionNotFound, TimeExhausted, or unexpected web3 error — journal and continue
        reason = f"receipt retrieval failed: {exc}"
        logger.error("Cycle %d: %s (tx=%s)", cycle, reason, tx.hex() if hasattr(tx, "hex") else tx)
        await mark_pending_order_reconciled(db, vault_address=vault, order_key=intent_key)
        # TEST-03 diagnostic capture (DIAGNOSTIC_CAPTURE env — observe-only, no side effects)
        _emit_diagnostic(
            capture_path=_diag_path,
            cycle=cycle,
            prompt=prompt,
            raw_response=raw,
            parsed_decision=decision.model_dump(),
            rationale=getattr(decision, "rationale", None),
            outcome="error",
            malformed_reason=reason,
        )
        return {"status": "error", "reason": reason, "intent_key": intent_key}

    # On-chain revert: status==0 means the transaction was included but reverted.
    if receipt.get("status") == 0:
        reason = f"on-chain revert (tx={receipt.get('transactionHash', b'').hex()[:10]})"
        logger.error(
            "Cycle %d: %s market=%s side=%s — journaling as cycle error, loop continues",
            cycle,
            reason,
            decision.market,
            decision.side,
        )
        await mark_pending_order_reconciled(db, vault_address=vault, order_key=intent_key)
        # TEST-03 diagnostic capture (DIAGNOSTIC_CAPTURE env — observe-only, no side effects)
        _emit_diagnostic(
            capture_path=_diag_path,
            cycle=cycle,
            prompt=prompt,
            raw_response=raw,
            parsed_decision=decision.model_dump(),
            rationale=getattr(decision, "rationale", None),
            outcome="error",
            malformed_reason=reason,
        )
        return {"status": "error", "reason": reason, "intent_key": intent_key}

    # Recover the REAL order_key from the OrderCreated event (mock_harness CR-01 pattern):
    created = mock_perps.events.OrderCreated().process_receipt(receipt)
    if not created:
        logger.error("Cycle %d: OrderCreated event not found in receipt", cycle)
        await mark_pending_order_reconciled(db, vault_address=vault, order_key=intent_key)
        # TEST-03 diagnostic capture (DIAGNOSTIC_CAPTURE env — observe-only, no side effects)
        _emit_diagnostic(
            capture_path=_diag_path,
            cycle=cycle,
            prompt=prompt,
            raw_response=raw,
            parsed_decision=decision.model_dump(),
            rationale=getattr(decision, "rationale", None),
            outcome="error",
            malformed_reason="OrderCreated event not found in open tx receipt",
        )
        return {
            "status": "error",
            "error": "OrderCreated event not found in open tx receipt",
            "intent_key": intent_key,
        }
    order_key_hex = "0x" + created[0]["args"]["orderKey"].hex()

    # Normalize submit tx hash to 0x-prefixed hex string (GAP #10: stored for reconcile).
    _raw_tx_hex = tx.hex() if hasattr(tx, "hex") else str(tx)
    submit_tx_hash_hex = _raw_tx_hex if _raw_tx_hex.startswith("0x") else "0x" + _raw_tx_hex

    # 8d. PROMOTE the intent row: insert the real-key row and mark intent reconciled.
    # submit_tx_hash is stored so reconcile_pending_orders can detect a pending-in-mempool
    # tx on restart and suppress duplicate resubmission (GAP #10).
    await record_pending_order(
        db,
        vault_address=vault,
        order_key=order_key_hex,
        session_id=session_id,
        execute_after_block=execute_after_block,
        status="pending",
        decision_snapshot=decision.model_dump(),
        submit_tx_hash=submit_tx_hash_hex,
    )
    await record_journal_pending(
        db,
        vault_address=vault,
        order_key=order_key_hex,
        raw_request={"prompt": prompt},
        raw_response=raw,
        canonical_decision=decision.model_dump(),
    )
    # Flip the intent row to 'reconciled' so it is no longer returned by
    # get_unresolved_pending_orders on the next restart:
    await mark_pending_order_reconciled(db, vault_address=vault, order_key=intent_key)
    # NOTE: do NOT call executeOrder here — the keeper_monitor task does that after
    #       the block delay has elapsed (D-13 / T-02-21 prohibition).

    logger.info(
        "Cycle %d: submitted — intent_key=%s real_key=%s execute_after=%d",
        cycle,
        intent_key[:20],
        order_key_hex[:10],
        execute_after_block,
    )
    # TEST-03 diagnostic capture (DIAGNOSTIC_CAPTURE env — observe-only, no side effects)
    _emit_diagnostic(
        capture_path=_diag_path,
        cycle=cycle,
        prompt=prompt,
        raw_response=raw,
        parsed_decision=decision.model_dump(),
        rationale=getattr(decision, "rationale", None),
        outcome="open",
        malformed_reason=None,
    )
    return {
        "status": "submitted",
        "action": decision.action,
        "order_key": order_key_hex,
        "intent_key": intent_key,
        "execute_after_block": execute_after_block,
    }


# ---------------------------------------------------------------------------
# reconcile_pending_orders — startup ORCH-08 reconciliation
# ---------------------------------------------------------------------------


async def reconcile_pending_orders(
    web3: Any,
    mock_perps: Any,
    db: Any,
    *,
    vault: str,
    vault_contract: Any = None,
    orchestrator_address: str | None = None,
) -> int:
    """Check unresolved DB rows against the adapter's pendingOrders before any resubmit.

    On startup the driver calls this BEFORE the first cycle to determine which
    pending_orders rows represent orders that actually landed on-chain.

    Reconciliation logic:
    - intent-* key (starts with "intent-"): synthetic pre-submit key.
      GAP #10 fix: if the DB row has a submit_tx_hash, call eth_getTransactionByHash
      first — if the tx is pending OR mined, do NOT resubmit (it will/did land).
      Only resubmit if the tx is truly absent (returns None).
      If no submit_tx_hash on the row, it is safe to resubmit once.
    - Real hex key (0x...): check adapter.pendingOrders(key_bytes).vault.
      If vault != address(0): order is on-chain.
        GAP #2 fix: if pendingOrders(key)[4] (executed==true), the order was
        executed on-chain but clearTradingLock was never called (SIGKILL race).
        Call vault.clearTradingLock(orderKey) unconditionally as a startup heal.
      If vault == address(0): order not found on-chain → submit never landed →
        GAP #10: check submit_tx_hash first before marking resubmittable.

    Note: reconciliation reads from the ADAPTER (mock_perps / GMXAdapter) — it reads
    on-chain pending order state.  It does NOT interact with vault_contract for reads
    (D-16 adapter-for-reads / vault-for-writes split), but DOES call vault_contract
    for the clearTradingLock startup heal (GAP #2).

    Args:
        web3: AsyncWeb3 instance (for eth_getTransactionByHash, GAP #10).
        mock_perps: Adapter contract instance (MockPerps or GMXAdapter) — READ side only.
        db: AsyncSession for DB reads.
        vault: Vault address to reconcile.
        vault_contract: MTokenVault contract instance (GAP #2 startup heal). Optional —
            when provided together with orchestrator_address, clearTradingLock is called
            for any executed-on-chain orders whose lock was never cleared (SIGKILL race).
        orchestrator_address: Checksummed operator-trade EOA address. Required for
            clearTradingLock (onlyOrchestrator modifier). Signing middleware must already
            be loaded on web3 (D-16).

    Returns:
        Number of rows that are safe to resubmit (the driver will re-drive them
        on the next cycle via normal run_live_cycle logic).
    """
    from orchestrator.alerts.sink import AlertSeverity, send_alert

    unresolved = await get_unresolved_pending_orders(db, vault_address=vault)
    resubmittable = 0
    for order in unresolved:
        key: str = order["order_key"]
        if key.startswith("intent-"):
            # Synthetic intent key — NOT an on-chain key.
            # GAP #10: check submit_tx_hash before marking resubmittable.
            submit_tx = order.get("submit_tx_hash")
            if submit_tx:
                try:
                    tx_data = await web3.eth.get_transaction(submit_tx)
                    if tx_data is not None:
                        # Tx is pending OR already mined — do NOT resubmit
                        logger.info(
                            "reconcile: intent %s has submit_tx=%s still in mempool/mined — "
                            "skipping resubmit (GAP #10 duplicate-prevention)",
                            key,
                            submit_tx[:12],
                        )
                        continue
                except Exception as exc:  # noqa: BLE001
                    # eth_getTransactionByHash returned error (not just None) —
                    # treat as "tx absent" (safe to resubmit) and log at WARNING.
                    logger.warning(
                        "reconcile: eth_getTransactionByHash failed for submit_tx=%s key=%s: %s — "
                        "treating tx as absent (safe to resubmit once)",
                        submit_tx[:12] if submit_tx else "None",
                        key,
                        exc,
                    )
            resubmittable += 1
            logger.info(
                "reconcile: intent %s has no resolved on-chain order — safe to resubmit once",
                key,
            )
            continue

        # Real hex key: check on-chain
        try:
            key_bytes = bytes.fromhex(key.removeprefix("0x"))
            # pendingOrders(bytes32) struct: (positionKey[0], executeAfterBlock[1], vault[2],
            #                                 isClose[3], executed[4])
            onchain = await mock_perps.functions.pendingOrders(key_bytes).call()
            vault_on_chain: str = onchain[2]
            order_executed: bool = bool(onchain[4]) if len(onchain) > 4 else False
            zero_addr = "0x" + "0" * 40
            if vault_on_chain.lower() != zero_addr.lower():
                # Order IS on-chain — keeper will execute it (or already executed it).
                if order_executed:
                    # GAP #2: Order was executed on-chain but SIGKILL prevented
                    # clearTradingLock from firing → _tradingLocked stays true forever.
                    # Startup heal: call clearTradingLock unconditionally (idempotent).
                    logger.info(
                        "reconcile: order %s executed on-chain but lock not cleared "
                        "(SIGKILL race) — firing clearTradingLock startup heal (GAP #2)",
                        key[:10],
                    )
                    if vault_contract is not None and orchestrator_address is not None:
                        try:
                            heal_tx = await vault_contract.functions.clearTradingLock(
                                key_bytes
                            ).transact({"from": orchestrator_address})
                            heal_receipt = await web3.eth.wait_for_transaction_receipt(
                                heal_tx, timeout=30
                            )
                            if heal_receipt.get("status") == 0:
                                logger.error(
                                    "reconcile: clearTradingLock startup heal REVERTED for "
                                    "order_key=%s — vault may still be locked",
                                    key[:10],
                                )
                            else:
                                logger.info(
                                    "reconcile: clearTradingLock startup heal OK for order_key=%s",
                                    key[:10],
                                )
                        except Exception as heal_exc:  # noqa: BLE001
                            logger.error(
                                "reconcile: clearTradingLock startup heal FAILED for order_key=%s: %s",
                                key[:10],
                                heal_exc,
                            )
                            await send_alert(
                                f"Startup heal: clearTradingLock failed for order_key={key[:10]}: "
                                f"{heal_exc}. Vault may still be locked.",
                                AlertSeverity.CRITICAL,
                                context={"vault_address": vault, "order_key": key},
                            )
                    else:
                        logger.warning(
                            "reconcile: order %s executed+locked but vault_contract not wired "
                            "— cannot fire clearTradingLock startup heal (GAP #2). "
                            "Pass vault_contract + orchestrator_address to reconcile.",
                            key[:10],
                        )
                else:
                    # Order on-chain, not yet executed — keeper will execute it; do NOT resubmit
                    logger.info(
                        "reconcile: order %s already on-chain (vault=%s), skipping resubmit",
                        key[:10],
                        vault_on_chain[:10],
                    )
            else:
                # Order NOT found on-chain (vault==address(0)).
                # GAP #10: check submit_tx_hash before marking resubmittable.
                submit_tx = order.get("submit_tx_hash")
                if submit_tx:
                    try:
                        tx_data = await web3.eth.get_transaction(submit_tx)
                        if tx_data is not None:
                            # Tx is pending in mempool or already mined — do NOT resubmit
                            logger.info(
                                "reconcile: order %s not found on-chain yet but submit_tx=%s "
                                "still exists (pending/mined) — skipping resubmit (GAP #10)",
                                key[:10],
                                submit_tx[:12],
                            )
                            continue
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "reconcile: eth_getTransactionByHash failed for submit_tx=%s "
                            "order_key=%s: %s — treating as absent",
                            submit_tx[:12] if submit_tx else "None",
                            key[:10],
                            exc,
                        )
                # Submit never landed (or tx absent) — safe to resubmit once
                resubmittable += 1
                logger.info(
                    "reconcile: order %s not found on-chain — submit never landed, safe to resubmit",
                    key[:10],
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("reconcile: failed to check on-chain state for %s: %s", key[:10], exc)

    logger.info(
        "reconcile: %d unresolved row(s), %d resubmittable (ORCH-08)",
        len(unresolved),
        resubmittable,
    )
    return resubmittable


# ---------------------------------------------------------------------------
# run_session — session driver loop (D-12 / ORCH-02)
# ---------------------------------------------------------------------------


async def run_session(
    web3: Any,
    mock_perps: Any,
    aggregators: dict[str, Any],
    vault: str,
    model: str,
    *,
    config: SessionConfig,
    db: Any,
    redis: Any | None,
    deployer_address: str,
    vault_contract: Any = None,
    operator_trade_account: Any = None,
    # D-11 multi-model: shared NonceManager bound to the operator-trade EOA. When provided
    # (gate 3-model launcher), ALL vault + keeper writes from this EOA serialize through it so
    # concurrent models cannot collide on nonces. None → single-model auto-nonce (unchanged).
    nonce_manager: Any = None,
    # D-11 multi-model: when the gate launcher owns ONE shared price-pusher (one PriceWalk over the
    # shared feeds, fanned out to all 3 models for identical market conditions), it passes
    # launch_price_pusher=False + the shared walk + this model's snapshot queue. Single-model leaves
    # the defaults → each session runs its own walk + price_pusher (unchanged).
    launch_price_pusher: bool = True,
    external_walk: Any = None,
    external_snapshot_queue: Any = None,
    # D-11 multi-model: when the gate launcher shares ONE web3 across all 3 models, the
    # operator-trade signing middleware is injected ONCE by the launcher (run_gate
    # _build_web3_with_signers). Each model's run_session must then SKIP re-injecting it, or
    # web3.py raises "You can't add the same un-named instance twice" on the 2nd model and all
    # three crash -> AUTO_PAUSED. Default True preserves single-model behavior (run_session
    # injects it itself, exactly once).
    inject_signing_middleware: bool = True,
    # GAP #4/#6: price_pusher_address separates price-push signing from trade-submission
    # signing (SEC-01 key separation). Defaults to deployer_address for backward compat.
    # When set, the price pusher uses this address to sign setPrice() calls instead of
    # reusing the operator-trade EOA. Wire via PRICE_PUSHER_KEY in run_session.py.
    price_pusher_address: str | None = None,
    # Journal publisher params (PERPS-02 / D-08/D-09/D-10): optional, forwarded
    # to run_keeper_monitor so the keeper can publish_journal_entry on OrderExecuted.
    # All default None to preserve backward-compat with Phase-2 anvil tests.
    journal_registry: Any | None = None,
    operator_journal_private_key: bytes | None = None,
    pinata_jwt: str | None = None,
    filebase_access_key: str | None = None,
    filebase_secret_key: str | None = None,
    operator_journal_key_address: str | None = None,
    telegram_bot_token: str | None = None,
    telegram_chat_id: str | None = None,
) -> dict:
    """Run the full trading session loop (ORCH-02 / D-12 / D-16).

    Sequence:
    1. create_session in DB (idempotent — safe on restart).
    2. Load signing middleware for operator-trade EOA (D-16 — once at startup).
    3. reconcile_pending_orders (ORCH-08 startup check).
    4. Seed=logged prominently (D-01 replay requirement).
    5. Launch price_pusher + keeper_monitor as separate asyncio.Tasks.
    6. Cycle loop until session_duration_seconds elapsed.
       - Paused → back off to paused_poll_interval_seconds (D-16).
       - Run run_live_cycle for each active cycle.
    7. Session end: stop_event.set(), cancel/await tasks, end_session (D-12).
       Positions are left open — settlement contract handles draining.

    Args:
        web3: AsyncWeb3 instance.
        mock_perps: Adapter contract instance (MockPerps or GMXAdapter).
                    Used for event decoding + read calls (D-16 split).
        aggregators: Mapping of asset → MockChainlinkAggregator contract.
        vault: Vault address.
        model: LLM model identifier.
        config: SessionConfig (seed, duration, cadence, etc.).
        db: AsyncSession for DB writes.
        redis: Optional redis.asyncio client.
        deployer_address: Deployer EOA (used by keeper_monitor for executeOrder).
        price_pusher_address: Optional address for price-push signing (GAP #4/#6 SEC-01
            key separation). When None, defaults to deployer_address (backward compat).
        vault_contract: MTokenVault contract instance (D-16). When provided, trade
            submission goes through vault.openLong/openShort/closePosition as the
            operator-trade EOA.  When None, falls back to legacy adapter impersonation
            (anvil-only).  Do NOT put the private key in SessionConfig (SEC-01).
        operator_trade_account: LocalAccount from eth_account.Account.from_key(...).
            The private key is used ONLY to load signing middleware (once at startup).
            The address is derived as operator_trade_account.address and threaded into
            transact calls.  When None, signing middleware is not loaded (legacy path).
        journal_registry: JournalRegistry contract instance (D-10). When provided
            together with operator_journal_private_key and pinata_jwt, the keeper
            publishes journal entries on OrderExecuted (PERPS-02).
        operator_journal_private_key: Raw 32-byte private key for EIP-191 signing.
        pinata_jwt: Pinata V3 JWT for IPFS pinning (JOURNAL-02).
        filebase_access_key: Filebase S3 access key (SigV4) for backup pinning (D-08).
        filebase_secret_key: Filebase S3 secret key (SigV4) for backup pinning (D-08).
        operator_journal_key_address: Hex address for operator-journal key transact from.
        telegram_bot_token: Optional Telegram bot token for alert sink (D-15).
        telegram_chat_id: Optional Telegram chat ID for alert sink (D-15).

    Returns:
        Summary dict: {"cycles": int, "seed": int, "session_id": str}
    """
    # Step 1: Create session (idempotent)
    await create_session(
        db,
        session_id=config.session_id,
        session_key=config.session_key,
        duration_seconds=config.session_duration_seconds,
    )

    # Step 2: Load signing middleware for operator-trade EOA (D-16 REQUIRED-REGARDLESS).
    # construct_sign_and_send_raw_middleware intercepts .transact() calls whose "from"
    # matches the account address and auto-signs + submits the raw transaction.
    # This is the ONLY place the private key crosses into web3 — SEC-01 compliance.
    # On anvil: operator_trade_account is anvil account[N] (well-known dev key).
    # On Sepolia: operator_trade_account is loaded from gitignored OPERATOR_TRADE_KEY env var.
    operator_trade_address: str | None = None
    if operator_trade_account is not None:
        # Always derive the address (enables the D-16 vault-submit path downstream) even when
        # injection is skipped — see inject_signing_middleware (D-11 shared-web3 multi-model).
        operator_trade_address = operator_trade_account.address
        if inject_signing_middleware:
            # web3.py 7.x API: SignAndSendRawMiddlewareBuilder.build is @curry-decorated.
            # Calling build(account) WITHOUT w3 returns a curry partial that the middleware
            # onion will call with (w3) during initialization.  This is the correct injection
            # pattern — passing the fully-built instance (build(account, w3)) causes a
            # TypeError because the onion then calls the instance as if it were a class.
            # Replaces the web3.py 6.x construct_sign_and_send_raw_middleware function.
            signing_mw_partial = SignAndSendRawMiddlewareBuilder.build(operator_trade_account)
            web3.middleware_onion.inject(signing_mw_partial, layer=0)
            logger.info(
                "run_session: signing middleware loaded for operator-trade EOA %s (D-16)",
                operator_trade_address,
            )
        else:
            # D-11 multi-model: the gate launcher already injected this EOA's middleware ONCE
            # on the shared web3; re-injecting per-model would raise "can't add the same
            # un-named instance twice". Skip injection but keep operator_trade_address set.
            logger.info(
                "run_session: operator-trade EOA %s — signing middleware injection SKIPPED "
                "(already loaded on shared web3 by the gate launcher; D-11)",
                operator_trade_address,
            )
    else:
        logger.warning(
            "run_session: no operator_trade_account provided — "
            "falling back to legacy vault from-impersonation (anvil only, NOT Sepolia)"
        )

    # Step 3: Startup reconciliation (ORCH-08)
    # GAP #2: pass vault_contract + orchestrator_address so reconcile can fire
    # clearTradingLock startup heal for orders executed on-chain with lock not cleared.
    await reconcile_pending_orders(
        web3,
        mock_perps,
        db,
        vault=vault,
        vault_contract=vault_contract,
        orchestrator_address=operator_trade_address,
    )

    # Step 4: Log seed prominently (D-01 — session is fully replayable from seed)
    logger.warning(
        "SESSION START seed=%s session_id=%s duration=%ss cadence=%ss model=%s vault=%s",
        config.price_seed,
        config.session_id,
        config.session_duration_seconds,
        config.cadence_seconds,
        model,
        vault[:10],
    )

    # Step 5: Launch background tasks
    stop_event = asyncio.Event()
    # D-11 multi-model: reuse the launcher's shared walk when provided (all 3 models see the SAME
    # market — fairness); else build a per-session walk (single-model, unchanged).
    walk = external_walk or PriceWalk(
        config.price_seed,
        config.starting_prices,
        config.drift,
        config.volatility,
    )

    # CR-03: snapshot queue for consistent market_table data.
    # price_pusher publishes {asset: {mark, funding, change_24h}} after each step.
    # maxsize=1 ensures the driver always reads the latest snapshot (stale snapshots
    # are discarded by price_pusher before publishing a new one).
    # D-11 multi-model: use the launcher-provided per-model snapshot queue (fed by the ONE shared
    # price-pusher) when present; else a private queue fed by this session's own pusher (unchanged).
    snapshot_queue: asyncio.Queue = external_snapshot_queue or asyncio.Queue(maxsize=1)

    # CR-04: The keeper_monitor runs as a SEPARATE asyncio.Task concurrently with the
    # cycle loop.  SQLAlchemy AsyncSession is NOT safe for concurrent access from
    # multiple coroutines — sharing `db` between run_live_cycle and run_keeper_monitor
    # causes "session is in prepared state" errors when both try to execute SQL at the
    # same time.  Fix: create a dedicated AsyncSession for the keeper, bound to the
    # same engine as the caller's session (db.bind is the AsyncEngine), so keeper
    # writes never contend with the driver's writes.
    keeper_db = AsyncSession(db.bind)

    # GAP #4/#6: use price_pusher_address for price-push signing when set;
    # fall back to deployer_address for backward compat (SEC-01 key separation).
    _price_pusher_from = price_pusher_address or deployer_address
    logger.info(
        "run_session: price_pusher from_address=%s%s (GAP #4/#6 key separation)",
        _price_pusher_from[:10],
        " (PRICE_PUSHER_KEY)" if price_pusher_address else " (OPERATOR_TRADE_KEY fallback)",
    )
    # D-11 multi-model: when the gate launcher owns the ONE shared price-pusher
    # (launch_price_pusher=False), this session does NOT start its own — three pushers would fight
    # over the shared feeds + collide on the price-pusher nonce. Single-model (default True) starts
    # its own, unchanged.
    price_pusher_task = None
    if launch_price_pusher:
        price_pusher_task = asyncio.create_task(
            run_price_pusher(
                web3,
                aggregators,
                walk,
                _price_pusher_from,
                config.cadence_seconds,
                stop_event,
                snapshot_queue=snapshot_queue,
            ),
            name=f"price_pusher-{config.session_id[:8]}",
        )
    keeper_task = asyncio.create_task(
        run_keeper_monitor(
            web3,
            mock_perps,
            keeper_db,
            deployer_address=deployer_address,
            vault_address=vault,
            redis=redis,
            session_id=config.session_id,
            stop_event=stop_event,
            poll_seconds=2.0,
            # VAULT-06: wire vault_contract + orchestrator address so the keeper calls
            # clearTradingLock(orderKey) after each OrderExecuted.  Without this, the
            # vault's _tradingLocked flag stays true and every subsequent openLong/
            # openShort/closePosition reverts "Vault: order in flight" — bricking the
            # session after its first trade.  Both default None for backward-compat with
            # Phase-2 anvil tests that do not use a vault_contract.
            vault_contract=vault_contract,
            orchestrator_address=operator_trade_address,
            nonce_manager=nonce_manager,
            # Journal publisher params (PERPS-02 / D-08/D-09/D-10).
            # All default None — backward-compat with Phase-2 anvil tests.
            # When all three required params are non-None, the keeper publishes
            # journal entries on OrderExecuted (wired here once at session start).
            journal_registry=journal_registry,
            operator_journal_private_key=operator_journal_private_key,
            pinata_jwt=pinata_jwt,
            filebase_access_key=filebase_access_key,
            filebase_secret_key=filebase_secret_key,
            operator_journal_key_address=operator_journal_key_address,
            telegram_bot_token=telegram_bot_token,
            telegram_chat_id=telegram_chat_id,
        ),
        name=f"keeper-{config.session_id[:8]}",
    )

    # Step 6: Cycle loop
    # CR-01: Rehydrate FailureTracker from DB before loop starts so a model that was
    # 2/3 of the way to pause before a SIGKILL resumes at the correct streak count
    # (ORCH-06 restart-safety requirement).
    tracker = FailureTracker()
    latest_status = await get_latest_model_status(db, vault_address=vault)
    if latest_status is not None:
        api_streak = latest_status.get("api_failure_streak") or 0
        malformed_streak_val = latest_status.get("malformed_streak") or 0
        if api_streak > 0 or malformed_streak_val > 0:
            tracker.api_failure_streak = api_streak
            tracker.malformed_streak = malformed_streak_val
            from orchestrator.loop.failure_tracker import (
                API_FAILURE_PAUSE_THRESHOLD,
                MALFORMED_PAUSE_THRESHOLD,
            )

            if (
                tracker.api_failure_streak >= API_FAILURE_PAUSE_THRESHOLD
                or tracker.malformed_streak >= MALFORMED_PAUSE_THRESHOLD
            ):
                tracker.paused = True
            logger.info(
                "run_session: rehydrated FailureTracker from DB — "
                "api_failure_streak=%d malformed_streak=%d paused=%s",
                tracker.api_failure_streak,
                tracker.malformed_streak,
                tracker.paused,
            )
    start = time.monotonic()
    cycle = 0

    # Simple NAV/positions state — in production these are read from the vault contract
    nav_table = "| Vault | NAV | mTOKEN Supply |\n|-------|-----|---------------|\n| mock | $10,000 | 10,000 |"
    available_usdc = 10_000.0
    recent_decisions: list[str] = []

    try:
        while (time.monotonic() - start) < config.session_duration_seconds:
            cycle += 1
            elapsed = time.monotonic() - start

            if tracker.should_pause():
                # D-16: back off to slow-poll interval while paused
                await asyncio.sleep(config.paused_poll_interval_seconds)
                # After sleeping, attempt a probe cycle (run_live_cycle will try call_claude;
                # a success resets the tracker via tracker.record_success())
            else:
                await asyncio.sleep(0)  # yield to event loop before cycle

            # WR-03: Build open_positions from ON-CHAIN state each cycle (restart-safe).
            # This reflects chain reality rather than in-memory guesses and makes
            # the D-10 one-position-per-asset check authoritative.
            open_positions = await _build_open_positions(mock_perps, vault)

            positions_table = (
                "No open positions."
                if not open_positions
                else "\n".join(
                    f"| {mkt} | {p.get('side', '?')} | {p.get('size_usd', 0):.0f} |"
                    for mkt, p in open_positions.items()
                )
            )

            # CR-03: Try to get the latest consistent snapshot published by price_pusher.
            # If no snapshot yet (first cycle before price_pusher steps), fall back to
            # None (run_live_cycle will use the legacy on-chain read + walk path).
            current_snapshot: dict | None = None
            if not snapshot_queue.empty():
                try:
                    current_snapshot = snapshot_queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass

            result = await run_live_cycle(
                web3,
                mock_perps,
                vault,
                model,
                cycle,
                config=config,
                walk=walk,
                aggregators=aggregators,
                tracker=tracker,
                db=db,
                redis=redis,
                session_id=config.session_id,
                seq=cycle,
                available_usdc=available_usdc,
                open_positions=open_positions,
                nav_table=nav_table,
                positions_table=positions_table,
                recent_decisions="\n".join(recent_decisions[-5:]) or "None",
                elapsed_seconds=elapsed,
                market_snapshot=current_snapshot,
                vault_contract=vault_contract,
                operator_trade_address=operator_trade_address,
                nonce_manager=nonce_manager,
            )

            # Keep last-5 decision summary
            recent_decisions.append(
                f"cycle={cycle} status={result.get('status', '?')} action={result.get('action', '?')}"
            )
            if len(recent_decisions) > 10:
                recent_decisions = recent_decisions[-10:]

            # Normal cadence sleep (paused path already slept above)
            if not tracker.should_pause():
                await asyncio.sleep(config.cadence_seconds)

    finally:
        # Step 7: D-12 session end — stop background tasks, mark session ended
        stop_event.set()
        elapsed_total = time.monotonic() - start
        logger.warning(
            "SESSION END session_id=%s cycles=%d elapsed=%.1fs — "
            "positions left open (settlement contract drains)",
            config.session_id,
            cycle,
            elapsed_total,
        )

        # Cancel background tasks gracefully (price_pusher_task is None when the launcher owns the
        # shared pusher — D-11 multi-model — so guard against None).
        for task in (price_pusher_task, keeper_task):
            if task is None:
                continue
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        # CR-04: Close the keeper's dedicated session now that the keeper task is done.
        # This releases the DB connection back to the pool before end_session runs.
        try:
            await keeper_db.close()
        except Exception:  # noqa: BLE001
            pass  # best-effort close; don't mask shutdown errors

        await end_session(db, session_id=config.session_id)

        # SETTLE_ON_END gate (GAP #9): run the settlement keeper ONLY when explicitly
        # requested at session-end — never on a Phase-3 mid-session gate stop.
        # Enable by setting SETTLE_ON_END=1 in the environment, or by passing
        # settle_on_end=True to run_session (not yet added to the signature to avoid
        # breaking existing callers — checked via os.environ here).
        #
        # Requires: settlement_contract, mock_perps, vault_contract, orchestrator
        # address all wired (they are: vault_contract and operator_trade_address come
        # from run_session params; mock_perps is the adapter).
        _settle = os.environ.get("SETTLE_ON_END", "").strip().lower() in ("1", "true", "yes")
        if _settle:
            _sett_addr = os.environ.get("SETTLEMENT_CONTRACT_ADDRESS", "").strip()
            if _sett_addr and vault_contract is not None and operator_trade_address is not None:
                try:
                    from orchestrator.loop.run_session import _CONTRACTS_OUT, _load_abi

                    _sett_artifact = (
                        _CONTRACTS_OUT / "SettlementContract.sol" / "SettlementContract.json"
                    )
                    _sett_abi: list = []
                    try:
                        _sett_abi = _load_abi(_sett_artifact)
                    except FileNotFoundError:
                        logger.warning(
                            "run_session: SettlementContract artifact not found "
                            "(SETTLE_ON_END=1 set but forge build not run?)"
                        )
                    _settlement_contract = web3.eth.contract(address=_sett_addr, abi=_sett_abi)
                    logger.warning(
                        "run_session: SETTLE_ON_END=1 — launching settlement keeper "
                        "(vault=%s settlement=%s)",
                        vault[:10],
                        _sett_addr[:10],
                    )
                    settle_result = await run_settlement_keeper(
                        web3,
                        mock_perps,
                        _settlement_contract,
                        vault_contract,
                        vault_address=vault,
                        orchestrator_address=operator_trade_address,
                        deployer_address=deployer_address,
                        telegram_bot_token=telegram_bot_token,
                        telegram_chat_id=telegram_chat_id,
                    )
                    logger.warning(
                        "run_session: settlement keeper complete — status=%s positions_closed=%d",
                        settle_result.get("status"),
                        settle_result.get("positions_closed", 0),
                    )
                except Exception as sett_exc:  # noqa: BLE001
                    logger.error(
                        "run_session: settlement keeper raised (non-fatal, session still ended): %s",
                        sett_exc,
                    )
            else:
                if not _sett_addr:
                    logger.warning(
                        "run_session: SETTLE_ON_END=1 but SETTLEMENT_CONTRACT_ADDRESS not set — "
                        "settlement keeper skipped. Set SETTLEMENT_CONTRACT_ADDRESS to the "
                        "SettlementContract address for this vault."
                    )
                else:
                    logger.warning(
                        "run_session: SETTLE_ON_END=1 but vault_contract or "
                        "operator_trade_address not wired — settlement keeper skipped."
                    )

    return {
        "cycles": cycle,
        "seed": config.price_seed,
        "session_id": config.session_id,
    }

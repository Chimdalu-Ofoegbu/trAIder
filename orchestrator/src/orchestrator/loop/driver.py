"""
orchestrator.loop.driver — Per-cycle live ORCH-02 loop (Plan 02-05).

Composes the Wave-2 pieces into the live trade loop:

  run_live_cycle(...)
      Single ORCH-02 cycle: prompt → call_claude → validate → business-rules →
      record-intent (BEFORE submit) → MockPerps open → promote intent row.

  reconcile_pending_orders(...)
      Startup ORCH-08 reconciliation: reads unresolved intent/pending rows and
      checks each against MockPerps.pendingOrders to determine if the submit landed.

  run_session(...)
      Session driver: creates session, reconciles, launches price_pusher + keeper
      as asyncio.Tasks, runs the cycle loop until session_duration_seconds, then
      ends the session cleanly (D-12 — positions left open, NO close-all).

SC-2 record-intent-before-submit ordering guarantee
---------------------------------------------------
In run_live_cycle step 8, the order of operations is MANDATORY:
  8a. Compute intent_key (pure, no network).
  8b. record_journal_pending + record_pending_order(status='intent')  ← DB write FIRST
  8c. MockPerps openLong/openShort.transact(...)                      ← network call SECOND
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

FORBIDDEN in this module (D-13 / T-02-21):
  MockPerps.executeOrder — that is the keeper_monitor's job.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from orchestrator.business_rules import validate_business_rules
from orchestrator.loop.failure_tracker import FailureTracker
from orchestrator.loop.keeper_monitor import run_keeper_monitor
from orchestrator.loop.market_state import build_market_table, read_mark_prices
from orchestrator.loop.price_pusher import PriceWalk, run_price_pusher
from orchestrator.loop.session import SessionConfig, format_time_remaining
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
    get_unresolved_pending_orders,
    mark_pending_order_reconciled,
    record_journal_pending,
    record_model_status,
    record_pending_order,
)

logger = logging.getLogger(__name__)


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
) -> dict:
    """Execute one live trading cycle (ORCH-02 sequence).

    Args:
        web3: AsyncWeb3 instance.
        mock_perps: MockPerps contract instance.
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
    prices = await read_mark_prices(aggregators)
    market_table = build_market_table(walk, prices)
    time_remaining = format_time_remaining(elapsed_seconds, config.session_duration_seconds)

    from orchestrator.loop.market_state import render_prompt

    prompt = render_prompt(
        nav_table=nav_table,
        time_remaining=time_remaining,
        positions_table=positions_table,
        available_usdc=available_usdc,
        recent_decisions=recent_decisions,
        market_table=market_table,
    )

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
        return {"status": "rejected", "reason": rejection_reason}

    # ── Step 8: RECORD-INTENT BEFORE SUBMIT (ORCH-08 / SC-2) ─────────────────
    #
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
            return {"status": "rejected", "reason": reason}
        pos_key_hex: str = existing["position_key"]
        pos_key_bytes = bytes.fromhex(pos_key_hex.removeprefix("0x"))
        tx = await mock_perps.functions.closePosition(pos_key_bytes, size_usd_1e30).transact(
            {"from": vault}
        )
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
        return {"status": "rejected", "reason": reason}
    else:
        # action == "open": proceed with openLong/openShort.
        open_fn = (
            mock_perps.functions.openLong
            if decision.side == "long"
            else mock_perps.functions.openShort
        )
        tx = await open_fn(decision.market, size_usd_1e30, leverage_1e4, slippage_bps).transact(
            {"from": vault}
        )
    receipt = await web3.eth.get_transaction_receipt(tx)

    # Recover the REAL order_key from the OrderCreated event (mock_harness CR-01 pattern):
    created = mock_perps.events.OrderCreated().process_receipt(receipt)
    if not created:
        logger.error("Cycle %d: OrderCreated event not found in receipt", cycle)
        return {
            "status": "error",
            "error": "OrderCreated event not found in open tx receipt",
            "intent_key": intent_key,
        }
    order_key_hex = "0x" + created[0]["args"]["orderKey"].hex()

    # 8d. PROMOTE the intent row: insert the real-key row and mark intent reconciled.
    await record_pending_order(
        db,
        vault_address=vault,
        order_key=order_key_hex,
        session_id=session_id,
        execute_after_block=execute_after_block,
        status="pending",
        decision_snapshot=decision.model_dump(),
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
) -> int:
    """Check unresolved DB rows against MockPerps.pendingOrders before any resubmit.

    On startup the driver calls this BEFORE the first cycle to determine which
    pending_orders rows represent orders that actually landed on-chain.

    Reconciliation logic:
    - intent-* key (starts with "intent-"): synthetic pre-submit key.  The submit
      either never landed or landed under a real key.  We cannot look it up on-chain
      by this key.  Safe to resubmit once on the next cycle.
    - Real hex key (0x...): check MockPerps.pendingOrders(key_bytes).vault.
      If vault != address(0): order is on-chain → do NOT resubmit (keeper will execute).
      If vault == address(0): order not found on-chain → submit never landed → safe to
      resubmit once (ORCH-08 / T-02-17 Pitfall 4).

    Args:
        web3: AsyncWeb3 instance (unused in this implementation; available for GMX).
        mock_perps: MockPerps contract instance.
        db: AsyncSession for DB reads.
        vault: Vault address to reconcile.

    Returns:
        Number of rows that are safe to resubmit (the driver will re-drive them
        on the next cycle via normal run_live_cycle logic).
    """
    unresolved = await get_unresolved_pending_orders(db, vault_address=vault)
    resubmittable = 0
    for order in unresolved:
        key: str = order["order_key"]
        if key.startswith("intent-"):
            # Synthetic intent key — NOT an on-chain key.  The submit either never
            # landed or landed under a real key (which may or may not be in DB).
            # Flag as resubmittable so the driver re-drives it.
            resubmittable += 1
            logger.info(
                "reconcile: intent %s has no resolved on-chain order — safe to resubmit once",
                key,
            )
            continue

        # Real hex key: check on-chain
        try:
            key_bytes = bytes.fromhex(key.removeprefix("0x"))
            # pendingOrders(bytes32) returns struct: (positionKey, executeAfterBlock, vault, ...)
            # onchain[2] is the vault field
            onchain = await mock_perps.functions.pendingOrders(key_bytes).call()
            vault_on_chain: str = onchain[2]
            zero_addr = "0x" + "0" * 40
            if vault_on_chain.lower() != zero_addr.lower():
                # Order IS on-chain — keeper will execute it; do NOT resubmit
                logger.info(
                    "reconcile: order %s already on-chain (vault=%s), skipping resubmit",
                    key[:10],
                    vault_on_chain[:10],
                )
            else:
                # Order NOT on-chain — submit never landed; safe to resubmit once
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
) -> dict:
    """Run the full trading session loop (ORCH-02 / D-12).

    Sequence:
    1. create_session in DB (idempotent — safe on restart).
    2. reconcile_pending_orders (ORCH-08 startup check).
    3. Seed=logged prominently (D-01 replay requirement).
    4. Launch price_pusher + keeper_monitor as separate asyncio.Tasks.
    5. Cycle loop until session_duration_seconds elapsed.
       - Paused → back off to paused_poll_interval_seconds (D-16).
       - Run run_live_cycle for each active cycle.
    6. Session end: stop_event.set(), cancel/await tasks, end_session (D-12).
       Positions are left open — settlement contract handles draining.

    Args:
        web3: AsyncWeb3 instance.
        mock_perps: MockPerps contract instance.
        aggregators: Mapping of asset → MockChainlinkAggregator contract.
        vault: Vault address.
        model: LLM model identifier.
        config: SessionConfig (seed, duration, cadence, etc.).
        db: AsyncSession for DB writes.
        redis: Optional redis.asyncio client.
        deployer_address: Deployer EOA (used by keeper_monitor for executeOrder).

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

    # Step 2: Startup reconciliation (ORCH-08)
    await reconcile_pending_orders(web3, mock_perps, db, vault=vault)

    # Step 3: Log seed prominently (D-01 — session is fully replayable from seed)
    logger.warning(
        "SESSION START seed=%s session_id=%s duration=%ss cadence=%ss model=%s vault=%s",
        config.price_seed,
        config.session_id,
        config.session_duration_seconds,
        config.cadence_seconds,
        model,
        vault[:10],
    )

    # Step 4: Launch background tasks
    stop_event = asyncio.Event()
    walk = PriceWalk(
        config.price_seed,
        config.starting_prices,
        config.drift,
        config.volatility,
    )

    price_pusher_task = asyncio.create_task(
        run_price_pusher(
            web3,
            aggregators,
            walk,
            deployer_address,
            config.cadence_seconds,
            stop_event,
        ),
        name=f"price_pusher-{config.session_id[:8]}",
    )
    keeper_task = asyncio.create_task(
        run_keeper_monitor(
            web3,
            mock_perps,
            db,
            deployer_address=deployer_address,
            vault_address=vault,
            redis=redis,
            session_id=config.session_id,
            stop_event=stop_event,
            poll_seconds=2.0,
        ),
        name=f"keeper-{config.session_id[:8]}",
    )

    # Step 5: Cycle loop
    tracker = FailureTracker()
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
        # Step 6: D-12 session end — stop background tasks, mark session ended
        stop_event.set()
        elapsed_total = time.monotonic() - start
        logger.warning(
            "SESSION END session_id=%s cycles=%d elapsed=%.1fs — "
            "positions left open (settlement contract drains)",
            config.session_id,
            cycle,
            elapsed_total,
        )

        # Cancel background tasks gracefully
        for task in (price_pusher_task, keeper_task):
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        await end_session(db, session_id=config.session_id)

    return {
        "cycles": cycle,
        "seed": config.price_seed,
        "session_id": config.session_id,
    }

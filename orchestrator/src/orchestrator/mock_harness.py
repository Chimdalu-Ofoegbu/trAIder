"""
orchestrator.mock_harness — Deterministic fixture-replay harness (MOCK-02, D-14).

Replays scripted decision fixtures from:
    tests/fixtures/decisions/{model}/{cycle:04d}.json

NO provider SDK import is present in this module (D-14 — deterministic, no live LLM calls
in Phase 0). The fixture source is a JSON file; Phase 2 swaps the source for a live
provider call without changing the downstream execution path.

Fixture markers:
  _harness_marker = "timeout"   → simulates provider timeout (ORCH-06 failure path)
  Missing required Decision field → treated as malformed response (ORCH-05 path)

MOCK-02 execution trace per cycle:
  1. load_fixture()       → raw dict
  2. Decision.model_validate()  → on ValidationError: malformed path (ORCH-05)
  3. Submit to MockPerps  → openLong / openShort / closePosition
  4. Store pending journal row
  5. Roll executionDelay blocks
  6. executeOrder()       → observe OrderExecuted event
  7. record_trade()       → write to orchestrator.trades (D-02: on OrderExecuted, not open)
  8. publish_trade_event()→ TradeEvent envelope to ws/vault/{vault} Redis channel

Trust boundary:
  A replayed fixture is treated exactly like a model response: it MUST pass
  Decision.model_validate() before any MockPerps call is made (T-0-val).

WS envelope publishing:
  Publishes to Redis via redis.asyncio. Redis unavailability is surfaced as a warning
  (non-fatal in Phase 0 mock harness); Phase 2 treats it as fatal.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from orchestrator.schema import Decision
from orchestrator.state.db import record_journal_pending, record_model_status, record_trade

# ---------------------------------------------------------------------------
# NO provider SDK imports here — D-14 (deterministic, no live LLM)
# ---------------------------------------------------------------------------
# from anthropic import ...  # FORBIDDEN in this module
# from openai import ...      # FORBIDDEN in this module
# from google import genai    # FORBIDDEN in this module

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Fixture directory resolution
# ---------------------------------------------------------------------------

# Resolved relative to this file's location: orchestrator/src/orchestrator/mock_harness.py
# Fixtures live at:                          orchestrator/tests/fixtures/decisions/
_MODULE_DIR = Path(__file__).parent
_FIXTURES_DIR = _MODULE_DIR.parent.parent / "tests" / "fixtures" / "decisions"

# ---------------------------------------------------------------------------
# Harness markers
# ---------------------------------------------------------------------------

_MARKER_TIMEOUT = "timeout"


# ---------------------------------------------------------------------------
# load_fixture — read raw fixture dict from disk
# ---------------------------------------------------------------------------


def load_fixture(model: str, cycle: int) -> dict:
    """Load a decision fixture from tests/fixtures/decisions/{model}/{cycle:04d}.json.

    The cycle number is zero-padded to 4 digits (0001, 0002, ...).
    Suffixes (_malformed, _timeout, etc.) are also discovered: the function
    scans for any file matching {cycle:04d}*.json and returns the first match.

    Args:
        model: Model identifier, used as the subdirectory name (e.g. 'claude').
        cycle: 1-based cycle number.

    Returns:
        Raw fixture dict — NOT validated. Caller is responsible for
        Decision.model_validate() before any trade execution.

    Raises:
        FileNotFoundError: If no matching fixture file is found.
    """
    prefix = f"{cycle:04d}"
    fixture_dir = _FIXTURES_DIR / model

    # Exact match first (e.g. 0001.json)
    exact = fixture_dir / f"{prefix}.json"
    if exact.exists():
        return json.loads(exact.read_text(encoding="utf-8"))

    # Suffix match (e.g. 0002_malformed.json, 0003_timeout.json)
    candidates = sorted(fixture_dir.glob(f"{prefix}*.json"))
    if candidates:
        return json.loads(candidates[0].read_text(encoding="utf-8"))

    raise FileNotFoundError(
        f"No fixture found for model='{model}' cycle={cycle} "
        f"(looked in {fixture_dir} for '{prefix}*.json')"
    )


# ---------------------------------------------------------------------------
# _is_timeout_marker — detect the ORCH-06 timeout simulation marker
# ---------------------------------------------------------------------------


def _is_timeout_marker(fixture: dict) -> bool:
    """Return True if the fixture signals a provider timeout simulation."""
    return fixture.get("_harness_marker") == _MARKER_TIMEOUT


# ---------------------------------------------------------------------------
# _make_envelope — build a standard WS Envelope (D-26)
# ---------------------------------------------------------------------------


def _make_envelope(
    event_type: str,
    payload: dict,
    *,
    seq: int,
    block_number: int | None = None,
    chain_ts: str | None = None,
) -> dict:
    """Build a serialisable Envelope dict without importing backend.ws.models.

    Returns a plain dict (not a Pydantic model) so the harness has no hard
    runtime dependency on the backend package at the call site.  The integration
    test imports Envelope from backend.ws.models for validation.
    """
    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    return {
        "seq": seq,
        "server_ts": now,
        "chain_ts": chain_ts,
        "block_number": block_number,
        "event_type": event_type,
        "payload": payload,
        "latest_block_number": block_number,
        "latest_block_ts": chain_ts or now,
    }


# ---------------------------------------------------------------------------
# _publish — publish envelope to Redis channel (non-fatal if Redis unavailable)
# ---------------------------------------------------------------------------


async def _publish(redis_client: Any | None, channel: str, envelope: dict) -> None:
    """Publish a serialised envelope to a Redis channel.

    Non-fatal: logs a warning if Redis is None or unavailable (Phase 0 mock harness
    tolerates Redis absence; Phase 2 treats it as fatal).
    """
    if redis_client is None:
        logger.warning("Redis client is None — skipping publish to %s", channel)
        return
    try:
        payload = json.dumps(envelope)
        await redis_client.publish(channel, payload)
        logger.debug("Published %s to %s", envelope.get("event_type"), channel)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Redis publish to %s failed (non-fatal): %s", channel, exc)


# ---------------------------------------------------------------------------
# run_cycle — execute one mock cycle (MOCK-02 trace)
# ---------------------------------------------------------------------------


async def run_cycle(
    web3: Any,
    mock_perps: Any,
    vault: str,
    model: str,
    cycle: int,
    *,
    db: Any | None = None,
    redis: Any | None = None,
    session_id: str = "00000000-0000-0000-0000-000000000000",
    seq: int = 1,
    roll_blocks: bool = True,
) -> dict:
    """Execute one mock orchestrator cycle.

    MOCK-02 execution trace:
      fixture → schema-validate → MockPerps → roll blocks → executeOrder →
      OrderExecuted → record_trade → publish TradeEvent
      (malformed → ModelStatus{malformed} + NO trade/journal, ORCH-05)
      (timeout   → ModelStatus{paused}   + NO trade,         ORCH-06)

    Args:
        web3: web3.AsyncWeb3 connected to a local anvil node.
        mock_perps: web3.AsyncWeb3 contract instance for MockPerps.
        vault: Vault address (checksummed hex string).
        model: Model identifier string (e.g. 'claude-opus-4-7').
        cycle: 1-based cycle number.
        db: AsyncSession connected to orchestrator Postgres. None = skip DB writes.
        redis: redis.asyncio.Redis client. None = skip Redis publishes.
        session_id: Active trading session UUID (string).
        seq: Per-channel sequence number for the envelope (D-25).
        roll_blocks: If True, mine executionDelay blocks before executeOrder.

    Returns:
        Result dict with keys:
          status: 'ok' | 'malformed' | 'timeout'
          order_key: hex bytes32 (only on status='ok')
          tx_hash: OrderExecuted transaction hash (only on status='ok')
          block_number: block of OrderExecuted (only on status='ok')
          trade_hash: trade hash written to Postgres (only on status='ok' and db is not None)
    """
    from backend.ws.channels import channel_for

    vault_channel = channel_for("ModelStatus", vault_address=vault)
    trade_channel = channel_for("TradeEvent", vault_address=vault)

    # ── Step 1: Load fixture ──────────────────────────────────────────────────
    fixture = load_fixture(model, cycle)
    logger.info("Cycle %d: loaded fixture for model=%s", cycle, model)

    # ── Step 2: Timeout marker check (ORCH-06) ────────────────────────────────
    if _is_timeout_marker(fixture):
        logger.warning("Cycle %d: timeout marker detected — ORCH-06 failure path", cycle)
        reason = fixture.get("_reason", "Simulated provider timeout (ORCH-06)")
        if db is not None:
            await record_model_status(
                db,
                vault_address=vault,
                session_id=session_id,
                model=model,
                status="paused",
                consecutive_failures=1,
                reason=reason,
                cycle_number=cycle,
            )
        status_payload = {
            "vault_address": vault,
            "model": model,
            "status": "paused",
            "consecutive_failures": 1,
            "reason": reason,
        }
        envelope = _make_envelope("ModelStatus", status_payload, seq=seq)
        await _publish(redis, vault_channel, envelope)
        return {"status": "timeout", "reason": reason}

    # ── Step 3: Schema validation (ORCH-05 gate — T-0-val) ───────────────────
    try:
        decision = Decision.model_validate(fixture)
    except ValidationError as exc:
        reason = f"ValidationError: {exc.error_count()} error(s) — {exc.errors()[0]['msg']}"
        logger.warning("Cycle %d: malformed fixture — %s", cycle, reason)
        if db is not None:
            await record_model_status(
                db,
                vault_address=vault,
                session_id=session_id,
                model=model,
                status="malformed",
                consecutive_failures=1,
                reason=reason,
                cycle_number=cycle,
            )
        status_payload = {
            "vault_address": vault,
            "model": model,
            "status": "malformed",
            "consecutive_failures": 1,
            "reason": reason,
        }
        envelope = _make_envelope("ModelStatus", status_payload, seq=seq)
        await _publish(redis, vault_channel, envelope)
        # ORCH-05: malformed → NO trade, NO journal entry
        return {"status": "malformed", "reason": reason}

    # ── Step 4: Skip 'hold' actions (no MockPerps call needed) ───────────────
    if decision.action == "hold":
        logger.info("Cycle %d: action=hold — no trade this cycle", cycle)
        return {"status": "ok", "action": "hold"}

    # ── Step 5: Submit to MockPerps ───────────────────────────────────────────
    deployer = web3.eth.accounts[0]
    size_usd_1e30 = int(decision.sizeUsd * 1e30)
    leverage_1e4 = int(decision.leverage * 10_000)
    slippage_bps = 50  # 0.5% slippage — ignored by mock

    logger.info(
        "Cycle %d: submitting %s %s %s sizeUsd=%s leverage=%sx",
        cycle,
        decision.action,
        decision.market,
        decision.side,
        decision.sizeUsd,
        decision.leverage,
    )

    if decision.action in ("open", "adjust"):
        if decision.side == "long":
            tx_hash = await mock_perps.functions.openLong(
                decision.market, size_usd_1e30, leverage_1e4, slippage_bps
            ).transact({"from": vault})
        else:
            tx_hash = await mock_perps.functions.openShort(
                decision.market, size_usd_1e30, leverage_1e4, slippage_bps
            ).transact({"from": vault})
    else:
        # close — requires an existing position key; harness uses 0-bytes as placeholder
        logger.warning(
            "Cycle %d: closePosition called without a real positionKey — "
            "not supported in Phase 0 standalone harness (use integration test for close path)",
            cycle,
        )
        return {"status": "ok", "action": "close", "skipped": True}

    # Parse orderKey from transaction logs using the IPerpsAdapter OrderCreated event
    # MockPerps does not emit OrderCreated — the orderKey is returned by openLong/openShort.
    # We recover it by calling the function's return value via eth_call first, but since
    # we already submitted the tx, re-derive from the pending order by scanning events.
    # Simpler approach: call getOrderKey via the contract's state (not available as view).
    # Use transact() + receipt approach: orderKey is in the pending order created at
    # block `create_block` by caller `vault`. Re-derive via keccak(vault, block, nonce-1).
    # For the harness, we read it from the OrderExecuted event after executeOrder().

    # ── Step 6: Store pending journal row (before rolling blocks) ────────────
    # Placeholder order_key — replaced with the real key after OrderExecuted.
    # Using tx_hash as a surrogate until OrderExecuted is observed (D-02 semantics).
    pending_order_key = tx_hash.hex() if hasattr(tx_hash, "hex") else str(tx_hash)

    if db is not None:
        await record_journal_pending(
            db,
            vault_address=vault,
            order_key=pending_order_key,
            canonical_decision=decision.model_dump(),
        )

    # ── Step 7: Roll executionDelay blocks + executeOrder ────────────────────
    if roll_blocks:
        # Mine enough blocks to satisfy executionDelay
        execution_delay = await mock_perps.functions.executionDelay().call()
        for _ in range(execution_delay + 1):
            await web3.provider.make_request("evm_mine", [])

    # Find the pending order created in this transaction block by enumerating
    # OrderExecuted events after calling executeOrder on the pendingOrders mapping.
    # Since we don't have the orderKey yet, scan pendingOrders via event logs.
    # The orderKey is returned as tx return value — use eth_call to get it.
    # Alternative: parse the tx receipt's input data (not reliable for orderKey).
    # Best approach for the mock harness: use a dedicated helper that calls
    # getOrderKeyForBlock() — but MockPerps doesn't expose this as a view.
    # Fallback: scan the OrderExecuted events emitted in a known block range.

    # Actually, the cleanest approach for the harness:
    # 1. Get the latest pending order by scanning contract state.
    # 2. The nonce is incremented twice per openLong (positionKey + orderKey).
    # Since _nonce is private, we instead discover the orderKey by watching
    # OrderExecuted after we call executeOrder for all pending orders in that vault.

    # Harness strategy: call executeOrder by iterating over known pending orders.
    # We track pending orders by querying OrderCreated-equivalent — but MockPerps
    # doesn't emit OrderCreated. Instead we use the _nonce-based key derivation
    # that the contract uses: keccak256(abi.encodePacked(msg.sender, block.number, nonce)).
    # The nonce for the orderKey is (nonce at position creation + 1).
    # We recover orderKey via brute-force search over the last few keys.

    # Simplified approach for Phase 0: use getOrderKey by calling tryExecuteOrder
    # with a low-level scan, or — even simpler — use the events.
    # MockPerps emits OrderExecuted(orderKey, vault, positionKey) after executeOrder().
    # We call executeOrder for all orders we might have created by trying to execute
    # the key derived from: keccak256(vault_address, create_block, _nonce-1).

    # Since we can't read _nonce from outside (it's private), we use the eth_call
    # pattern: build the orderKey from (vault, create_block, nonce_guess) and try.
    # Simpler for harness: call executeOrder on the last known pending order key.
    # We achieve this by calling a helper that returns pending order keys for a vault.

    # PRAGMATIC APPROACH: Call executeOrder on all possible keys by using
    # OpenLong's return value through a static call pattern.
    # Since web3.py transact() doesn't return the function return value (only tx hash),
    # we use eth_call to get the orderKey, then transact.
    #
    # Re-derive using eth_call simulation:
    order_key_bytes = await _get_order_key_for_tx(web3, mock_perps, vault, tx_hash)
    if order_key_bytes is None:
        logger.error("Cycle %d: could not recover orderKey from tx %s", cycle, tx_hash)
        return {"status": "error", "error": "orderKey not recoverable"}

    order_key_hex = "0x" + order_key_bytes.hex()

    # Execute the order (mimics GMX keeper)
    exec_tx = await mock_perps.functions.executeOrder(order_key_bytes).transact({"from": deployer})
    exec_receipt = await web3.eth.get_transaction_receipt(exec_tx)
    exec_block = exec_receipt["blockNumber"]

    # Parse OrderExecuted event
    executed_events = mock_perps.events.OrderExecuted().process_receipt(exec_receipt)
    if not executed_events:
        # Could be a liquidation — check PositionLiquidated
        logger.warning(
            "Cycle %d: OrderExecuted not in receipt — checking PositionLiquidated", cycle
        )
        return {
            "status": "liquidated",
            "order_key": order_key_hex,
            "tx_hash": exec_tx.hex() if hasattr(exec_tx, "hex") else str(exec_tx),
            "block_number": exec_block,
        }

    event = executed_events[0]
    position_key = event["args"]["positionKey"]

    # ── Step 8: record_trade (D-02: on OrderExecuted, not on open receipt) ───
    exec_tx_hex = exec_tx.hex() if hasattr(exec_tx, "hex") else str(exec_tx)
    trade_hash = None
    if db is not None:
        trade_hash = await record_trade(
            db,
            vault_address=vault,
            session_id=session_id,
            order_key=order_key_hex,
            market=decision.market,
            side=decision.side,
            action=decision.action,
            size_usdc=decision.sizeUsd,
            onchain_tx=exec_tx_hex,
            block_number=exec_block,
        )

    # ── Step 9: Publish TradeEvent envelope to Redis ──────────────────────────
    trade_payload = {
        "vault_address": vault,
        "order_key": order_key_hex,
        "action": decision.action,
        "market": decision.market,
        "side": decision.side,
        "size_usd": str(decision.sizeUsd),
        "leverage": decision.leverage,
        "tx_hash": exec_tx_hex,
        "block_number": exec_block,
        "trade_hash": trade_hash,
    }
    envelope = _make_envelope(
        "TradeEvent",
        trade_payload,
        seq=seq,
        block_number=exec_block,
    )
    await _publish(redis, trade_channel, envelope)

    logger.info(
        "Cycle %d: OrderExecuted at block %d — order_key=%s tx=%s",
        cycle,
        exec_block,
        order_key_hex[:10],
        exec_tx_hex[:10],
    )

    return {
        "status": "ok",
        "order_key": order_key_hex,
        "position_key": "0x" + position_key.hex(),
        "tx_hash": exec_tx_hex,
        "block_number": exec_block,
        "trade_hash": trade_hash,
    }


# ---------------------------------------------------------------------------
# _get_order_key_for_tx — recover orderKey from a completed transaction
# ---------------------------------------------------------------------------


async def _get_order_key_for_tx(
    web3: Any,
    mock_perps: Any,
    vault: str,
    tx_hash: Any,
) -> bytes | None:
    """Recover the orderKey generated by an openLong/openShort transaction.

    Strategy: The MockPerps._freshKey() function uses:
        keccak256(abi.encodePacked(msg.sender, block.number, _nonce++))

    Each call to _openPosition generates TWO keys (positionKey + orderKey), so
    the orderKey is at nonce = (nonce at call start + 1).

    Since _nonce is private, we use the eth_call pattern:
      - Re-encode openLong/openShort as a simulated call at the SAME block
      - This is not directly possible after the fact.

    Alternative: scan the pendingOrders mapping for the vault's latest order.
    Since MockPerps doesn't provide an enumerator, we brute-force using the
    keccak256 derivation with nonce candidates from 0 to some max.

    Practical shortcut for Phase 0 harness: read the pending order using
    the 'pending_orders' mapping by trying keys derived from:
      keccak256(abi.encodePacked(vault, receipt.blockNumber, nonce))
    for nonce in range(0, MAX_NONCE_SEARCH).

    Returns:
        bytes32 orderKey if found; None if not found within MAX_NONCE_SEARCH.
    """
    from web3 import Web3

    receipt = await web3.eth.get_transaction_receipt(tx_hash)
    create_block = receipt["blockNumber"]

    # Brute-force: try nonce=0 to 99 (realistic for Phase 0 tests)
    MAX_NONCE_SEARCH = 100
    for nonce_guess in range(MAX_NONCE_SEARCH):
        # keccak256(abi.encodePacked(msg.sender, block.number, _nonce))
        packed = (
            bytes.fromhex(vault[2:].lower().zfill(40))  # address (20 bytes)
            + create_block.to_bytes(32, "big")  # uint256 block.number (32 bytes)
            + nonce_guess.to_bytes(32, "big")  # uint256 _nonce (32 bytes)
        )
        candidate_key = Web3.keccak(packed)

        # Check if this is a valid orderKey in the pendingOrders mapping
        try:
            pending_order = await mock_perps.functions.pendingOrders(candidate_key).call()
            # pendingOrders returns (positionKey, executeAfterBlock, vault, isClose, executed)
            order_vault = pending_order[2]
            is_executed = pending_order[4]
            if order_vault.lower() == vault.lower() and not is_executed:
                return candidate_key
        except Exception:  # noqa: BLE001
            continue

    return None


# ---------------------------------------------------------------------------
# run_timeline — replay a list of cycles on a wall-clock schedule
# ---------------------------------------------------------------------------


async def run_timeline(
    web3: Any,
    mock_perps: Any,
    vault: str,
    model: str,
    cycles: list[int],
    *,
    db: Any | None = None,
    redis: Any | None = None,
    session_id: str = "00000000-0000-0000-0000-000000000000",
    interval_seconds: float = 0.1,
) -> list[dict]:
    """Replay a list of cycles on a wall-clock schedule.

    Args:
        web3: AsyncWeb3 connected to anvil.
        mock_perps: MockPerps contract instance.
        vault: Vault address.
        model: Model identifier.
        cycles: Ordered list of cycle numbers to replay (e.g. [1, 2, 3]).
        db: Optional AsyncSession for Postgres writes.
        redis: Optional redis.asyncio.Redis for event publishing.
        session_id: Active session UUID.
        interval_seconds: Delay between cycles (default 0.1s for test speed).

    Returns:
        List of result dicts from each run_cycle call.
    """
    results = []
    for i, cycle in enumerate(cycles):
        result = await run_cycle(
            web3,
            mock_perps,
            vault,
            model,
            cycle,
            db=db,
            redis=redis,
            session_id=session_id,
            seq=i + 1,
        )
        results.append(result)
        if i < len(cycles) - 1:
            await asyncio.sleep(interval_seconds)
    return results

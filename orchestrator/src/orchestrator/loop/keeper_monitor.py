"""
orchestrator.loop.keeper_monitor — Async keeper: executes block-ready pending orders (D-13 / ORCH-08).

Runs as a SEPARATE asyncio.Task alongside the loop driver.  Never in the driver's
cycle body — that would violate D-13 (Pitfall 3: same-cycle execution bypasses the
pending-order window that SC-2 / record-intent-before-submit relies on).

Responsibilities:
  - Poll get_pending_orders_ready every ~2s for rows with status='pending'
    and execute_after_block <= current_block.
  - Call MockPerps.executeOrder for each ready order.
  - On OrderExecuted: record_trade (D-02 — only on OrderExecuted, never on create)
    + mark_pending_order_executed.
  - On PositionLiquidated (no OrderExecuted): mark_pending_order_executed without
    a trade row.
  - On any exception (e.g. "too early" revert): log warning, leave the row pending,
    retry next poll.
  - Exit cleanly when stop_event is set (D-12 session end).

Design notes:
  - Uses asyncio.sleep only — never time.sleep.
  - get_pending_orders_ready gates on status='pending' (NOT 'intent') — intent rows
    are unresolved pre-submit rows that the driver must re-drive, not the keeper.
  - record_trade is called ONLY inside the OrderExecuted branch (D-02).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from orchestrator.alerts.sink import AlertSeverity, send_alert
from orchestrator.journal.publisher import publish_journal_entry
from orchestrator.mock_harness import _make_envelope, _publish
from orchestrator.state.db import (
    get_pending_orders_ready,
    mark_pending_order_executed,
    record_trade,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# execute_ready_orders — single poll pass
# ---------------------------------------------------------------------------


async def execute_ready_orders(
    web3: Any,
    mock_perps: Any,
    db_session: Any,
    *,
    deployer_address: str,
    vault_address: str,
    redis: Any | None = None,
    session_id: str,
    seq_counter: int,
    # Journal publisher params (PERPS-02 / D-08/D-09): optional to preserve
    # backward-compat with existing callers (anvil tests, Phase-2 harness).
    # When all three are provided, publish_journal_entry fires after OrderExecuted.
    journal_registry: Any | None = None,
    operator_journal_private_key: bytes | None = None,
    pinata_jwt: str | None = None,
    filebase_access_key: str | None = None,
    filebase_secret_key: str | None = None,
    operator_journal_key_address: str | None = None,
    telegram_bot_token: str | None = None,
    telegram_chat_id: str | None = None,
) -> list[dict]:
    """Execute all pending orders whose execute_after_block has elapsed.

    Args:
        web3: AsyncWeb3 instance connected to the local anvil node.
        mock_perps: MockPerps contract instance (AsyncWeb3 contract).
        db_session: AsyncSession for orchestrator DB reads/writes.
        deployer_address: Deployer EOA that calls executeOrder (MockPerps keeper role).
        vault_address: Vault address to filter pending_orders rows.
        redis: Optional redis.asyncio client for TradeEvent publishing.
        session_id: Active trading session UUID string.
        seq_counter: Envelope sequence number for the TradeEvent.
        journal_registry: JournalRegistry contract instance for onchain attestation.
                          When provided together with operator_journal_private_key and
                          pinata_jwt, publish_journal_entry fires after OrderExecuted.
        operator_journal_private_key: Raw 32-byte private key for EIP-191 signing.
        pinata_jwt: Pinata V3 JWT for IPFS pinning.
        filebase_access_key: Filebase S3 access key (SigV4) for backup pinning (D-08).
        filebase_secret_key: Filebase S3 secret key (SigV4) for backup pinning (D-08).
        operator_journal_key_address: Hex address for the journal key (transact from).
        telegram_bot_token: Optional Telegram bot token for alert sink.
        telegram_chat_id: Optional Telegram chat ID for alert sink.

    Returns:
        List of result dicts, one per order attempted:
          {"status": "executed" | "liquidated" | "error", "order_key": hex_str}
    """
    from backend.ws.channels import channel_for

    current_block = await web3.eth.get_block_number()
    ready = await get_pending_orders_ready(
        db_session,
        current_block,
        vault_address=vault_address,
    )

    results: list[dict] = []
    for order in ready:
        order_key_hex: str = order["order_key"]
        order_key_bytes = bytes.fromhex(order_key_hex.removeprefix("0x"))
        decision_snap: dict = order.get("decision_snapshot") or {}

        try:
            exec_tx = await mock_perps.functions.executeOrder(order_key_bytes).transact(
                {"from": deployer_address}
            )
            # GAP-1a fix (same race as driver): use wait_for_transaction_receipt so
            # the executeOrder tx is confirmed before we parse its events.
            exec_receipt = await web3.eth.wait_for_transaction_receipt(exec_tx, timeout=30)
            exec_block = exec_receipt["blockNumber"]

            # ── Branch: OrderExecuted (D-02: record_trade ONLY here) ─────────
            executed_events = mock_perps.events.OrderExecuted().process_receipt(exec_receipt)
            if executed_events:
                # Normalise tx hash to 0x-prefixed hex string
                raw_hex = exec_tx.hex() if hasattr(exec_tx, "hex") else str(exec_tx)
                exec_tx_hex = raw_hex if raw_hex.startswith("0x") else "0x" + raw_hex

                trade_hash = await record_trade(
                    db_session,
                    vault_address=vault_address,
                    session_id=session_id,
                    order_key=order_key_hex,
                    market=decision_snap.get("market", "UNKNOWN"),
                    side=decision_snap.get("side", "long"),
                    action=decision_snap.get("action", "open"),
                    size_usdc=float(decision_snap.get("sizeUsd", 0.0)),
                    onchain_tx=exec_tx_hex,
                    block_number=exec_block,
                )

                await mark_pending_order_executed(
                    db_session,
                    vault_address=vault_address,
                    order_key=order_key_hex,
                )

                # Publish TradeEvent envelope
                trade_payload = {
                    "vault_address": vault_address,
                    "order_key": order_key_hex,
                    "action": decision_snap.get("action", "open"),
                    "market": decision_snap.get("market", "UNKNOWN"),
                    "side": decision_snap.get("side", "long"),
                    "size_usd": str(decision_snap.get("sizeUsd", 0.0)),
                    "leverage": decision_snap.get("leverage", 1.0),
                    "tx_hash": exec_tx_hex,
                    "block_number": exec_block,
                    "trade_hash": trade_hash,
                }
                envelope = _make_envelope(
                    "TradeEvent",
                    trade_payload,
                    seq=seq_counter,
                    block_number=exec_block,
                )
                trade_channel = channel_for("TradeEvent", vault_address=vault_address)
                await _publish(redis, trade_channel, envelope)

                logger.info(
                    "keeper: OrderExecuted block=%d order_key=%s tx=%s",
                    exec_block,
                    order_key_hex[:10],
                    exec_tx_hex[:10],
                )

                # ── PERPS-02 / JOURNAL-01: publish journal ONLY on OrderExecuted ──
                # Wired here and NEVER in driver.py (front-running mitigation 9.1 /
                # D-09 pin scope: trade entries only, gated on the confirmed event).
                if journal_registry and operator_journal_private_key and pinata_jwt:
                    # Build the journal payload from the trade snapshot
                    journal_payload = dict(trade_payload)
                    try:
                        await publish_journal_entry(
                            web3,
                            journal_registry,
                            db_session,
                            vault_address=vault_address,
                            trade_hash=trade_hash,
                            order_key=order_key_hex,
                            payload=journal_payload,
                            operator_journal_private_key=operator_journal_private_key,
                            pinata_jwt=pinata_jwt,
                            filebase_access_key=filebase_access_key,
                            filebase_secret_key=filebase_secret_key,
                            operator_journal_key_address=operator_journal_key_address,
                            telegram_bot_token=telegram_bot_token,
                            telegram_chat_id=telegram_chat_id,
                        )
                    except Exception as pub_exc:  # noqa: BLE001
                        # Pin/record failure: log + alert, do NOT crash the monitor.
                        # The pending_pin DB row and reconcile backstop handle retry.
                        logger.warning(
                            "keeper: publish_journal_entry failed for order_key=%s (will retry): %s",
                            order_key_hex[:10],
                            pub_exc,
                        )
                        await send_alert(
                            f"Journal publish failed for order_key={order_key_hex[:10]}: {pub_exc}",
                            AlertSeverity.WARNING,
                            context={"vault_address": vault_address, "order_key": order_key_hex},
                            telegram_bot_token=telegram_bot_token,
                            telegram_chat_id=telegram_chat_id,
                        )

                results.append({"status": "executed", "order_key": order_key_hex})

            else:
                # ── Branch: PositionLiquidated (no OrderExecuted) ─────────────
                liq_events = mock_perps.events.PositionLiquidated().process_receipt(exec_receipt)
                await mark_pending_order_executed(
                    db_session,
                    vault_address=vault_address,
                    order_key=order_key_hex,
                )
                logger.warning(
                    "keeper: PositionLiquidated for order_key=%s liq_events=%s",
                    order_key_hex[:10],
                    len(liq_events),
                )
                results.append({"status": "liquidated", "order_key": order_key_hex})

        except Exception as exc:  # noqa: BLE001
            # e.g. "execution reverted: too early" — leave pending, retry next poll
            logger.warning(
                "keeper: executeOrder failed for %s (will retry): %s",
                order_key_hex[:10],
                exc,
            )
            results.append({"status": "error", "order_key": order_key_hex, "reason": str(exc)})

    return results


# ---------------------------------------------------------------------------
# run_keeper_monitor — long-running asyncio.Task
# ---------------------------------------------------------------------------


async def run_keeper_monitor(
    web3: Any,
    mock_perps: Any,
    db_session: Any,
    *,
    deployer_address: str,
    vault_address: str,
    redis: Any | None,
    session_id: str,
    stop_event: asyncio.Event,
    poll_seconds: float = 2.0,
    # Journal publisher params (PERPS-02): optional, forwarded to execute_ready_orders.
    journal_registry: Any | None = None,
    operator_journal_private_key: bytes | None = None,
    pinata_jwt: str | None = None,
    filebase_access_key: str | None = None,
    filebase_secret_key: str | None = None,
    operator_journal_key_address: str | None = None,
    telegram_bot_token: str | None = None,
    telegram_chat_id: str | None = None,
) -> None:
    """Poll pending_orders for block-ready orders and execute them.

    Designed to run as a SEPARATE asyncio.Task (not inside run_live_cycle).
    Stops cleanly when stop_event is set (D-12 session-end signal).

    Args:
        web3: AsyncWeb3 instance.
        mock_perps: MockPerps contract instance.
        db_session: AsyncSession for DB reads/writes.
        deployer_address: Deployer EOA (keeper role).
        vault_address: Vault address to monitor.
        redis: Optional redis.asyncio client.
        session_id: Active trading session UUID.
        stop_event: asyncio.Event — set by the session driver at session end (D-12).
        poll_seconds: Keeper poll interval in seconds (default 2.0 — much faster
                      than the 60s trading cadence so orders aren't left waiting).
        journal_registry: JournalRegistry contract for onchain attestation (PERPS-02).
        operator_journal_private_key: Raw key bytes for EIP-191 signing.
        pinata_jwt: Pinata JWT for IPFS pinning.
        filebase_access_key: Filebase S3 access key (SigV4) for backup pinning (D-08).
        filebase_secret_key: Filebase S3 secret key (SigV4) for backup pinning (D-08).
        operator_journal_key_address: Hex address for journal key transact from.
        telegram_bot_token: Optional Telegram token for WARNING alerts.
        telegram_chat_id: Optional Telegram chat ID.
    """
    logger.info(
        "run_keeper_monitor: starting (vault=%s poll=%.1fs)",
        vault_address[:10],
        poll_seconds,
    )
    _seq = 0
    while not stop_event.is_set():
        _seq += 1
        # WR-05: wrap poll iteration in try/except so a transient web3 or DB error
        # does not silently kill the keeper task.  execute_ready_orders already wraps
        # individual order failures; this outer guard catches get_block_number() and
        # get_pending_orders_ready() failures that would otherwise terminate the loop.
        # asyncio.CancelledError is deliberately NOT caught — let cancellation propagate
        # for clean shutdown (D-12 stop_event path).
        try:
            await execute_ready_orders(
                web3,
                mock_perps,
                db_session,
                deployer_address=deployer_address,
                vault_address=vault_address,
                redis=redis,
                session_id=session_id,
                seq_counter=_seq,
                journal_registry=journal_registry,
                operator_journal_private_key=operator_journal_private_key,
                pinata_jwt=pinata_jwt,
                filebase_access_key=filebase_access_key,
                filebase_secret_key=filebase_secret_key,
                operator_journal_key_address=operator_journal_key_address,
                telegram_bot_token=telegram_bot_token,
                telegram_chat_id=telegram_chat_id,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "run_keeper_monitor: unhandled exception in poll iteration (will retry): %s",
                exc,
            )
        # NEVER time.sleep — must keep the event loop responsive
        await asyncio.sleep(poll_seconds)

    logger.info("run_keeper_monitor: stop_event set — exiting (vault=%s)", vault_address[:10])

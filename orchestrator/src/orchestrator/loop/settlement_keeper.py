"""
orchestrator.loop.settlement_keeper — Settlement keeper: drain positions + endSession (GAP #9).

Implements the settlement flow required to finalize a trAIder session and allow holders
to call SettlementContract.claim(). The contract cannot wait inside a transaction while
MockPerps async-executes close orders, so the orchestrator must pre-drain positions and
only then call endSession.

Correct settlement flow (from 03-INTEGRATION-MATRIX.md GAP #9):
  1. Enumerate open positions: adapter.getOpenPositionKeys(vault).
  2. For each key: vault.closePosition(positionKey, 0) → queues a close in MockPerps.
     (Caller = operator-trade EOA, onlyOrchestrator on the vault.)
  3. Wait executionDelay blocks (~40-60s on Sepolia; poll like keeper_monitor does).
  4. mockPerps.executeOrder(orderKey) for each close order → positions settled.
  5. Verify adapter.positionValueUSDC(vault) == 0 (poll until true or timeout).
  6. Call settlement.endSession() → positionValueUSDC == 0 check passes → rate frozen.
  7. Holders can now call settlement.claim() — keeper itself does NOT claim for holders
     (pull pattern; unclaimed funds stay in vault indefinitely).

endSession access constraint:
  - callable by sessionFactory at any time
  - callable by ANYONE once block.timestamp >= deadline (SETT-02 recovery hatch)
  - orchestrator EOA can ONLY call endSession when the session is past its deadline

When to run:
  - gated behind SETTLE_ON_END env flag or explicit parameter; NEVER fires on a normal
    mid-session stop (the Phase-3 gate stops mid-72h session).
  - wire at explicit session-end in driver.run_session or via standalone CLI.

See also: contracts/src/SettlementContract.sol, contracts/src/mTokenVault.sol,
          orchestrator.loop.keeper_monitor (executeOrder poll/retry pattern reused here).

Usage (callable routine):
    result = await drain_and_settle(
        web3, mock_perps, settlement_contract, vault_contract,
        vault_address=vault_addr,
        orchestrator_address=operator_trade_addr,
        deployer_address=operator_trade_addr,    # for executeOrder (permissionless)
        max_drain_wait_blocks=60,
        poll_interval_seconds=2.0,
        telegram_bot_token=...,
        telegram_chat_id=...,
    )

RUNBOOK note:
  - "endSession not permitted before deadline": the orchestrator EOA is not the sessionFactory
    and the session deadline has not passed. The keeper will log a clear INFO message and
    exit without crashing. To finalize early, call endSession from the sessionFactory address
    or wait for the session deadline to pass and re-run the keeper.
  - After deadline passes, anyone (including the orchestrator) may call endSession. The keeper
    will detect this on re-run and proceed with settlement.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from orchestrator.alerts.sink import AlertSeverity, send_alert

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Conservative block-poll budget: 60 blocks * ~12s/block ≈ 12 minutes max wait.
# Sepolia executionDelay = 3 blocks ≈ 40-60s.  This budget covers 20× the delay.
DEFAULT_MAX_DRAIN_WAIT_BLOCKS: int = 60

# Poll interval while waiting for executionDelay to elapse (matches keeper_monitor).
DEFAULT_POLL_INTERVAL_SECONDS: float = 2.0

# How many seconds to wait before re-checking positionValueUSDC after executeOrder.
DEFAULT_VALUE_CHECK_INTERVAL_SECONDS: float = 2.0

# Maximum polls to verify positionValueUSDC == 0 before declaring timeout.
DEFAULT_VALUE_ZERO_MAX_POLLS: int = 30


# ---------------------------------------------------------------------------
# _execute_close_order — single close order with "too early" retry loop
# ---------------------------------------------------------------------------


async def _execute_close_order(
    web3: Any,
    mock_perps: Any,
    order_key_bytes: bytes,
    *,
    deployer_address: str,
    max_wait_blocks: int = DEFAULT_MAX_DRAIN_WAIT_BLOCKS,
    poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
    telegram_bot_token: str | None = None,
    telegram_chat_id: str | None = None,
) -> bool:
    """Execute a single pending close order with retry on 'too early' revert.

    Mimics keeper_monitor's executeOrder poll/retry pattern (GAP #9).

    Args:
        web3: AsyncWeb3 instance.
        mock_perps: MockPerps contract instance.
        order_key_bytes: Raw 32-byte order key.
        deployer_address: EOA to call executeOrder (permissionless — any address works).
        max_wait_blocks: Maximum number of blocks to poll before declaring timeout.
        poll_interval_seconds: Seconds between poll iterations.
        telegram_bot_token: Optional Telegram token for alerts.
        telegram_chat_id: Optional Telegram chat ID for alerts.

    Returns:
        True if the order was executed successfully, False on timeout.
    """
    order_key_hex = "0x" + order_key_bytes.hex()
    start_block = await web3.eth.get_block_number()
    timeout_block = start_block + max_wait_blocks

    logger.info(
        "settlement_keeper._execute_close_order: waiting to execute order_key=%s "
        "(start_block=%d timeout_block=%d)",
        order_key_hex[:10],
        start_block,
        timeout_block,
    )

    polls = 0
    while True:
        current_block = await web3.eth.get_block_number()
        if current_block > timeout_block:
            logger.error(
                "settlement_keeper._execute_close_order: TIMEOUT waiting for order_key=%s "
                "(current_block=%d timeout_block=%d)",
                order_key_hex[:10],
                current_block,
                timeout_block,
            )
            await send_alert(
                f"Settlement keeper: executeOrder TIMEOUT for order_key={order_key_hex[:10]} "
                f"after {max_wait_blocks} blocks. Position may not be drained.",
                AlertSeverity.CRITICAL,
                context={"order_key": order_key_hex, "current_block": str(current_block)},
                telegram_bot_token=telegram_bot_token,
                telegram_chat_id=telegram_chat_id,
            )
            return False

        polls += 1
        try:
            exec_tx = await mock_perps.functions.executeOrder(order_key_bytes).transact(
                {"from": deployer_address}
            )
            exec_receipt = await web3.eth.wait_for_transaction_receipt(exec_tx, timeout=30)

            if exec_receipt.get("status") == 0:
                logger.error(
                    "settlement_keeper._execute_close_order: executeOrder tx reverted for "
                    "order_key=%s (receipt status=0)",
                    order_key_hex[:10],
                )
                await send_alert(
                    f"Settlement keeper: executeOrder tx REVERTED for order_key={order_key_hex[:10]}",
                    AlertSeverity.CRITICAL,
                    context={"order_key": order_key_hex, "receipt": str(exec_receipt)},
                    telegram_bot_token=telegram_bot_token,
                    telegram_chat_id=telegram_chat_id,
                )
                return False

            # Check for OrderExecuted or PositionLiquidated
            executed_events = mock_perps.events.OrderExecuted().process_receipt(exec_receipt)
            liq_events = mock_perps.events.PositionLiquidated().process_receipt(exec_receipt)

            if executed_events or liq_events:
                if liq_events:
                    logger.warning(
                        "settlement_keeper._execute_close_order: PositionLiquidated for "
                        "order_key=%s (collateral wiped — position closed at zero value)",
                        order_key_hex[:10],
                    )
                else:
                    logger.info(
                        "settlement_keeper._execute_close_order: OrderExecuted for order_key=%s "
                        "(block=%d polls=%d)",
                        order_key_hex[:10],
                        exec_receipt.get("blockNumber", "?"),
                        polls,
                    )
                return True

            # Receipt OK but no matching event — unexpected; log and treat as error
            logger.error(
                "settlement_keeper._execute_close_order: no OrderExecuted/PositionLiquidated "
                "in receipt for order_key=%s — unexpected state",
                order_key_hex[:10],
            )
            return False

        except Exception as exc:  # noqa: BLE001
            exc_str = str(exc).lower()
            if "too early" in exc_str:
                # Expected: executionDelay not yet elapsed — retry next poll
                logger.debug(
                    "settlement_keeper._execute_close_order: too early for order_key=%s "
                    "(block=%d polls=%d) — retrying",
                    order_key_hex[:10],
                    current_block,
                    polls,
                )
            elif "order not found" in exc_str or "order already executed" in exc_str:
                # Order was already executed (e.g. by another keeper) — treat as success
                logger.info(
                    "settlement_keeper._execute_close_order: order_key=%s already executed "
                    "('%s') — treating as success",
                    order_key_hex[:10],
                    exc_str[:60],
                )
                return True
            else:
                logger.warning(
                    "settlement_keeper._execute_close_order: unexpected error for order_key=%s "
                    "(will retry): %s",
                    order_key_hex[:10],
                    exc,
                )

        await asyncio.sleep(poll_interval_seconds)


# ---------------------------------------------------------------------------
# drain_and_settle — the full settlement keeper routine
# ---------------------------------------------------------------------------


async def drain_and_settle(
    web3: Any,
    mock_perps: Any,
    settlement_contract: Any,
    vault_contract: Any,
    *,
    vault_address: str,
    orchestrator_address: str,
    deployer_address: str,
    max_drain_wait_blocks: int = DEFAULT_MAX_DRAIN_WAIT_BLOCKS,
    poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
    value_check_interval_seconds: float = DEFAULT_VALUE_CHECK_INTERVAL_SECONDS,
    value_zero_max_polls: int = DEFAULT_VALUE_ZERO_MAX_POLLS,
    telegram_bot_token: str | None = None,
    telegram_chat_id: str | None = None,
) -> dict:
    """Drain all open positions and call settlement.endSession() to freeze the redemption rate.

    Implements the correct settlement flow for MockPerps async execution (GAP #9):
      1. Enumerate open positions via adapter.getOpenPositionKeys(vault).
      2. For each key: vault.closePosition(positionKey, 0) — queues a MockPerps close.
         (Signed as operator-trade EOA — onlyOrchestrator gate on the vault.)
      3. Wait executionDelay blocks, then call mockPerps.executeOrder(orderKey) per order.
      4. Poll positionValueUSDC(vault) until == 0 or timeout.
      5. Call settlement.endSession() — endSession checks positionValueUSDC(vault) == 0
         and freezes the redemption rate.

    endSession access constraint (SETT-02):
      - If called before the session deadline AND sender != sessionFactory, endSession reverts.
      - In that case, we log clearly and return without crashing.
      - The caller should wait until the deadline has passed and re-invoke.

    Args:
        web3: AsyncWeb3 instance.
        mock_perps: MockPerps contract instance (IPerpsAdapter — for getOpenPositionKeys,
                    executeOrder, positionValueUSDC).
        settlement_contract: SettlementContract instance (for endSession).
        vault_contract: MTokenVault contract instance (for closePosition — onlyOrchestrator).
        vault_address: Checksummed vault address string.
        orchestrator_address: Operator-trade EOA (onlyOrchestrator for vault.closePosition).
        deployer_address: EOA for executeOrder (permissionless — any address works).
        max_drain_wait_blocks: Block budget to wait for executeOrder per order.
        poll_interval_seconds: Seconds between executeOrder retry polls.
        value_check_interval_seconds: Seconds between positionValueUSDC == 0 polls.
        value_zero_max_polls: Maximum times to poll positionValueUSDC before timeout.
        telegram_bot_token: Optional Telegram token for CRITICAL alerts.
        telegram_chat_id: Optional Telegram chat ID for alerts.

    Returns:
        Dict with keys:
          status: 'settled' | 'not_permitted' | 'already_settled' | 'drain_timeout' |
                  'value_nonzero_timeout' | 'error'
          positions_closed: int (number of close orders submitted)
          message: human-readable summary
    """
    logger.info(
        "drain_and_settle: starting settlement keeper for vault=%s",
        vault_address[:10],
    )

    # ── Step 0: Check if already settled ─────────────────────────────────────
    try:
        already_settled: bool = await settlement_contract.functions.settled().call()
    except Exception as exc:  # noqa: BLE001
        logger.error("drain_and_settle: failed to read settlement.settled(): %s", exc)
        return {"status": "error", "positions_closed": 0, "message": str(exc)}

    if already_settled:
        logger.info(
            "drain_and_settle: settlement.settled == true — already finalized, nothing to do"
        )
        return {
            "status": "already_settled",
            "positions_closed": 0,
            "message": "Settlement already finalized; holders may call claim().",
        }

    # ── Step 1: Enumerate open positions ─────────────────────────────────────
    try:
        open_keys: list[bytes] = await mock_perps.functions.getOpenPositionKeys(
            vault_address
        ).call()
    except Exception as exc:  # noqa: BLE001
        logger.error("drain_and_settle: getOpenPositionKeys failed: %s", exc)
        await send_alert(
            f"Settlement keeper: getOpenPositionKeys failed for vault {vault_address[:10]}: {exc}",
            AlertSeverity.CRITICAL,
            context={"vault_address": vault_address, "error": str(exc)},
            telegram_bot_token=telegram_bot_token,
            telegram_chat_id=telegram_chat_id,
        )
        return {"status": "error", "positions_closed": 0, "message": str(exc)}

    logger.info(
        "drain_and_settle: found %d open position(s) for vault=%s",
        len(open_keys),
        vault_address[:10],
    )

    # ── Step 2: Submit close orders via vault.closePosition (onlyOrchestrator) ──
    close_order_keys: list[bytes] = []
    positions_closed = 0

    for pos_key_bytes in open_keys:
        pos_key_hex = "0x" + pos_key_bytes.hex()
        try:
            close_tx = await vault_contract.functions.closePosition(
                pos_key_bytes,
                0,  # sizeUsd=0 → full close in MockPerps
            ).transact({"from": orchestrator_address})
            close_receipt = await web3.eth.wait_for_transaction_receipt(close_tx, timeout=30)

            if close_receipt.get("status") == 0:
                logger.error(
                    "drain_and_settle: closePosition tx REVERTED for pos_key=%s",
                    pos_key_hex[:10],
                )
                await send_alert(
                    f"Settlement keeper: vault.closePosition REVERTED for pos_key={pos_key_hex[:10]}",
                    AlertSeverity.CRITICAL,
                    context={
                        "vault_address": vault_address,
                        "pos_key": pos_key_hex,
                        "orchestrator_address": orchestrator_address,
                    },
                    telegram_bot_token=telegram_bot_token,
                    telegram_chat_id=telegram_chat_id,
                )
                continue  # try remaining positions

            # Recover close order key from OrderCreated event
            order_created_events = mock_perps.events.OrderCreated().process_receipt(close_receipt)
            if order_created_events:
                close_order_key_bytes: bytes = order_created_events[0]["args"]["orderKey"]
                close_order_keys.append(close_order_key_bytes)
                positions_closed += 1
                logger.info(
                    "drain_and_settle: closePosition OK pos_key=%s order_key=%s",
                    pos_key_hex[:10],
                    "0x" + close_order_key_bytes.hex()[:8],
                )
            else:
                logger.error(
                    "drain_and_settle: no OrderCreated event in closePosition receipt for "
                    "pos_key=%s — cannot recover order_key for executeOrder",
                    pos_key_hex[:10],
                )
                await send_alert(
                    f"Settlement keeper: no OrderCreated event after closePosition for "
                    f"pos_key={pos_key_hex[:10]}",
                    AlertSeverity.CRITICAL,
                    context={"vault_address": vault_address, "pos_key": pos_key_hex},
                    telegram_bot_token=telegram_bot_token,
                    telegram_chat_id=telegram_chat_id,
                )

        except Exception as exc:  # noqa: BLE001
            logger.error(
                "drain_and_settle: vault.closePosition raised for pos_key=%s: %s",
                pos_key_hex[:10],
                exc,
            )
            await send_alert(
                f"Settlement keeper: vault.closePosition raised for pos_key={pos_key_hex[:10]}: {exc}",
                AlertSeverity.CRITICAL,
                context={
                    "vault_address": vault_address,
                    "pos_key": pos_key_hex,
                    "error": str(exc),
                },
                telegram_bot_token=telegram_bot_token,
                telegram_chat_id=telegram_chat_id,
            )

    logger.info(
        "drain_and_settle: submitted %d close order(s) — now waiting for executeOrder",
        positions_closed,
    )

    # ── Step 3: executeOrder for each close order (with "too early" retry) ────
    execute_successes = 0
    for order_key_bytes in close_order_keys:
        ok = await _execute_close_order(
            web3,
            mock_perps,
            order_key_bytes,
            deployer_address=deployer_address,
            max_wait_blocks=max_drain_wait_blocks,
            poll_interval_seconds=poll_interval_seconds,
            telegram_bot_token=telegram_bot_token,
            telegram_chat_id=telegram_chat_id,
        )
        if ok:
            execute_successes += 1
        else:
            logger.error(
                "drain_and_settle: failed to execute close order_key=%s — "
                "positionValueUSDC may not reach 0",
                "0x" + order_key_bytes.hex()[:8],
            )

    if execute_successes < len(close_order_keys):
        logger.error(
            "drain_and_settle: only %d/%d close orders executed successfully — "
            "aborting settlement (positions not fully drained)",
            execute_successes,
            len(close_order_keys),
        )
        return {
            "status": "drain_timeout",
            "positions_closed": positions_closed,
            "message": (
                f"Only {execute_successes}/{len(close_order_keys)} close orders executed. "
                "Re-run the settlement keeper after positions drain."
            ),
        }

    # ── Step 4: Poll positionValueUSDC until == 0 ──────────────────────────────
    logger.info("drain_and_settle: polling positionValueUSDC until == 0 ...")
    polls = 0
    while polls < value_zero_max_polls:
        polls += 1
        try:
            pos_val: int = await mock_perps.functions.positionValueUSDC(vault_address).call()
            if pos_val == 0:
                logger.info("drain_and_settle: positionValueUSDC == 0 confirmed (polls=%d)", polls)
                break
            logger.debug(
                "drain_and_settle: positionValueUSDC=%d (still non-zero, polls=%d)",
                pos_val,
                polls,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "drain_and_settle: positionValueUSDC call failed (non-fatal, retrying): %s", exc
            )
        await asyncio.sleep(value_check_interval_seconds)
    else:
        # Exceeded poll budget
        logger.error(
            "drain_and_settle: positionValueUSDC still non-zero after %d polls — "
            "endSession would revert; aborting",
            polls,
        )
        await send_alert(
            f"Settlement keeper: positionValueUSDC still non-zero after {polls} polls for "
            f"vault={vault_address[:10]}. endSession cannot proceed.",
            AlertSeverity.CRITICAL,
            context={"vault_address": vault_address, "polls": str(polls)},
            telegram_bot_token=telegram_bot_token,
            telegram_chat_id=telegram_chat_id,
        )
        return {
            "status": "value_nonzero_timeout",
            "positions_closed": positions_closed,
            "message": (
                f"positionValueUSDC still non-zero after {polls} polls. "
                "Re-run the settlement keeper."
            ),
        }

    # ── Step 5: Call settlement.endSession() ─────────────────────────────────
    # endSession access constraint (SETT-02):
    #   - factory at any time
    #   - anyone once block.timestamp >= deadline
    # Orchestrator EOA is NOT the factory, so it can only call endSession post-deadline.
    # Check the deadline to provide a clear log message if not yet permitted.
    try:
        deadline: int = await settlement_contract.functions.deadline().call()
        block_ts_data = await web3.eth.get_block("latest")
        block_ts: int = int(block_ts_data["timestamp"])

        if block_ts < deadline:
            # Pre-deadline: orchestrator EOA cannot call endSession (only factory can).
            # Log clearly and return without crashing.  The operator must either:
            #   (a) wait for the deadline to pass (and re-run this keeper), OR
            #   (b) call endSession from the sessionFactory address manually.
            msg = (
                f"drain_and_settle: session deadline not yet passed "
                f"(deadline={deadline} block_ts={block_ts} diff={deadline - block_ts}s). "
                "Orchestrator EOA cannot call endSession before deadline (SETT-02). "
                "Wait for deadline OR call endSession from the sessionFactory address. "
                "RUNBOOK: positions are fully drained — re-run keeper after deadline to finalize."
            )
            logger.info(msg)
            return {
                "status": "not_permitted",
                "positions_closed": positions_closed,
                "message": msg,
            }
    except Exception as exc:  # noqa: BLE001
        # Deadline check failure is non-fatal — attempt endSession anyway (may succeed or revert)
        logger.warning(
            "drain_and_settle: failed to read settlement.deadline() (non-fatal, attempting endSession): %s",
            exc,
        )

    try:
        end_tx = await settlement_contract.functions.endSession().transact(
            {"from": orchestrator_address}
        )
        end_receipt = await web3.eth.wait_for_transaction_receipt(end_tx, timeout=60)

        if end_receipt.get("status") == 0:
            # Most likely cause: pre-deadline + not factory → "Settlement: not authorized before deadline"
            # OR: "Settlement: positions not drained" (positions still open — race condition)
            logger.error(
                "drain_and_settle: settlement.endSession() tx REVERTED. "
                "Possible causes: (1) pre-deadline and not factory, (2) positions not fully drained. "
                "RUNBOOK: check deadline and positionValueUSDC before re-running."
            )
            await send_alert(
                f"Settlement keeper: endSession() tx REVERTED for vault={vault_address[:10]}. "
                "Check RUNBOOK: deadline or positions not drained.",
                AlertSeverity.CRITICAL,
                context={
                    "vault_address": vault_address,
                    "orchestrator_address": orchestrator_address,
                    "receipt": str(end_receipt)[:200],
                },
                telegram_bot_token=telegram_bot_token,
                telegram_chat_id=telegram_chat_id,
            )
            # Distinguish between not-permitted and drained-but-revert by re-checking settled
            try:
                if await settlement_contract.functions.settled().call():
                    # Actually settled — receipt status may have been misread (race)
                    pass
                else:
                    return {
                        "status": "error",
                        "positions_closed": positions_closed,
                        "message": "endSession() tx reverted — see RUNBOOK in settlement_keeper.py",
                    }
            except Exception:  # noqa: BLE001
                pass
            return {
                "status": "error",
                "positions_closed": positions_closed,
                "message": "endSession() tx reverted — see RUNBOOK in settlement_keeper.py",
            }

        logger.warning(
            "drain_and_settle: settlement.endSession() CONFIRMED — "
            "vault=%s redemption rate frozen; holders may call settlement.claim()",
            vault_address[:10],
        )
        return {
            "status": "settled",
            "positions_closed": positions_closed,
            "message": (
                "Settlement finalized. Holders may call settlement.claim() to withdraw USDC. "
                f"Drain tx count: {positions_closed}. "
                "Claim path: settlement.claim() — pull pattern, no loop, unclaimed funds stay indefinitely."
            ),
        }

    except Exception as exc:  # noqa: BLE001
        exc_str = str(exc)
        # Check for the specific "not authorized before deadline" revert
        if (
            "not authorized before deadline" in exc_str.lower()
            or "not authorized" in exc_str.lower()
        ):
            msg = (
                f"drain_and_settle: endSession() reverted 'not authorized before deadline'. "
                "Orchestrator EOA cannot call endSession before deadline (SETT-02). "
                "RUNBOOK: wait for the session deadline, then re-run the settlement keeper. "
                f"Positions are fully drained ({positions_closed} orders executed)."
            )
            logger.info(msg)
            return {
                "status": "not_permitted",
                "positions_closed": positions_closed,
                "message": msg,
            }

        # Unexpected exception
        logger.error(
            "drain_and_settle: settlement.endSession() raised unexpectedly: %s",
            exc,
        )
        await send_alert(
            f"Settlement keeper: endSession() raised for vault={vault_address[:10]}: {exc}",
            AlertSeverity.CRITICAL,
            context={
                "vault_address": vault_address,
                "error": str(exc),
            },
            telegram_bot_token=telegram_bot_token,
            telegram_chat_id=telegram_chat_id,
        )
        return {
            "status": "error",
            "positions_closed": positions_closed,
            "message": str(exc),
        }


# ---------------------------------------------------------------------------
# run_settlement_keeper — coroutine wrapper for asyncio.create_task
# ---------------------------------------------------------------------------


async def run_settlement_keeper(
    web3: Any,
    mock_perps: Any,
    settlement_contract: Any,
    vault_contract: Any,
    *,
    vault_address: str,
    orchestrator_address: str,
    deployer_address: str,
    max_drain_wait_blocks: int = DEFAULT_MAX_DRAIN_WAIT_BLOCKS,
    poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
    value_check_interval_seconds: float = DEFAULT_VALUE_CHECK_INTERVAL_SECONDS,
    value_zero_max_polls: int = DEFAULT_VALUE_ZERO_MAX_POLLS,
    telegram_bot_token: str | None = None,
    telegram_chat_id: str | None = None,
) -> dict:
    """Thin coroutine wrapper around drain_and_settle for use with asyncio.create_task.

    Suitable for: await run_settlement_keeper(...) or asyncio.create_task(run_settlement_keeper(...))

    Gate: this should ONLY be called at explicit session-end (SETTLE_ON_END env flag or
    explicit parameter).  It must NOT fire on a Phase-3 gate mid-session stop.

    See drain_and_settle for full documentation.
    """
    return await drain_and_settle(
        web3,
        mock_perps,
        settlement_contract,
        vault_contract,
        vault_address=vault_address,
        orchestrator_address=orchestrator_address,
        deployer_address=deployer_address,
        max_drain_wait_blocks=max_drain_wait_blocks,
        poll_interval_seconds=poll_interval_seconds,
        value_check_interval_seconds=value_check_interval_seconds,
        value_zero_max_polls=value_zero_max_polls,
        telegram_bot_token=telegram_bot_token,
        telegram_chat_id=telegram_chat_id,
    )

"""
gate/evidence.py — measured (chain-derived) evidence for assert_hard_gate_set.

Anti-false-green (04-GATE discipline): the live gate must NEVER assert its own
success by construction. Every D-16 criterion that CAN be measured from chain
state IS measured here, after the harness completes:

  (a) models traded     — MockPerps OrderCreated logs per vault in the gate window.
                          Grouped by positionKey: the first OrderCreated for a key is
                          the open; each subsequent one is a close (MockPerps emits
                          OrderCreated for BOTH open and close orders — CR-01).
  (b) AMM live          — pool globalState (price, tick) snapshot before the run
                          compared to after; any change = live price discovery.
  (d) settlement        — SettlementContract.settled() per vault (AND), genuine
                          distribution = supplySnapshot() > 0 (operator/MM is
                          guaranteed 0 by the D-18 contract guard at endSession),
                          operator_claimed = any Claimed log with holder == mmAddress.

  (c) gap-close is already measured live (arb bot + harness step-2 callbacks).
  (f) fairness is guaranteed by construction in build_live_shared_deps — ONE seeded
      PriceWalk fans the SAME step to all 3 model queues; there is no per-model
      price path to diverge. Stated as an architectural invariant, not re-measured.
  (e)/(g) are artifact/run-shape checks owned by assert_hard_gate_set itself.

Window edge (documented, conservative): a close for a position opened BEFORE
from_block appears as that key's first in-window event and is counted as an open.
The gate window starts before the models trade, so in practice keys open in-window.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Vault order is fixed by the manifest: [vaultClaude, vaultGpt, vaultGem].
MODEL_ORDER: tuple[str, str, str] = ("claude", "gpt", "gemini")


async def snapshot_pool_states(pools: list[Any]) -> list[tuple[int, int] | None]:
    """Read (price, tick) per pool for the before/after AMM-liveness comparison.

    Returns None for a pool whose read fails (compared as unequal later only if
    the post-run read succeeds — a dead RPC must not fake 'AMM live').
    """
    states: list[tuple[int, int] | None] = []
    for pool in pools:
        try:
            gs = await pool.functions.globalState().call()
            states.append((int(gs[0]), int(gs[1])))
        except Exception as exc:  # noqa: BLE001
            logger.warning("snapshot_pool_states: read failed for %s: %s",
                           str(getattr(pool, "address", pool))[:10], exc)
            states.append(None)
    return states


async def _count_opens_closes(
    mock_perps: Any, vault_address: str, from_block: int
) -> tuple[int, int]:
    """Count real opens/closes for one vault from MockPerps OrderCreated logs.

    OrderCreated(orderKey, positionKey, vault) fires for opens AND closes; the
    first event for a positionKey is its open, later ones are closes.
    """
    logs = await mock_perps.events.OrderCreated.get_logs(
        from_block=from_block,
        argument_filters={"vault": vault_address},
    )
    seen: set[bytes] = set()
    opens = 0
    closes = 0
    for log in sorted(logs, key=lambda l: (l["blockNumber"], l.get("logIndex", 0))):
        key = bytes(log["args"]["positionKey"])
        if key in seen:
            closes += 1
        else:
            seen.add(key)
            opens += 1
    return opens, closes


async def measure_gate_evidence(
    *,
    mock_perps: Any,
    vaults_with_addrs: list[tuple[Any, str]],
    settlement_contracts: list[Any],
    pools: list[Any],
    pool_snapshots_before: list[tuple[int, int] | None],
    operator_lp_key: str,
    accumulator: Any,
    from_block: int,
) -> None:
    """Populate the accumulator from CHAIN STATE after the harness completes.

    Replaces the old post-harness block that hardcoded 1 open + 1 close per model
    and settlement.all_settled=True regardless of what happened on-chain.
    Failures to read are recorded as ABSENT evidence (criterion fails honestly),
    never as success.
    """
    # ── (a) per-model real opens/closes from MockPerps logs ────────────────
    for (vault_contract, vault_address), model in zip(vaults_with_addrs, MODEL_ORDER):
        try:
            opens, closes = await _count_opens_closes(mock_perps, vault_address, from_block)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "measure_gate_evidence: OrderCreated scan failed for %s (%s): %s "
                "— recording 0 trades (criterion (a) will fail honestly)",
                model, vault_address[:10], exc,
            )
            opens, closes = 0, 0
        for _ in range(opens):
            accumulator.record_trade(model, "open")
        for _ in range(closes):
            accumulator.record_trade(model, "close")
        logger.info(
            "measure_gate_evidence: (a) %s vault=%s opens=%d closes=%d [measured from chain]",
            model, vault_address[:10], opens, closes,
        )

    # ── (b) AMM liveness: pool state changed vs pre-run snapshot ───────────
    after = await snapshot_pool_states(pools)
    changed = any(
        b is not None and a is not None and a != b
        for b, a in zip(pool_snapshots_before, after)
    )
    if changed:
        accumulator.mark_pool_state_changed()
    logger.info(
        "measure_gate_evidence: (b) pool state changed=%s before=%s after=%s",
        changed, pool_snapshots_before, after,
    )

    # ── (d) settlement: settled()/supplySnapshot()/Claimed — all from chain ──
    all_settled = True
    distribute_nonempty: dict[str, bool] = {}
    operator_claimed = False
    op = operator_lp_key.lower()

    for (vault_contract, vault_address), sc in zip(vaults_with_addrs, settlement_contracts):
        try:
            settled = bool(await sc.functions.settled().call())
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "measure_gate_evidence: settled() read failed for vault=%s: %s — "
                "treating as NOT settled", vault_address[:10], exc,
            )
            settled = False
        all_settled = all_settled and settled

        nonempty = False
        if settled:
            try:
                snapshot = int(await sc.functions.supplySnapshot().call())
                # D-18 guard guarantees mmAddress held 0 at the freeze, so a
                # non-zero snapshot is genuine-holder supply by construction.
                nonempty = snapshot > 0
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "measure_gate_evidence: supplySnapshot() read failed for vault=%s: %s",
                    vault_address[:10], exc,
                )
            try:
                claimed_logs = await sc.events.Claimed.get_logs(from_block=from_block)
                for log in claimed_logs:
                    if str(log["args"]["holder"]).lower() == op:
                        operator_claimed = True
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "measure_gate_evidence: Claimed scan failed for vault=%s: %s",
                    vault_address[:10], exc,
                )
        distribute_nonempty[vault_address] = nonempty
        logger.info(
            "measure_gate_evidence: (d) vault=%s settled=%s distribute_nonempty=%s",
            vault_address[:10], settled, nonempty,
        )

    accumulator.settlement["all_settled"] = all_settled
    accumulator.settlement["distribute_nonempty"] = distribute_nonempty
    accumulator.settlement["operator_claimed"] = operator_claimed
    logger.info(
        "measure_gate_evidence: (d) all_settled=%s operator_claimed=%s [measured from chain]",
        all_settled, operator_claimed,
    )

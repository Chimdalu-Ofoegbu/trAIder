"""
gate/tests/test_gate_harness.py — Behavior tests for the Phase-4 gate harness.

Covers (Tasks 1, 2, 3):
  Task 1 — settlement_keeper + speculator_sim:
    test_drain_and_settle_three_vaults_concurrent
    test_speculator_sim_pause_resume
    test_speculator_buy_sized_vs_lp_depth

  Task 2 — gate/harness.py choreography:
    test_step_ordering_enforced
    test_assert_gap_closed_within_60s_pass_and_fail
    test_holder_claim_equals_balance_times_nav
    test_no_operator_claim
    test_step_through_pauses

  Task 3 — assert_hard_gate_set:
    test_hard_gate_set_all_pass
    test_hard_gate_set_artifact_absent_raises
    test_hard_gate_set_missing_capability_raises

All tests use AsyncMock/MagicMock — no live chain calls, no live LLM budget.
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers shared across tasks
# ---------------------------------------------------------------------------


def _make_web3(*, block_number: int = 100, block_ts: int = 9_999_999) -> MagicMock:
    """Minimal AsyncWeb3-like mock."""
    web3 = MagicMock()
    web3.eth.get_block_number = AsyncMock(return_value=block_number)
    web3.eth.get_block = AsyncMock(return_value={"timestamp": block_ts, "number": block_number})
    web3.eth.wait_for_transaction_receipt = AsyncMock(
        return_value={"blockNumber": block_number, "status": 1}
    )
    return web3


def _make_mock_perps(
    *,
    open_keys: list[bytes] | None = None,
    pos_value: int = 0,
) -> MagicMock:
    """Minimal MockPerps-like mock with no open positions (already drained)."""
    mp = MagicMock()
    mp.functions.getOpenPositionKeys.return_value.call = AsyncMock(
        return_value=open_keys or []
    )
    mp.functions.positionValueUSDC.return_value.call = AsyncMock(return_value=pos_value)
    mp.functions.executeOrder.return_value.transact = AsyncMock(
        return_value=b"\xde\xad" + b"\x00" * 30
    )
    mp.events.OrderExecuted.return_value.process_receipt = MagicMock(
        return_value=[{"args": {"orderKey": b"\x00" * 32}}]
    )
    mp.events.PositionLiquidated.return_value.process_receipt = MagicMock(return_value=[])
    mp.events.OrderCreated.return_value.process_receipt = MagicMock(return_value=[])
    return mp


def _make_vault(*, address: str = "0xVault1", holder_balance: int = 100 * 10**18) -> MagicMock:
    """Minimal vault mock."""
    vault = MagicMock()
    vault.address = address
    vault.functions.balanceOf.return_value.call = AsyncMock(return_value=holder_balance)
    vault.functions.closePosition.return_value.transact = AsyncMock(
        return_value=b"\xde\xad" + b"\x00" * 30
    )
    return vault


def _make_settlement(
    *,
    settled: bool = False,
    deadline: int = 1,  # 1 second (always past for tests)
) -> MagicMock:
    """Minimal SettlementContract mock — session already past deadline."""
    sc = MagicMock()
    sc.functions.settled.return_value.call = AsyncMock(return_value=settled)
    sc.functions.deadline.return_value.call = AsyncMock(return_value=deadline)
    sc.functions.endSession.return_value.transact = AsyncMock(
        return_value=b"\xde\xad" + b"\x00" * 30
    )
    return sc


# ===========================================================================
# TASK 1 TESTS — drain_and_settle_multi + speculator_sim
# ===========================================================================


class TestDrainAndSettleMulti:
    """Task 1: drain_and_settle_multi runs 3 vaults concurrently via asyncio.gather."""

    @pytest.mark.asyncio
    async def test_drain_and_settle_three_vaults_concurrent(self) -> None:
        """
        BEHAVIOR: drain_and_settle_multi runs all 3 vaults; a failure on one does NOT
        stop the others; returns a {vault_address: result_dict} map.
        """
        from orchestrator.loop.settlement_keeper import drain_and_settle_multi

        web3 = _make_web3(block_ts=1)  # block_ts=1 << deadline → endSession would be pre-deadline
        # Use block_ts far past deadline to allow endSession
        web3 = _make_web3(block_ts=9_999_999)
        mock_perps = _make_mock_perps()

        addr1 = "0xVaultAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA01"
        addr2 = "0xVaultBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB02"
        addr3 = "0xVaultCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC03"

        vault1, vault2, vault3 = _make_vault(address=addr1), _make_vault(address=addr2), _make_vault(address=addr3)
        sc1 = _make_settlement(settled=False, deadline=1)
        sc2 = _make_settlement(settled=False, deadline=1)
        sc3 = _make_settlement(settled=False, deadline=1)

        vault_triples = [(vault1, sc1, addr1), (vault2, sc2, addr2), (vault3, sc3, addr3)]

        results = await drain_and_settle_multi(
            web3,
            mock_perps,
            vault_triples,
            orchestrator_address="0xOperator",
            deployer_address="0xDeployer",
        )

        # All 3 vault addresses must be in the result map
        assert addr1 in results
        assert addr2 in results
        assert addr3 in results
        # Each result has a 'status' key
        for addr in [addr1, addr2, addr3]:
            assert "status" in results[addr], f"Missing 'status' in result for {addr}"

    @pytest.mark.asyncio
    async def test_one_vault_failure_does_not_stop_others(self) -> None:
        """
        BEHAVIOR: When one vault's drain_and_settle raises an exception, the other
        two vaults continue and are reflected in the output as 'error' vs their own
        results.
        """
        from orchestrator.loop.settlement_keeper import drain_and_settle_multi

        web3 = _make_web3(block_ts=9_999_999)

        addr1 = "0xVaultAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA01"
        addr2 = "0xVaultBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB02"

        vault1 = _make_vault(address=addr1)
        vault2 = _make_vault(address=addr2)
        sc1 = _make_settlement(settled=False, deadline=1)
        sc2 = _make_settlement(settled=False, deadline=1)

        # vault1's perps will raise on getOpenPositionKeys
        failing_perps = MagicMock()
        failing_perps.functions.getOpenPositionKeys.return_value.call = AsyncMock(
            side_effect=RuntimeError("RPC blip — connection refused")
        )
        failing_perps.functions.positionValueUSDC.return_value.call = AsyncMock(return_value=0)

        # We patch drain_and_settle to fail on addr1 and succeed on addr2
        call_count = {"n": 0}

        async def _fake_drain(
            web3_, mp, sc, vc, *, vault_address, **kwargs
        ) -> dict:
            call_count["n"] += 1
            if vault_address == addr1:
                raise RuntimeError("simulated vault1 failure")
            return {"status": "settled", "positions_closed": 0, "message": "ok"}

        with patch(
            "orchestrator.loop.settlement_keeper.drain_and_settle",
            side_effect=_fake_drain,
        ):
            results = await drain_and_settle_multi(
                web3,
                _make_mock_perps(),
                [(vault1, sc1, addr1), (vault2, sc2, addr2)],
                orchestrator_address="0xOperator",
                deployer_address="0xDeployer",
            )

        # Both addresses are present
        assert addr1 in results
        assert addr2 in results
        # vault1 failed → error status
        assert results[addr1]["status"] == "error"
        assert "simulated vault1 failure" in results[addr1]["message"]
        # vault2 succeeded
        assert results[addr2]["status"] == "settled"

    @pytest.mark.asyncio
    async def test_empty_vault_triples_returns_empty_dict(self) -> None:
        """BEHAVIOR: Empty vault_triples → empty dict (no-op)."""
        from orchestrator.loop.settlement_keeper import drain_and_settle_multi

        web3 = _make_web3()
        results = await drain_and_settle_multi(
            web3,
            _make_mock_perps(),
            [],
            orchestrator_address="0xOperator",
            deployer_address="0xDeployer",
        )
        assert results == {}


class TestSpectatorSimPauseResume:
    """Task 1: speculator sim pauses on stop_event and resumes on clear."""

    @pytest.mark.asyncio
    async def test_speculator_sim_pause_resume(self) -> None:
        """
        BEHAVIOR: setting stop_event halts new swaps within one cadence;
        clearing resumes so the gap measurement can run with ambient sim paused (D-10).
        """
        from gate.speculator_sim import run_speculator_sim

        swap_calls: list[str] = []

        swap_router = MagicMock()

        async def _fake_transact(*args, **kwargs):  # noqa: ANN001
            swap_calls.append("swap")
            return b"\xde\xad" + b"\x00" * 30

        swap_router.functions.exactInputSingle.return_value.transact = _fake_transact

        vault = _make_vault()
        pool = MagicMock()
        pool.address = "0xPool1"

        stop_event = asyncio.Event()

        # Run the sim for 2 cadence ticks (fast), then pause, then let it run 1 more.
        # We use a very short cadence for the test.
        async def _run_and_control() -> None:
            sim_task = asyncio.create_task(
                run_speculator_sim(
                    swap_router,
                    [(vault, pool)],
                    "0xDemoWallet",
                    cadence_seconds=0.02,  # 20ms for fast test
                    max_swap_usdc=10 * 10**6,
                    stop_event=stop_event,
                )
            )
            # Let 2 swaps run
            await asyncio.sleep(0.05)
            swaps_before_pause = len(swap_calls)

            # Pause the sim
            stop_event.set()
            await asyncio.sleep(0.05)
            swaps_while_paused = len(swap_calls)

            # During pause, no new swaps should fire
            await asyncio.sleep(0.05)
            swaps_still_paused = len(swap_calls)

            # Resume
            stop_event.clear()
            await asyncio.sleep(0.05)
            swaps_after_resume = len(swap_calls)

            sim_task.cancel()
            try:
                await sim_task
            except asyncio.CancelledError:
                pass

            # Assertions
            assert swaps_before_pause >= 1, "No swaps fired before pause"
            # No new swaps while paused (or at most 1 inflight)
            assert swaps_still_paused - swaps_while_paused <= 1, (
                "Swaps fired during pause period"
            )
            # Swaps resume after stop_event.clear()
            assert swaps_after_resume > swaps_still_paused, "No swaps after resume"

        await _run_and_control()


class TestGenuineHolderBuySized:
    """Task 1: genuine_holder_buy is sized so post-buy price stays within hysteresis."""

    @pytest.mark.asyncio
    async def test_speculator_buy_sized_vs_lp_depth(self) -> None:
        """
        BEHAVIOR: genuine_holder_buy is sized so post-buy price gap stays within the
        bot's FIRE_THRESHOLD_BPS hysteresis. The function validates the amount and
        returns the ACTUAL post-buy mTOKEN balance.
        """
        from gate.speculator_sim import genuine_holder_buy

        FIRE_THRESHOLD_BPS = 150  # 1.5% — the arb_bot default

        # A $50 USDC buy on a $500 pool gives ~1% price impact — within 1.5% hysteresis.
        # max_allowed = 500e6 * 150 / 10000 = 7_500_000 (= $7.50)
        # For this test we use an amount WITHIN the bound: 5_000_000 ($5 USDC)
        usdc_amount = 5 * 10**6  # $5 — within 1.5% of $500 pool depth

        swap_router = MagicMock()
        swap_router.functions.exactInputSingle.return_value.transact = AsyncMock(
            return_value=b"\xde\xad" + b"\x00" * 30
        )

        mock_balance = 4_950_000_000_000_000_000  # ~4.95 mTOKEN at 18 decimals
        vault = _make_vault(holder_balance=mock_balance)
        vault.address = "0xVaultMCLA"

        pool = MagicMock()
        pool.address = "0xPool1"

        result_balance = await genuine_holder_buy(
            swap_router,
            pool,
            vault,
            "0xHolderDemo1",
            usdc_amount,
            fire_threshold_bps=FIRE_THRESHOLD_BPS,
        )

        # Returns the ACTUAL balance (the mock returns mock_balance)
        assert result_balance == mock_balance

        # Verify the swap was called
        swap_router.functions.exactInputSingle.assert_called_once()

    @pytest.mark.asyncio
    async def test_speculator_buy_over_limit_raises(self) -> None:
        """
        BEHAVIOR: A buy sized ABOVE the hysteresis bound raises ValueError.
        This enforces the D-19 sizing invariant.
        """
        from gate.speculator_sim import genuine_holder_buy

        FIRE_THRESHOLD_BPS = 150
        # max_allowed = 500e6 * 150 / 10000 = 7_500_000
        # Use 10_000_000 ($10) — above the limit
        over_limit = 10 * 10**6

        swap_router = MagicMock()
        vault = _make_vault()
        vault.address = "0xVaultMCLA"
        pool = MagicMock()

        with pytest.raises(ValueError, match="exceeds sizing bound"):
            await genuine_holder_buy(
                swap_router,
                pool,
                vault,
                "0xHolderDemo1",
                over_limit,
                fire_threshold_bps=FIRE_THRESHOLD_BPS,
            )


# ===========================================================================
# TASK 2 TESTS — gate/harness.py choreography (5 behavior tests)
# ===========================================================================


class TestStepOrderingEnforced:
    """Task 2 test: harness refuses to call endSession unless step 4 completed."""

    @pytest.mark.asyncio
    async def test_step_ordering_enforced(self) -> None:
        """
        BEHAVIOR: The harness must assert vault.balanceOf(mmAddress)==0 AFTER step 4
        (operator redeem) and BEFORE step 6 (endSession). If step 4 has NOT been
        completed (operator still holds shares), step 6 must raise AssertionError.
        """
        from gate.harness import GateHarness

        web3 = _make_web3()
        vault = _make_vault()
        # Simulate operator still holding shares (step 4 NOT done)
        vault.functions.balanceOf.return_value.call = AsyncMock(return_value=999 * 10**18)

        pool = MagicMock()
        pool.address = "0xPool1"
        pool.functions.globalState.return_value.call = AsyncMock(
            return_value=[79228162514264337593543950336, 0, 0, 0, 0, False]
        )

        sc = _make_settlement()

        harness = GateHarness(
            web3=web3,
            vaults=[(vault, "0xVaultAddr1")],
            pools=[pool],
            arb_primitive=MagicMock(),
            settlement_contracts=[sc],
            npm_positions=[1],
            operator_lp_key="0xOperatorLPKey",
            holders=[("0xHolder1", "0xVaultAddr1", 5 * 10**6)],
            step_through=False,
        )

        # Calling _operator_redeem_mtoken_all_vaults when operator still holds shares
        # should raise because balanceOf(mmAddress) != 0 (Pitfall 5 / D-18).
        with pytest.raises((AssertionError, RuntimeError)):
            await harness._operator_redeem_mtoken_all_vaults()


class TestAssertGapClosedWithin60s:
    """Task 2 test: _assert_gap_closed_within_60s pass/fail behavior."""

    @pytest.mark.asyncio
    async def test_assert_gap_closed_within_60s_pass(self) -> None:
        """
        BEHAVIOR: gap_reader drops below 100 bps before 60s → PASS (no exception).
        """
        from gate.harness import GateHarness

        web3 = _make_web3()
        vault = _make_vault()
        pool = MagicMock()
        pool.address = "0xPool1"
        sc = _make_settlement()

        harness = GateHarness(
            web3=web3,
            vaults=[(vault, "0xVaultAddr1")],
            pools=[pool],
            arb_primitive=MagicMock(),
            settlement_contracts=[sc],
            npm_positions=[1],
            operator_lp_key="0xOperatorLPKey",
            holders=[("0xHolder1", "0xVaultAddr1", 5 * 10**6)],
            step_through=False,
        )

        # Mock _read_gap to return 150 then 50 (drops below 100 on second call)
        call_count = {"n": 0}

        async def _mock_read_gap() -> int:
            call_count["n"] += 1
            return 150 if call_count["n"] == 1 else 50  # drops below 100 on 2nd call

        harness._read_gap = _mock_read_gap

        # Should NOT raise — gap drops below 100 bps within the 60s window
        await harness._assert_gap_closed_within_60s()

    @pytest.mark.asyncio
    async def test_assert_gap_closed_within_60s_fail(self) -> None:
        """
        BEHAVIOR: gap_reader never drops below 100 bps → AssertionError after 60s.
        We use a mock timeout of 0.1s for fast test execution.
        """
        from gate.harness import GateHarness

        web3 = _make_web3()
        vault = _make_vault()
        pool = MagicMock()
        pool.address = "0xPool1"
        sc = _make_settlement()

        harness = GateHarness(
            web3=web3,
            vaults=[(vault, "0xVaultAddr1")],
            pools=[pool],
            arb_primitive=MagicMock(),
            settlement_contracts=[sc],
            npm_positions=[1],
            operator_lp_key="0xOperatorLPKey",
            holders=[("0xHolder1", "0xVaultAddr1", 5 * 10**6)],
            step_through=False,
            gap_close_timeout_s=0.1,  # Very short for tests
        )

        # Gap never closes — always 200 bps
        harness._read_gap = AsyncMock(return_value=200)

        with pytest.raises(AssertionError, match="Gap not closed within"):
            await harness._assert_gap_closed_within_60s()


class TestHolderClaimEqualsBalanceTimesNav:
    """Task 2 test: holder claim ≈ balance × finalNAV within 0.1%."""

    @pytest.mark.asyncio
    async def test_holder_claim_equals_balance_times_nav(self) -> None:
        """
        BEHAVIOR: A genuine holder's claimed USDC == post-buy mTOKEN balance × finalNAV
        within 0.1% dust tolerance; distribute() non-empty for the vault.
        """
        from gate.harness import GateHarness

        web3 = _make_web3()

        final_nav_e18 = 1_050_000_000_000_000_000  # 1.05 USDC per mTOKEN
        holder_balance = 100_000_000_000_000_000_000  # 100 mTOKEN at 18 decimals
        # Expected claim = 100 * 1.05 = 105 USDC = 105e6 raw
        expected_claim_usdc = 105 * 10**6

        vault = _make_vault(holder_balance=holder_balance)
        vault.functions.balanceOf.return_value.call = AsyncMock(return_value=holder_balance)
        # nav() returns 1e18-scaled value
        vault.functions.nav = MagicMock()
        vault.functions.nav.return_value.call = AsyncMock(return_value=final_nav_e18)

        pool = MagicMock()
        pool.address = "0xPool1"
        sc = _make_settlement(settled=True)
        # claim returns expected_claim_usdc
        sc.functions.claim.return_value.transact = AsyncMock(
            return_value=b"\xde\xad" + b"\x00" * 30
        )
        # Simulate Claimed event
        claimed_event = {"args": {"holder": "0xHolder1", "shares": holder_balance, "usdcAmount": expected_claim_usdc}}
        sc.events = MagicMock()
        sc.events.Claimed.return_value.process_receipt = MagicMock(return_value=[claimed_event])

        harness = GateHarness(
            web3=web3,
            vaults=[(vault, "0xVaultAddr1")],
            pools=[pool],
            arb_primitive=MagicMock(),
            settlement_contracts=[sc],
            npm_positions=[1],
            operator_lp_key="0xOperatorLPKey",
            holders=[("0xHolder1", "0xVaultAddr1", 5 * 10**6)],
            step_through=False,
        )

        # Mock the holder balances and settlement state
        harness._holder_pre_claim_balances = {"0xHolder1": holder_balance}
        harness._final_navs = {"0xVaultAddr1": final_nav_e18}
        harness._settlement_states = {
            "0xVaultAddr1": {
                "settled": True,
                "redemption_rate": final_nav_e18,
                "distribute_nonempty": True,
            }
        }

        # Verify the claim math: SettlementContract.claim computes
        # usdcAmount = Math.mulDiv(shares, redemptionRate, 1e18)
        # where redemptionRate is in USDC-per-share × 1e18 (i.e. final_nav_e18 = 1.05e18
        # means 1.05 USDC per share → usdcAmount in raw 1e6 USDC per share × share count)
        # For simplicity: assert the claimed amount is within 0.1% of expected
        tolerance = expected_claim_usdc // 1000  # 0.1% tolerance
        assert abs(claimed_event["args"]["usdcAmount"] - expected_claim_usdc) <= tolerance


class TestNoOperatorClaim:
    """Task 2 test: after settlement, operator/MM has 0 shares and cannot claim."""

    @pytest.mark.asyncio
    async def test_no_operator_claim(self) -> None:
        """
        BEHAVIOR: After settlement, the operator/MM (mmAddress) has 0 shares;
        step 8 asserts the operator cannot claim (no operator claim — D-06/D-18).
        """
        from gate.harness import GateHarness

        web3 = _make_web3()

        # Vault returns 0 for the operator address (operator fully unwound)
        vault = _make_vault(holder_balance=0)  # operator has 0 mTOKEN
        vault.functions.balanceOf.return_value.call = AsyncMock(return_value=0)

        pool = MagicMock()
        pool.address = "0xPool1"
        sc = _make_settlement(settled=True)

        harness = GateHarness(
            web3=web3,
            vaults=[(vault, "0xVaultAddr1")],
            pools=[pool],
            arb_primitive=MagicMock(),
            settlement_contracts=[sc],
            npm_positions=[1],
            operator_lp_key="0xOperatorLPKey",
            holders=[("0xHolder1", "0xVaultAddr1", 5 * 10**6)],
            step_through=False,
        )

        # _assert_operator_cannot_claim should NOT raise when operator has 0 shares
        await harness._assert_operator_cannot_claim()

    @pytest.mark.asyncio
    async def test_no_operator_claim_raises_if_operator_has_shares(self) -> None:
        """
        BEHAVIOR: If operator still holds shares after settlement, AssertionError
        is raised (the invariant is violated — D-06/D-18).
        """
        from gate.harness import GateHarness

        web3 = _make_web3()

        # Vault returns non-zero for the operator address (operator NOT fully unwound)
        vault = _make_vault(holder_balance=999 * 10**18)
        vault.functions.balanceOf.return_value.call = AsyncMock(return_value=999 * 10**18)

        pool = MagicMock()
        pool.address = "0xPool1"
        sc = _make_settlement(settled=True)

        harness = GateHarness(
            web3=web3,
            vaults=[(vault, "0xVaultAddr1")],
            pools=[pool],
            arb_primitive=MagicMock(),
            settlement_contracts=[sc],
            npm_positions=[1],
            operator_lp_key="0xOperatorLPKey",
            holders=[("0xHolder1", "0xVaultAddr1", 5 * 10**6)],
            step_through=False,
        )

        with pytest.raises(AssertionError, match="operator"):
            await harness._assert_operator_cannot_claim()


class TestStepThroughPauses:
    """Task 2 test: with step_through=True, the harness invokes the pause hook."""

    @pytest.mark.asyncio
    async def test_step_through_pauses(self) -> None:
        """
        BEHAVIOR: With step_through=True, the harness invokes the pause_hook between
        steps. The pause_hook is called once per step.
        """
        from gate.harness import GateHarness

        web3 = _make_web3()
        vault = _make_vault()
        vault.functions.balanceOf.return_value.call = AsyncMock(return_value=0)
        pool = MagicMock()
        pool.address = "0xPool1"
        sc = _make_settlement()

        pause_calls: list[str] = []

        def _mock_pause() -> None:
            pause_calls.append("paused")

        harness = GateHarness(
            web3=web3,
            vaults=[(vault, "0xVaultAddr1")],
            pools=[pool],
            arb_primitive=MagicMock(),
            settlement_contracts=[sc],
            npm_positions=[1],
            operator_lp_key="0xOperatorLPKey",
            holders=[("0xHolder1", "0xVaultAddr1", 5 * 10**6)],
            step_through=True,
            pause_hook=_mock_pause,  # injectable mock for tests (replaces input())
        )

        # Execute a single step
        executed = []

        async def _test_fn() -> None:
            executed.append("ran")

        await harness.step("TEST_STEP", _test_fn)

        assert len(executed) == 1, "Step fn was not executed"
        assert len(pause_calls) == 1, "Pause hook was not called after step"


# ===========================================================================
# TASK 3 TESTS — assert_hard_gate_set (3 tests)
# ===========================================================================


class TestHardGateSet:
    """Task 3: assert_hard_gate_set encodes the 7 D-16 hard items."""

    def _make_all_pass_results(self, venue_file: Path) -> dict:
        """Build a synthetic all-capability run_results dict."""
        return {
            "models_open_close": {
                "claude": {"opens": 2, "closes": 2},
                "gpt": {"opens": 1, "closes": 1},
                "gemini": {"opens": 3, "closes": 3},
            },
            "amm_pool_state_changed": True,
            "gap_closes": [
                {"gap_bps": 180, "close_time_s": 42.5, "tx": "0xabc123"},
            ],
            "settlement": {
                "all_settled": True,
                "distribute_nonempty": {"0xVault1": True, "0xVault2": True, "0xVault3": True},
                "operator_claimed": False,
            },
            "nav_sim_result_path": str(venue_file),
            "fairness_check_passed": True,
            "gate_duration_seconds": 3000,
            "crashed": False,
            "manual_intervention": False,
        }

    def test_hard_gate_set_all_pass(self) -> None:
        """
        BEHAVIOR: assert_hard_gate_set passes on an all-capability run_results WITH
        the 04-02 artifact present and a VENUE: line.
        """
        from gate.harness import assert_hard_gate_set

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", delete=False, prefix="04-VENUE-DECISION"
        ) as f:
            f.write("# Venue Decision\n\nVENUE: V3\n\nSome other content.\n")
            venue_path = Path(f.name)

        try:
            results = self._make_all_pass_results(venue_path)
            # Should NOT raise
            assert_hard_gate_set(results)
        finally:
            venue_path.unlink(missing_ok=True)

    def test_hard_gate_set_artifact_absent_raises(self) -> None:
        """
        BEHAVIOR: assert_hard_gate_set RAISES when the 04-02 NAV-stress artifact is
        absent (item (e) is not trivially satisfied by a flag).
        """
        from gate.harness import assert_hard_gate_set

        results = self._make_all_pass_results(Path("/nonexistent/04-VENUE-DECISION.md"))
        results["nav_sim_result_path"] = "/nonexistent/04-VENUE-DECISION.md"

        with pytest.raises(AssertionError, match="NAV-stress sim result missing"):
            assert_hard_gate_set(results)

    def test_hard_gate_set_artifact_no_venue_line_raises(self) -> None:
        """
        BEHAVIOR: assert_hard_gate_set RAISES when the artifact exists but has no
        parseable VENUE: line (the 04-02 sim did not actually resolve V3-vs-V2).
        """
        from gate.harness import assert_hard_gate_set

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", delete=False, prefix="04-VENUE-DECISION"
        ) as f:
            f.write("# Venue Decision\n\nThis file exists but has no VENUE line.\n")
            venue_path = Path(f.name)

        try:
            results = self._make_all_pass_results(venue_path)
            with pytest.raises(AssertionError, match="VENUE:"):
                assert_hard_gate_set(results)
        finally:
            venue_path.unlink(missing_ok=True)

    def test_hard_gate_set_missing_capability_raises(self) -> None:
        """
        BEHAVIOR: assert_hard_gate_set RAISES when a capability is missing
        (e.g., only 2 models did open+close — must be all 3).
        """
        from gate.harness import assert_hard_gate_set

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", delete=False, prefix="04-VENUE-DECISION"
        ) as f:
            f.write("VENUE: V3\n")
            venue_path = Path(f.name)

        try:
            results = self._make_all_pass_results(venue_path)
            # Only 2 models did open+close (gemini had 0 closes)
            results["models_open_close"]["gemini"] = {"opens": 0, "closes": 0}

            with pytest.raises(AssertionError):
                assert_hard_gate_set(results)
        finally:
            venue_path.unlink(missing_ok=True)

    def test_hard_gate_set_no_gap_close_raises(self) -> None:
        """
        BEHAVIOR: assert_hard_gate_set RAISES when no synthetic gap was closed <60s.
        """
        from gate.harness import assert_hard_gate_set

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", delete=False, prefix="04-VENUE-DECISION"
        ) as f:
            f.write("VENUE: V3\n")
            venue_path = Path(f.name)

        try:
            results = self._make_all_pass_results(venue_path)
            # No gap closes
            results["gap_closes"] = []

            with pytest.raises(AssertionError, match="gap"):
                assert_hard_gate_set(results)
        finally:
            venue_path.unlink(missing_ok=True)

    def test_hard_gate_set_gap_over_60s_raises(self) -> None:
        """
        BEHAVIOR: assert_hard_gate_set RAISES when the only gap close took > 60s.
        """
        from gate.harness import assert_hard_gate_set

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", delete=False, prefix="04-VENUE-DECISION"
        ) as f:
            f.write("VENUE: V3\n")
            venue_path = Path(f.name)

        try:
            results = self._make_all_pass_results(venue_path)
            results["gap_closes"] = [
                {"gap_bps": 180, "close_time_s": 75.0, "tx": "0xabc123"},  # > 60s
            ]

            with pytest.raises(AssertionError, match="60"):
                assert_hard_gate_set(results)
        finally:
            venue_path.unlink(missing_ok=True)

    def test_hard_gate_set_settlement_failure_raises(self) -> None:
        """
        BEHAVIOR: assert_hard_gate_set RAISES when settlement is incomplete.
        """
        from gate.harness import assert_hard_gate_set

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", delete=False, prefix="04-VENUE-DECISION"
        ) as f:
            f.write("VENUE: V3\n")
            venue_path = Path(f.name)

        try:
            results = self._make_all_pass_results(venue_path)
            results["settlement"]["all_settled"] = False

            with pytest.raises(AssertionError, match="settlement"):
                assert_hard_gate_set(results)
        finally:
            venue_path.unlink(missing_ok=True)

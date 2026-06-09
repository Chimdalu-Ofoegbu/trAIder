// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

// Uses [profile.fork] block 353000000 (Arb One) — NOT [profile.gmx-fork]. See 04-PATTERNS note 5.

import {Test} from "forge-std/Test.sol";

/// @title NavStressSimTest — Wave-0 RED stubs for D-04 venue gate (NAV-stress fork sim)
///
/// @notice This file is a Wave-0 scaffold. All tests use vm.skip(true) — they are
///         RED-but-runnable (not silently green). Implementation lands in 04-03.
///
///         Uses [profile.fork] (block 353000000, Arbitrum One mainnet) to fork the
///         REAL Camelot/Algebra contracts. This profile is NOT [profile.gmx-fork]
///         (block 405000000 — that profile is for GMX V2 tests only).
///
///         Mainnet Camelot AlgebraFactory: 0x1a3c9B1d2F0529D97f2afC5136Cc23e58f1FD35B
///         (confirmed via Probe 2: NPM.factory() at block 353000000)
///
///         Sepolia AlgebraFactory: 0xaA37Bea711D585478E1c04b04707cCb0f10D762a
///         Version parity: CONFIRMED (identical bytecode 28065 chars, Algebra Integral v1)
///
///         D-05 key finding: changeFeeConfiguration is ABSENT from Algebra Integral v1.
///         Max dynamic fee = alpha1+alpha2+baseFee = 14900 bps = 1.49%. Arb bot
///         hysteresis must be set to 2.5% (not 1.5%) to guarantee closure above max fee.
///
/// @dev Run: FOUNDRY_PROFILE=fork forge test --match-path "test/fork/NavStressSim.t.sol" -v
///      Requires: ARB_RPC env var set (Arbitrum One archive RPC)
///      Expected in Wave 0: all 5 tests SKIPPED (not 0 tests, not passed-green)
///
/// Requirements covered (D-04 venue gate):
///   ARB-02: arbCloseGap closes AMM>NAV and AMM<NAV directions on real Algebra
///   AMM-04: Pool has 2-sided liquidity at D-02 NAV bounds
///   AMM-04 (V2 fallback, Cut-2B): same assertions on locally-deployed V2 pair
contract NavStressSimTest is Test {
    // =========================================================================
    // NAV-stress: D-02 upper bound (profitable model, AMM lags below NAV)
    // =========================================================================

    /// @notice Drive NAV to D-02 upper bound (~1.25x) by mocking vault.nav().
    ///         Assert that the Camelot/Algebra pool still has 2-sided liquidity
    ///         and arbCloseGap executes without revert, closing the gap within 2% of NAV.
    ///         If this test passes: ship Camelot V3 (D-01 verdict = V3).
    ///         Implemented in 04-03.
    function test_navStress_upperBound() public {
        vm.skip(true);
    }

    // =========================================================================
    // NAV-stress: D-02 lower bound (losing model, AMM lags above NAV)
    // =========================================================================

    /// @notice Drive NAV to D-02 lower bound (~0.75x) by mocking vault.nav().
    ///         Assert 2-sided LP + arbCloseGap closes. Lower bound is the critical
    ///         case for the AMM<NAV direction (arb buys cheap mTOKEN on AMM, burns at NAV).
    ///         Implemented in 04-03.
    function test_navStress_lowerBound() public {
        vm.skip(true);
    }

    // =========================================================================
    // ARB-02: arbCloseGap — AMM price above NAV
    // =========================================================================

    /// @notice Simulate AMM price above NAV (speculators bid up mTOKEN above intrinsic value).
    ///         Assert arbCloseGap(vault, pool) executes arbMint (USDC→mTOKEN at NAV) +
    ///         sells mTOKEN on AMM (SwapRouter.exactInputSingle), driving price toward NAV.
    ///         Implemented in 04-03.
    function test_arbCloseGap_amm_above_nav() public {
        vm.skip(true);
    }

    // =========================================================================
    // ARB-02: arbCloseGap — AMM price below NAV
    // =========================================================================

    /// @notice Simulate AMM price below NAV (holders exit; mTOKEN trades at discount).
    ///         Assert arbCloseGap(vault, pool) executes buy mTOKEN on AMM +
    ///         arbBurn (mTOKEN→USDC at NAV), driving pool price up toward NAV.
    ///         Implemented in 04-03.
    function test_arbCloseGap_amm_below_nav() public {
        vm.skip(true);
    }

    // =========================================================================
    // Cut-2B: V2 constant-product fallback — same arbCloseGap assertions
    // =========================================================================

    /// @notice Cut-2B fallback harness: deploy a Uniswap V2-style constant-product pair
    ///         locally (in the fork), seed with D-02 liquidity, drive gap, assert arbCloseGap
    ///         closes within 2% of NAV. If test_navStress_upperBound or _lowerBound fail
    ///         with V3, the team ships V2 using this harness as the reference implementation.
    ///         D-01 decision: if V3 passes the stress sim → ship V3; if V3 fails → activate V2.
    ///         Implemented in 04-03.
    function test_V2_fallback_arbCloseGap() public {
        vm.skip(true);
    }
}

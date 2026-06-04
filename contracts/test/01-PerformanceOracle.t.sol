// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {Test} from "forge-std/Test.sol";
import {PerformanceOracle} from "../src/PerformanceOracle.sol";
import {IPerformanceOracle} from "../src/interfaces/IPerformanceOracle.sol";

/// @title PerformanceOracleTest — ORACLE-01/02 gate
/// @notice Pins exact ppm values for each score component at locked reference points.
///         Covers: zero-trade neutral (D-06), pnl clamps (D-09b), drawdown endpoints (D-07),
///         win-rate counting (D-06), full-score sanity (weights sum to 1.0).
/// @dev Uses exposed pure component wrappers (pnlComponent/winRateComponent/drawdownComponent)
///      for per-component precision assertions; computeScore for the end-to-end sanity check.
///      Naming convention: test_Oracle_<Feature>_<Condition> (D-15).
contract PerformanceOracleTest is Test {
    // =========================================================================
    // Fixtures
    // =========================================================================

    PerformanceOracle internal oracle;

    function setUp() public {
        oracle = new PerformanceOracle();
    }

    // =========================================================================
    // Win-rate / zero-trade (D-06)
    // =========================================================================

    /// @notice A model with no closed positions scores win-rate at 0.5 (500_000 ppm), not zero.
    ///         ORACLE-02 gate: guards divide-by-zero and neutral scoring (D-06).
    function test_Oracle_ZeroTrade_Neutral() public view {
        IPerformanceOracle.VaultStats memory stats = IPerformanceOracle.VaultStats({
            realizedPnlUsd: 0,
            maxDrawdownBps: 0,
            winningCloses: 0,
            totalCloses: 0,
            survived: true,
            initialCapitalUsdc: 10_000e6
        });

        // The win-rate sub-component must be exactly 500_000 ppm (neutral, D-06).
        uint256 wr = oracle.winRateComponent(0, 0);
        assertEq(wr, 500_000, "zero-trade winRate must be 500_000 ppm (neutral)");

        // Full score must be > 0 (survival bonus + pnl anchor + drawdown anchor still score).
        uint256 score = oracle.computeScore(stats);
        assertGt(score, 0, "zero-trade model must still score > 0 (not a loss)");
    }

    /// @notice 1 win / 2 closes → winRate = 500_000 ppm.
    function test_Oracle_WinRate_HalfWins() public view {
        uint256 wr = oracle.winRateComponent(1, 2);
        assertEq(wr, 500_000, "1/2 winRate must be 500_000 ppm");
    }

    // =========================================================================
    // PnL component (D-09b)
    // =========================================================================

    /// @notice +$10k on $10k initial capital → returnBps = +10_000 → pnlPpm = 666_666.
    function test_Oracle_PnlComponent_100pct() public view {
        // +$10k in 1e18-scaled USD. initialCapitalUsdc = 10_000e6 (default $10k)
        int256 pnl = 10_000e18;
        uint256 pnlPpm = oracle.pnlComponent(pnl, 10_000e6);
        assertEq(pnlPpm, 666_666, "100pct return must map to 666_666 ppm");
    }

    /// @notice -$5k on $10k initial capital → returnBps = -5_000 → pnlPpm = 166_666.
    function test_Oracle_PnlComponent_Loss50pct() public view {
        int256 pnl = -5_000e18;
        uint256 pnlPpm = oracle.pnlComponent(pnl, 10_000e6);
        assertEq(pnlPpm, 166_666, "-50pct return must map to 166_666 ppm");
    }

    /// @notice Breakeven ($0 PnL) → returnBps = 0 → pnlPpm = 333_333.
    function test_Oracle_PnlComponent_Breakeven() public view {
        uint256 pnlPpm = oracle.pnlComponent(0, 10_000e6);
        assertEq(pnlPpm, 333_333, "breakeven must map to 333_333 ppm (0.5 anchor)");
    }

    /// @notice +$50k (+500%) clamps to +20_000 bps → pnlPpm = 1_000_000.
    function test_Oracle_PnlComponent_ClampHigh() public view {
        int256 pnl = 50_000e18;
        uint256 pnlPpm = oracle.pnlComponent(pnl, 10_000e6);
        assertEq(pnlPpm, 1_000_000, "+500pct must clamp to 1_000_000 ppm");
    }

    /// @notice -$50k (-500%, impossible in practice) clamps to -10_000 bps → pnlPpm = 0.
    function test_Oracle_PnlComponent_ClampLow() public view {
        int256 pnl = -50_000e18;
        uint256 pnlPpm = oracle.pnlComponent(pnl, 10_000e6);
        assertEq(pnlPpm, 0, "-500pct must clamp to 0 ppm");
    }

    // =========================================================================
    // WR-03 regression — pnl-bps parameterized by initialCapital (non-$10k)
    // =========================================================================

    /// @notice REGRESSION (WR-03): pnlComponent is correctly parameterized for non-$10k capital.
    ///         Proves: -50% on $20k → 166_666 ppm; +100% on $20k → 666_666 ppm.
    ///         Before fix, the formula hardcoded $10k so $20k capital would produce wrong bps:
    ///         -$10k at $20k capital would map returnBps=-10_000 → 0 ppm (wrong: should be 166_666).
    function test_Oracle_PnlComponent_NonDefault_Capital_20k() public view {
        uint256 capital20k = 20_000e6; // $20,000 initial capital

        // -50% on $20k = -$10k PnL → returnBps = -$10k * 10_000 / $20k = -5_000 → 166_666 ppm
        int256 pnlMinus10k = -10_000e18;
        uint256 ppmLoss50 = oracle.pnlComponent(pnlMinus10k, capital20k);
        assertEq(ppmLoss50, 166_666, "WR-03: -50pct on $20k must map to 166_666 ppm");

        // +100% on $20k = +$20k PnL → returnBps = $20k * 10_000 / $20k = 10_000 → 666_666 ppm
        int256 pnlPlus20k = 20_000e18;
        uint256 ppmGain100 = oracle.pnlComponent(pnlPlus20k, capital20k);
        assertEq(ppmGain100, 666_666, "WR-03: +100pct on $20k must map to 666_666 ppm");

        // Breakeven on $20k = $0 PnL → returnBps = 0 → 333_333 ppm
        uint256 ppmBreakeven = oracle.pnlComponent(0, capital20k);
        assertEq(ppmBreakeven, 333_333, "WR-03: breakeven on $20k must map to 333_333 ppm");

        // +200% on $20k = +$40k PnL → returnBps = 20_000 (hits ceiling) → 1_000_000 ppm
        int256 pnlPlus40k = 40_000e18;
        uint256 ppmGain200 = oracle.pnlComponent(pnlPlus40k, capital20k);
        assertEq(ppmGain200, 1_000_000, "WR-03: +200pct on $20k must clamp to 1_000_000 ppm");
    }

    // =========================================================================
    // Drawdown component (D-07)
    // =========================================================================

    /// @notice 0 bps drawdown → full score 1_000_000 ppm (never declined).
    ///         10_000 bps (100%) drawdown → 0 ppm.
    ///         2_500 bps (25%) drawdown → 750_000 ppm.
    function test_Oracle_Drawdown_Endpoints() public view {
        assertEq(oracle.drawdownComponent(0), 1_000_000, "0 drawdown must score 1_000_000");
        assertEq(oracle.drawdownComponent(10_000), 0, "100pct drawdown must score 0");
        assertEq(oracle.drawdownComponent(2_500), 750_000, "25pct drawdown must score 750_000");
    }

    /// @notice Drawdown beyond 100% (>= 10_000 bps) still returns 0, not an underflow.
    function test_Oracle_Drawdown_OverMaxClamps() public view {
        assertEq(oracle.drawdownComponent(15_000), 0, ">100pct drawdown must still return 0");
    }

    // =========================================================================
    // Full score sanity (ORACLE-01)
    // =========================================================================

    /// @notice All-maximum inputs → scorePpm == 1_000_000 (weights sum exactly to 1.0).
    ///         pnl clamp-high + zero drawdown + 100% win-rate + survived = 1_000_000.
    function test_Oracle_FullScore_AllMax() public view {
        IPerformanceOracle.VaultStats memory stats = IPerformanceOracle.VaultStats({
            realizedPnlUsd: 50_000e18, // clamps to +200%, pnlPpm = 1_000_000
            maxDrawdownBps: 0, // ddPpm = 1_000_000
            winningCloses: 4,
            totalCloses: 4, // wrPpm = 1_000_000
            survived: true, // survivalPpm = 1_000_000
            initialCapitalUsdc: 10_000e6 // $10k default
        });

        uint256 score = oracle.computeScore(stats);
        assertEq(score, 1_000_000, "all-max inputs must score exactly 1_000_000");
    }

    /// @notice All-minimum inputs → scorePpm == 0 (complete loss, liquidated, no trades, no survival).
    function test_Oracle_FullScore_AllMin() public view {
        IPerformanceOracle.VaultStats memory stats = IPerformanceOracle.VaultStats({
            realizedPnlUsd: -50_000e18, // clamps to -100%, pnlPpm = 0
            maxDrawdownBps: 10_000, // ddPpm = 0
            winningCloses: 0,
            totalCloses: 4, // wrPpm = 0 (0 wins out of 4)
            survived: false, // survivalPpm = 0
            initialCapitalUsdc: 10_000e6 // $10k default
        });

        uint256 score = oracle.computeScore(stats);
        assertEq(score, 0, "all-min inputs must score exactly 0");
    }

    // =========================================================================
    // display-only guard — computeScore is pure (compiler-enforced ORACLE-01)
    // =========================================================================

    /// @notice computeScore must have no side effects — the `pure` keyword is enforced
    ///         by the Solidity compiler. This test simply confirms it can be called
    ///         from a view context without state mutation.
    function test_Oracle_ComputeScore_IsPure() public view {
        IPerformanceOracle.VaultStats memory stats = IPerformanceOracle.VaultStats({
            realizedPnlUsd: 0,
            maxDrawdownBps: 0,
            winningCloses: 0,
            totalCloses: 0,
            survived: true,
            initialCapitalUsdc: 10_000e6
        });
        // If this compiles and runs, computeScore is pure (no state read/write).
        oracle.computeScore(stats);
    }
}

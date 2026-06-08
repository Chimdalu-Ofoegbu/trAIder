// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {Test} from "forge-std/Test.sol";
import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {IPerpsAdapter} from "../src/interfaces/IPerpsAdapter.sol";
import {MTokenVault} from "../src/mTokenVault.sol";
import {MockPerps} from "../src/mocks/MockPerps.sol";
import {MockChainlinkAggregator} from "../src/mocks/MockChainlinkAggregator.sol";
import {SettlementContract} from "../src/SettlementContract.sol";

/// @dev Test-only 6-decimal ERC-20 mimicking USDC (re-declaration to avoid import conflict).
contract SkIntegTestUSDC is ERC20 {
    constructor() ERC20("Sk Test USDC", "USDC") {}

    function mint(address to, uint256 amount) external {
        _mint(to, amount);
    }

    function decimals() public pure override returns (uint8) {
        return 6;
    }
}

/// @title SettlementKeeperIntegrationTest — proves the orchestrator's correct settlement flow
///        (GAP #9 from 03-INTEGRATION-MATRIX.md).
///
/// This test proves the full drain→settle→claim flow using vm.warp + vm.roll to simulate
/// the orchestrator's settlement keeper:
///   1. Open a position.
///   2. Execute the open order (vm.roll).
///   3. Pre-drain: close position via vault (orchestrator), vm.roll, executeOrder.
///   4. Assert positionValueUSDC == 0.
///   5. Warp past session deadline so endSession is permissionless (SETT-02).
///   6. Call settlement.endSession() — positionValueUSDC check passes → rate frozen.
///   7. Holder claims → USDC paid out pro-rata.
///   8. settled == true; claim idempotent after shares exhausted.
///
/// Also proves:
///   - endSession reverts before deadline if called by a non-factory address.
///   - endSession with open positions (positionValueUSDC > 0) reverts "positions not drained".
contract SettlementKeeperIntegrationTest is Test {
    // =========================================================================
    // Constants
    // =========================================================================

    int256 internal constant ETH_PRICE = 300_000_000_000; // $3,000.00 (8-dec)
    uint256 internal constant SESSION_DURATION = 72 hours;
    uint256 internal constant INITIAL_USDC = 1000e6; // 1,000 USDC seed

    // Position: 30e30 USD at 3x leverage → collateral = 10 USDC (1e7 raw)
    uint256 internal constant SIZE_USD = 30e30;
    uint256 internal constant LEVERAGE = 30_000; // 3x

    // =========================================================================
    // Fixtures
    // =========================================================================

    SkIntegTestUSDC internal usdc;
    MockPerps internal perps;
    MockChainlinkAggregator internal ethFeed;
    MockChainlinkAggregator internal btcFeed;
    MockChainlinkAggregator internal solFeed;
    MTokenVault internal vault;
    SettlementContract internal settlement;

    address internal sessionFactory;
    address internal orchestrator;
    address internal operator;
    address internal holder;

    bytes32 internal positionKey;
    bytes32 internal openOrderKey;

    // =========================================================================
    // setUp
    // =========================================================================

    function setUp() public {
        // Advance time so feed timestamps don't underflow staleness checks
        vm.warp(10_001);

        sessionFactory = makeAddr("sessionFactory");
        orchestrator = makeAddr("orchestrator");
        operator = makeAddr("operator");
        holder = makeAddr("holder");

        usdc = new SkIntegTestUSDC();

        ethFeed = new MockChainlinkAggregator(ETH_PRICE, block.timestamp);
        btcFeed = new MockChainlinkAggregator(ETH_PRICE, block.timestamp);
        solFeed = new MockChainlinkAggregator(ETH_PRICE, block.timestamp);

        perps = new MockPerps(address(ethFeed), address(btcFeed), address(solFeed));
        // executionDelay defaults to 1 for local tests

        vault = new MTokenVault(
            IERC20(address(usdc)),
            "mCLA-SK1",
            "mCLA-SK1",
            address(perps),
            address(0), // no sequencer feed
            address(ethFeed),
            address(btcFeed),
            address(solFeed),
            sessionFactory,
            orchestrator,
            operator,
            INITIAL_USDC,
            true // useSepoliaStaleness
        );

        // Deploy settlement with deadline 72h from now
        settlement = new SettlementContract(
            address(usdc), address(perps), address(vault), sessionFactory, block.timestamp + SESSION_DURATION
        );

        // Wire settlement into vault (factory-gated)
        vm.prank(sessionFactory);
        vault.setSettlement(address(settlement));

        // Start session (factory-gated)
        vm.prank(sessionFactory);
        vault.startSession(SESSION_DURATION);

        // Seed vault: holder deposits 1,000 USDC
        usdc.mint(holder, INITIAL_USDC);
        vm.startPrank(holder);
        usdc.approve(address(vault), INITIAL_USDC);
        vault.deposit(INITIAL_USDC, holder);
        vm.stopPrank();

        // Open one ETH long (orchestrator-gated)
        vm.prank(orchestrator);
        openOrderKey = vault.openLong("ETH", SIZE_USD, LEVERAGE, 30);

        // Execute the open order (simulates keeper_monitor)
        vm.roll(block.number + perps.executionDelay());
        perps.executeOrder(openOrderKey);

        // Recover positionKey
        (positionKey,,,,) = perps.pendingOrders(openOrderKey);

        // Clear vault trading lock (orchestrator)
        vm.prank(orchestrator);
        vault.clearTradingLock(openOrderKey);

        // Assert position is open
        assertGt(perps.positionValueUSDC(address(vault)), 0, "setUp: position must be open");
    }

    // =========================================================================
    // test_FullDrainSettleClaim — proves the orchestrator's correct settlement flow
    // =========================================================================

    /// @notice Full settlement flow: open→close→drain→endSession→claim.
    ///         Simulates the orchestrator settlement keeper exactly:
    ///           1. vault.closePosition (orchestrator role)
    ///           2. vm.roll + executeOrder (keeper role)
    ///           3. Warp past deadline (permissionless endSession)
    ///           4. settlement.endSession()
    ///           5. holder.claim() → USDC paid out
    function test_FullDrainSettleClaim() public {
        // ── A. Pre-drain: submit close order via vault.closePosition (GAP #9 step 2) ──
        vm.prank(orchestrator);
        bytes32 closeOrderKey = vault.closePosition(positionKey, 0);

        // ── B. Execute the close order after executionDelay (GAP #9 step 3-4) ──
        vm.roll(block.number + perps.executionDelay());
        perps.executeOrder(closeOrderKey);

        // Clear the vault trading lock after close
        vm.prank(orchestrator);
        vault.clearTradingLock(closeOrderKey);

        // ── C. Verify positionValueUSDC == 0 (GAP #9 step 5) ──
        assertEq(
            perps.positionValueUSDC(address(vault)), 0, "GAP #9: positionValueUSDC must be 0 after close order executed"
        );

        // ── D. Warp past session deadline so endSession is permissionless (SETT-02) ──
        vm.warp(settlement.deadline() + 1);
        // Update feed timestamps so they stay fresh after warp (< MAX_STALENESS_SEP=21600s)
        ethFeed.setPriceAt(ETH_PRICE, block.timestamp);
        btcFeed.setPriceAt(ETH_PRICE, block.timestamp);
        solFeed.setPriceAt(ETH_PRICE, block.timestamp);

        // ── E. Call settlement.endSession (GAP #9 step 6) ──
        // vault.endSession was NOT called (settlement.endSession calls it internally)
        settlement.endSession();

        // Settled flag must be true
        assertTrue(settlement.settled(), "GAP #9: settled must be true after endSession");
        assertGt(settlement.redemptionRate(), 0, "GAP #9: redemptionRate must be frozen");

        // ── F. Holder claim → receives pro-rata USDC (GAP #9 step 7) ──
        uint256 shares = vault.balanceOf(holder);
        assertGt(shares, 0, "holder must have shares to claim");

        uint256 expected_usdc = (shares * settlement.redemptionRate()) / 1e18;
        assertGt(expected_usdc, 0, "holder expected_usdc must be > 0");

        vm.prank(holder);
        settlement.claim();

        // Vault shares burned, USDC received
        assertEq(vault.balanceOf(holder), 0, "holder shares must be burned after claim");
        assertEq(IERC20(address(usdc)).balanceOf(holder), expected_usdc, "holder must receive expected pro-rata USDC");

        // Settlement holds 0 USDC (D-18 locked custody)
        assertEq(IERC20(address(usdc)).balanceOf(address(settlement)), 0, "settlement must hold 0 USDC");
    }

    // =========================================================================
    // test_EndSessionReverts_BeforeDeadline_NonFactory
    // =========================================================================

    /// @notice endSession reverts before deadline when called by non-factory (SETT-02).
    ///         The orchestrator EOA is NOT the sessionFactory — it must wait for deadline.
    function test_EndSessionReverts_BeforeDeadline_NonFactory() public {
        // Pre-drain (so positionValueUSDC would pass — just need to test the auth gate)
        vm.prank(orchestrator);
        bytes32 closeKey = vault.closePosition(positionKey, 0);
        vm.roll(block.number + perps.executionDelay());
        perps.executeOrder(closeKey);
        vm.prank(orchestrator);
        vault.clearTradingLock(closeKey);

        // Deadline has NOT passed — orchestrator EOA is not factory
        // endSession must revert "not authorized before deadline"
        vm.expectRevert(bytes("Settlement: not authorized before deadline"));
        vm.prank(orchestrator); // NOT sessionFactory
        settlement.endSession();
    }

    // =========================================================================
    // test_EndSessionReverts_WithOpenPositions
    // =========================================================================

    /// @notice endSession reverts when positions are NOT drained (positionValueUSDC > 0).
    ///         This proves why the orchestrator must pre-drain before calling endSession.
    function test_EndSessionReverts_WithOpenPositions() public {
        // Do NOT close the open position
        assertGt(perps.positionValueUSDC(address(vault)), 0, "position must still be open");

        // Warp past deadline so auth gate passes
        vm.warp(settlement.deadline() + 1);
        ethFeed.setPriceAt(ETH_PRICE, block.timestamp);
        btcFeed.setPriceAt(ETH_PRICE, block.timestamp);
        solFeed.setPriceAt(ETH_PRICE, block.timestamp);

        // endSession must revert "positions not drained"
        vm.expectRevert(bytes("Settlement: positions not drained"));
        settlement.endSession();
    }

    // =========================================================================
    // test_DoubleEndSession_Reverts
    // =========================================================================

    /// @notice Double endSession reverts "already settled" (idempotent guard).
    function test_DoubleEndSession_Reverts() public {
        // Full drain + settle first
        vm.prank(orchestrator);
        bytes32 closeKey2 = vault.closePosition(positionKey, 0);
        vm.roll(block.number + perps.executionDelay());
        perps.executeOrder(closeKey2);
        vm.prank(orchestrator);
        vault.clearTradingLock(closeKey2);

        vm.warp(settlement.deadline() + 1);
        ethFeed.setPriceAt(ETH_PRICE, block.timestamp);
        btcFeed.setPriceAt(ETH_PRICE, block.timestamp);
        solFeed.setPriceAt(ETH_PRICE, block.timestamp);
        settlement.endSession();
        assertTrue(settlement.settled());

        // Second call must revert
        vm.expectRevert(bytes("Settlement: already settled"));
        settlement.endSession();
    }

    // =========================================================================
    // test_FactoryCanEndSessionBeforeDeadline
    // =========================================================================

    /// @notice SessionFactory can call endSession before the deadline (SETT-02).
    function test_FactoryCanEndSessionBeforeDeadline() public {
        // Pre-drain
        vm.prank(orchestrator);
        bytes32 closeKey3 = vault.closePosition(positionKey, 0);
        vm.roll(block.number + perps.executionDelay());
        perps.executeOrder(closeKey3);
        vm.prank(orchestrator);
        vault.clearTradingLock(closeKey3);

        assertEq(perps.positionValueUSDC(address(vault)), 0);

        // Deadline has NOT passed, but sessionFactory is allowed
        assertTrue(block.timestamp < settlement.deadline(), "deadline should not have passed");
        vm.prank(sessionFactory);
        settlement.endSession(); // must succeed

        assertTrue(settlement.settled(), "factory endSession before deadline must settle");
    }

    // =========================================================================
    // test_ClaimAfterSettle_ProRata
    // =========================================================================

    /// @notice claim() pays exactly pro-rata USDC from the vault (D-14/D-18).
    function test_ClaimAfterSettle_ProRata() public {
        // Drain and settle via factory (pre-deadline path)
        vm.prank(orchestrator);
        bytes32 ck = vault.closePosition(positionKey, 0);
        vm.roll(block.number + perps.executionDelay());
        perps.executeOrder(ck);
        vm.prank(orchestrator);
        vault.clearTradingLock(ck);

        vm.prank(sessionFactory);
        settlement.endSession();

        uint256 vaultUsdcAtSettle = IERC20(address(usdc)).balanceOf(address(vault));
        uint256 holderShares = vault.balanceOf(holder);
        uint256 totalShares = vault.totalSupply();

        // Rate = vaultUsdc * 1e18 / totalShares
        uint256 expectedRate = (vaultUsdcAtSettle * 1e18) / totalShares;
        assertEq(settlement.redemptionRate(), expectedRate, "rate must match manual calculation");

        // Expected USDC = shares * rate / 1e18 (rounds down per D-14)
        uint256 expectedUsdcForHolder = (holderShares * expectedRate) / 1e18;

        vm.prank(holder);
        settlement.claim();

        assertEq(IERC20(address(usdc)).balanceOf(holder), expectedUsdcForHolder, "pro-rata USDC must match formula");
    }
}

// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {Test} from "forge-std/Test.sol";
import {MockPerps} from "../src/mocks/MockPerps.sol";
import {MockChainlinkAggregator} from "../src/mocks/MockChainlinkAggregator.sol";
import {IPerpsAdapter} from "../src/interfaces/IPerpsAdapter.sol";

/// @title MockPerpsTest — MOCK-01 gate: async/liquidation/override/staleness behaviours
/// @notice Proves MockPerps satisfies IPerpsAdapter and all four D-11/D-12/D-13 invariants:
///         1. OrderExecuted emitted exactly after executionDelay blocks (D-13).
///         2. positionValueUSDC = collateral + (mark-entry)×signedSize in USDC (D-03).
///         3. Auto-liquidation when collateral + pnl <= 0 (D-12).
///         4. setMarkOverride is deployer-only; expired override falls back to Chainlink (D-11).
///         5. Stale Chainlink reverts (D-03).
/// @dev Uses MockChainlinkAggregator to control mark + staleness without an anvil fork.
///      Naming convention: test_FunctionName_Condition_Expected (D-15).
contract MockPerpsTest is Test {
    // =========================================================================
    // Test fixtures
    // =========================================================================

    MockPerps internal perps;
    MockChainlinkAggregator internal ethFeed;
    MockChainlinkAggregator internal btcFeed;
    MockChainlinkAggregator internal solFeed;

    /// @dev Vault address used as msg.sender for position calls.
    address internal vault = makeAddr("vault");

    /// @dev Non-owner address used to assert access-control reverts.
    address internal stranger = makeAddr("stranger");

    /// @dev ETH price seed: $3,000 in 8-decimal Chainlink format.
    int256 internal constant ETH_PRICE_8DEC = 300_000_000_000; // $3,000.00

    /// @dev BTC price seed: $65,000 in 8-decimal Chainlink format.
    int256 internal constant BTC_PRICE_8DEC = 6_500_000_000_000; // $65,000.00

    /// @dev SOL price seed: $150 in 8-decimal Chainlink format.
    int256 internal constant SOL_PRICE_8DEC = 15_000_000_000; // $150.00

    // =========================================================================
    // Setup
    // =========================================================================

    function setUp() public {
        // Deploy mock Chainlink feeds with fresh timestamps
        ethFeed = new MockChainlinkAggregator(ETH_PRICE_8DEC, block.timestamp);
        btcFeed = new MockChainlinkAggregator(BTC_PRICE_8DEC, block.timestamp);
        solFeed = new MockChainlinkAggregator(SOL_PRICE_8DEC, block.timestamp);

        // Deploy MockPerps; this test contract is the deployer (owner)
        perps = new MockPerps(address(ethFeed), address(btcFeed), address(solFeed));

        // executionDelay defaults to 1 block
        assertEq(perps.executionDelay(), 1, "default executionDelay should be 1");
    }

    // =========================================================================
    // Test 1: Async OrderExecuted after executionDelay blocks (D-13)
    // =========================================================================

    /// @notice Proves OrderExecuted is NOT emitted at open, then emitted exactly once
    ///         after rolling executionDelay blocks and calling executeOrder.
    function test_OpenLong_AfterDelay_EmitsOrderExecuted() public {
        // --- Open a long position as the vault ---
        uint256 sizeUsd = 10_000 * 1e30; // $10,000 in 1e30 format
        uint256 leverage = 20_000; // 2x (1e4-scaled: 20000 = 2x)
        uint256 slippageBps = 30; // 0.3%

        vm.prank(vault);
        bytes32 orderKey = perps.openLong("ETH", sizeUsd, leverage, slippageBps);
        assertNotEq(orderKey, bytes32(0), "orderKey must be non-zero");

        // Retrieve the positionKey from the pending order before rolling
        (bytes32 positionKey,,,,) = perps.pendingOrders(orderKey);
        assertNotEq(positionKey, bytes32(0), "positionKey must be non-zero");

        // --- Confirm no OrderExecuted emitted in the same block ---
        // (Cannot assert negative event emission in Foundry; we assert state instead:
        // the order is not yet executed immediately after creation.)
        (, uint256 executeAfterBlock,,,) = perps.pendingOrders(orderKey);
        assertEq(executeAfterBlock, block.number + 1, "executeAfterBlock should be block.number + delay");

        // Attempting execution before the delay should revert (D-13, T-0-async mitigation)
        vm.expectRevert("MockPerps: too early");
        perps.executeOrder(orderKey);

        // --- Roll exactly executionDelay blocks ---
        vm.roll(block.number + perps.executionDelay());

        // --- Execute order: expect OrderExecuted(orderKey, vault, positionKey) ---
        vm.expectEmit(true, true, false, true);
        emit IPerpsAdapter.OrderExecuted(orderKey, vault, positionKey);
        perps.executeOrder(orderKey);

        // Verify order is now marked executed
        (,,,, bool executed) = perps.pendingOrders(orderKey);
        assertTrue(executed, "order should be marked executed");
    }

    /// @notice Proves a short position follows the same async pattern.
    function test_OpenShort_AfterDelay_EmitsOrderExecuted() public {
        uint256 sizeUsd = 5_000 * 1e30;
        uint256 leverage = 30_000; // 3x max

        vm.prank(vault);
        bytes32 orderKey = perps.openShort("BTC", sizeUsd, leverage, 30);
        assertNotEq(orderKey, bytes32(0), "orderKey must be non-zero");

        (bytes32 positionKey,,,,) = perps.pendingOrders(orderKey);

        vm.roll(block.number + perps.executionDelay());

        vm.expectEmit(true, true, false, true);
        emit IPerpsAdapter.OrderExecuted(orderKey, vault, positionKey);
        perps.executeOrder(orderKey);
    }

    /// @notice Proves closePosition also follows the async pattern and emits OrderExecuted.
    function test_ClosePosition_AfterDelay_EmitsOrderExecuted() public {
        // Open, execute, then close
        vm.prank(vault);
        bytes32 openKey = perps.openLong("ETH", 10_000 * 1e30, 20_000, 30);
        (bytes32 positionKey,,,,) = perps.pendingOrders(openKey);

        vm.roll(block.number + 1);
        perps.executeOrder(openKey);

        // Now close the position
        vm.prank(vault);
        bytes32 closeKey = perps.closePosition(positionKey, 10_000 * 1e30);
        assertNotEq(closeKey, bytes32(0), "closeKey must be non-zero");

        // Too early to execute
        vm.expectRevert("MockPerps: too early");
        perps.executeOrder(closeKey);

        vm.roll(block.number + 1);

        vm.expectEmit(true, true, false, true);
        emit IPerpsAdapter.OrderExecuted(closeKey, vault, positionKey);
        perps.executeOrder(closeKey);
    }

    // =========================================================================
    // Test 2: PnL formula correctness (D-03 / D-12)
    // =========================================================================

    /// @notice Proves positionValueUSDC = collateral + (mark-entry)×signedSize.
    ///         Uses an ETH long: entry $3,000 → mark $3,300, expect +$300/unit PnL.
    function test_PositionValue_PnlFormula_LongProfitable_Correct() public {
        // sizeUsd = $10,000 at 1x for simplicity (leverage=10000=1x)
        // collateral = sizeUsd / leverage = 10000 / 1 = $10,000 USDC
        // But leverage is 1e4-scaled: 10000 = 1x, so collateralRaw = sizeUsd * 10000 / 10000 = sizeUsd
        // collateral_usdc = sizeUsd / (1e30 / 1e6) = sizeUsd / 1e24
        // sizeUsd = 10000 * 1e30 → collateral_usdc = 10000 * 1e6 = 10_000_000_000 (10,000 USDC)

        uint256 sizeUsd = 10_000 * 1e30;
        uint256 leverage = 10_000; // 1x in 1e4-scaled units

        // Entry: ETH = $3,000 (set in setUp via ethFeed.setPrice)
        ethFeed.setPrice(ETH_PRICE_8DEC); // $3,000

        vm.prank(vault);
        bytes32 openKey = perps.openLong("ETH", sizeUsd, leverage, 0);
        (bytes32 positionKey,,,,) = perps.pendingOrders(openKey);

        // Change mark to $3,300 BEFORE reading positionValueUSDC
        int256 newMarkPrice = 330_000_000_000; // $3,300 in 8-decimal
        ethFeed.setPrice(newMarkPrice);

        // Execute open order (position must be non-closed for positionValueUSDC to count it)
        vm.roll(block.number + 1);
        perps.executeOrder(openKey);

        // Expected PnL using the correct formula (D-12, financially-sound):
        // pnl_usdc = signedSize * (mark - entry) / (entry * 1e24)
        //
        // ETH long: entry=$3,000 (300e9), mark=$3,300 (330e9), sizeUsd=10000e30
        // priceDelta = 330e9 - 300e9 = 30e9
        // pnl = 10000e30 * 30e9 / (300e9 * 1e24)
        //      = 10000 * 30 / 300 * (1e30 * 1e9) / (1e9 * 1e24)
        //      = 10000 * 0.1 * 1e39 / 1e33
        //      = 1000 * 1e6 USDC = $1,000
        // Verification: 10% price move on $10,000 1x long = $1,000 PnL. Correct.

        // Compute expected value per implementation formula:
        // collateral_usdc: sizeUsd * 10000 / leverage / (1e30/1e6) = 10000e30/1e24 = 10000e6
        uint256 expectedCollateral = 10_000 * 1e6; // $10,000 USDC

        // pnl = signedSize * (mark - entry) / (entry * 1e24)
        // casting to 'int256' is safe: sizeUsd fits well within int256 range for any realistic trade
        // forge-lint: disable-next-line(unsafe-typecast)
        int256 signedSize = int256(sizeUsd); // 10_000 * 1e30 (positive long)
        int256 priceDelta = 330_000_000_000 - 300_000_000_000; // 30e9
        int256 entryPrice = ETH_PRICE_8DEC; // 300e9
        int256 expectedPnl = (signedSize * priceDelta) / (entryPrice * int256(1e24));

        // casting to 'int256' is safe: expectedCollateral is uint256 bounded by vault capital
        // forge-lint: disable-next-line(unsafe-typecast)
        int256 expectedValue = int256(expectedCollateral) + expectedPnl;
        assertTrue(expectedValue > 0, "expectedValue should be positive (profitable long)");

        uint256 actualValue = perps.positionValueUSDC(vault);
        // casting to 'uint256' is safe: expectedValue > 0 (asserted above)
        // forge-lint: disable-next-line(unsafe-typecast)
        assertEq(actualValue, uint256(expectedValue), "positionValueUSDC should match formula");

        // Suppress unused variable warning
        positionKey;
    }

    /// @notice Proves a losing short position reduces positionValueUSDC below collateral.
    function test_PositionValue_PnlFormula_ShortLosing_ReducesValue() public {
        // Open a short: entry $150 SOL, mark rises to $165 (short loses)
        ethFeed.setPrice(ETH_PRICE_8DEC); // irrelevant but keep fresh
        solFeed.setPrice(SOL_PRICE_8DEC); // $150 entry

        uint256 sizeUsd = 1_000 * 1e30; // $1,000
        uint256 leverage = 10_000; // 1x

        vm.prank(vault);
        bytes32 openKey = perps.openShort("SOL", sizeUsd, leverage, 0);
        (bytes32 positionKey,,,,) = perps.pendingOrders(openKey);

        // Mark rises: $165 — short loses
        solFeed.setPrice(16_500_000_000); // $165

        vm.roll(block.number + 1);
        perps.executeOrder(openKey);

        uint256 collateral = 1_000 * 1e6;
        // pnl = signedSize * (mark - entry) / (entry * 1e24)
        // SOL short: entry=$150 (150e8), mark=$165 (165e8), sizeUsd=1000e30
        // priceDelta = 165e8 - 150e8 = 15e8
        // signedSize = -1000e30 (negative for short)
        // pnl = (-1000e30) * 15e8 / (150e8 * 1e24) = -1000 * 15 / 150 * (1e30*1e8)/(1e8*1e24)
        //      = -100 * 1e6 USDC = -$100 (10% move on $1000 short = -$100 loss)
        int256 priceDelta = 16_500_000_000 - 15_000_000_000; // 15e8
        // casting to 'int256' is safe: sizeUsd < int256 max for any realistic trade
        // forge-lint: disable-next-line(unsafe-typecast)
        int256 signedSize = -int256(sizeUsd); // negative (short)
        int256 entryPrice = SOL_PRICE_8DEC; // 150e8
        int256 expectedPnl = (signedSize * priceDelta) / (entryPrice * int256(1e24));
        assertTrue(expectedPnl < 0, "short should have negative pnl when mark rises");

        // casting to 'int256' is safe: collateral is USDC 6-dec, well within int256
        // forge-lint: disable-next-line(unsafe-typecast)
        int256 expectedNet = int256(collateral) + expectedPnl;
        assertTrue(expectedNet > 0, "should still be > 0 (not fully liquidated)");

        uint256 actualValue = perps.positionValueUSDC(vault);
        // casting to 'uint256' is safe: expectedNet > 0 (asserted above)
        // forge-lint: disable-next-line(unsafe-typecast)
        assertEq(actualValue, uint256(expectedNet), "losing short should reduce positionValueUSDC");

        // Suppress unused variable warning
        positionKey;
    }

    // =========================================================================
    // Test 3: Auto-liquidation when collateral + pnl <= 0 (D-12)
    // =========================================================================

    /// @notice Proves a position auto-liquidates (PositionLiquidated emitted) when
    ///         collateral + pnl <= 0 at close execution, forcing the settlement path.
    ///
    ///         Uses 3x leverage to make liquidation achievable with a ~33% price drop.
    ///         At 3x: collateral = sizeUsd/3 in USDC. A 33% drop wipes collateral entirely.
    ///         PnL formula: signedSize * (mark - entry) / (entry * 1e24)
    ///         collateral at 3x = 1000e30 / 3 / 1e24 = 333e6
    ///         PnL at 50% drop ($150→$75): 1000e30 * (-75e8) / (150e8 * 1e24) = -500e6
    ///         net = 333e6 - 500e6 = -167e6 < 0 → liquidated.
    function test_Liquidation_WhenCollateralPlusPnlNegative_Closes() public {
        // Open SOL long at $150 with 3x leverage.
        solFeed.setPrice(SOL_PRICE_8DEC); // $150 entry = 15_000_000_000 (8-dec)

        uint256 sizeUsd = 1_000 * 1e30; // $1,000 notional
        uint256 leverage = 30_000; // 3x in 1e4-scaled units

        vm.prank(vault);
        bytes32 openKey = perps.openLong("SOL", sizeUsd, leverage, 0);
        (bytes32 positionKey,,,,) = perps.pendingOrders(openKey);

        // Execute open
        vm.roll(block.number + 1);
        perps.executeOrder(openKey);

        // Crash SOL by 50%: $150 → $75. At 3x leverage, position is fully insolvent.
        // pnl = 1000e30 * (75e8 - 150e8) / (150e8 * 1e24) = 1000e30 * (-75e8) / (150e8 * 1e24)
        //     = 1000 * (-75) / 150 * 1e6 = -500 * 1e6 = -$500 USDC
        // collateral = 1000e30 * 10000 / 30000 / 1e24 = 333.3e6 ≈ $333
        // net = 333e6 - 500e6 = -167e6 < 0 → liquidate
        solFeed.setPrice(7_500_000_000); // $75.00 in 8-decimal Chainlink format

        // Now close the long position
        vm.prank(vault);
        bytes32 closeKey = perps.closePosition(positionKey, sizeUsd);

        // Roll and execute close — expect PositionLiquidated instead of OrderExecuted
        vm.roll(block.number + 1);
        vm.expectEmit(true, true, false, false);
        emit MockPerps.PositionLiquidated(positionKey, vault);
        perps.executeOrder(closeKey);

        // Position should now be closed
        (,,, bool closed,) = _getPositionState(positionKey);
        assertTrue(closed, "liquidated position should be closed");

        // positionValueUSDC should return 0 — all vault positions are closed/liquidated
        assertEq(perps.positionValueUSDC(vault), 0, "closed position contributes 0 to NAV");
    }

    /// @notice Proves checkLiquidation closes position and emits PositionLiquidated
    ///         when collateral + pnl <= 0, without requiring a closePosition call.
    ///         Uses 3x leverage: a 50% crash makes collateral + pnl < 0.
    function test_CheckLiquidation_WhenUndercollateralized_LiquidatesOpenPosition() public {
        solFeed.setPrice(SOL_PRICE_8DEC); // $150

        uint256 sizeUsd = 500 * 1e30; // $500 notional
        uint256 leverage = 30_000; // 3x — collateral ≈ $167

        vm.prank(vault);
        bytes32 openKey = perps.openLong("SOL", sizeUsd, leverage, 0);
        (bytes32 positionKey,,,,) = perps.pendingOrders(openKey);

        vm.roll(block.number + 1);
        perps.executeOrder(openKey);

        // Crash SOL 50%: $150 → $75. At 3x, position is insolvent.
        // pnl = 500e30 * (75e8 - 150e8) / (150e8 * 1e24) = 500 * (-75/150) * 1e6 = -250e6
        // collateral = 500e30 * 10000 / 30000 / 1e24 = 166.67e6
        // net = 166.67e6 - 250e6 = -83.33e6 < 0 → liquidate
        solFeed.setPrice(7_500_000_000); // $75 in 8-decimal

        vm.expectEmit(true, true, false, false);
        emit MockPerps.PositionLiquidated(positionKey, vault);
        perps.checkLiquidation(positionKey);

        (,,, bool closed,) = _getPositionState(positionKey);
        assertTrue(closed, "position should be closed after liquidation");
    }

    // =========================================================================
    // Test 4: Deployer-only setMarkOverride (D-11, T-0-mock)
    // =========================================================================

    /// @notice Proves non-owner call to setMarkOverride reverts (T-0-mock mitigation).
    function test_SetMarkOverride_NonDeployer_Reverts() public {
        vm.prank(stranger);
        vm.expectRevert(); // OZ Ownable: OwnableUnauthorizedAccount
        perps.setMarkOverride("ETH", 400_000_000_000, block.timestamp + 1 days);
    }

    /// @notice Proves owner can set a mark override that takes effect immediately
    ///         and that an expired override falls back to Chainlink.
    function test_SetMarkOverride_OwnerCanSet_ExpiredFallsBackToChainlink() public {
        // Set Chainlink ETH to $3,000
        ethFeed.setPrice(ETH_PRICE_8DEC);

        // Owner sets override to $4,000, expires in 1 day
        int256 overridePrice = 400_000_000_000; // $4,000
        perps.setMarkOverride("ETH", overridePrice, block.timestamp + 1 days);

        // Open a long — entry should be $4,000 (override active)
        vm.prank(vault);
        bytes32 openKey = perps.openLong("ETH", 1_000 * 1e30, 10_000, 0);
        (bytes32 positionKey,,,,) = perps.pendingOrders(openKey);

        vm.roll(block.number + 1);
        perps.executeOrder(openKey);

        // Verify entry price stored is $4,000 override
        (, int256 entryPrice,,,) = _getPositionState(positionKey);
        assertEq(entryPrice, overridePrice, "entry should use override price");

        // Warp past override expiry
        vm.warp(block.timestamp + 2 days);
        // Update Chainlink to $3,200 (fresh timestamp)
        ethFeed.setPrice(320_000_000_000); // $3,200

        // positionValueUSDC should now use Chainlink ($3,200), not the expired override
        // pnl = (3200e8 - 4000e8) * 1000e30 / 1e32 = (-800e8) * 1e33 / 1e32 = -8000e8 = negative pnl
        // collateral = 1000e6, pnl will be negative → position value < collateral
        uint256 value = perps.positionValueUSDC(vault);

        // Calculate expected with Chainlink price using the correct formula:
        // pnl = signedSize * (mark - entry) / (entry * 1e24)
        // entry = $4,000 override (what was used at open), mark = $3,200 (Chainlink after warp)
        // delta = 320e9 - 400e9 = -80e9
        // pnl = 1000e30 * (-80e9) / (400e9 * 1e24) = -1000 * 80 / 400 * 1e6 = -200 * 1e6 = -$200 USDC
        int256 delta = 320_000_000_000 - 400_000_000_000; // -80e9
        // casting to 'int256' is safe: sizeUsd < int256 max
        // forge-lint: disable-next-line(unsafe-typecast)
        int256 size = int256(1_000 * 1e30);
        int256 entryForPnl = 400_000_000_000; // $4,000 was the entry price at open
        int256 pnl = (size * delta) / (entryForPnl * int256(1e24));
        // casting to 'int256' is safe: 1_000 * 1e6 < int256 max
        // forge-lint: disable-next-line(unsafe-typecast)
        int256 net = int256(1_000 * 1e6) + pnl;

        if (net > 0) {
            // casting to 'uint256' is safe: net > 0
            // forge-lint: disable-next-line(unsafe-typecast)
            assertEq(value, uint256(net), "expired override: should use Chainlink fallback price");
        } else {
            assertEq(value, 0, "expired override: deeply underwater position contributes 0");
        }
    }

    /// @notice Proves setExecutionDelay is also deployer-only.
    function test_SetExecutionDelay_NonDeployer_Reverts() public {
        vm.prank(stranger);
        vm.expectRevert(); // OZ Ownable: OwnableUnauthorizedAccount
        perps.setExecutionDelay(5);
    }

    /// @notice Proves owner can update executionDelay and it is respected for new orders.
    function test_SetExecutionDelay_OwnerCanSet_NewDelayEnforced() public {
        perps.setExecutionDelay(3);
        assertEq(perps.executionDelay(), 3);

        ethFeed.setPrice(ETH_PRICE_8DEC);
        vm.prank(vault);
        bytes32 openKey = perps.openLong("ETH", 1_000 * 1e30, 10_000, 0);

        (, uint256 executeAfterBlock,,,) = perps.pendingOrders(openKey);
        assertEq(executeAfterBlock, block.number + 3, "executeAfterBlock should use new delay");

        // 2 blocks not enough
        vm.roll(block.number + 2);
        vm.expectRevert("MockPerps: too early");
        perps.executeOrder(openKey);

        // 3 blocks: succeeds
        vm.roll(block.number + 1); // total +3
        (bytes32 positionKey,,,,) = perps.pendingOrders(openKey);
        vm.expectEmit(true, true, false, true);
        emit IPerpsAdapter.OrderExecuted(openKey, vault, positionKey);
        perps.executeOrder(openKey);
    }

    // =========================================================================
    // Test 5: Chainlink staleness revert (D-03)
    // =========================================================================

    /// @notice Proves positionValueUSDC reverts when Chainlink updatedAt is older
    ///         than MAX_STALENESS (1 hour), enforcing D-03 oracle-manipulation mitigation.
    function test_StaleChainlink_Reverts() public {
        // Open a position (fresh price)
        ethFeed.setPrice(ETH_PRICE_8DEC);
        vm.prank(vault);
        bytes32 openKey = perps.openLong("ETH", 1_000 * 1e30, 10_000, 0);

        vm.roll(block.number + 1);
        perps.executeOrder(openKey);

        // Warp time forward by 2 hours, then set feed's updatedAt to time BEFORE the warp.
        // This simulates a stale feed: updatedAt is now 2 hours in the past.
        vm.warp(block.timestamp + 2 hours);
        // Set the feed's updatedAt to a timestamp that is MAX_STALENESS + 1 seconds old
        // relative to current block.timestamp after the warp.
        uint256 staleUpdatedAt = block.timestamp - perps.MAX_STALENESS() - 1;
        ethFeed.setPriceAt(ETH_PRICE_8DEC, staleUpdatedAt);

        // positionValueUSDC should revert with stale price
        vm.expectRevert("MockPerps: stale price");
        perps.positionValueUSDC(vault);
    }

    /// @notice Proves positionValueUSDC does NOT revert when price is fresh (< MAX_STALENESS).
    function test_FreshChainlink_DoesNotRevert() public {
        ethFeed.setPrice(ETH_PRICE_8DEC);
        vm.prank(vault);
        bytes32 openKey = perps.openLong("ETH", 1_000 * 1e30, 10_000, 0);

        vm.roll(block.number + 1);
        perps.executeOrder(openKey);

        // 30 minutes elapsed — still within 1-hour MAX_STALENESS window
        vm.warp(block.timestamp + 30 minutes);
        // Feed updatedAt must also advance to be "fresh enough"
        ethFeed.setPrice(ETH_PRICE_8DEC); // resets updatedAt to current block.timestamp

        uint256 value = perps.positionValueUSDC(vault);
        // collateral = 1000e6, pnl = 0 (price unchanged), value = collateral
        assertEq(value, 1_000 * 1e6, "fresh price should return collateral (no pnl)");
    }

    /// @notice Proves an active (unexpired) override bypasses the staleness check.
    function test_ActiveOverride_BypassesStalenessCheck() public {
        // Warp to a known realistic timestamp so we can set stale timestamps without underflow
        vm.warp(86400); // 1 day from epoch (avoids underflow when subtracting hours)

        // Set an active override (expires far future)
        int256 overridePrice = ETH_PRICE_8DEC;
        perps.setMarkOverride("ETH", overridePrice, block.timestamp + 365 days);

        // Make Chainlink feed stale: set updatedAt to 2 hours ago (safe since block.timestamp=86400)
        ethFeed.setPriceAt(ETH_PRICE_8DEC, block.timestamp - 2 hours);

        // Open position (uses override, no staleness check applied to stale feed)
        vm.prank(vault);
        bytes32 openKey = perps.openLong("ETH", 1_000 * 1e30, 10_000, 0);
        vm.roll(block.number + 1);
        perps.executeOrder(openKey);

        // positionValueUSDC should use override price, not hit the stale revert
        // pnl = 0 (mark == entry since override == entry), value = collateral
        uint256 value = perps.positionValueUSDC(vault);
        assertEq(value, 1_000 * 1e6, "active override should bypass stale Chainlink");
    }

    // =========================================================================
    // Test 5b: CR-03 stale-round guards (answeredInRound + updatedAt == 0)
    // =========================================================================

    /// @notice (CR-03-a) Proves positionValueUSDC reverts with "MockPerps: stale round"
    ///         when answeredInRound < roundId — the canonical Chainlink carried-answer guard.
    ///         Uses setStaleRound to inject answeredInRound = roundId - 1.
    function test_StaleRound_AnsweredInRoundLtRoundId_Reverts() public {
        // Open a position with a fresh price so positionValueUSDC loop executes
        ethFeed.setPrice(ETH_PRICE_8DEC);
        vm.prank(vault);
        bytes32 openKey = perps.openLong("ETH", 1_000 * 1e30, 10_000, 0);
        vm.roll(block.number + 1);
        perps.executeOrder(openKey);

        // Force a stale-round scenario: answeredInRound < roundId (carried-over answer)
        // ethFeed.roundId was incremented by setPrice() → now some value R.
        // We set roundId = R+1, answeredInRound = R (i.e., < roundId) with a fresh updatedAt.
        uint80 currentRoundId = ethFeed.roundId();
        ethFeed.setStaleRound(
            currentRoundId + 1, // new roundId
            ETH_PRICE_8DEC, // answer
            block.timestamp, // updatedAt fresh (not stale by time)
            currentRoundId // answeredInRound < new roundId → stale round
        );

        // Should revert with the stale-round guard, not the time-staleness guard
        vm.expectRevert("MockPerps: stale round");
        perps.positionValueUSDC(vault);
    }

    /// @notice (CR-03-b) Proves positionValueUSDC reverts with "MockPerps: round not complete"
    ///         when updatedAt == 0 — indicates a round that has never received an answer.
    function test_StaleRound_UpdatedAtZero_Reverts() public {
        // Open a position so positionValueUSDC loop executes
        ethFeed.setPrice(ETH_PRICE_8DEC);
        vm.prank(vault);
        bytes32 openKey = perps.openLong("ETH", 1_000 * 1e30, 10_000, 0);
        vm.roll(block.number + 1);
        perps.executeOrder(openKey);

        // Force updatedAt = 0 (round never completed) — answeredInRound matches roundId
        uint80 currentRoundId = ethFeed.roundId();
        ethFeed.setStaleRound(
            currentRoundId + 1, // new roundId
            ETH_PRICE_8DEC, // answer
            0, // updatedAt = 0 → round not complete
            currentRoundId + 1 // answeredInRound == roundId (valid on that axis)
        );

        // Should revert with the round-not-complete guard
        vm.expectRevert("MockPerps: round not complete");
        perps.positionValueUSDC(vault);
    }

    /// @notice (CR-03-c) Proves the existing fresh-price path still returns a value after
    ///         CR-03 guards are added — regression check that valid data passes all guards.
    function test_StaleRound_FreshValidRound_StillReturnsValue() public {
        // Open a position
        ethFeed.setPrice(ETH_PRICE_8DEC);
        vm.prank(vault);
        bytes32 openKey = perps.openLong("ETH", 1_000 * 1e30, 10_000, 0);
        vm.roll(block.number + 1);
        perps.executeOrder(openKey);

        // Ensure feed is in a fully-valid state: updatedAt fresh, answeredInRound == roundId
        ethFeed.clearStaleRound();
        ethFeed.setPrice(ETH_PRICE_8DEC); // resets updatedAt to block.timestamp

        // Should NOT revert — all guards pass
        uint256 value = perps.positionValueUSDC(vault);
        // collateral = 1000e6, pnl = 0 (price unchanged), value = collateral
        assertEq(value, 1_000 * 1e6, "fresh valid round should return collateral (no pnl)");
    }

    // =========================================================================
    // Test 6: Unsupported market
    // =========================================================================

    /// @notice Proves openLong reverts for unknown market strings.
    function test_OpenLong_UnknownMarket_Reverts() public {
        vm.prank(vault);
        vm.expectRevert("MockPerps: unsupported market");
        perps.openLong("DOGE", 1_000 * 1e30, 10_000, 0);
    }

    // =========================================================================
    // Test 7: Double execution reverts
    // =========================================================================

    /// @notice Proves executeOrder reverts if called twice for the same order.
    function test_ExecuteOrder_AlreadyExecuted_Reverts() public {
        ethFeed.setPrice(ETH_PRICE_8DEC);
        vm.prank(vault);
        bytes32 openKey = perps.openLong("ETH", 1_000 * 1e30, 10_000, 0);

        vm.roll(block.number + 1);
        perps.executeOrder(openKey);

        vm.expectRevert("MockPerps: order already executed");
        perps.executeOrder(openKey);
    }

    // =========================================================================
    // Internal helpers
    // =========================================================================

    /// @dev Reads position state from MockPerps.positions using individual field accessors.
    ///      MockPerps.positions returns a struct; we re-read individual fields via a helper
    ///      since Solidity returns structs with dynamic types (string) as multiple values.
    function _getPositionState(bytes32 positionKey)
        internal
        view
        returns (int256 signedSize, int256 entryPrice, uint256 collateral, bool closed, address posVault)
    {
        MockPerps.Position memory pos = _readPosition(positionKey);
        return (pos.signedSize, pos.entryPrice, pos.collateral, pos.closed, pos.vault);
    }

    /// @dev Reads a Position struct from the public mapping (workaround for dynamic-type structs).
    function _readPosition(bytes32 positionKey) internal view returns (MockPerps.Position memory pos) {
        (string memory market, int256 signedSize, int256 entryPrice, uint256 collateral, address v, bool closed) =
            perps.positions(positionKey);
        pos.market = market;
        pos.signedSize = signedSize;
        pos.entryPrice = entryPrice;
        pos.collateral = collateral;
        pos.vault = v;
        pos.closed = closed;
    }
}

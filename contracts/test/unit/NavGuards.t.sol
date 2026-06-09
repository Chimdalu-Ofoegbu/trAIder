// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {Test} from "forge-std/Test.sol";
import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {IPerpsAdapter} from "../../src/interfaces/IPerpsAdapter.sol";
import {MTokenVault} from "../../src/mTokenVault.sol";
import {MockPerps} from "../../src/mocks/MockPerps.sol";
import {MockChainlinkAggregator} from "../../src/mocks/MockChainlinkAggregator.sol";
import {SettlementContract} from "../../src/SettlementContract.sol";

// =========================================================================
// Minimal 6-decimal ERC-20 for test USDC
// =========================================================================

/// @dev Unit-test-only 6-decimal ERC-20 (duplicated here to avoid test cross-imports).
contract NavGuardTestUSDC is ERC20 {
    constructor() ERC20("NavGuard Test USDC", "USDC") {}

    function mint(address to, uint256 amount) external {
        _mint(to, amount);
    }

    function decimals() public pure override returns (uint8) {
        return 6;
    }
}

// =========================================================================
// NavGuardsTest — D-05/D-06 NAV guard unit tests (CONTRACTS-07 / CONTRACTS-08)
// =========================================================================

/// @title NavGuardsTest — NAV guard unit tests (D-05 / D-06)
/// @notice Proves two critical invariants for the oracle outage rescue path:
///
///         D-05 PLANNER CONSTRAINT (load-bearing):
///           `positionValueUSDC(vault)` returns 0 on an EMPTY position set WITHOUT
///           triggering a staleness revert. This is the key property that keeps the
///           operator rescue path (endSession → drain → settle) unfrozen by oracle
///           outage once positions have been closed.
///
///         D-06 staleness guard:
///           The vault's staleness gate reverts mint when any Chainlink feed is stale,
///           proving the oracle outage detection works correctly.
///
///         Both tests use the unit profile (no fork) — MockPerps + MockChainlinkAggregator.
///
/// @dev Test names match the authoritative 03-VALIDATION.md scaffold targets.
///      Run: forge test --match-path "test/unit/NavGuards.t.sol" -vv
contract NavGuardsTest is Test {
    // =========================================================================
    // Constants
    // =========================================================================

    int256 internal constant ETH_PRICE_8DEC = 300_000_000_000; // $3,000.00
    int256 internal constant BTC_PRICE_8DEC = 6_500_000_000_000; // $65,000.00
    int256 internal constant SOL_PRICE_8DEC = 15_000_000_000; // $150.00

    uint256 internal constant SESSION_DURATION = 72 hours;
    uint256 internal constant INITIAL_USDC = 100e6; // 100 USDC seed capital

    // =========================================================================
    // Fixtures
    // =========================================================================

    NavGuardTestUSDC internal usdc;
    MockPerps internal perps;
    MockChainlinkAggregator internal ethFeed;
    MockChainlinkAggregator internal btcFeed;
    MockChainlinkAggregator internal solFeed;
    MTokenVault internal vault;
    SettlementContract internal settlement;

    address internal sessionFactory;
    address internal orchestrator;
    address internal operator;
    address internal user;

    // =========================================================================
    // setUp — basic harness (no open positions — D-05 starts clean)
    // =========================================================================

    function setUp() public {
        // Warp to a timestamp large enough to prevent underflow when we later push
        // feed timestamps far in the past (e.g., block.timestamp - 999_999).
        // 1_000_000 > 999_999 so no uint256 underflow.
        vm.warp(1_000_001);

        sessionFactory = makeAddr("sessionFactory");
        orchestrator = makeAddr("orchestrator");
        operator = makeAddr("operator");
        user = makeAddr("user");

        // Deploy USDC
        usdc = new NavGuardTestUSDC();

        // Deploy Chainlink feeds with fresh timestamps.
        // useSepoliaStaleness=true in vault (6h MAX_STALENESS) so staleness
        // only fires when we explicitly push updatedAt far into the past.
        ethFeed = new MockChainlinkAggregator(ETH_PRICE_8DEC, block.timestamp);
        btcFeed = new MockChainlinkAggregator(BTC_PRICE_8DEC, block.timestamp);
        solFeed = new MockChainlinkAggregator(SOL_PRICE_8DEC, block.timestamp);

        // Deploy MockPerps adapter with the same feeds.
        perps = new MockPerps(address(ethFeed), address(btcFeed), address(solFeed));

        // Deploy vault — NO sequencer feed (address(0) skips the sequencer check).
        // useSepoliaStaleness=true: MAX_STALENESS = 6h for all feeds; makes the
        // D-06 staleness test easy to trigger with a far-past updatedAt.
        vault = new MTokenVault(
            IERC20(address(usdc)),
            "mCLA-S1",
            "mCLA-S1",
            address(perps),
            address(0), // no sequencer feed
            address(ethFeed),
            address(btcFeed),
            address(solFeed),
            sessionFactory,
            orchestrator,
            operator,
            INITIAL_USDC,
            true // useSepoliaStaleness: 6h threshold for all feeds
        );

        // Deploy SettlementContract (mmAddress_=address(0): guard disabled in unit tests)
        settlement = new SettlementContract(
            address(usdc),
            address(perps),
            address(vault),
            sessionFactory,
            block.timestamp + SESSION_DURATION,
            address(0)
        );

        // Wire settlement (factory-gated, one-time)
        vm.prank(sessionFactory);
        vault.setSettlement(address(settlement));

        // Start session so deposits are accepted
        vm.prank(sessionFactory);
        vault.startSession(SESSION_DURATION);

        // Fund user and approve vault
        usdc.mint(user, 10_000e6);
        vm.prank(user);
        usdc.approve(address(vault), type(uint256).max);
    }

    // =========================================================================
    // D-05 — empty-set positionValueUSDC returns 0 with no staleness revert
    // =========================================================================

    /// @notice Proves positionValueUSDC returns 0 and does NOT revert when the vault
    ///         has NO open positions, even when the Chainlink price feed is stale.
    ///
    ///         This is the D-05 load-bearing constraint: MockPerps.positionValueUSDC
    ///         loops over vaultPositionKeys[vault]. When that array is empty, the loop
    ///         body never executes, so _markPrice (which enforces staleness) is never
    ///         called. The function returns 0 with no revert.
    ///
    ///         Note for GMXAdapter (03-05 executor): GMXAdapter MUST mirror this early-
    ///         return behavior. If GMXAdapter calls Chainlink inside positionValueUSDC
    ///         regardless of whether any positions are open, it violates D-05 and the
    ///         operator rescue path (endSession → drain → settle) will be frozen by an
    ///         oracle outage even after positions are drained.
    function test_positionValueUSDC_empty_no_revert() public {
        // Verify the vault has no open positions (sanity check — setUp opens none).
        bytes32[] memory keys = perps.getOpenPositionKeys(address(vault));
        assertEq(keys.length, 0, "setUp must leave vault with no open positions");

        // Push the ETH feed stale: updatedAt far in the past (> MAX_STALENESS_SEP = 6h = 21600s).
        // If positionValueUSDC reads the feed, it would revert with "MockPerps: stale price".
        ethFeed.setPriceAt(ETH_PRICE_8DEC, block.timestamp - 999_999);

        // D-05: positionValueUSDC on an empty vault must return 0 with NO revert.
        // The empty loop short-circuits before any feed read — stale price is never evaluated.
        uint256 val = perps.positionValueUSDC(address(vault));
        assertEq(val, 0, "D-05: positionValueUSDC must return 0 on empty set");

        // Cross-check: verify the stale feed WOULD revert positionValueUSDC if there were a
        // non-closed position. We use a fresh address (not vault) that has a direct position
        // to avoid touching vault mint guards. Specifically, open a position via a different
        // "vault" address (an EOA that acts as msg.sender for MockPerps).
        // MockPerps.openLong/openShort require msg.sender == vault, so we call directly.
        address altVault = makeAddr("altVault");

        // Fund altVault with ETH for gas and call MockPerps as altVault
        // to create a position entry in vaultPositionKeys[altVault].
        // Use a mark override so the open succeeds despite stale Chainlink.
        perps.setMarkOverride("ETH", ETH_PRICE_8DEC, block.timestamp + 1 hours);

        // Simulate MockPerps.openLong called by altVault (msg.sender = altVault).
        // Since openLong is gated to msg.sender, we prank as altVault.
        vm.prank(altVault);
        bytes32 altOpenKey = perps.openLong("ETH", 10e30, 10_000, 30);
        vm.roll(block.number + perps.executionDelay());
        perps.executeOrder(altOpenKey);

        // Expire the mark override — _markPrice now falls back to stale Chainlink
        perps.setMarkOverride("ETH", ETH_PRICE_8DEC, block.timestamp - 1);

        // Non-empty case: positionValueUSDC for altVault MUST revert (D-03 enforcement)
        vm.expectRevert("MockPerps: stale price");
        perps.positionValueUSDC(altVault);

        // Re-confirm: original vault (no positions) still returns 0 — stale feed
        // only affects non-empty position sets.
        uint256 valAfter = perps.positionValueUSDC(address(vault));
        assertEq(valAfter, 0, "D-05: vault with no positions must still return 0 after cross-check");
    }

    // =========================================================================
    // D-05 — oracle-stale drain+settle succeeds (endSession survives oracle outage)
    // =========================================================================

    /// @notice Proves that SettlementContract.endSession() SUCCEEDS after draining all
    ///         positions, even when the Chainlink price feed is stale.
    ///
    ///         Flow:
    ///           1. Open a position (vault has non-zero positionValueUSDC).
    ///           2. Pre-drain: issue close via vault.settlementClosePosition + vm.roll + executeOrder.
    ///           3. Push the ETH feed stale (updatedAt far in the past).
    ///           4. Call settlement.endSession() from sessionFactory.
    ///           5. Assert: endSession succeeds (no revert), settlement.settled == true.
    ///
    ///         This proves D-05: once positions are drained (empty set), the oracle outage
    ///         does NOT freeze the settlement path — positionValueUSDC short-circuits to 0
    ///         without reading the Chainlink feed.
    ///
    ///         Also demonstrates D-06 behavioral context: the stale feed WOULD block
    ///         new mints on this vault, but settlement (which calls positionValueUSDC on
    ///         the empty set) is unaffected.
    function test_oracle_stale_drain_settle() public {
        // Step 1: Deposit capital and open an ETH long position.
        vm.prank(user);
        vault.deposit(100e6, user);

        uint256 sizeUsd = 30e30;
        uint256 leverage = 30_000; // 3x max
        vm.prank(orchestrator);
        bytes32 openKey = vault.openLong("ETH", sizeUsd, leverage, 30);

        // Advance blocks so the async open order executes
        vm.roll(block.number + perps.executionDelay());
        perps.executeOrder(openKey);
        vm.prank(orchestrator);
        vault.clearTradingLock(openKey);

        // Confirm position is open and has value
        assertGt(perps.positionValueUSDC(address(vault)), 0, "position must be open after openLong");

        // Step 2: Pre-drain via vault.settlementClosePosition (as the settlement contract).
        // This issues the close order; MockPerps queues it for execution after executionDelay blocks.
        bytes32[] memory openKeys = perps.getOpenPositionKeys(address(vault));
        assertEq(openKeys.length, 1, "must have exactly 1 open position before drain");

        bytes32[] memory closeOrderKeys = new bytes32[](openKeys.length);
        for (uint256 i = 0; i < openKeys.length; i++) {
            vm.prank(address(settlement));
            closeOrderKeys[i] = vault.settlementClosePosition(openKeys[i], 0);
        }

        // Advance blocks past executionDelay and execute the close order
        vm.roll(block.number + perps.executionDelay());
        for (uint256 i = 0; i < closeOrderKeys.length; i++) {
            perps.executeOrder(closeOrderKeys[i]);
        }

        // Verify positions are fully drained (positionValueUSDC == 0 before staleness push)
        assertEq(perps.positionValueUSDC(address(vault)), 0, "D-05: positions must be drained before staleness push");

        // Step 3: Push the ETH price feed stale (far past MAX_STALENESS_SEP = 6h = 21600s).
        // A fresh positionValueUSDC with ANY open position would revert here.
        ethFeed.setPriceAt(ETH_PRICE_8DEC, block.timestamp - 999_999);

        // Staleness confirmation: with the empty set, positionValueUSDC still returns 0 (no revert).
        assertEq(perps.positionValueUSDC(address(vault)), 0, "D-05: stale feed must not affect empty-set call");

        // Step 4: Call settlement.endSession() from sessionFactory.
        // endSession will:
        //   - Call vault.endSession() (no-op if already ended — try/catch swallows)
        //   - Set sessionEnded = true
        //   - Enumerate open keys → empty (all pre-drained) → drain loop is a no-op
        //   - Call positionValueUSDC → 0 (empty set, no feed read) → check passes
        //   - Snapshot supply + freeze rate + set settled = true
        vm.prank(sessionFactory);
        settlement.endSession(); // must NOT revert (D-05 crux)

        // Step 5: Assert settlement reached settled state.
        assertTrue(settlement.settled(), "D-05: settlement must be settled after endSession with stale oracle");
        assertGt(settlement.redemptionRate(), 0, "redemptionRate must be frozen (non-zero, vault holds USDC)");

        // Sanity: user shares > 0, can compute claim amount
        uint256 userShares = vault.balanceOf(user);
        assertGt(userShares, 0, "user must have shares to claim");
    }

    // =========================================================================
    // D-06 — staleness guard: mint reverts when feed is stale
    // =========================================================================

    /// @notice Proves the vault's staleness state machine: when a Chainlink feed crosses
    ///         MAX_STALENESS, _checkAndUpdateStaleness marks _stalenessCrossedAt, and
    ///         subsequent mints revert with MintBlockedStaleFeed.
    ///
    ///         D-06 gate: staleness guard reverts mint when updatedAt exceeds MAX_STALENESS.
    ///         Burn path stays live (tested in 01-MTokenVault.t.sol; referenced here for context).
    function test_staleness_guard_blocks_mint() public {
        // Deposit some capital
        vm.prank(user);
        vault.deposit(100e6, user);

        // Push the ETH feed stale: updatedAt far in the past.
        // MAX_STALENESS_SEP = 21_600s; we push 999_999s in the past → clearly stale.
        ethFeed.setPriceAt(ETH_PRICE_8DEC, block.timestamp - 999_999);

        // The staleness state machine fires on the NEXT state-changing call.
        // _checkAndUpdateStaleness marks _stalenessCrossedAt on the first stale read.
        // Subsequent calls during the GRACE_WINDOW emit "grace" stage.
        // After ESCALATION_THRESHOLD (600s) in the escalated state, _sessionPaused fires.
        //
        // For this test: warp past ESCALATION_THRESHOLD to ensure _stalenessCrossedAt is set
        // and the staleness machine has latched (the first mint call marks it, the second reverts).
        //
        // First call: sets _stalenessCrossedAt but does NOT revert yet (it's within the grace window)
        // because GRACE_WINDOW (60s) has not elapsed — the call emits OracleStale("grace") but
        // does not revert. The _requireFreshNavForMint check then reverts because _stalenessCrossedAt > 0.
        //
        // Wait — _requireFreshNavForMint checks `if (_stalenessCrossedAt > 0) revert MintBlockedStaleFeed()`.
        // _checkAndUpdateStaleness sets _stalenessCrossedAt on the SAME call as deposit. So the
        // first stale deposit call: (1) checkAndUpdate marks stalenessCrossedAt, (2) requireFreshNav
        // reads it (> 0) and reverts MintBlockedStaleFeed. This means even the FIRST deposit
        // after staleness is hit will revert.
        vm.prank(user);
        vm.expectRevert(MTokenVault.MintBlockedStaleFeed.selector);
        vault.deposit(10e6, user);
    }
}

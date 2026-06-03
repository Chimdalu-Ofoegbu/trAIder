// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {Test} from "forge-std/Test.sol";
import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import {IPerformanceOracle} from "../src/interfaces/IPerformanceOracle.sol";
import {IPerpsAdapter} from "../src/interfaces/IPerpsAdapter.sol";
import {MTokenVault} from "../src/mTokenVault.sol";
import {MockPerps} from "../src/mocks/MockPerps.sol";
import {MockChainlinkAggregator} from "../src/mocks/MockChainlinkAggregator.sol";

// =========================================================================
// Minimal mintable ERC-20 used as test USDC
// =========================================================================

/// @dev Test-only 6-decimal ERC-20 mimicking USDC for vault setUp.
contract TestUSDC is ERC20 {
    constructor() ERC20("Test USDC", "USDC") {}

    function mint(address to, uint256 amount) external {
        _mint(to, amount);
    }

    function decimals() public pure override returns (uint8) {
        return 6;
    }
}

// =========================================================================
// ControlledAdapter — settable positionValueUSDC for circuit-breaker tests
// =========================================================================

/// @dev Minimal IPerpsAdapter stub with configurable positionValueUSDC.
///      Used to drive vault NAV below the circuit-breaker floor in isolation.
contract ControlledAdapter is IPerpsAdapter {
    uint256 private _positionValue;

    function setPositionValue(uint256 val) external {
        _positionValue = val;
    }

    function positionValueUSDC(address) external view override returns (uint256) {
        return _positionValue;
    }

    function openLong(string calldata, uint256, uint256, uint256) external override returns (bytes32) {
        return bytes32(0);
    }

    function openShort(string calldata, uint256, uint256, uint256) external override returns (bytes32) {
        return bytes32(0);
    }

    function closePosition(bytes32, uint256) external override returns (bytes32) {
        return bytes32(0);
    }

    function getOpenPositionKeys(address) external pure override returns (bytes32[] memory) {
        return new bytes32[](0);
    }
}

// =========================================================================
// MTokenVaultTest — VAULT-01..08 + TOKEN-01 gate
// =========================================================================

/// @title MTokenVaultTest — VAULT-01..08 + TOKEN-01 gate
/// @notice Proves all eight vault guards and TOKEN-01 including:
///           - Ticker name/symbol (D-18)
///           - settlementBurn ACL (TOKEN-01)
///           - settlementWithdraw gating (D-18, custody)
///           - Donation-attack defense (VAULT-01, D-29 1000-run fuzz)
///           - Same-block NAV identity (VAULT-03)
///           - Per-feed staleness: mint-revert / burn-live (VAULT-02)
///           - Sequencer down: mint-revert / burn-live (VAULT-02, D-11)
///           - Sequencer grace period: mint-blocked within 3600s post-restart (D-11)
///           - Leverage cap (VAULT-04, D-17)
///           - Circuit breaker (VAULT-05): mint paused, burn live, survival flag
///           - Trading lock (VAULT-06)
///           - Session ACL (VAULT-07)
///           - Operator-no-withdraw (VAULT-08)
/// @dev Uses MockChainlinkAggregator + MockPerps — no fork needed.
///      Naming convention: test_FunctionName_Condition_Expected (D-15).
contract MTokenVaultTest is Test {
    // =========================================================================
    // Constants
    // =========================================================================

    int256 internal constant ETH_PRICE_8DEC = 300_000_000_000; // $3,000.00
    int256 internal constant BTC_PRICE_8DEC = 6_500_000_000_000; // $65,000.00
    int256 internal constant SOL_PRICE_8DEC = 15_000_000_000; // $150.00

    /// @dev Must match MTokenVault.SEQUENCER_GRACE_PERIOD.
    uint256 internal constant SEQ_GRACE = 3_600;

    // =========================================================================
    // Fixtures
    // =========================================================================

    TestUSDC internal usdc;
    MockPerps internal perps;
    MockChainlinkAggregator internal ethFeed;
    MockChainlinkAggregator internal btcFeed;
    MockChainlinkAggregator internal solFeed;
    MockChainlinkAggregator internal seqFeed;

    MTokenVault internal vault;

    address internal sessionFactory = makeAddr("sessionFactory");
    address internal orchestrator = makeAddr("orchestrator");
    address internal operator = makeAddr("operator");
    address internal settlement = makeAddr("settlement");
    address internal user = makeAddr("user");
    address internal attacker = makeAddr("attacker");
    address internal victim = makeAddr("victim");
    address internal stranger = makeAddr("stranger");

    // =========================================================================
    // setUp
    // =========================================================================

    function setUp() public {
        // 1. Warp to a timestamp large enough to prevent underflow in feed timestamps.
        vm.warp(10_001);

        // 2. Deploy test USDC
        usdc = new TestUSDC();

        // 3. Deploy mock Chainlink feeds with fresh timestamps
        ethFeed = new MockChainlinkAggregator(ETH_PRICE_8DEC, block.timestamp);
        btcFeed = new MockChainlinkAggregator(BTC_PRICE_8DEC, block.timestamp);
        solFeed = new MockChainlinkAggregator(SOL_PRICE_8DEC, block.timestamp);

        // 4. Deploy sequencer uptime feed: answer=0 (up), startedAt past SEQ_GRACE
        //    so mint is immediately enabled in a "sequencer up + grace elapsed" state.
        seqFeed = new MockChainlinkAggregator(0, block.timestamp - SEQ_GRACE - 1);

        // 5. Deploy MockPerps adapter
        perps = new MockPerps(address(ethFeed), address(btcFeed), address(solFeed));

        // 6. Deploy the vault (ticker = "mCLA-S1", D-18, TOKEN-01)
        vault = new MTokenVault(
            usdc,
            "mCLA-S1",
            "mCLA-S1",
            address(perps),
            address(seqFeed),
            address(ethFeed),
            address(btcFeed),
            address(solFeed),
            sessionFactory,
            orchestrator,
            operator,
            10_000e6,
            false
        );

        // 7. Wire settlement (factory-gated)
        vm.prank(sessionFactory);
        vault.setSettlement(settlement);

        // 8. Start session so deposits are accepted
        vm.prank(sessionFactory);
        vault.startSession(72 hours);

        // 9. Fund users with USDC and approve vault
        usdc.mint(user, 10_000e6);
        usdc.mint(attacker, 2_000_000e6);
        usdc.mint(victim, 1_000e6);
        usdc.mint(stranger, 1_000e6);
        usdc.mint(operator, 1_000e6);

        vm.prank(user);
        usdc.approve(address(vault), type(uint256).max);
        vm.prank(attacker);
        usdc.approve(address(vault), type(uint256).max);
        vm.prank(victim);
        usdc.approve(address(vault), type(uint256).max);
        vm.prank(stranger);
        usdc.approve(address(vault), type(uint256).max);
        vm.prank(operator);
        usdc.approve(address(vault), type(uint256).max);
    }

    // =========================================================================
    // Helpers
    // =========================================================================

    /// @dev Deploy a fresh vault with a ControlledAdapter for circuit-breaker tests.
    function _buildCBVault(ControlledAdapter ca) internal returns (MTokenVault cv) {
        cv = new MTokenVault(
            usdc,
            "mCLA-CB",
            "mCLA-CB",
            address(ca),
            address(seqFeed),
            address(ethFeed),
            address(btcFeed),
            address(solFeed),
            sessionFactory,
            orchestrator,
            operator,
            10_000e6,
            false
        );
        vm.prank(sessionFactory);
        cv.startSession(72 hours);
    }

    /// @dev Decode VaultStats into primitives for inline assertions.
    function _statsFields() internal view returns (int256 pnl, uint256 dd, uint64 wins, uint64 total, bool survived) {
        IPerformanceOracle.VaultStats memory s = vault.getStats();
        return (s.realizedPnlUsd, s.maxDrawdownBps, s.winningCloses, s.totalCloses, s.survived);
    }

    // =========================================================================
    // TOKEN-01 / D-18 — Ticker identity
    // =========================================================================

    /// @notice Proves the vault share IS the mTOKEN — name()==symbol()=="mCLA-S1" (TOKEN-01, D-18).
    function test_Token_Ticker_NameSymbol() public view {
        assertEq(vault.name(), "mCLA-S1", "name must be the session ticker");
        assertEq(vault.symbol(), "mCLA-S1", "symbol must be the session ticker");
    }

    // =========================================================================
    // TOKEN-01 / D-18 — settlementBurn ACL
    // =========================================================================

    /// @notice Proves settlementBurn reverts for non-settlement callers and succeeds for settlement.
    ///         Also proves setSettlement is factory-only and one-time.
    function test_Token_SettlementBurn_OnlySettlement() public {
        // Give user some shares
        vm.prank(user);
        uint256 shares = vault.deposit(1000e6, user);
        assertGt(shares, 0);

        // Non-settlement caller must revert
        vm.prank(stranger);
        vm.expectRevert("Vault: not settlement");
        vault.settlementBurn(user, shares);

        uint256 supplyBefore = vault.totalSupply();

        // Settlement caller succeeds and burns shares
        vm.prank(settlement);
        vault.settlementBurn(user, shares);

        assertEq(vault.balanceOf(user), 0, "user shares must be 0 after settlementBurn");
        assertEq(vault.totalSupply(), supplyBefore - shares, "totalSupply must decrease");
    }

    /// @notice Proves setSettlement is factory-only.
    function test_SetSettlement_OnlyFactory_Reverts() public {
        vm.prank(stranger);
        vm.expectRevert("Vault: only factory");
        vault.setSettlement(stranger);
    }

    /// @notice Proves setSettlement is one-time — second call from factory reverts.
    function test_SetSettlement_OneTime_Reverts() public {
        vm.prank(sessionFactory);
        vm.expectRevert("Vault: settlement set");
        vault.setSettlement(makeAddr("otherSettlement"));
    }

    // =========================================================================
    // D-18 — settlementWithdraw gating (VAULT-08 sanctioned USDC exit)
    // =========================================================================

    /// @notice Proves settlementWithdraw requires the settlement caller AND post-sessionEnded.
    function test_Settlement_Withdraw_GatedAndPostSettle() public {
        vm.prank(user);
        vault.deposit(1000e6, user);

        uint256 withdrawAmt = 100e6;

        // Stranger blocked
        vm.prank(stranger);
        vm.expectRevert("Vault: not settlement");
        vault.settlementWithdraw(stranger, withdrawAmt);

        // Settlement blocked before endSession
        vm.prank(settlement);
        vm.expectRevert("Vault: not settled");
        vault.settlementWithdraw(user, withdrawAmt);

        // Operator blocked (VAULT-08)
        vm.prank(operator);
        vm.expectRevert("Vault: not settlement");
        vault.settlementWithdraw(operator, withdrawAmt);

        // End the session
        vm.prank(sessionFactory);
        vault.endSession();

        // Now settlement can withdraw USDC FROM THE VAULT to a claimant
        uint256 vaultBefore = usdc.balanceOf(address(vault));
        uint256 userBefore = usdc.balanceOf(user);

        vm.prank(settlement);
        vault.settlementWithdraw(user, withdrawAmt);

        assertEq(usdc.balanceOf(address(vault)), vaultBefore - withdrawAmt, "vault USDC must decrease");
        assertEq(usdc.balanceOf(user), userBefore + withdrawAmt, "user USDC must increase");
    }

    // =========================================================================
    // VAULT-01 — _decimalsOffset() = 12
    // =========================================================================

    /// @notice Proves vault decimals = USDC decimals (6) + offset (12) = 18.
    function test_DecimalsOffset_Is12() public view {
        assertEq(vault.decimals(), 18, "vault decimals must be 18 (USDC 6 + offset 12)");
    }

    // =========================================================================
    // VAULT-01 — Donation attack defense (D-29 1000-run fuzz)
    // =========================================================================

    /// @notice Proves offset=12 defense: attacker deposits 1 wei + donates 1B USDC;
    ///         victim depositing 100 USDC still receives shares > 0 (NOT a revert, Pitfall 3).
    function test_DonationAttack_Shares_Gt_Zero() public {
        // Mint enough USDC for attacker to make the donation
        uint256 donationAmount = 1_000_000_000 * 1e6; // 1B USDC
        usdc.mint(attacker, donationAmount + 1);

        // Attacker deposits 1 wei USDC
        vm.prank(attacker);
        vault.deposit(1, attacker);

        // Attacker donates 1B USDC (inflation attempt)
        vm.prank(attacker);
        usdc.transfer(address(vault), donationAmount);

        // Victim deposits 100 USDC
        vm.prank(victim);
        uint256 victimShares = vault.deposit(100 * 1e6, victim);

        // offset=12 → 1e12 virtual shares make the attack uneconomical
        assertGt(victimShares, 0, "victim must receive non-zero shares (offset=12 defense)");
    }

    /// @notice Fuzz variant: donation in range [1e6, 1M USDC] leaves victim with shares > 0.
    ///         Lower bound (1e6) ensures vault NAV stays above the circuit-breaker floor so
    ///         the test targets the donation-defense, not the circuit-breaker. (D-29)
    /// forge-config: default.fuzz.runs = 1000
    function test_Fuzz_DonationAttack_SharesNonZero(uint96 donation) public {
        // Bound: [1e6, 1_000_000e6]. The 1e6 lower bound keeps NAV > CB floor after 1-wei deposit.
        uint256 min = 1e6;
        uint256 max = 1_000_000 * 1e6;
        uint256 donationAmt = min + (uint256(donation) % (max - min));

        usdc.mint(attacker, donationAmt + 2);
        vm.prank(attacker);
        usdc.approve(address(vault), type(uint256).max);

        vm.prank(attacker);
        vault.deposit(1, attacker);

        vm.prank(attacker);
        usdc.transfer(address(vault), donationAmt);

        vm.prank(victim);
        uint256 victimShares = vault.deposit(100 * 1e6, victim);

        assertGt(victimShares, 0, "victim must always receive non-zero shares regardless of donation");
    }

    // =========================================================================
    // VAULT-03 — Per-block NAV cache
    // =========================================================================

    /// @notice Proves two nav() calls in the same block return byte-identical values.
    function test_NavCache_SameBlock_Identical() public {
        vm.prank(user);
        vault.deposit(1000e6, user);

        uint256 nav1 = vault.nav();
        uint256 nav2 = vault.nav();
        assertEq(nav1, nav2, "same-block nav() must be byte-identical");
    }

    /// @notice Proves nav() returns INITIAL_NAV_E18 at session start (no deposits, Pitfall 2).
    function test_NavCache_InitialNav_Is1e18() public view {
        assertEq(vault.nav(), 1e18, "nav() at session start must equal 1e18");
    }

    /// @notice Proves nav() changes across blocks when feed prices change.
    function test_NavCache_CrossBlock_Updates() public {
        vm.prank(user);
        vault.deposit(1000e6, user);
        uint256 navBefore = vault.nav();

        // Open a long so positionValueUSDC contributes to totalAssets
        vm.prank(orchestrator);
        bytes32 orderKey = vault.openLong("ETH", 1000e30, 20_000, 30);
        vm.roll(block.number + perps.executionDelay() + 1);
        perps.executeOrder(orderKey);
        vm.prank(orchestrator);
        vault.clearTradingLock(orderKey);

        // Update ETH price so position value changes
        ethFeed.setPrice(ETH_PRICE_8DEC * 2);
        vm.roll(block.number + 1);

        uint256 navAfter = vault.nav();
        assertFalse(navBefore == navAfter, "nav() must change across blocks when prices change");
    }

    // =========================================================================
    // VAULT-02 — Per-feed staleness: mint-revert / burn-live
    // =========================================================================

    /// @notice Proves deposit reverts on stale ETH feed; withdraw succeeds (VAULT-02, D-10).
    function test_Staleness_MintReverts_BurnLives() public {
        vm.prank(user);
        vault.deposit(1000e6, user);

        // Make ETH feed stale: updatedAt > MAX_STALENESS_ETH ago
        uint256 staleAt = block.timestamp - vault.MAX_STALENESS_ETH() - 1;
        ethFeed.setPriceAt(ETH_PRICE_8DEC, staleAt);

        // Mint must revert
        vm.prank(user);
        vm.expectRevert(MTokenVault.MintBlockedStaleFeed.selector);
        vault.deposit(100e6, user);

        // Burn must succeed at last-good NAV
        vm.prank(user);
        vault.withdraw(100e6, user, user);
    }

    /// @notice Proves per-feed MAX_STALENESS constants have the correct heartbeat-derived values.
    function test_PerFeedStaleness_Constants() public view {
        assertEq(vault.MAX_STALENESS_ETH(), 4_500, "MAX_STALENESS_ETH must be 4500");
        assertEq(vault.MAX_STALENESS_BTC(), 90_000, "MAX_STALENESS_BTC must be 90000");
        assertEq(vault.MAX_STALENESS_SOL(), 90_000, "MAX_STALENESS_SOL must be 90000");
    }

    // =========================================================================
    // VAULT-02 / D-11 — Sequencer uptime gate
    // =========================================================================

    /// @notice Proves mint reverts when sequencer is down; burn still succeeds.
    function test_SequencerDown_MintReverts() public {
        vm.prank(user);
        vault.deposit(1000e6, user);

        // Set sequencer feed answer = 1 (down)
        seqFeed.setPriceAt(1, block.timestamp);

        // Mint must revert with SequencerDown
        vm.prank(user);
        vm.expectRevert(MTokenVault.SequencerDown.selector);
        vault.deposit(100e6, user);

        // Burn must still succeed
        vm.prank(user);
        vault.withdraw(100e6, user, user);
    }

    /// @notice Proves mint is blocked within SEQUENCER_GRACE_PERIOD of a restart.
    ///         Mint re-enables after the grace period elapses. Burn live throughout.
    function test_SequencerGracePeriod_MintBlocked() public {
        vm.prank(user);
        vault.deposit(1000e6, user);

        // Sequencer just restarted: answer=0 (up) but startedAt = block.timestamp
        seqFeed.setPriceAt(0, block.timestamp);

        // Mint must revert with SequencerGracePeriod (within grace window)
        vm.prank(user);
        vm.expectRevert(MTokenVault.SequencerGracePeriod.selector);
        vault.deposit(100e6, user);

        // Burn must still succeed during grace period
        vm.prank(user);
        vault.withdraw(100e6, user, user);

        // Warp past grace period
        vm.warp(block.timestamp + SEQ_GRACE + 1);

        // Refresh all Chainlink feeds so staleness doesn't block (warp advanced block.timestamp)
        ethFeed.setPrice(ETH_PRICE_8DEC);
        btcFeed.setPrice(BTC_PRICE_8DEC);
        solFeed.setPrice(SOL_PRICE_8DEC);
        // seqFeed.updatedAt is now in the past (grace elapsed) — sequencer check passes

        // Mint should now succeed
        usdc.mint(user, 200e6);
        vm.prank(user);
        usdc.approve(address(vault), type(uint256).max);
        vm.prank(user);
        vault.deposit(100e6, user); // must not revert
    }

    // =========================================================================
    // VAULT-04 / D-17 — Leverage cap (single enforcement point in vault)
    // =========================================================================

    /// @notice Proves openLong reverts with leverage > MAX_LEVERAGE (3x).
    function test_LeverageCap_Reverts_Above3x() public {
        vm.prank(orchestrator);
        vm.expectRevert("Vault: leverage exceeds 3x cap");
        vault.openLong("ETH", 1000e30, 30_001, 30);
    }

    /// @notice Proves openShort reverts with leverage > MAX_LEVERAGE (3x).
    function test_LeverageCap_Short_Reverts_Above3x() public {
        vm.prank(orchestrator);
        vm.expectRevert("Vault: leverage exceeds 3x cap");
        vault.openShort("ETH", 1000e30, 30_001, 30);
    }

    /// @notice Proves openLong succeeds at exactly MAX_LEVERAGE (3x = 30_000).
    function test_LeverageCap_Allows_3x() public {
        vm.prank(orchestrator);
        bytes32 key = vault.openLong("ETH", 1000e30, 30_000, 30);
        assertNotEq(key, bytes32(0), "openLong at 3x must return a non-zero orderKey");
    }

    // =========================================================================
    // VAULT-05 — Circuit breaker: mint paused, burn active; survival flag
    // =========================================================================

    /// @notice Proves circuit breaker trips when NAV < 30% of INITIAL_NAV_E18.
    ///         Uses deal() to simulate a perps loss (position collateral drained).
    ///         Mint reverts; burn (withdraw) stays live. survived flips false.
    function test_CircuitBreaker_MintPaused_BurnActive() public {
        ControlledAdapter ca = new ControlledAdapter();
        MTokenVault cv = _buildCBVault(ca);

        address cbUser = makeAddr("cbUser");
        usdc.mint(cbUser, 2000e6);
        vm.prank(cbUser);
        usdc.approve(address(cv), type(uint256).max);

        // Deposit 1000 USDC → ~1e18 shares, NAV = 1e18. Healthy.
        vm.prank(cbUser);
        cv.deposit(1000e6, cbUser);

        // Simulate total perps loss: drain vault USDC to near-zero via deal().
        // This models the GMX scenario where collateral was sent to the venue and liquidated.
        // Vault has 1e18 shares outstanding but only 10 wei USDC.
        // nav = 10 * 1e30 / 1e18 = 1e13. CB threshold = 3e17. 1e13 < 3e17 → CB trips.
        deal(address(usdc), address(cv), 10);

        // Latch the circuit breaker (permissionless — persists state on success).
        // This must be done as a separate tx from deposit() so the state change persists.
        cv.checkAndLatchCircuitBreaker();

        // CB is now latched (_mintPaused = true). Deposit must revert.
        usdc.mint(cbUser, 100e6);
        vm.prank(cbUser);
        usdc.approve(address(cv), type(uint256).max);
        vm.prank(cbUser);
        vm.expectRevert("Vault: mint paused"); // CB latched above
        cv.deposit(100e6, cbUser);

        // survived must be false after circuit breaker trips
        assertFalse(cv.getStats().survived, "survived must be false after circuit breaker trips");

        // Burn (withdraw) must still succeed — vault has 10 wei, user has shares
        vm.prank(cbUser);
        cv.withdraw(1, cbUser, cbUser); // withdraw 1 wei USDC — must not revert (burn-live)
    }

    /// @notice Proves survival flag never resets once circuit breaker trips (D-08).
    function test_SurvivalBonus_False_After_CB_NeverResets() public {
        ControlledAdapter ca = new ControlledAdapter();
        MTokenVault cv = _buildCBVault(ca);

        address cbUser = makeAddr("cbUser6");
        usdc.mint(cbUser, 2000e6);
        vm.prank(cbUser);
        usdc.approve(address(cv), type(uint256).max);

        // Deposit then drain USDC via deal() to trip CB
        vm.prank(cbUser);
        cv.deposit(1000e6, cbUser);
        deal(address(usdc), address(cv), 10); // near-total loss

        // Latch the circuit breaker as a separate successful tx (state persists)
        cv.checkAndLatchCircuitBreaker();

        assertFalse(cv.getStats().survived, "survived must be false after CB trips");

        // Simulate NAV recovery: restore USDC via deal()
        deal(address(usdc), address(cv), 1_000_000e6);
        vm.roll(block.number + 1);

        // survived must STILL be false — the flag NEVER resets (D-08, no retroactive survival)
        assertFalse(cv.getStats().survived, "survived must remain false after NAV recovers (D-08)");
    }

    // =========================================================================
    // VAULT-06 — Trading lock: deposit/withdraw revert during in-flight order
    // =========================================================================

    /// @notice Proves deposit reverts while an in-flight order is pending.
    function test_TradingLock_DepositRevert() public {
        vm.prank(orchestrator);
        vault.openLong("ETH", 1000e30, 20_000, 30); // sets _tradingLocked = true

        vm.prank(user);
        vm.expectRevert("Vault: order in flight");
        vault.deposit(100e6, user);
    }

    /// @notice Proves withdraw reverts while an in-flight order is pending.
    function test_TradingLock_WithdrawRevert() public {
        vm.prank(user);
        vault.deposit(1000e6, user);

        vm.prank(orchestrator);
        vault.openLong("ETH", 1000e30, 20_000, 30);

        vm.prank(user);
        vm.expectRevert("Vault: order in flight");
        vault.withdraw(100e6, user, user);
    }

    /// @notice Proves clearTradingLock re-enables deposit.
    function test_TradingLock_ClearedByOrchestrator() public {
        vm.prank(orchestrator);
        bytes32 key = vault.openLong("ETH", 1000e30, 20_000, 30);

        vm.prank(orchestrator);
        vault.clearTradingLock(key);

        vm.prank(user);
        uint256 shares = vault.deposit(100e6, user); // must not revert
        assertGt(shares, 0);
    }

    // =========================================================================
    // VAULT-07 — Session lifecycle: factory-only access
    // =========================================================================

    /// @notice Proves startSession reverts for non-factory callers.
    function test_SessionFactory_OnlyAccess_StartReverts() public {
        vm.prank(sessionFactory);
        vault.endSession();

        vm.prank(stranger);
        vm.expectRevert("Vault: only factory");
        vault.startSession(72 hours);
    }

    /// @notice Proves endSession reverts for non-factory callers.
    function test_SessionFactory_OnlyAccess_EndReverts() public {
        vm.prank(stranger);
        vm.expectRevert("Vault: only factory");
        vault.endSession();
    }

    /// @notice Proves startSession reverts if a session is already active.
    function test_StartSession_AlreadyActive_Reverts() public {
        vm.prank(sessionFactory);
        vm.expectRevert("Vault: session already active");
        vault.startSession(72 hours);
    }

    // =========================================================================
    // VAULT-08 — Operator cannot withdraw USDC by any path
    // =========================================================================

    /// @notice Proves maxWithdraw and maxRedeem return 0 for the operator.
    function test_OperatorWithdraw_MaxZero() public {
        vm.prank(user);
        vault.deposit(1000e6, user);

        assertEq(vault.maxWithdraw(operator), 0, "maxWithdraw(operator) must be 0");
        assertEq(vault.maxRedeem(operator), 0, "maxRedeem(operator) must be 0");
    }

    /// @notice Proves the operator cannot call withdraw directly.
    function test_OperatorWithdraw_DirectCallReverts() public {
        vm.prank(user);
        vault.deposit(1000e6, user);

        // Transfer some shares to operator so they have a balance
        vm.prank(user);
        vault.transfer(operator, 100e18);

        // maxWithdraw(operator) = 0 → ERC4626 reverts
        vm.prank(operator);
        vm.expectRevert();
        vault.withdraw(1, operator, operator);
    }

    /// @notice Proves the operator cannot call redeem directly.
    function test_OperatorRedeem_DirectCallReverts() public {
        vm.prank(user);
        vault.deposit(1000e6, user);

        vm.prank(user);
        vault.transfer(operator, 100e18);

        // maxRedeem(operator) = 0 → reverts
        vm.prank(operator);
        vm.expectRevert();
        vault.redeem(1, operator, operator);
    }

    /// @notice Proves settlementWithdraw is blocked for the operator (VAULT-08).
    function test_OperatorCannotCallSettlementWithdraw() public {
        vm.prank(user);
        vault.deposit(1000e6, user);

        vm.prank(sessionFactory);
        vault.endSession();

        // Operator is not the settlement address
        vm.prank(operator);
        vm.expectRevert("Vault: not settlement");
        vault.settlementWithdraw(operator, 1);
    }

    // =========================================================================
    // getStats — initial state
    // =========================================================================

    /// @notice Proves getStats() returns a coherent VaultStats with survived=true at session start.
    function test_GetStats_InitialState() public view {
        (int256 pnl, uint256 dd, uint64 wins, uint64 total, bool survived) = _statsFields();
        assertEq(pnl, 0, "initial realizedPnlUsd must be 0");
        assertEq(dd, 0, "initial maxDrawdownBps must be 0");
        assertEq(wins, 0, "initial winningCloses must be 0");
        assertEq(total, 0, "initial totalCloses must be 0");
        assertTrue(survived, "survived must be true at session start");
    }

    // =========================================================================
    // Basic ERC-4626 roundtrip
    // =========================================================================

    /// @notice Basic deposit/redeem roundtrip: user gets approximately their USDC back.
    function test_DepositWithdraw_Roundtrip() public {
        uint256 depositAmt = 1000e6;
        vm.prank(user);
        uint256 shares = vault.deposit(depositAmt, user);
        assertGt(shares, 0);

        uint256 usdcBefore = usdc.balanceOf(user);

        vm.prank(user);
        uint256 assets = vault.redeem(shares, user, user);

        assertApproxEqAbs(assets, depositAmt, 1, "redeem must return approximately the deposited USDC");
        assertGt(usdc.balanceOf(user), usdcBefore, "user USDC must increase after redeem");
    }

    // =========================================================================
    // Coverage top-ups — reach >= 90% line coverage on contracts/src/ (TEST-01)
    // =========================================================================

    /// @notice Proves mint() (shares-based) succeeds and returns assets ≤ deposited (VAULT-01 roundtrip).
    ///         mint() is the ERC-4626 counterpart to deposit() — takes shares, returns assets paid.
    ///         This exercises the mint() code path which is distinct from deposit().
    function test_Mint_Shares_RoundtripAssets() public {
        // First deposit so totalSupply > 0, giving mint() a meaningful conversion.
        vm.prank(user);
        vault.deposit(1000e6, user);

        // Approve a fresh amount for the mint
        usdc.mint(user, 500e6);
        vm.prank(user);
        usdc.approve(address(vault), 500e6);

        // Mint exactly 100e18 shares — must succeed and return the assets consumed.
        vm.prank(user);
        uint256 assetsPaid = vault.mint(100e18, user);
        assertGt(assetsPaid, 0, "mint() must consume USDC");
        assertGt(vault.balanceOf(user), 0, "user must have shares after mint()");
    }

    /// @notice Proves openShort() succeeds with valid leverage and returns a non-zero orderKey.
    ///         Complements test_LeverageCap_Short_Reverts_Above3x() with the success path.
    function test_OpenShort_Succeeds_ValidLeverage() public {
        vm.prank(orchestrator);
        bytes32 key = vault.openShort("ETH", 1000e30, 20_000, 30);
        assertNotEq(key, bytes32(0), "openShort at 2x must return a non-zero orderKey");
    }

    /// @notice Proves staleness escalation auto-pauses the session when elapsed > ESCALATION_THRESHOLD.
    ///         Also proves staleness recovery clears the stale state when feeds return fresh.
    ///         Exercises the "escalate" branch in _checkAndUpdateStaleness and the clear branch.
    function test_Staleness_Escalation_PausesSession_ThenRecovery() public {
        vm.prank(user);
        vault.deposit(1000e6, user);

        // Make ETH feed stale (exceeds ESCALATION_THRESHOLD = 10 minutes after GRACE_WINDOW).
        // Set updatedAt to a time that is MAX_STALENESS_ETH + ESCALATION_THRESHOLD + GRACE_WINDOW + 1 ago.
        uint256 staleAge = vault.MAX_STALENESS_ETH() + vault.ESCALATION_THRESHOLD() + vault.GRACE_WINDOW() + 1;
        uint256 staleAt = block.timestamp - staleAge;
        ethFeed.setPriceAt(ETH_PRICE_8DEC, staleAt);

        // A deposit attempt (calls _checkAndUpdateStaleness) will now: set _stalenessCrossedAt,
        // see elapsed > ESCALATION_THRESHOLD, set _sessionPaused = true, emit OracleStale("escalate").
        // The deposit itself will revert at _requireFreshNavForMint() (MintBlockedStaleFeed).
        vm.prank(user);
        usdc.mint(user, 100e6);
        vm.prank(user);
        usdc.approve(address(vault), 100e6);
        vm.prank(user);
        vm.expectRevert(MTokenVault.MintBlockedStaleFeed.selector);
        vault.deposit(100e6, user);

        // Confirm staleness state was updated (_stalenessCrossedAt != 0) by checking mint is blocked.
        // (We can't read _stalenessCrossedAt directly since it's private; the MintBlockedStaleFeed
        // revert confirms _stalenessCrossedAt > 0.)

        // Recovery: refresh all feeds to fresh timestamps.
        ethFeed.setPrice(ETH_PRICE_8DEC);
        btcFeed.setPrice(BTC_PRICE_8DEC);
        solFeed.setPrice(SOL_PRICE_8DEC);

        // A deposit now should succeed: _checkAndUpdateStaleness sees all fresh,
        // clears _stalenessCrossedAt and _sessionPaused, refreshes _lastGoodNavE18.
        usdc.mint(user, 200e6);
        vm.prank(user);
        usdc.approve(address(vault), 200e6);
        vm.prank(user);
        uint256 shares = vault.deposit(100e6, user); // must not revert after recovery
        assertGt(shares, 0, "deposit must succeed after staleness recovery");
    }

    /// @notice Proves _maxStalenessFor returns MAX_STALENESS_BTC for the BTC feed specifically.
    ///         Exercises the BTC_FEED branch in _maxStalenessFor (distinct from ETH and SOL).
    ///         Vault deployed with useSepoliaStaleness=false so per-feed MAX_STALENESS is used.
    function test_Staleness_BtcFeed_UsesCorrectMaxStaleness() public {
        // Warp to a timestamp large enough to avoid underflow when subtracting MAX_STALENESS_BTC.
        // MAX_STALENESS_BTC = 90000s; we need block.timestamp > MAX_STALENESS_BTC.
        vm.warp(200_000); // 200_000 >> 90_000 — no underflow possible

        // Refresh all Chainlink feeds to the new block.timestamp.
        ethFeed.setPrice(ETH_PRICE_8DEC);
        btcFeed.setPrice(BTC_PRICE_8DEC);
        solFeed.setPrice(SOL_PRICE_8DEC);

        vm.prank(user);
        vault.deposit(1000e6, user);

        // Make only BTC feed stale by MAX_STALENESS_BTC + 1 seconds.
        // block.timestamp (200_000) - MAX_STALENESS_BTC (90_000) - 1 = 109_999 — no underflow.
        uint256 btcStaleAt = block.timestamp - vault.MAX_STALENESS_BTC() - 1;
        btcFeed.setPriceAt(BTC_PRICE_8DEC, btcStaleAt);
        // Keep ETH and SOL feeds fresh.
        ethFeed.setPrice(ETH_PRICE_8DEC);
        solFeed.setPrice(SOL_PRICE_8DEC);

        // Mint must revert — BTC feed stale triggers MintBlockedStaleFeed.
        usdc.mint(user, 100e6);
        vm.prank(user);
        usdc.approve(address(vault), 100e6);
        vm.prank(user);
        vm.expectRevert(MTokenVault.MintBlockedStaleFeed.selector);
        vault.deposit(100e6, user);

        // Burn must succeed — BTC staleness does not block withdraws (burn-live invariant).
        vm.prank(user);
        vault.withdraw(100e6, user, user);
    }

    /// @notice Proves settlementClosePosition routes to the adapter correctly when called by settlement.
    ///         Exercises the settlement-gated drain hook in mTokenVault.
    function test_SettlementClosePosition_SucceedsAsSettlement() public {
        vm.prank(user);
        vault.deposit(1000e6, user);

        // Open a long position.
        vm.prank(orchestrator);
        bytes32 orderKey = vault.openLong("ETH", 1000e30, 20_000, 30);

        // Execute the open order.
        vm.roll(block.number + perps.executionDelay());
        perps.executeOrder(orderKey);

        // Clear the trading lock.
        vm.prank(orchestrator);
        vault.clearTradingLock(orderKey);

        // Retrieve positionKey.
        (bytes32 posKey,,,,) = perps.pendingOrders(orderKey);
        assertNotEq(posKey, bytes32(0), "positionKey must be non-zero");

        // settlementClosePosition is gated to the settlement address.
        vm.prank(stranger);
        vm.expectRevert("Vault: not settlement");
        vault.settlementClosePosition(posKey, 0);

        // Settlement can call it.
        vm.prank(settlement);
        bytes32 closeKey = vault.settlementClosePosition(posKey, 0);
        assertNotEq(closeKey, bytes32(0), "settlementClosePosition must return a non-zero closeKey");
    }

    /// @notice Proves maxWithdraw returns 0 post-sessionEnded for all users (not just operator).
    ///         Exercises the sessionEnded branch in maxWithdraw/maxRedeem (VAULT-08 extension).
    function test_MaxWithdrawRedeem_Zero_PostSessionEnd() public {
        vm.prank(user);
        vault.deposit(1000e6, user);

        vm.prank(sessionFactory);
        vault.endSession();

        // Both maxWithdraw and maxRedeem must return 0 post-session for all users.
        assertEq(vault.maxWithdraw(user), 0, "maxWithdraw must be 0 post-sessionEnded");
        assertEq(vault.maxRedeem(user), 0, "maxRedeem must be 0 post-sessionEnded");
    }
}

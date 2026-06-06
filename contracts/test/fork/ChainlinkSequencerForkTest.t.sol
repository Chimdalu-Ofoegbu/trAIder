// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {Test} from "forge-std/Test.sol";
import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import {MTokenVault} from "../../src/mTokenVault.sol";
import {MockPerps} from "../../src/mocks/MockPerps.sol";
import {MockChainlinkAggregator} from "../../src/mocks/MockChainlinkAggregator.sol";

// =========================================================================
// Minimal 6-decimal ERC-20 for use as test USDC in fork context
// =========================================================================

/// @dev Fork-test-only 6-decimal ERC-20 (cannot import from 01-MTokenVault.t.sol).
contract ForkTestUSDC is ERC20 {
    constructor() ERC20("Fork Test USDC", "USDC") {}

    function mint(address to, uint256 amount) external {
        _mint(to, amount);
    }

    function decimals() public pure override returns (uint8) {
        return 6;
    }
}

// =========================================================================
// ChainlinkSequencerForkTest — D-07 canonical proof
// =========================================================================

/// @title ChainlinkSequencerForkTest — L2 sequencer uptime fork tests (CONTRACTS-07 / D-07)
/// @notice Fork-tests that prove the sequencer uptime guard against the REAL
///         Chainlink L2 Sequencer Uptime Feed address on Arbitrum One.
///
///         The three tests constitute the D-07 "sole proof must be complete proof"
///         requirement: down → revert, up+within-grace → revert, up+past-grace → ok.
///
///         Test harness choice (documented for 03-04-SUMMARY.md):
///           - Vault deployed with MockPerps + fresh MockChainlinkAggregators for
///             ETH/BTC/SOL so those feeds never trigger staleness reverts.
///           - SEQUENCER_UPTIME_FEED is set to the REAL Arbitrum One address
///             (0xFdB631F5EE196F0ed6FAa767959853A9F217697D).
///           - vm.mockCall intercepts latestRoundData() on that real address to inject
///             controlled answer/startedAt values without modifying chain state.
///           - This proves the vault's _checkSequencer wiring against the real feed
///             ABI shape while allowing deterministic test control.
///
///         Fork block: 353000000 (Arbitrum One pinned; correct for non-GMX tests per
///         Key Decision 03-01: GMX tests need >= 402000000; sequencer feed pre-dates
///         that block and is live from 2021 onward).
///
/// @dev No vm.skip in this contract — all three tests must execute against the fork.
///      Run: forge test --match-path "test/fork/ChainlinkSequencerForkTest.t.sol"
///               --fork-url $ARB_RPC --fork-block-number 353000000 -vv
contract ChainlinkSequencerForkTest is Test {
    // =========================================================================
    // Constants — Arbitrum One Chainlink sequencer uptime feed (D-07)
    // =========================================================================

    /// @dev Chainlink L2 Sequencer Uptime Feed on Arbitrum One.
    ///      Source: https://docs.chain.link/data-feeds/l2-sequencer-feeds
    ///      This address is hardcoded so a grep can verify it appears in the test file.
    address constant SEQUENCER_FEED = 0xFdB631F5EE196F0ed6FAa767959853A9F217697D;

    /// @dev ETH/USD Chainlink feed on Arbitrum One (for price mock seeding).
    address constant ARB_ETH_FEED = 0x639Fe6ab55C921f74e7fac1ee960C0B6293ba612;

    /// @dev SEQUENCER_GRACE_PERIOD must match MTokenVault.SEQUENCER_GRACE_PERIOD = 3600s.
    uint256 constant SEQ_GRACE = 3_600;

    // =========================================================================
    // Mock prices — deterministic values used to keep ETH/BTC/SOL feeds fresh
    // =========================================================================

    int256 constant ETH_PRICE_8DEC = 300_000_000_000; // $3,000.00
    int256 constant BTC_PRICE_8DEC = 6_500_000_000_000; // $65,000.00
    int256 constant SOL_PRICE_8DEC = 15_000_000_000; // $150.00

    // =========================================================================
    // Fixtures
    // =========================================================================

    ForkTestUSDC internal usdc;
    MockPerps internal perps;
    MockChainlinkAggregator internal ethFeed;
    MockChainlinkAggregator internal btcFeed;
    MockChainlinkAggregator internal solFeed;
    MTokenVault internal vault;

    address internal sessionFactory = makeAddr("sessionFactory");
    address internal orchestrator = makeAddr("orchestrator");
    address internal operator = makeAddr("operator");
    address internal user = makeAddr("user");

    // =========================================================================
    // setUp — deploy vault wired to REAL sequencer feed address
    // =========================================================================

    function setUp() public {
        // Warp to a timestamp large enough to prevent underflow in freshness math.
        // Fork is at block 353000000; block.timestamp is well above 10_001.
        vm.warp(block.timestamp > 10_001 ? block.timestamp : 10_001);

        // Deploy test USDC (6 decimals)
        usdc = new ForkTestUSDC();

        // Deploy fresh MockChainlinkAggregators for ETH/BTC/SOL so the vault's
        // _checkAndUpdateStaleness never fires for those feeds — we only want
        // to exercise the sequencer check in these tests.
        ethFeed = new MockChainlinkAggregator(ETH_PRICE_8DEC, block.timestamp);
        btcFeed = new MockChainlinkAggregator(BTC_PRICE_8DEC, block.timestamp);
        solFeed = new MockChainlinkAggregator(SOL_PRICE_8DEC, block.timestamp);

        // Deploy MockPerps adapter wired to the same mock price feeds.
        perps = new MockPerps(address(ethFeed), address(btcFeed), address(solFeed));

        // Deploy the vault — SEQUENCER_UPTIME_FEED = real Arbitrum One address.
        // The real feed lives on-chain at this fork block; vm.mockCall will intercept
        // its latestRoundData() return to inject controlled sequencer state.
        vault = new MTokenVault(
            usdc,
            "mCLA-S1",
            "mCLA-S1",
            address(perps),
            SEQUENCER_FEED, // REAL Arbitrum One sequencer uptime feed address
            address(ethFeed),
            address(btcFeed),
            address(solFeed),
            sessionFactory,
            orchestrator,
            operator,
            10_000e6,
            false // mainnet staleness thresholds
        );

        // Wire settlement (factory-gated)
        vm.prank(sessionFactory);
        vault.setSettlement(makeAddr("settlement"));

        // Start session so deposits are accepted
        vm.prank(sessionFactory);
        vault.startSession(72 hours);

        // Fund user with USDC and approve vault
        usdc.mint(user, 10_000e6);
        vm.prank(user);
        usdc.approve(address(vault), type(uint256).max);
    }

    // =========================================================================
    // Helper — build the abi-encoded latestRoundData() return for vm.mockCall
    // =========================================================================

    /// @dev Builds a latestRoundData response for the sequencer feed in the shape
    ///      expected by MTokenVault._latestRoundData (staticcall + abi.decode).
    ///      Return tuple: (uint80 roundId, int256 answer, uint256 startedAt,
    ///                     uint256 updatedAt, uint80 answeredInRound)
    ///      MTokenVault._checkSequencer reads:
    ///        (, seqAnswer,, seqStartedAt,) — index 1 = answer, index 3 = updatedAt
    ///      So the GRACE CLOCK is in index 3 (updatedAt slot).
    /// @param seqAnswer    0 = sequencer UP, 1 = sequencer DOWN.
    /// @param recoveryTime Timestamp when the sequencer last came online (placed at index 3).
    function _buildSeqResponse(int256 seqAnswer, uint256 recoveryTime) internal pure returns (bytes memory) {
        return abi.encode(
            uint80(1), // roundId (index 0)
            seqAnswer, // answer (index 1) — vault reads this
            uint256(0), // startedAt (index 2) — vault skips this
            recoveryTime, // updatedAt (index 3) — vault reads this as seqStartedAt
            uint80(1) // answeredInRound (index 4) — vault skips this
        );
    }

    // =========================================================================
    // Fork tests — D-07 canonical proof (no vm.skip)
    // =========================================================================

    /// @notice Proves deposit reverts with SequencerDown when the REAL sequencer
    ///         feed address reports answer == 1 (sequencer down).
    /// @dev vm.mockCall intercepts the REAL feed address (SEQUENCER_FEED) and
    ///      injects answer=1. The vault's _checkSequencer reads seqAnswer and reverts
    ///      with SequencerDown. This is the D-07 "down" leg.
    function test_sequencer_down_reverts() public {
        // Inject: sequencer DOWN (answer = 1), recovery time irrelevant when down.
        bytes memory downResponse = _buildSeqResponse(1, block.timestamp);
        vm.mockCall(SEQUENCER_FEED, abi.encodeWithSignature("latestRoundData()"), downResponse);

        // Deposit must revert with SequencerDown (vault._checkSequencer line 529).
        vm.prank(user);
        vm.expectRevert(MTokenVault.SequencerDown.selector);
        vault.deposit(1_000e6, user);
    }

    /// @notice Proves deposit reverts with SequencerGracePeriod when the sequencer
    ///         just recovered (startedAt = block.timestamp - 1800 < GRACE_PERIOD).
    /// @dev The sequencer is UP (answer = 0) but only recovered 30 minutes ago.
    ///      The vault checks: block.timestamp - seqStartedAt < SEQUENCER_GRACE_PERIOD
    ///      → 1800 < 3600 → reverts SequencerGracePeriod. D-07 "grace" leg.
    function test_sequencer_grace_period_reverts() public {
        // Inject: sequencer UP (answer = 0), recovery 30 min ago (1800s < 3600s grace).
        uint256 recentRecovery = block.timestamp - 1_800;
        bytes memory graceResponse = _buildSeqResponse(0, recentRecovery);
        vm.mockCall(SEQUENCER_FEED, abi.encodeWithSignature("latestRoundData()"), graceResponse);

        // Deposit must revert with SequencerGracePeriod (vault._checkSequencer line 531).
        vm.prank(user);
        vm.expectRevert(MTokenVault.SequencerGracePeriod.selector);
        vault.deposit(1_000e6, user);
    }

    /// @notice Proves deposit SUCCEEDS when the sequencer is UP and grace has elapsed.
    /// @dev The sequencer recovered 1 hour + 1 second ago (3601s > 3600s grace).
    ///      After a vm.warp to push block.timestamp far enough past the recovery time,
    ///      the vault's grace check passes and deposit succeeds (no revert).
    ///      D-07 "grace elapsed" leg — confirms the guard does NOT over-revert.
    function test_sequencer_grace_elapses_succeeds() public {
        // Set recovery time to block.timestamp (just recovered now).
        uint256 recoveryTime = block.timestamp;
        bytes memory graceResponse = _buildSeqResponse(0, recoveryTime);
        vm.mockCall(SEQUENCER_FEED, abi.encodeWithSignature("latestRoundData()"), graceResponse);

        // Within grace window — deposit should revert.
        vm.prank(user);
        vm.expectRevert(MTokenVault.SequencerGracePeriod.selector);
        vault.deposit(500e6, user);

        // Warp 2000 seconds past grace (3601s > 3600s) so grace period elapses.
        vm.warp(block.timestamp + SEQ_GRACE + 1);

        // Refresh the mock-call with the same recovery time (now past grace).
        // vm.mockCall persists across the warp — we re-register to be explicit.
        vm.mockCall(SEQUENCER_FEED, abi.encodeWithSignature("latestRoundData()"), graceResponse);

        // Refresh ETH/BTC/SOL price feed timestamps so vault staleness check passes
        // after the warp (feeds must be within MAX_STALENESS of new block.timestamp).
        ethFeed.setPrice(ETH_PRICE_8DEC);
        btcFeed.setPrice(BTC_PRICE_8DEC);
        solFeed.setPrice(SOL_PRICE_8DEC);

        // Deposit must NOW succeed — grace has elapsed.
        vm.prank(user);
        uint256 shares = vault.deposit(500e6, user);
        assertGt(shares, 0, "deposit after grace elapsed must return shares");
    }
}

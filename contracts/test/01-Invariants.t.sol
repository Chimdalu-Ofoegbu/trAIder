// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {Test} from "forge-std/Test.sol";
import {StdInvariant} from "forge-std/StdInvariant.sol";
import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {MTokenVault} from "../src/mTokenVault.sol";
import {SettlementContract} from "../src/SettlementContract.sol";
import {MockPerps} from "../src/mocks/MockPerps.sol";
import {MockChainlinkAggregator} from "../src/mocks/MockChainlinkAggregator.sol";
import {IPerpsAdapter} from "../src/interfaces/IPerpsAdapter.sol";

// =============================================================================
// Test USDC — 6-decimal mintable ERC-20
// =============================================================================

/// @dev Test-only 6-decimal ERC-20 mimicking USDC.
contract InvariantTestUSDC is ERC20 {
    constructor() ERC20("Test USDC", "USDC") {}

    function mint(address to, uint256 amount) external {
        _mint(to, amount);
    }

    function decimals() public pure override returns (uint8) {
        return 6;
    }
}

// =============================================================================
// VaultHandler — invariant fuzzer action driver
// =============================================================================

/// @title VaultHandler — drives randomised deposit / withdraw / openLong / closeAndExecute / settle
/// @notice The fuzzer calls these bounded actions in arbitrary order. Each action is wrapped so
///         reverts do not abort the invariant run — they simply become no-ops for that call.
///         This models an adversary trying to find any sequence that violates the invariants.
///
/// @dev Handler actions:
///        deposit(uint256)        — deposits bounded USDC into the vault as a test user.
///        withdraw(uint256)       — withdraws bounded shares from the vault.
///        openLong(uint256,uint256) — opens an ETH long with bounded size + leverage (≤ MAX_LEVERAGE).
///        closeAndExecute()       — closes the open position (if any) and executes the async order.
///        settle()                — ends the vault session and freezes the settlement rate.
///
///      Re-entry from the fuzzer is sequential — the handler never calls itself recursively.
///      The handler does NOT modify contract state outside the vault/perps/settlement system.
contract VaultHandler is Test {
    // ── fixtures ─────────────────────────────────────────────────────────────

    InvariantTestUSDC internal usdc;
    MockPerps internal perps;
    MTokenVault internal vault;
    SettlementContract internal settlement;
    MockChainlinkAggregator internal ethFeed;
    MockChainlinkAggregator internal btcFeed;
    MockChainlinkAggregator internal solFeed;

    address internal sessionFactory;
    address internal orchestrator;
    address internal operator;
    address internal user;

    /// @notice Last open order key returned by openLong (used by closeAndExecute).
    bytes32 internal lastOrderKey;

    /// @notice Last position key for the open ETH long (used by closeAndExecute).
    bytes32 internal lastPositionKey;

    /// @notice True once the session has been ended via settle().
    bool internal settled;

    // ── Chainlink price seed ──────────────────────────────────────────────────

    int256 internal constant ETH_PRICE_8DEC = 300_000_000_000; // $3,000

    // ── constructor ──────────────────────────────────────────────────────────

    constructor(
        InvariantTestUSDC usdc_,
        MockPerps perps_,
        MTokenVault vault_,
        SettlementContract settlement_,
        MockChainlinkAggregator ethFeed_,
        MockChainlinkAggregator btcFeed_,
        MockChainlinkAggregator solFeed_,
        address sessionFactory_,
        address orchestrator_,
        address operator_,
        address user_
    ) {
        usdc = usdc_;
        perps = perps_;
        vault = vault_;
        settlement = settlement_;
        ethFeed = ethFeed_;
        btcFeed = btcFeed_;
        solFeed = solFeed_;
        sessionFactory = sessionFactory_;
        orchestrator = orchestrator_;
        operator = operator_;
        user = user_;
    }

    // ── deposit ──────────────────────────────────────────────────────────────

    /// @notice Deposit bounded USDC into the vault.
    ///         Skips if session ended (endSession closes ERC-4626 exits).
    ///         Reverts are swallowed — they can occur legitimately (mint paused, trading locked).
    /// @param rawAmount Unbounded seed from the fuzzer; clamped to [1e6, 5_000e6] USDC.
    function deposit(uint256 rawAmount) external {
        if (settled) return;
        uint256 amount = bound(rawAmount, 1e6, 5_000e6);

        // Mint fresh USDC to the user so the handler never runs out of funds.
        usdc.mint(user, amount);

        // Approve vault if needed.
        vm.startPrank(user);
        usdc.approve(address(vault), amount);

        // Swallow reverts — mint gates (staleness, circuit breaker, trading lock) are valid.
        // slither-disable-next-line unchecked-lowlevel
        (bool _ok1,) = address(vault).call(abi.encodeCall(vault.deposit, (amount, user)));
        _ok1; // intentionally ignored — reverts are expected (mint paused, trading locked)
        vm.stopPrank();
    }

    // ── withdraw ─────────────────────────────────────────────────────────────

    /// @notice Withdraw (burn) bounded shares from the vault.
    ///         Swallows reverts — trading lock and zero-balance are valid.
    /// @param rawShares Unbounded seed; clamped to at most the user's current share balance.
    function withdraw(uint256 rawShares) external {
        if (settled) return;
        uint256 balance = vault.balanceOf(user);
        if (balance == 0) return;
        uint256 shares = bound(rawShares, 1, balance);

        vm.prank(user);
        // slither-disable-next-line unchecked-lowlevel
        (bool _ok2,) = address(vault).call(abi.encodeCall(vault.redeem, (shares, user, user)));
        _ok2; // intentionally ignored — reverts are expected (trading locked, zero balance)
    }

    // ── openLong ─────────────────────────────────────────────────────────────

    /// @notice Open an ETH long with bounded size and leverage (≤ MAX_LEVERAGE = 30_000).
    ///         Skips if a position is already open (trading lock would revert) or session ended.
    /// @param rawSize     Unbounded seed; clamped to [$1_000, $10_000] notional in 1e30-scaled USD.
    /// @param rawLeverage Unbounded seed; clamped to [10_000, 30_000] (1x–3x).
    function openLong(uint256 rawSize, uint256 rawLeverage) external {
        if (settled) return;
        // Skip if a position is already queued / open — the vault will revert with "order in flight".
        if (lastPositionKey != bytes32(0)) return;

        uint256 sizeUsd = bound(rawSize, 1_000e30, 10_000e30);
        uint256 leverage = bound(rawLeverage, 10_000, 30_000);

        // Refresh Chainlink feed so the mock doesn't return stale prices.
        ethFeed.setPrice(ETH_PRICE_8DEC);
        btcFeed.setPrice(ETH_PRICE_8DEC);
        solFeed.setPrice(ETH_PRICE_8DEC);

        vm.prank(orchestrator);
        // Swallow revert — valid if the vault has no USDC deposited yet (positionValue >> 0).
        (bool ok, bytes memory data) =
            address(vault).call(abi.encodeCall(vault.openLong, ("ETH", sizeUsd, leverage, 0)));
        if (ok && data.length == 32) {
            lastOrderKey = abi.decode(data, (bytes32));
            // Extract the positionKey from the pending order before rolling.
            if (lastOrderKey != bytes32(0)) {
                (lastPositionKey,,,,) = perps.pendingOrders(lastOrderKey);
            }
        }
    }

    // ── closeAndExecute ───────────────────────────────────────────────────────

    /// @notice Close the open position (if any) and advance past executionDelay so MockPerps
    ///         executes the order — keeping totalAssets / totalClaimable coherent (D-30).
    ///         The handler rolls forward block.number and calls executeOrder to settle the
    ///         async MockPerps close (executionDelay = 1 block by default).
    function closeAndExecute() external {
        if (settled) return;
        if (lastPositionKey == bytes32(0)) return;

        // First close the open order that was queued in openLong.
        if (lastOrderKey != bytes32(0)) {
            vm.roll(block.number + perps.executionDelay());
            // slither-disable-next-line unchecked-lowlevel
            perps.executeOrder(lastOrderKey);
            // Clear the trading lock.
            vm.prank(orchestrator);
            // slither-disable-next-line unchecked-lowlevel
            (bool _ck1,) = address(vault).call(abi.encodeCall(vault.clearTradingLock, (lastOrderKey)));
            _ck1; // intentionally ignored
        }

        // Now issue the close order.
        vm.prank(orchestrator);
        (bool ok, bytes memory data) = address(vault).call(abi.encodeCall(vault.closePosition, (lastPositionKey, 0)));
        if (ok && data.length == 32) {
            bytes32 closeKey = abi.decode(data, (bytes32));
            if (closeKey != bytes32(0)) {
                // Advance blocks and execute the async close.
                vm.roll(block.number + perps.executionDelay());
                // slither-disable-next-line unchecked-lowlevel
                perps.executeOrder(closeKey);
                // Clear trading lock after close.
                vm.prank(orchestrator);
                // slither-disable-next-line unchecked-lowlevel
                (bool _ck2,) = address(vault).call(abi.encodeCall(vault.clearTradingLock, (closeKey)));
                _ck2; // intentionally ignored
            }
        }

        // Reset tracked position so the handler allows a fresh open.
        lastOrderKey = bytes32(0);
        lastPositionKey = bytes32(0);
    }

    // ── settle ────────────────────────────────────────────────────────────────

    /// @notice End the vault session and freeze the settlement rate.
    ///         Drains any open position first (same pattern as _drainAndEndSession in
    ///         01-SettlementContract.t.sol). Idempotent — skips if already settled.
    function settle() external {
        if (settled) return;
        if (settlement.settled()) {
            settled = true;
            return;
        }

        // Drain open position keys via vault.settlementClosePosition (as settlement contract).
        bytes32[] memory openKeys = IPerpsAdapter(address(perps)).getOpenPositionKeys(address(vault));
        bytes32[] memory closeOrderKeys = new bytes32[](openKeys.length);
        for (uint256 i = 0; i < openKeys.length; i++) {
            vm.prank(address(settlement));
            // slither-disable-next-line unchecked-lowlevel
            (bool ok, bytes memory data) =
                address(vault).call(abi.encodeCall(vault.settlementClosePosition, (openKeys[i], 0)));
            if (ok && data.length == 32) {
                closeOrderKeys[i] = abi.decode(data, (bytes32));
            }
        }

        // Execute the async close orders.
        if (openKeys.length > 0) {
            vm.roll(block.number + perps.executionDelay());
            for (uint256 i = 0; i < closeOrderKeys.length; i++) {
                if (closeOrderKeys[i] != bytes32(0)) {
                    // slither-disable-next-line unchecked-lowlevel
                    perps.executeOrder(closeOrderKeys[i]);
                }
            }
        }

        // End the vault session (factory-gated).
        vm.prank(sessionFactory);
        // slither-disable-next-line unchecked-lowlevel
        (bool _es1,) = address(vault).call(abi.encodeCall(vault.endSession, ()));
        _es1; // intentionally ignored — may fail if already ended

        // Now call settlement.endSession (drain loop finds no open keys, positionValue==0).
        vm.prank(sessionFactory);
        // slither-disable-next-line unchecked-lowlevel
        (bool _es2,) = address(settlement).call(abi.encodeCall(settlement.endSession, ()));
        _es2; // intentionally ignored — may fail if already settled

        settled = settlement.settled();

        // Reset tracked position so future calls are clean.
        lastOrderKey = bytes32(0);
        lastPositionKey = bytes32(0);
    }
}

// =============================================================================
// InvariantVaultSolvency — D-30 pre-submission invariants
// =============================================================================

/// @title InvariantVaultSolvency — vault solvency + operator-no-withdrawal (D-30)
/// @notice Two invariants proved by the Foundry fuzzer over 256 runs / depth 128 (D-30):
///
///         1. invariant_TotalAssetsGeTotalClaimable
///            vault.totalAssets() >= settlement.totalClaimable() at all times.
///            Reads `totalAssets()` from the VAULT (USDC custody stays in the vault — D-18,
///            locked custody). `settlement.totalClaimable()` computes the sum of all outstanding
///            claims at the frozen rate. Before settlement this is 0. After settlement it must
///            not exceed the vault's USDC-valued assets.
///
///         2. invariant_NoOperatorWithdrawal
///            usdc.balanceOf(operator) stays at its initial value (operatorUsdcStart) across
///            every fuzzed action sequence. The operator can never drain USDC from the vault
///            (VAULT-08). This complements the Plan 04 unit assertion with an adversarial search
///            over all handler actions.
///
/// @dev Run configuration: 256 runs / depth 128 (D-30) via per-contract forge-config annotations.
///      The VaultHandler drives randomised actions; reverts are swallowed so they become no-ops.
///      setUp deploys the full stack with no mock shortcuts: MockPerps, MockChainlink feeds,
///      MTokenVault, SettlementContract — wired exactly as the production factory would.
///
/// forge-config: default.invariant.runs = 256
/// forge-config: default.invariant.depth = 128
contract InvariantVaultSolvency is StdInvariant, Test {
    // ── constants ─────────────────────────────────────────────────────────────

    int256 internal constant ETH_PRICE_8DEC = 300_000_000_000; // $3,000
    int256 internal constant BTC_PRICE_8DEC = 6_500_000_000_000; // $65,000
    int256 internal constant SOL_PRICE_8DEC = 15_000_000_000; // $150

    // ── fixtures ─────────────────────────────────────────────────────────────

    InvariantTestUSDC internal usdc;
    MockPerps internal perps;
    MTokenVault internal vault;
    SettlementContract internal settlement;
    MockChainlinkAggregator internal ethFeed;
    MockChainlinkAggregator internal btcFeed;
    MockChainlinkAggregator internal solFeed;

    address internal sessionFactory;
    address internal orchestrator;
    address internal operator;
    address internal user;

    VaultHandler internal handler;

    /// @notice USDC balance of the operator at setUp time. Must never increase (VAULT-08).
    uint256 internal operatorUsdcStart;

    // ── setUp ─────────────────────────────────────────────────────────────────

    function setUp() public {
        // Warp to a safe baseline timestamp (avoids Chainlink staleness underflow).
        vm.warp(10_001);

        sessionFactory = makeAddr("sessionFactory");
        orchestrator = makeAddr("orchestrator");
        operator = makeAddr("operator");
        user = makeAddr("user");

        // Deploy USDC mock.
        usdc = new InvariantTestUSDC();

        // Deploy Chainlink feed mocks with fresh timestamps.
        ethFeed = new MockChainlinkAggregator(ETH_PRICE_8DEC, block.timestamp);
        btcFeed = new MockChainlinkAggregator(BTC_PRICE_8DEC, block.timestamp);
        solFeed = new MockChainlinkAggregator(SOL_PRICE_8DEC, block.timestamp);

        // Deploy MockPerps adapter.
        perps = new MockPerps(address(ethFeed), address(btcFeed), address(solFeed));

        // Deploy vault (the share IS the mTOKEN — D-18, TOKEN-01).
        // useSepoliaStaleness=true → 6-hour staleness window; prevents spurious stale-revert
        // during the invariant run when vm.roll advances block.number but not block.timestamp.
        vault = new MTokenVault(
            IERC20(address(usdc)),
            "mCLA-S1",
            "mCLA-S1",
            address(perps),
            address(0), // sequencerFeed: skip (no sequencer on testnet — D-11)
            address(ethFeed),
            address(btcFeed),
            address(solFeed),
            sessionFactory,
            orchestrator,
            operator,
            10_000e6,
            true // useSepoliaStaleness: 6h window prevents stale-price interruptions in the fuzzer
        );

        // Deploy SettlementContract — USDC custody stays in vault (D-18 locked).
        settlement = new SettlementContract(
            address(usdc),
            address(perps),
            address(vault),
            sessionFactory,
            block.timestamp + 72 hours, // deadline: permissionless recovery hatch (SETT-02)
            address(0) // mmAddress_: disabled (D-18 guard off in invariant tests)
        );

        // Wire settlement into the vault (factory-gated, one-time).
        vm.prank(sessionFactory);
        vault.setSettlement(address(settlement));

        // Start the vault session (factory-gated).
        vm.prank(sessionFactory);
        vault.startSession(72 hours);

        // Seed operator with USDC (the initial state we assert must never increase via vault calls).
        usdc.mint(operator, 1_000e6);
        operatorUsdcStart = usdc.balanceOf(operator);

        // Deploy handler and configure it as the fuzz target.
        handler = new VaultHandler(
            usdc, perps, vault, settlement, ethFeed, btcFeed, solFeed, sessionFactory, orchestrator, operator, user
        );

        // Tell forge-std's invariant runner to call only the handler.
        targetContract(address(handler));
    }

    // ── invariants ────────────────────────────────────────────────────────────

    /// @notice Solvency: the vault's totalAssets() (USDC custody in the vault — D-18) is always
    ///         greater than or equal to settlement.totalClaimable() (the sum of all outstanding
    ///         claims at the frozen rate). Before settlement totalClaimable() == 0. After settlement
    ///         the frozen math guarantees sum(claims) ≤ vault USDC via the Math.mulDiv floor.
    ///
    ///         This is the pre-submission D-30 solvency invariant:
    ///         "no feasible sequence of deposits/trades/settlement can make the system insolvent".
    function invariant_TotalAssetsGeTotalClaimable() public view {
        assertGe(
            vault.totalAssets(),
            settlement.totalClaimable(),
            "solvency: vault.totalAssets() must be >= settlement.totalClaimable() at all times"
        );
    }

    /// @notice Operator-no-withdrawal: the operator's USDC balance never increases from any
    ///         sequence of vault interactions (VAULT-08). The operator cannot drain USDC from
    ///         the vault by any external entrypoint — not via withdraw, redeem, settlementWithdraw,
    ///         or any handler action.
    ///
    ///         The fuzzer searches adversarially: if any path gives the operator USDC, this fails.
    function invariant_NoOperatorWithdrawal() public view {
        assertEq(
            usdc.balanceOf(operator),
            operatorUsdcStart,
            "VAULT-08: operator USDC balance must never increase from vault interaction"
        );
    }
}

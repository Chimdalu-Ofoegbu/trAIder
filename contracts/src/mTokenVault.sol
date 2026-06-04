// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {ERC4626} from "@openzeppelin/contracts/token/ERC20/extensions/ERC4626.sol";
import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import {IERC4626} from "@openzeppelin/contracts/interfaces/IERC4626.sol";
import {Math} from "@openzeppelin/contracts/utils/math/Math.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {ReentrancyGuardTransient} from "@openzeppelin/contracts/utils/ReentrancyGuardTransient.sol";
import {IMTokenVault} from "./interfaces/IMTokenVault.sol";
import {IPerpsAdapter} from "./interfaces/IPerpsAdapter.sol";
import {IPerformanceOracle} from "./interfaces/IPerformanceOracle.sol";

/// @title MTokenVault — trAIder ERC-4626 vault whose share IS the tradeable mTOKEN (TOKEN-01, D-18)
/// @notice Each model (Claude/GPT/Gemini) gets one vault deployed by SessionFactory.
///         The vault's OZ ERC-4626 share token IS the mTOKEN (e.g. "mCLA-S1") — there is
///         NO separate standalone mToken contract (D-18). Satisfies TOKEN-01 because ERC-4626
///         is itself an ERC-20 whose mint/burn is vault-controlled.
///
///         Carries all eight VAULT guards:
///           VAULT-01 — _decimalsOffset()=12 (donation/inflation defense, OZ v5.4)
///           VAULT-02 — Chainlink NAV with per-feed staleness + sequencer-uptime gate
///           VAULT-03 — per-block NAV cache (same-block reads byte-identical)
///           VAULT-04 — 3x leverage cap enforced here, never in adapters (D-17)
///           VAULT-05 — 30% circuit breaker: mint paused, burn stays live
///           VAULT-06 — trading lock: deposit/withdraw revert during in-flight order
///           VAULT-07 — startSession/endSession callable only by SessionFactory
///           VAULT-08 — operator cannot withdraw vault USDC by any external entrypoint
///
/// @dev Inherits: ERC4626 (OZ v5.4) > ERC20 > ERC20Permit > IERC4626
///               ReentrancyGuardTransient (Cancun tstore/tload, EIP-1153)
///               IMTokenVault
///
///      Share token name/symbol are set to the session ticker at construction, e.g. "mCLA-S1".
///      USDC custody stays IN the vault through settlement — there is NO separate custodian.
///      The only USDC-out paths are: ERC4626 holder withdraw/redeem, and the gated
///      settlementWithdraw (settlement-only AND post-_sessionEnded). VAULT-08 sanctioned.
contract MTokenVault is ERC4626, ReentrancyGuardTransient, IMTokenVault {
    using SafeERC20 for IERC20;

    // =========================================================================
    // Constants
    // =========================================================================

    /// @notice 3x leverage cap in 1e4-scaled bps (D-17). Enforced ONLY in the vault —
    ///         adapters trust this check and do NOT re-implement it.
    uint256 public constant MAX_LEVERAGE = 30_000;

    /// @notice 30% circuit breaker threshold in bps relative to INITIAL_NAV_E18 (VAULT-05).
    uint256 public constant CIRCUIT_BREAKER_BPS = 3_000;

    /// @notice Initial NAV is definitionally 1:1 by design (Pitfall 2 avoidance).
    ///         Set as a constant so the circuit breaker floor is 0.3e18 regardless of
    ///         deposit timing. No snapshot at startSession is needed.
    uint256 public constant INITIAL_NAV_E18 = 1e18;

    /// @notice Per-feed MAX_STALENESS derived from Chainlink heartbeat + margin.
    ///         Mainnet values (D-12). Sepolia uses MAX_STALENESS_SEP for all feeds.
    uint256 public constant MAX_STALENESS_ETH = 4_500; // 3600s heartbeat + 900s margin
    uint256 public constant MAX_STALENESS_BTC = 90_000; // 86400s heartbeat + 3600s margin
    uint256 public constant MAX_STALENESS_SOL = 90_000; // 86400s heartbeat + 3600s margin
    uint256 public constant MAX_STALENESS_SEP = 21_600; // Sepolia fallback: 6h all feeds

    /// @notice Post-restart grace period before mint re-enables after sequencer recovery (D-11).
    uint256 public constant SEQUENCER_GRACE_PERIOD = 3_600; // 1 hour (Chainlink recommendation)

    /// @notice Seconds after a feed crosses MAX_STALENESS before the GRACE_WINDOW opens.
    uint256 public constant GRACE_WINDOW = 60;

    /// @notice Seconds after GRACE_WINDOW before trading+mint session auto-pause (D-10).
    uint256 public constant ESCALATION_THRESHOLD = 600; // 10 minutes

    // =========================================================================
    // Immutable addresses
    // =========================================================================

    /// @notice Perps adapter — positionValueUSDC feeds totalAssets, openLong/Short route here.
    address public immutable adapter;

    /// @notice Chainlink ETH/USD feed (Arbitrum One: 0x639Fe6ab55C921f74e7fac1ee960C0B6293ba612).
    address public immutable ETH_FEED;

    /// @notice Chainlink BTC/USD feed (Arbitrum One: 0x6ce185560a4963c47a8Ec16f4EF5d62A0000E708).
    address public immutable BTC_FEED;

    /// @notice Chainlink SOL/USD feed (Arbitrum One: 0x24ceA4b8ce57cdA5058b924B9B9987992450590c).
    address public immutable SOL_FEED;

    /// @notice Chainlink Arbitrum sequencer uptime feed.
    ///         Pass address(0) for Sepolia (no sequencer feed — D-11, skips the check).
    address public immutable SEQUENCER_UPTIME_FEED;

    /// @notice The SessionFactory that deployed this vault (VAULT-07).
    address public immutable sessionFactory;

    /// @notice The orchestrator key that may call openLong/openShort/closePosition.
    ///         Distinct from operator (which must NOT withdraw USDC — VAULT-08).
    address public immutable orchestrator;

    /// @notice The operator key that funds/runs the session but CANNOT withdraw USDC (VAULT-08).
    address public immutable operator;

    /// @notice If true, uses MAX_STALENESS_SEP for all feeds (Arbitrum Sepolia testnet).
    bool public immutable useSepoliaStaleness;

    // =========================================================================
    // State — session
    // =========================================================================

    /// @notice True during an active trading session (set by startSession, cleared by endSession).
    bool public sessionActive;

    /// @notice True once endSession has been called (enables settlementWithdraw).
    bool public sessionEnded;

    /// @notice Session start timestamp (unix, for time-remaining display).
    uint256 public sessionStart;

    /// @notice Session duration in seconds (set by startSession).
    uint256 public sessionDuration;

    // =========================================================================
    // State — settlement hooks (D-18, TOKEN-01)
    // =========================================================================

    /// @notice The registered SettlementContract. Set once via setSettlement (factory-gated).
    ///         The settlement contract calls settlementBurn to burn shares and
    ///         settlementWithdraw to pay USDC from the vault. No other external burn/USDC-out
    ///         beyond ERC4626 withdraw/redeem (VAULT-08 sanctioned).
    address public settlement;

    // =========================================================================
    // State — NAV cache (VAULT-03)
    // =========================================================================

    /// @dev Per-block NAV cache. Two nav() reads in the same block return byte-identical values.
    ///      Written only from state-changing paths (deposit/withdraw/open/close).
    ///      The view nav() recomputes _computeNav() if cache is stale — same inputs → same output.
    struct NavCache {
        uint128 navE18; // cached NAV in 1e18-scaled
        uint64 blockNumber; // block at which cache was written
        uint64 _pad; // padding for slot alignment
    }

    NavCache private _navCache;

    // =========================================================================
    // State — staleness machine (D-10, VAULT-02)
    // =========================================================================

    /// @dev Last successful NAV compute when all feeds were fresh. Used for burn path.
    uint256 private _lastGoodNavE18;

    /// @dev Timestamp when the first feed crossed its MAX_STALENESS (0 = not stale).
    uint256 private _stalenessCrossedAt;

    /// @dev Last successfully-read adapter position value (USDC, 6 decimals).
    ///      Updated every time positionValueUSDC() succeeds on a state-changing path.
    ///      Used as last-known-good fallback in totalAssets() when the adapter reverts
    ///      (e.g., stale Chainlink feed inside the adapter). Implements CONTRACTS-08:
    ///      burn/exit stays live on last-known-good NAV when the oracle is stale.
    uint256 private _lastGoodPositionValueUSDC;

    // =========================================================================
    // State — circuit breaker + trading (VAULT-05, VAULT-06)
    // =========================================================================

    /// @dev True once the circuit breaker trips (NAV < 30% of INITIAL_NAV_E18). Never resets.
    bool private _mintPaused;

    /// @dev True while an in-flight order is pending. Blocks deposit/withdraw (VAULT-06).
    bool private _tradingLocked;

    /// @dev True when staleness escalation auto-pauses the session (> ESCALATION_THRESHOLD stale).
    bool private _sessionPaused;

    // =========================================================================
    // State — performance counters (for getStats / ORACLE-01)
    // =========================================================================

    /// @dev Initial capital for PnL normalization in PerformanceOracle (passed at construction).
    uint256 public immutable initialCapitalUsdc;

    /// @dev Cumulative realized PnL in USD 1e18-scaled (signed). Updated by orchestrator via
    ///      recordClose (Phase 2). Zero in Phase 1.
    int256 private _realizedPnlUsd;

    /// @dev Maximum drawdown in basis points (peak-to-trough of NAV). Phase 2.
    uint256 private _maxDrawdownBps;

    /// @dev Number of closed positions with strictly positive realized PnL after fees. Phase 2.
    uint64 private _winningCloses;

    /// @dev Total closed positions. Phase 2.
    uint64 private _totalCloses;

    // =========================================================================
    // Events
    // =========================================================================

    /// @notice Emitted when a Chainlink feed crosses MAX_STALENESS.
    /// @param feed   Address of the stale Chainlink feed.
    /// @param updatedAt The feed's last updatedAt timestamp.
    /// @param stage  "grace" (within GRACE_WINDOW) or "escalate" (beyond ESCALATION_THRESHOLD).
    event OracleStale(address indexed feed, uint256 updatedAt, string stage);

    /// @notice Emitted when the 30% circuit breaker trips (VAULT-05).
    event CircuitBreakerTripped(uint256 indexed currentNavE18, uint256 indexed initialNavE18);

    /// @notice Emitted when setSettlement wires the SettlementContract.
    event SettlementSet(address indexed settlement);

    /// @notice Emitted when a session is started.
    event SessionStarted(uint256 durationSeconds, uint256 startedAt);

    /// @notice Emitted when a session ends.
    event SessionEnded(uint256 endedAt);

    // =========================================================================
    // Custom errors
    // =========================================================================

    /// @notice Mint/deposit reverted because a Chainlink feed is stale (D-10).
    error MintBlockedStaleFeed();

    /// @notice Trade/deposit reverted because the Arbitrum sequencer is down (D-11).
    error SequencerDown();

    /// @notice Trade/deposit reverted because the sequencer just restarted and the
    ///         SEQUENCER_GRACE_PERIOD has not elapsed (D-11).
    error SequencerGracePeriod();

    // =========================================================================
    // Modifiers
    // =========================================================================

    /// @dev Restricts startSession and setSettlement to the SessionFactory (VAULT-07).
    modifier onlySessionFactory() {
        require(msg.sender == sessionFactory, "Vault: only factory");
        _;
    }

    /// @dev Allows endSession to be called by SessionFactory OR the registered settlement.
    ///      WR-01/WR-05 fix: the SettlementContract must be able to put the vault into
    ///      settled mode as its FIRST action in endSession(), before any drain or burn.
    ///      This ensures maxWithdraw/maxRedeem return 0 during the drain window (blocking
    ///      normal ERC-4626 exits) and guarantees settlementBurn's sessionEnded guard is
    ///      satisfied during the claim() → settlementBurn → settlementWithdraw flow.
    ///      startSession and setSettlement remain factory-only (VAULT-07 preserved).
    modifier onlyFactoryOrSettlement() {
        require(
            msg.sender == sessionFactory || (settlement != address(0) && msg.sender == settlement),
            "Vault: only factory or settlement"
        );
        _;
    }

    /// @dev Restricts openLong, openShort, closePosition, clearTradingLock to the orchestrator.
    modifier onlyOrchestrator() {
        require(msg.sender == orchestrator, "Vault: only orchestrator");
        _;
    }

    // =========================================================================
    // Constructor
    // =========================================================================

    /// @notice Deploy the vault with a session ticker as its ERC-20 name/symbol (D-18, TOKEN-01).
    /// @dev The share token IS the tradeable mTOKEN — name_/symbol_ are the session ticker,
    ///      e.g. "mCLA-S1". There is NO standalone mToken contract (D-18). No mToken_ arg.
    ///      Passes name_/symbol_ to ERC20(name_, symbol_) so name() == symbol() == ticker.
    ///
    /// @param usdc            USDC token address (the ERC-4626 underlying asset).
    /// @param name_           ERC-20 name AND the session ticker (e.g. "mCLA-S1") — D-18.
    /// @param symbol_         ERC-20 symbol AND the session ticker (same string) — D-18.
    /// @param adapter_        IPerpsAdapter address (MockPerps in Phase 1, GMXAdapter in Phase 3).
    /// @param sequencerFeed_  Chainlink sequencer uptime feed (address(0) = skip, for Sepolia).
    /// @param ethFeed_        Chainlink ETH/USD feed.
    /// @param btcFeed_        Chainlink BTC/USD feed.
    /// @param solFeed_        Chainlink SOL/USD feed.
    /// @param sessionFactory_ SessionFactory address (only caller for start/endSession).
    /// @param orchestrator_   Orchestrator key for openLong/openShort/closePosition.
    /// @param operator_       Operator key — funds session; CANNOT withdraw USDC (VAULT-08).
    /// @param initialCapitalUsdc_  Seed capital for PnL normalization in PerformanceOracle.
    /// @param useSepoliaStaleness_ If true, uses MAX_STALENESS_SEP (6h) for all feeds.
    constructor(
        IERC20 usdc,
        string memory name_,
        string memory symbol_,
        address adapter_,
        address sequencerFeed_,
        address ethFeed_,
        address btcFeed_,
        address solFeed_,
        address sessionFactory_,
        address orchestrator_,
        address operator_,
        uint256 initialCapitalUsdc_,
        bool useSepoliaStaleness_
    ) ERC20(name_, symbol_) ERC4626(usdc) {
        // name_ and symbol_ ARE the session ticker — the share IS the mTOKEN (D-18, TOKEN-01)
        require(address(usdc) != address(0), "Vault: zero usdc");
        require(adapter_ != address(0), "Vault: zero adapter");
        require(sessionFactory_ != address(0), "Vault: zero factory");
        require(orchestrator_ != address(0), "Vault: zero orchestrator");
        require(operator_ != address(0), "Vault: zero operator");

        adapter = adapter_;
        SEQUENCER_UPTIME_FEED = sequencerFeed_;
        ETH_FEED = ethFeed_;
        BTC_FEED = btcFeed_;
        SOL_FEED = solFeed_;
        sessionFactory = sessionFactory_;
        orchestrator = orchestrator_;
        operator = operator_;
        initialCapitalUsdc = initialCapitalUsdc_ > 0 ? initialCapitalUsdc_ : 10_000e6;
        useSepoliaStaleness = useSepoliaStaleness_;

        // Seed last-good NAV to INITIAL_NAV_E18 so burn path has a safe fallback at genesis.
        _lastGoodNavE18 = INITIAL_NAV_E18;
    }

    // =========================================================================
    // ERC-4626 overrides — inflation defense + NAV
    // =========================================================================

    /// @notice Returns 12 so that mTOKEN (18 decimals) maps 1:1 in display to 1 USDC (6 decimals)
    ///         and the OZ v5.4 virtual-shares mechanism provides 1e12 inflation-attack defense
    ///         (VAULT-01, CLAUDE.md §3).
    /// @dev At offset=12 the first 1 USDC deposit mints 1e18 shares (display-1:1).
    ///      Virtual shares = 1e12 → first-deposit donation attack cannot profitably dilute depositor #2.
    function _decimalsOffset() internal pure override returns (uint8) {
        return 12;
    }

    /// @notice Returns total USDC-valued assets managed by the vault.
    /// @dev Overrides OZ base which only returns balanceOf. The perps position value (Chainlink-priced
    ///      via the adapter) MUST be included or NAV is understated during open positions (Pitfall 1).
    ///      totalAssets() is called internally by all ERC-4626 deposit/mint/withdraw/redeem paths.
    ///
    ///      REVERT-SAFE (CONTRACTS-08, CLAUDE.md §4): if the adapter reverts (e.g., stale Chainlink
    ///      feed inside positionValueUSDC), falls back to _lastGoodPositionValueUSDC so that
    ///      burn/withdraw/redeem NEVER revert due to oracle staleness. Mint asymmetry is preserved:
    ///      deposit/mint paths still revert via _requireFreshNavForMint() — this fallback only
    ///      benefits the burn path (VAULT-02/05, D-10).
    function totalAssets() public view virtual override(ERC4626, IERC4626) returns (uint256) {
        uint256 usdcBalance = IERC20(asset()).balanceOf(address(this));
        try IPerpsAdapter(adapter).positionValueUSDC(address(this)) returns (uint256 posVal) {
            return usdcBalance + posVal;
        } catch {
            // Adapter reverted (stale oracle, entry==0, etc.) — use last-known-good position value.
            // This ensures burn/withdraw/redeem stay live during stale oracle periods (CONTRACTS-08).
            return usdcBalance + _lastGoodPositionValueUSDC;
        }
    }

    // =========================================================================
    // NAV computation and cache (VAULT-02, VAULT-03)
    // =========================================================================

    /// @notice Returns the current NAV per mTOKEN in 1e18-scaled fixed-point (IMTokenVault).
    /// @dev Two calls in the same block return byte-identical values (VAULT-03 success criterion).
    ///      The _navCache is written only from state-changing paths; the view path calls
    ///      _computeNav() which is a pure function of totalAssets/totalSupply — same inputs
    ///      in the same block → byte-identical output. NAV uses only Chainlink-priced inputs
    ///      (via adapter.positionValueUSDC) — never a venue-internal price.
    ///      If no shares are outstanding, returns INITIAL_NAV_E18 (1:1 seed, Pitfall 2).
    function nav() external view override returns (uint256) {
        NavCache memory c = _navCache;
        if (c.blockNumber == block.number) return c.navE18;
        return _computeNav();
    }

    /// @dev Computes NAV from current totalAssets/totalSupply. Pure view — no storage writes.
    ///      Returns INITIAL_NAV_E18 when totalSupply is 0 (Pitfall 2 — no division by zero,
    ///      circuit breaker floor = 0.3e18 relative to the constant, not a snapshot).
    ///
    ///      NAV formula: totalAssets (6-dec USDC) * 10^_decimalsOffset() * 1e18 / totalSupply (18-dec shares)
    ///        = totalAssets * 1e12 * 1e18 / totalSupply = totalAssets * 1e30 / totalSupply.
    ///      Derivation at 1:1 seed (1000 USDC / 1000e18 shares):
    ///        = 1000e6 * 1e12 * 1e18 / 1000e18 = 1e9 * 1e30 / 1e21 = 1e18 ✓
    ///      The offset factor (1e12 = 10^_decimalsOffset()) normalises USDC 6-dec to the 18-dec
    ///      share decimal so that nav() == INITIAL_NAV_E18 at a true 1:1 USDC/mTOKEN NAV.
    function _computeNav() internal view returns (uint256) {
        uint256 supply = totalSupply();
        if (supply == 0) return INITIAL_NAV_E18;
        // Scale: totalAssets is 6-dec; shares are 18-dec. To express NAV in 1e18-scaled USD/share:
        //   nav = totalAssets * 10^offset * 1e18 / totalSupply (where offset = 12 for USDC)
        // Using 1e30 = 10^12 * 1e18. Math.mulDiv handles overflow safely.
        return Math.mulDiv(totalAssets(), 1e30, supply);
    }

    /// @dev Updates the per-block NAV cache from state-changing paths.
    ///      Also refreshes _lastGoodNavE18 and _lastGoodPositionValueUSDC if feeds are fresh.
    function _updateNavCache() internal returns (uint256 currentNav) {
        currentNav = _computeNav();
        // casting to 'uint128' is safe: NAV is 1e18-scaled and totalSupply >> 0,
        // so nav() < 2^128 at any realistic token supply.
        // forge-lint: disable-next-line(unsafe-typecast)
        _navCache = NavCache({navE18: uint128(currentNav), blockNumber: uint64(block.number), _pad: 0});
        if (_stalenessCrossedAt == 0) {
            _lastGoodNavE18 = currentNav;
            // Snapshot the current position value so the burn path has a fresh last-known-good
            // to fall back on if the adapter later reverts due to oracle staleness (CONTRACTS-08).
            // The try/catch mirrors totalAssets() — if positionValueUSDC reverts here, keep the
            // existing _lastGoodPositionValueUSDC unchanged.
            try IPerpsAdapter(adapter).positionValueUSDC(address(this)) returns (uint256 posVal) {
                _lastGoodPositionValueUSDC = posVal;
            } catch {}
        }
    }

    /// @dev Returns NAV to use on the BURN path. Never reverts (VAULT-02, CONTRACTS-08).
    ///      If any feed is stale, returns last-known-good NAV so exits are always possible.
    ///
    ///      NOTE: With totalAssets() now revert-safe (try/catch over positionValueUSDC),
    ///      _computeNav() itself no longer reverts. This function is kept for explicit
    ///      documentation of the burn-path semantics and used internally in the NAV view path.
    ///      _stalenessCrossedAt == 0 means feeds are fresh — use live NAV (totalAssets is live).
    ///      _stalenessCrossedAt > 0 means feeds are stale — totalAssets() falls back to
    ///      _lastGoodPositionValueUSDC, making _computeNav() return a last-good-based NAV.
    ///      Either path is safe for exits; the distinction exists only for the mint gate.
    function _navForBurn() internal view returns (uint256) {
        // totalAssets() is now revert-safe — _computeNav() never reverts regardless of staleness.
        // The stale path uses _lastGoodPositionValueUSDC fallback inside totalAssets().
        return _computeNav();
    }

    // =========================================================================
    // Staleness machine (D-10, VAULT-02)
    // =========================================================================

    /// @dev Returns the effective MAX_STALENESS for a given feed address.
    ///      Uses MAX_STALENESS_SEP for all feeds on Sepolia; per-feed mainnet values otherwise.
    function _maxStalenessFor(address feed) internal view returns (uint256) {
        if (useSepoliaStaleness) return MAX_STALENESS_SEP;
        if (feed == ETH_FEED) return MAX_STALENESS_ETH;
        if (feed == BTC_FEED) return MAX_STALENESS_BTC;
        if (feed == SOL_FEED) return MAX_STALENESS_SOL;
        return MAX_STALENESS_SEP; // safe fallback for unknown feeds
    }

    /// @dev Checks all registered Chainlink feeds and updates the staleness state machine.
    ///      MUST be called from state-changing paths (deposit/mint, openLong/openShort) only —
    ///      NOT from view functions (Pitfall 4: staleness state cannot be written in a view).
    ///
    ///      If any feed is stale:
    ///        - Records _stalenessCrossedAt if not already set.
    ///        - Emits OracleStale with the appropriate stage string.
    ///        - If elapsed > ESCALATION_THRESHOLD, auto-pauses the session.
    ///      If all feeds are fresh: clears staleness state and refreshes _lastGoodNavE18.
    function _checkAndUpdateStaleness() internal {
        address[3] memory checkFeeds = [ETH_FEED, BTC_FEED, SOL_FEED];
        address staleFound = address(0);
        uint256 staleUpdatedAt = 0;

        for (uint256 i = 0; i < 3; i++) {
            address feed = checkFeeds[i];
            if (feed == address(0)) continue;

            // slither-disable-next-line unused-return
            (uint80 roundId,,, uint256 updatedAt, uint80 answeredInRound) = _latestRoundData(feed);
            // Skip feeds that return degenerate data (not registered / offline)
            if (updatedAt == 0) continue;
            if (answeredInRound < roundId) continue;

            uint256 maxStal = _maxStalenessFor(feed);
            // slither-disable-next-line timestamp
            if (block.timestamp > updatedAt && block.timestamp - updatedAt > maxStal) {
                staleFound = feed;
                staleUpdatedAt = updatedAt;
                break; // any single stale feed triggers the machine
            }
        }

        if (staleFound != address(0)) {
            // Mark stale if not already marked
            if (_stalenessCrossedAt == 0) {
                _stalenessCrossedAt = block.timestamp;
            }
            // Determine escalation stage
            uint256 elapsed = block.timestamp - _stalenessCrossedAt;
            if (elapsed >= ESCALATION_THRESHOLD) {
                if (!_sessionPaused) {
                    _sessionPaused = true;
                }
                emit OracleStale(staleFound, staleUpdatedAt, "escalate");
            } else {
                emit OracleStale(staleFound, staleUpdatedAt, "grace");
            }
        } else {
            // All feeds fresh — clear staleness state and refresh last-good NAV
            if (_stalenessCrossedAt != 0) {
                _stalenessCrossedAt = 0;
                _sessionPaused = false;
            }
            // Refresh last-good NAV using a try/catch in case the adapter still reverts
            // on an internal feed that just recovered at the vault level but not in the adapter
            // (e.g., a partial Chainlink recovery window). Keeps _checkAndUpdateStaleness safe
            // even when positionValueUSDC internally reverts (CR-01 protection).
            try this.totalAssets() returns (uint256) {
                _lastGoodNavE18 = _computeNav();
            } catch {
                // Adapter still reverts — keep existing _lastGoodNavE18, do not update.
                // _lastGoodPositionValueUSDC is updated from _updateNavCache() on the next
                // successful state-changing tx, so no stale value is locked in permanently.
            }
        }
    }

    /// @dev Reverts mint/deposit if any feed is stale (VAULT-02, D-10).
    ///      Also runs the sequencer check (D-11).
    function _requireFreshNavForMint() internal view {
        if (_stalenessCrossedAt > 0) revert MintBlockedStaleFeed();
        _checkSequencer();
    }

    // =========================================================================
    // Sequencer uptime gate (D-11, VAULT-02)
    // =========================================================================

    /// @dev Checks the Chainlink Arbitrum sequencer uptime feed before allowing mint.
    ///      Skipped if SEQUENCER_UPTIME_FEED == address(0) (Sepolia — no feed exists).
    ///      Reverts with SequencerDown if the sequencer is currently down (answer == 1).
    ///      Reverts with SequencerGracePeriod if the sequencer just restarted and the
    ///      SEQUENCER_GRACE_PERIOD (3600s) has not elapsed since startedAt.
    ///      Burn path NEVER calls _checkSequencer — exits stay live during sequencer outage.
    function _checkSequencer() internal view {
        if (SEQUENCER_UPTIME_FEED == address(0)) return;
        // slither-disable-next-line unused-return
        (, int256 seqAnswer,, uint256 seqStartedAt,) = _latestRoundData(SEQUENCER_UPTIME_FEED);
        if (seqAnswer == 1) revert SequencerDown();
        // slither-disable-next-line timestamp
        if (block.timestamp - seqStartedAt < SEQUENCER_GRACE_PERIOD) revert SequencerGracePeriod();
    }

    // =========================================================================
    // Circuit breaker (VAULT-05)
    // =========================================================================

    /// @dev Checks current NAV against the 30% circuit breaker floor. Trips once, never resets.
    ///      INITIAL_NAV_E18 = 1e18 (constant — no snapshot, Pitfall 2).
    ///      D-08: survived = !_mintPaused; once tripped, survival bonus is 0 even if NAV recovers.
    function _checkCircuitBreaker(uint256 currentNavE18) internal {
        if (_mintPaused) return; // already tripped — no double-emit
        if (currentNavE18 * 10_000 < INITIAL_NAV_E18 * CIRCUIT_BREAKER_BPS) {
            _mintPaused = true;
            emit CircuitBreakerTripped(currentNavE18, INITIAL_NAV_E18);
        }
    }

    /// @notice Permissionless circuit-breaker latch: anyone can call this to evaluate NAV
    ///         and permanently set _mintPaused if nav < 30% of INITIAL_NAV_E18.
    /// @dev Necessary because the CB check inside deposit() cannot persist state when the
    ///      transaction reverts. This function SUCCEEDS (does not revert), so state changes
    ///      persist. After this call, deposit() will hit `require(!_mintPaused)` and revert.
    ///      If NAV is healthy, this is a no-op. If CB already tripped, this is a no-op.
    ///      Callable by anyone — permissionless safety valve (no privilege escalation risk:
    ///      setting _mintPaused can only BLOCK minting, it cannot drain funds or create tokens).
    function checkAndLatchCircuitBreaker() external {
        _checkCircuitBreaker(_computeNav());
    }

    // =========================================================================
    // ERC-4626 deposit / mint / withdraw / redeem overrides (VAULT-05/06)
    // =========================================================================

    /// @notice Deposit USDC and receive mTOKEN shares (standard ERC-4626).
    /// @dev Guards applied (MUST pass in order):
    ///        1. nonReentrant (T-1-reentrancy)
    ///        2. Trading lock — revert if in-flight order (VAULT-06)
    ///        3. Staleness state update (D-10)
    ///        4. Fresh-NAV gate — revert if any feed stale (VAULT-02)
    ///        5. Circuit breaker check — revert if NAV < 30% initial (VAULT-05)
    ///        6. Mint-paused gate
    ///        7. Session-paused gate (staleness escalation)
    ///      Burn path (withdraw/redeem) uses a symmetric but REDUCED gate set — no staleness revert.
    function deposit(uint256 assets, address receiver)
        public
        override(ERC4626, IERC4626)
        nonReentrant
        returns (uint256)
    {
        require(!_tradingLocked, "Vault: order in flight");
        _checkAndUpdateStaleness();
        _requireFreshNavForMint();
        uint256 currentNav = _updateNavCache();
        _checkCircuitBreaker(currentNav);
        require(!_mintPaused, "Vault: mint paused");
        require(!_sessionPaused, "Vault: session paused");
        return super.deposit(assets, receiver);
    }

    /// @notice Mint exact shares and receive them (standard ERC-4626).
    /// @dev Applies the same mint guards as deposit (VAULT-05/06).
    function mint(uint256 shares, address receiver) public override(ERC4626, IERC4626) nonReentrant returns (uint256) {
        require(!_tradingLocked, "Vault: order in flight");
        _checkAndUpdateStaleness();
        _requireFreshNavForMint();
        uint256 currentNav = _updateNavCache();
        _checkCircuitBreaker(currentNav);
        require(!_mintPaused, "Vault: mint paused");
        require(!_sessionPaused, "Vault: session paused");
        return super.mint(shares, receiver);
    }

    /// @notice Withdraw USDC by burning shares (standard ERC-4626).
    /// @dev Burns stay live regardless of staleness or circuit breaker state (VAULT-02/05,
    ///      CONTRACTS-08). Trading lock still applies (no withdrawal at stale mid-trade NAV).
    function withdraw(uint256 assets, address receiver, address owner)
        public
        override(ERC4626, IERC4626)
        nonReentrant
        returns (uint256)
    {
        require(!_tradingLocked, "Vault: order in flight");
        _updateNavCache();
        return super.withdraw(assets, receiver, owner);
    }

    /// @notice Redeem shares for USDC (standard ERC-4626).
    /// @dev Burns stay live regardless of staleness or circuit breaker state (VAULT-02/05).
    function redeem(uint256 shares, address receiver, address owner)
        public
        override(ERC4626, IERC4626)
        nonReentrant
        returns (uint256)
    {
        require(!_tradingLocked, "Vault: order in flight");
        _updateNavCache();
        return super.redeem(shares, receiver, owner);
    }

    // =========================================================================
    // VAULT-08 — operator-no-withdraw overrides
    // =========================================================================

    /// @notice Returns 0 for the operator (VAULT-08 — operator cannot withdraw USDC directly).
    ///         Also returns 0 post-settlement so only the settlementWithdraw path is used.
    function maxWithdraw(address owner) public view virtual override(ERC4626, IERC4626) returns (uint256) {
        if (owner == operator) return 0;
        if (sessionEnded) return 0;
        return super.maxWithdraw(owner);
    }

    /// @notice Returns 0 for the operator (VAULT-08). Also 0 post-settlement.
    function maxRedeem(address owner) public view virtual override(ERC4626, IERC4626) returns (uint256) {
        if (owner == operator) return 0;
        if (sessionEnded) return 0;
        return super.maxRedeem(owner);
    }

    // =========================================================================
    // Settlement hooks (TOKEN-01, D-18, VAULT-08)
    // =========================================================================

    /// @notice Wire the SettlementContract address. Factory-gated, one-time only.
    /// @dev Called by the SessionFactory in the same createSession tx (Plan 06).
    ///      After this, settlementBurn and settlementWithdraw gate on msg.sender == settlement.
    /// @param _settlement Address of the registered SettlementContract.
    function setSettlement(address _settlement) external onlySessionFactory {
        require(settlement == address(0), "Vault: settlement set");
        require(_settlement != address(0), "Vault: zero settlement");
        settlement = _settlement;
        emit SettlementSet(_settlement);
    }

    /// @notice Burns vault shares (the mTOKEN) on behalf of a holder during settlement.
    /// @dev Callable ONLY by the registered SettlementContract (TOKEN-01, D-18).
    ///      This is the ONLY external share-burn path beyond standard ERC4626 withdraw/redeem.
    ///      Plan 05's claim() calls this before paying USDC (CEI — burn before transfer).
    ///
    ///      CR-02: requires sessionEnded == true (symmetric with settlementWithdraw) so that
    ///      shares cannot be burned before the redemption rate is frozen. Without this guard,
    ///      a bug or malicious SettlementContract could burn all holder shares while
    ///      redemptionRate == 0, destroying shares without paying any USDC.
    ///
    ///      Note: sessionEnded is set by endSession() which is callable by sessionFactory OR
    ///      the registered settlement address (FIX 3 — WR-01/WR-05). This means
    ///      settlement.endSession() calls vault.endSession() as its first action, ensuring
    ///      sessionEnded == true before any drain+burn+pay sequence proceeds.
    /// @param from   Holder whose shares to burn.
    /// @param amount Number of shares to burn (in 18-decimal mTOKEN units).
    function settlementBurn(address from, uint256 amount) external {
        require(msg.sender == settlement, "Vault: not settlement");
        require(sessionEnded, "Vault: not settled"); // CR-02: symmetric with settlementWithdraw
        _burn(from, amount);
    }

    /// @notice Transfers USDC from the vault to a claimant during settlement.
    /// @dev Callable ONLY by the registered SettlementContract AND only after endSession.
    ///      USDC custody STAYS IN THE VAULT through settlement — this is the VAULT-08-sanctioned
    ///      exit: settlement-only AND post-_sessionEnded. The operator can never call this.
    ///      This is the ONLY USDC-out path beyond standard ERC4626 withdraw/redeem.
    /// @param to     Recipient of the USDC.
    /// @param amount Amount of USDC to transfer (6-decimal units).
    function settlementWithdraw(address to, uint256 amount) external {
        require(msg.sender == settlement, "Vault: not settlement");
        require(sessionEnded, "Vault: not settled");
        IERC20(asset()).safeTransfer(to, amount);
    }

    // =========================================================================
    // Trading — leverage cap + trading lock (VAULT-04, D-17, VAULT-06)
    // =========================================================================

    /// @notice Open a long perpetuals position. 3x cap enforced here — adapters trust this.
    /// @dev The leverage cap is the SINGLE enforcement point (D-17). Never in adapters.
    ///      Sets _tradingLocked=true until an OrderExecuted event clears it (VAULT-06).
    /// @param market     Venue-agnostic market identifier ("ETH", "BTC", or "SOL").
    /// @param sizeUsd    Position size in USD (1e30-scaled, GMX V2 precision).
    /// @param leverage   Leverage multiplier in 1e4-scaled bps (30_000 = 3x max).
    /// @param slippageBps Acceptable slippage in basis points.
    /// @return orderKey  Unique order key for async event tracking.
    function openLong(string calldata market, uint256 sizeUsd, uint256 leverage, uint256 slippageBps)
        external
        onlyOrchestrator
        nonReentrant
        returns (bytes32 orderKey)
    {
        require(leverage <= MAX_LEVERAGE, "Vault: leverage exceeds 3x cap");
        require(!_tradingLocked, "Vault: order in flight");
        _tradingLocked = true;
        return IPerpsAdapter(adapter).openLong(market, sizeUsd, leverage, slippageBps);
    }

    /// @notice Open a short perpetuals position. 3x cap enforced here — adapters trust this.
    /// @param market     Venue-agnostic market identifier ("ETH", "BTC", or "SOL").
    /// @param sizeUsd    Position size in USD (1e30-scaled).
    /// @param leverage   Leverage multiplier in 1e4-scaled bps (30_000 = 3x max).
    /// @param slippageBps Acceptable slippage in basis points.
    /// @return orderKey  Unique order key for async event tracking.
    function openShort(string calldata market, uint256 sizeUsd, uint256 leverage, uint256 slippageBps)
        external
        onlyOrchestrator
        nonReentrant
        returns (bytes32 orderKey)
    {
        require(leverage <= MAX_LEVERAGE, "Vault: leverage exceeds 3x cap");
        require(!_tradingLocked, "Vault: order in flight");
        _tradingLocked = true;
        return IPerpsAdapter(adapter).openShort(market, sizeUsd, leverage, slippageBps);
    }

    /// @notice Close an existing perpetuals position.
    /// @param positionKey The position key from the prior OrderExecuted event.
    /// @param sizeUsd     USD amount to close (1e30-scaled).
    /// @return orderKey   Unique order key for async event tracking.
    function closePosition(bytes32 positionKey, uint256 sizeUsd)
        external
        onlyOrchestrator
        nonReentrant
        returns (bytes32 orderKey)
    {
        require(!_tradingLocked, "Vault: order in flight");
        _tradingLocked = true;
        return IPerpsAdapter(adapter).closePosition(positionKey, sizeUsd);
    }

    /// @notice Clears the trading lock after the adapter emits OrderExecuted.
    /// @dev Callable by the orchestrator once the async order resolves (VAULT-06).
    ///      In production, the orchestrator monitors OrderExecuted events and calls this.
    /// @param orderKey The resolved order key (for logging/tracing).
    function clearTradingLock(bytes32 orderKey) external onlyOrchestrator {
        _tradingLocked = false;
        // orderKey emitted for off-chain indexing
        emit TradingLockCleared(orderKey);
    }

    /// @notice Emitted when the orchestrator clears the trading lock after OrderExecuted.
    event TradingLockCleared(bytes32 indexed orderKey);

    // =========================================================================
    // Session lifecycle (VAULT-07)
    // =========================================================================

    /// @notice Starts a new trading session. SessionFactory-only (VAULT-07).
    /// @dev Reverts if a session is already active (no double-start).
    ///      Called by the factory in the same createSession tx (Plan 06).
    /// @param durationSeconds Duration of the session in seconds (typically 259200 = 72h).
    function startSession(uint256 durationSeconds) external override onlySessionFactory {
        require(!sessionActive, "Vault: session already active");
        sessionActive = true;
        sessionStart = block.timestamp;
        sessionDuration = durationSeconds;
        emit SessionStarted(durationSeconds, block.timestamp);
    }

    /// @notice Ends the active trading session. Callable by the SessionFactory OR the
    ///         registered SettlementContract (WR-01 / WR-05 fix).
    /// @dev Sets sessionEnded=true (enables settlementWithdraw, blocks maxWithdraw/maxRedeem)
    ///      and clears sessionActive. Also clears _tradingLocked so any in-flight order
    ///      does NOT brick the settlement drain (WR-05: in-flight lock before settlement
    ///      cannot prevent settlementClosePosition from proceeding).
    ///
    ///      The settlement address is allowed to call this so SettlementContract.endSession()
    ///      can put the vault into settled mode as its FIRST action — before draining
    ///      positions and before any settlementBurn call. This satisfies:
    ///        - WR-01: maxWithdraw/maxRedeem→0 during the drain window (no race to redeem)
    ///        - WR-05: lock cleared so drain proceeds even if a trade was in flight
    ///        - CR-02: sessionEnded==true before settlementBurn can fire
    ///
    ///      startSession and setSettlement remain factory-only (VAULT-07 preserved).
    function endSession() external override onlyFactoryOrSettlement {
        require(sessionActive, "Vault: no active session");
        sessionActive = false;
        sessionEnded = true;
        // WR-05: clear any in-flight trading lock so the settlement drain can proceed
        // without being bricked by a pending order that the orchestrator never cleared.
        _tradingLocked = false;
        emit SessionEnded(block.timestamp);
    }

    // =========================================================================
    // Settlement drain hook (SETT-01)
    // =========================================================================

    /// @notice Closes a single open perpetuals position on behalf of the SettlementContract.
    /// @dev Gated to the registered settlement address (SETT-01). The settlement contract
    ///      calls this in its endSession drain loop — it cannot call the adapter directly
    ///      because the adapter gates on msg.sender == pos.vault (the vault IS the position
    ///      owner). This function bridges that call: settlement → vault → adapter (vault is
    ///      msg.sender to the adapter). Does NOT set _tradingLocked — settlement drains all
    ///      positions in one loop without needing per-order lock tracking.
    /// @param positionKey The position identifier from the prior OrderExecuted event.
    /// @param sizeUsd     USD amount to close, 1e30-scaled (pass 0 for full close in mock).
    /// @return orderKey   Unique order key for async tracking.
    function settlementClosePosition(bytes32 positionKey, uint256 sizeUsd) external returns (bytes32 orderKey) {
        require(msg.sender == settlement, "Vault: not settlement");
        return IPerpsAdapter(adapter).closePosition(positionKey, sizeUsd);
    }

    // =========================================================================
    // Stats (IMTokenVault, ORACLE-01)
    // =========================================================================

    /// @notice Returns the current performance snapshot for the PerformanceOracle.
    /// @dev Display-only — does NOT drive NAV. Full per-close PnL/drawdown accounting
    ///      lands in Phase 2 when the orchestrator can call recordClose.
    ///      In Phase 1: realizedPnlUsd=0, maxDrawdownBps=0, closes=0.
    ///      survived = !_mintPaused (D-08: circuit breaker trip sets this permanently false).
    function getStats() external view override returns (IPerformanceOracle.VaultStats memory stats) {
        stats.realizedPnlUsd = _realizedPnlUsd;
        stats.maxDrawdownBps = _maxDrawdownBps;
        stats.winningCloses = _winningCloses;
        stats.totalCloses = _totalCloses;
        stats.survived = !_mintPaused; // D-08: circuit breaker never resets
    }

    // =========================================================================
    // Chainlink read helper (Pattern from MockPerps._latestRoundData)
    // =========================================================================

    /// @dev Thin staticcall wrapper for AggregatorV3Interface.latestRoundData().
    ///      Same pattern as MockPerps.sol lines 483-495 — staticcall for interface safety.
    function _latestRoundData(address feed)
        internal
        view
        returns (uint80 roundId, int256 answer, uint256 startedAt, uint256 updatedAt, uint80 answeredInRound)
    {
        bytes memory data = abi.encodeWithSignature("latestRoundData()");
        (bool success, bytes memory result) = feed.staticcall(data);
        require(success, "Vault: feed call failed");
        (roundId, answer, startedAt, updatedAt, answeredInRound) =
            abi.decode(result, (uint80, int256, uint256, uint256, uint80));
    }
}

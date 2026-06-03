// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {Ownable} from "@openzeppelin/contracts/access/Ownable.sol";
import {IPerformanceOracle} from "./interfaces/IPerformanceOracle.sol";

/// @title PerformanceOracle — trAIder Coliseum Score computation (ORACLE-01/02)
/// @notice Computes the display-only Coliseum Score from a frozen `IPerformanceOracle.VaultStats`
///         snapshot. The score is PURE and stateless: it never reads vault NAV, token balances,
///         or any mutable on-chain state (ORACLE-01, D-43).
///
///         Score formula (ppm, D-01..D-09b):
///           scorePpm = pnlPpm·0.5 + inverseDrawdownPpm·0.2 + winRatePpm·0.2 + survivalPpm·0.1
///
///         where all components are in the range [0, 1_000_000] (parts per million, 1_000_000 = 1.0).
///
///         The score is intentionally one-directional: it is a downstream consumer of vault data
///         and MUST NOT be read by any NAV/mint/burn path. The Coliseum Score is display-only —
///         it surfaces model performance to spectators but has zero influence on vault economics.
///
///         Additional bookkeeping: the deployer may call `registerVault` to track which vaults
///         are active in the current session. This state is for UI indexing only and does not
///         affect score computation. `registerVaults` is gated behind `onlyOwner` so only the
///         SessionFactory (Plan 06) can populate the registry.
///
/// @dev NatSpec on every external function. Section dividers: `// ====`. No storage reads inside
///      `computeScore` or the exposed component wrappers — they are all `pure`.
///      Initial capital constant: $10_000 (INITIAL_CAPITAL_USD) is baked into the pnl component
///      normalization formula (D-09b). Changing it would break the locked reference values; do not
///      modify without a plan-level decision and a matching test update.
contract PerformanceOracle is Ownable {
    // =========================================================================
    // Constants
    // =========================================================================

    /// @dev Basis-point ceiling for max drawdown (100%). Drawdown >= this → 0 score (D-07).
    uint256 private constant MAX_DD_BPS = 10_000;

    /// @dev Parts-per-million denominator.
    uint256 private constant PPM = 1_000_000;

    /// @dev Clamp floor for returnBps (D-09b): -100% of $10k initial capital.
    int256 private constant RETURN_BPS_MIN = -10_000;

    /// @dev Clamp ceiling for returnBps (D-09b): +200% of $10k initial capital.
    int256 private constant RETURN_BPS_MAX = 20_000;

    /// @dev Normalisation range: RETURN_BPS_MAX - RETURN_BPS_MIN = 30_000.
    uint256 private constant RETURN_BPS_RANGE = 30_000;

    /// @dev Offset added before division: shifts [-10_000, +20_000] → [0, 30_000].
    uint256 private constant RETURN_BPS_OFFSET = 10_000;

    /// @dev 1e18 scaling factor for realizedPnlUsd (18-decimal USD representation).
    int256 private constant USD_SCALE = 1e18;

    /// @notice Win-rate neutral value when no trades have been closed (D-06).
    uint256 public constant NEUTRAL_WIN_RATE_PPM = 500_000;

    // =========================================================================
    // State
    // =========================================================================

    /// @notice Vaults registered for the current session (bookkeeping only, UI indexing).
    /// @dev Populated by SessionFactory (Plan 06) via registerVault. Does NOT affect scoring.
    mapping(address => bool) public registeredVaults;

    // =========================================================================
    // Events
    // =========================================================================

    /// @notice Emitted when a vault is added to the registry.
    /// @param vault The mTokenVault address that was registered.
    event VaultRegistered(address indexed vault);

    // =========================================================================
    // Constructor
    // =========================================================================

    constructor() Ownable(msg.sender) {}

    // =========================================================================
    // Admin — vault registry (bookkeeping, Plan 06)
    // =========================================================================

    /// @notice Registers a vault address for UI/session indexing.
    /// @dev Callable only by the owner (SessionFactory, Plan 06). Has no effect on scoring.
    ///      Re-registering an already-registered vault is a no-op (idempotent).
    /// @param vault The mTokenVault address to register.
    function registerVault(address vault) external onlyOwner {
        require(vault != address(0), "PerformanceOracle: zero vault address");
        if (!registeredVaults[vault]) {
            registeredVaults[vault] = true;
            emit VaultRegistered(vault);
        }
    }

    // =========================================================================
    // Core — Coliseum Score (ORACLE-01)
    // =========================================================================

    /// @notice Computes the Coliseum Score for a single model's session stats.
    /// @dev PURE: reads no storage, emits no events, has no side effects. The Solidity
    ///      compiler enforces this — any storage access would fail to compile (ORACLE-01, D-43).
    ///      Formula: scorePpm = (pnlPpm·500_000 + ddPpm·200_000 + wrPpm·200_000 + survPpm·100_000) / 1_000_000
    ///      Integer truncation is intentional — the error is at most 1 ppm per component.
    /// @param stats The session snapshot. Consumed but not stored.
    /// @return scorePpm Coliseum Score in parts per million [0, 1_000_000].
    function computeScore(IPerformanceOracle.VaultStats memory stats) external pure returns (uint256 scorePpm) {
        uint256 pnlPpm = _pnlComponent(stats.realizedPnlUsd);
        uint256 ddPpm = _drawdownComponent(stats.maxDrawdownBps);
        uint256 wrPpm = _winRateComponent(stats.winningCloses, stats.totalCloses);
        uint256 survPpm = stats.survived ? PPM : 0;

        // Weighted sum: weights (500_000 + 200_000 + 200_000 + 100_000) / 1_000_000 = 1.0
        scorePpm = (pnlPpm * 500_000 + ddPpm * 200_000 + wrPpm * 200_000 + survPpm * 100_000) / PPM;
    }

    // =========================================================================
    // Exposed component wrappers (pure — for per-component unit tests)
    // =========================================================================

    /// @notice Returns the PnL sub-component in ppm for a given realized PnL value.
    /// @dev Thin wrapper around `_pnlComponent`. Pure: no storage access.
    ///      Exposed so tests can pin exact ppm values at locked reference points (D-09b).
    /// @param realizedPnlUsd Cumulative realized PnL in 1e18-scaled USD (signed).
    /// @return pnlPpm PnL component in ppm [0, 1_000_000].
    function pnlComponent(int256 realizedPnlUsd) external pure returns (uint256 pnlPpm) {
        return _pnlComponent(realizedPnlUsd);
    }

    /// @notice Returns the win-rate sub-component in ppm.
    /// @dev Thin wrapper around `_winRateComponent`. Pure: no storage access.
    ///      Returns NEUTRAL_WIN_RATE_PPM (500_000) when totalCloses == 0 (D-06).
    /// @param winningCloses Number of closed positions with positive PnL after fees.
    /// @param totalCloses Total number of closed positions.
    /// @return wrPpm Win-rate component in ppm [0, 1_000_000].
    function winRateComponent(uint64 winningCloses, uint64 totalCloses) external pure returns (uint256 wrPpm) {
        return _winRateComponent(winningCloses, totalCloses);
    }

    /// @notice Returns the inverse-drawdown sub-component in ppm.
    /// @dev Thin wrapper around `_drawdownComponent`. Pure: no storage access.
    ///      0 bps drawdown → 1_000_000 ppm; 10_000 bps (100%) → 0 ppm (D-07).
    /// @param maxDrawdownBps Max peak-to-trough drawdown in basis points.
    /// @return ddPpm Drawdown component in ppm [0, 1_000_000].
    function drawdownComponent(uint256 maxDrawdownBps) external pure returns (uint256 ddPpm) {
        return _drawdownComponent(maxDrawdownBps);
    }

    // =========================================================================
    // Internal — component math
    // =========================================================================

    /// @dev PnL component (D-09b). Initial capital = $10_000 (baked in).
    ///      returnBps = realizedPnlUsd / 1e18  (18-dec USD → bps of $10k)
    ///      Clamped to [RETURN_BPS_MIN, RETURN_BPS_MAX] = [-10_000, +20_000].
    ///      pnlPpm = uint256(returnBps + RETURN_BPS_OFFSET) * PPM / RETURN_BPS_RANGE
    ///
    ///      Reference points (D-09b):
    ///        returnBps = -10_000 → 0 ppm          (−100%, clamp floor)
    ///        returnBps =      0 → 333_333 ppm      (breakeven)
    ///        returnBps = +10_000 → 666_666 ppm     (+100%)
    ///        returnBps = +20_000 → 1_000_000 ppm   (+200%, clamp ceiling)
    function _pnlComponent(int256 realizedPnlUsd) internal pure returns (uint256) {
        // Convert 1e18-scaled USD to returnBps (integer division — truncates toward zero)
        int256 returnBps = realizedPnlUsd / USD_SCALE;

        // Clamp to [-10_000, +20_000]
        if (returnBps < RETURN_BPS_MIN) returnBps = RETURN_BPS_MIN;
        if (returnBps > RETURN_BPS_MAX) returnBps = RETURN_BPS_MAX;

        // Shift to non-negative range [0, 30_000] then scale to ppm.
        // Both casts are safe: after clamping, returnBps ∈ [-10_000, +20_000] so
        // (returnBps + 10_000) ∈ [0, 30_000] — fits uint256 without overflow.
        // int256(RETURN_BPS_OFFSET) is a compile-time constant (10_000) — no truncation.
        // forge-lint: disable-next-line(unsafe-typecast)
        return uint256(returnBps + int256(RETURN_BPS_OFFSET)) * PPM / RETURN_BPS_RANGE;
    }

    /// @dev Win-rate component (D-06).
    ///      Returns NEUTRAL_WIN_RATE_PPM when totalCloses == 0 (zero-trade model, no division).
    ///      Otherwise: winningCloses * 1_000_000 / totalCloses.
    ///      Safe: division by totalCloses only when totalCloses > 0.
    function _winRateComponent(uint64 winningCloses, uint64 totalCloses) internal pure returns (uint256) {
        if (totalCloses == 0) return NEUTRAL_WIN_RATE_PPM;
        return uint256(winningCloses) * PPM / uint256(totalCloses);
    }

    /// @dev Inverse-drawdown component (D-07).
    ///      If maxDrawdownBps >= MAX_DD_BPS (>= 100%) → 0 ppm (clamp, no underflow).
    ///      Otherwise: (MAX_DD_BPS - maxDrawdownBps) * PPM / MAX_DD_BPS.
    ///      Reference: 0 bps → 1_000_000; 2_500 bps → 750_000; 10_000 bps → 0.
    function _drawdownComponent(uint256 maxDrawdownBps) internal pure returns (uint256) {
        if (maxDrawdownBps >= MAX_DD_BPS) return 0;
        return (MAX_DD_BPS - maxDrawdownBps) * PPM / MAX_DD_BPS;
    }
}

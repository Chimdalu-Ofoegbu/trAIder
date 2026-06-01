// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {IERC4626} from "@openzeppelin/contracts/interfaces/IERC4626.sol";
import {IPerformanceOracle} from "./IPerformanceOracle.sol";

/// @title IMTokenVault — trAIder model token vault interface (IFACE-01)
/// @notice Extends IERC4626 with trAIder-specific NAV, stats, and session hooks.
///         This interface is the frozen ABI surface consumed by:
///           - Phase 1: mTokenVault implementation
///           - Phase 2: orchestrator mock (reads nav(), getStats())
///           - Phase 5: frontend wagmi reads (nav(), asset(), share price)
/// @dev IERC4626 methods (asset, totalAssets, convertToShares, convertToAssets,
///      maxDeposit, previewDeposit, deposit, maxMint, previewMint, mint,
///      maxWithdraw, previewWithdraw, withdraw, maxRedeem, previewRedeem, redeem)
///      are inherited from IERC4626 and NOT redeclared here.
///
///      Implementation notes (Phase 1, NOT part of this interface):
///        - `_decimalsOffset()` MUST return 12 (CLAUDE.md §3 — OZ v5 inflation-attack defense,
///          makes mTOKEN 18-decimal while USDC is 6-decimal).
///        - Operator key cannot withdraw vault USDC directly (hard-coded in Phase 1 impl,
///          CLAUDE.md §"Operator key cannot withdraw vault USDC directly").
///        - stopLoss / takeProfit enforcement is deferred to Phase 1 (D-06).
///        - NAV uses Chainlink ETH/BTC/SOL USD feeds with staleness check, NEVER GMX
///          internal prices (avoids circular NAV dependency, CLAUDE.md §4).
interface IMTokenVault is IERC4626 {
    // =========================================================================
    // NAV
    // =========================================================================

    /// @notice Returns the current NAV per mTOKEN in 1e18-scaled fixed-point.
    /// @dev NAV = (totalUSDC + positionValueUSDC(adapter, vault)) / totalSupply,
    ///      where positionValueUSDC uses Chainlink mark prices with a staleness
    ///      check (VAULT-02, CLAUDE.md §4). NEVER uses GMX internal prices
    ///      (oracle-manipulation mitigation, spec §9.5).
    ///      If Chainlink price is stale, implementation SHOULD return the last
    ///      known-good NAV rather than reverting, to keep mint/burn live during
    ///      oracle outages (CONTRACTS-08 — burn stays active during circuit breaker).
    /// @return navPerToken1e18 NAV per mTOKEN in USD, 1e18-scaled.
    ///         At session start with 1 USDC seed per share: navPerToken1e18 = 1e18.
    function nav() external view returns (uint256 navPerToken1e18);

    // =========================================================================
    // Performance stats (PerformanceOracle feed)
    // =========================================================================

    /// @notice Returns the current performance snapshot for the PerformanceOracle.
    /// @dev PerformanceOracle (ORACLE-01) reads this view to compute the Coliseum Score.
    ///      The Coliseum Score is display-only and does NOT drive NAV (D-43).
    ///      Returned VaultStats struct is defined in IPerformanceOracle to avoid
    ///      circular imports between vault and oracle interfaces.
    ///      Win-rate semantics: see ORACLE-02 and IPerformanceOracle.VaultStats NatSpec.
    /// @return stats Current VaultStats snapshot.
    function getStats() external view returns (IPerformanceOracle.VaultStats memory stats);

    // =========================================================================
    // Session lifecycle (SessionFactory-only)
    // =========================================================================

    /// @notice Starts a new 72-hour trading session for this vault.
    /// @dev MUST revert if called by any address other than the SessionFactory (VAULT-07).
    ///      MUST revert if a session is already active.
    ///      Sets session start timestamp and durationSeconds for time-remaining display.
    ///      After this call the vault accepts deposits and the trading loop may begin.
    /// @param durationSeconds Duration of the trading session in seconds.
    ///        Typical value: 259200 (72 hours). Enforced by SessionFactory.
    function startSession(uint256 durationSeconds) external;

    /// @notice Ends the active trading session for this vault.
    /// @dev MUST revert if called by any address other than the SessionFactory (VAULT-07).
    ///      MUST revert if no session is active.
    ///      After this call, the vault stops accepting new deposits and the SettlementContract
    ///      may initiate pro-rata USDC distribution to mTOKEN holders (VAULT-08).
    ///      Funds exit only via SettlementContract or holder burn — the operator key
    ///      cannot withdraw vault USDC directly.
    function endSession() external;
}

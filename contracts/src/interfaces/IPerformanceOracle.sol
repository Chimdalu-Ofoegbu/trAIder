// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

/// @title IPerformanceOracle — trAIder performance data shared types
/// @notice Defines VaultStats struct consumed by IMTokenVault.getStats() and
///         PerformanceOracle (ORACLE-01). Keeping the struct here avoids a circular
///         import between the vault interface and the oracle interface.
/// @dev Struct field naming: UpperCamel type, camelCase fields (OZ + Foundry idiom, D-15).
///      Win-rate semantics (ORACLE-02): winningCloses = closed positions with strictly
///      positive realizedPnlUsd AFTER fees. Edge-case counting (partial fills, flips,
///      liquidations, funding, micro-trade minimum) is a Phase 1 operator decision and
///      intentionally NOT over-specified here.
interface IPerformanceOracle {
    /// @notice Performance snapshot emitted per trade cycle by IMTokenVault.
    /// @dev Read by PerformanceOracle to compute the on-chain Coliseum Score.
    ///      The Coliseum Score is display-only and does NOT drive NAV (D-43).
    ///      All USD values are in 1e18-scaled fixed-point (18 decimals).
    struct VaultStats {
        /// @notice Cumulative realized PnL in USD (1e18-scaled, signed).
        ///         Positive = profitable session, negative = loss.
        int256 realizedPnlUsd;
        /// @notice Maximum drawdown from peak NAV, expressed in basis points (1 bps = 0.01%).
        ///         Always >= 0. Computed as (peakNAV - troughNAV) / peakNAV * 10_000.
        uint256 maxDrawdownBps;
        /// @notice Number of closed positions with strictly positive realizedPnlUsd after fees.
        ///         See ORACLE-02 for exact semantics (Phase 1 decision).
        uint64 winningCloses;
        /// @notice Total number of closed positions (winning + losing).
        uint64 totalCloses;
        /// @notice True if the vault has not been force-settled due to NAV <= 0 or
        ///         emergency circuit-breaker (CONTRACTS-08). False once settlement triggered.
        bool survived;
        /// @notice Initial capital in USDC (6 decimals) used for PnL normalization.
        ///         WR-03 fix: PerformanceOracle._pnlComponent uses this to compute returnBps
        ///         correctly for any initial capital, not just the hardcoded $10,000 case.
        ///         Populated by MTokenVault.getStats() from immutable initialCapitalUsdc.
        ///         Defaults to 10_000e6 ($10,000) when the vault uses the factory default.
        uint256 initialCapitalUsdc;
    }
}

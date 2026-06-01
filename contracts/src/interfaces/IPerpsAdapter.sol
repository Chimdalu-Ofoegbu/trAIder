// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

/// @title IPerpsAdapter — trAIder venue-agnostic perpetuals interface (IFACE-02)
/// @notice Normalized interface implemented by both GMXAdapter (Phase 3) and
///         MockPerps (Phase 0 / cut line 1A). The vault and orchestrator interact
///         ONLY through this interface; venue internals never leak upstream (D-01).
///
///         Venue-agnostic design (D-01): The adapter implementation internalizes all
///         venue-specific mechanics (e.g., fee token wrapping, keeper fee parameters,
///         batch call encoding, and market-key encoding to venue addresses).
///         None of those appear in this interface — the vault sees only this surface.
///
///         Runtime swap (D-04): SessionFactory reads `PERPS_VENUE=gmx|mock` at deploy
///         time and wires the chosen adapter into each vault constructor. Swapping
///         venues = redeploy with new env var only (~10-minute budget, no governance
///         attack surface from a hot-swappable per-vault setter).
///
///         Markets supported (D-10): ETH, BTC, SOL. Passed as plain strings so the
///         adapter can map to venue-specific identifiers.
///         ARB perp is deferred to v2.
///
///         Async order pattern (D-02): openLong / openShort / closePosition return a
///         bytes32 orderKey immediately. The caller (orchestrator) subscribes to
///         OrderExecuted / OrderCancelled events filtered by orderKey to determine
///         final fill status. This mirrors GMX V2's two-step keeper execution flow
///         and prevents front-running: journals publish on OrderExecuted (spec §9.1),
///         NEVER on the initial order submission receipt.
interface IPerpsAdapter {
    // =========================================================================
    // Events — async order resolution (D-02)
    // =========================================================================

    /// @notice Emitted when a keeper (or mock) executes a pending order.
    /// @dev Orchestrator journals publish on this event, never on the order
    ///      submission receipt (front-running mitigation, spec §9.1).
    ///      Indexed fields allow efficient filter by orderKey and vault address.
    /// @param orderKey The bytes32 key returned by openLong / openShort / closePosition.
    /// @param vault The mTokenVault address that initiated the order.
    /// @param positionKey The on-chain position identifier after fill (venue-specific
    ///        encoding; used by closePosition on the next cycle).
    event OrderExecuted(
        bytes32 indexed orderKey,
        address indexed vault,
        bytes32 positionKey
    );

    /// @notice Emitted when a keeper (or mock) cancels a pending order.
    /// @dev Orchestrator marks the pending order as cancelled and does NOT publish
    ///      a journal entry for it (the order was never filled). Caller may retry.
    /// @param orderKey The bytes32 key returned by openLong / openShort / closePosition.
    /// @param vault The mTokenVault address that initiated the order.
    /// @param reason Human-readable cancellation reason (e.g., "insufficient margin",
    ///        "slippage exceeded", "keeper timeout").
    event OrderCancelled(
        bytes32 indexed orderKey,
        address indexed vault,
        string reason
    );

    // =========================================================================
    // Order creation — returns orderKey for async tracking (D-01 / D-02)
    // =========================================================================

    /// @notice Opens a long perpetual position for the calling vault.
    /// @dev Leverage is hard-capped at 3x by the consuming vault (VAULT-04) before
    ///      this call is made; the adapter accepts the param but vault enforces the cap.
    ///      The adapter implementation internalizes all venue-specific fee mechanics,
    ///      native token wrapping, batch call encoding, and market identifier mapping.
    ///      Caller does not manage any of these details.
    ///      For MockPerps: records position in storage and schedules OrderExecuted
    ///      emission after `executionDelay` blocks (D-13).
    /// @param market Venue-agnostic market identifier ("ETH", "BTC", or "SOL").
    ///        The adapter maps this to the venue-specific market address internally.
    /// @param sizeUsd Position size in USD, 1e30-scaled (GMX V2 precision standard).
    /// @param leverage Leverage multiplier in 1e4-scaled basis points
    ///        (e.g., 30000 = 3x). Maximum 3x enforced by vault (VAULT-04).
    /// @param slippageBps Acceptable slippage in basis points (e.g., 30 = 0.3%).
    /// @return orderKey Unique order identifier. Subscribe to OrderExecuted /
    ///         OrderCancelled events filtered by this key to determine fill status.
    function openLong(
        string calldata market,
        uint256 sizeUsd,
        uint256 leverage,
        uint256 slippageBps
    ) external returns (bytes32 orderKey);

    /// @notice Opens a short perpetual position for the calling vault.
    /// @dev See openLong NatSpec — identical params, opposite direction.
    ///      Leverage cap, adapter internals, and async pattern are identical.
    /// @param market Venue-agnostic market identifier ("ETH", "BTC", or "SOL").
    /// @param sizeUsd Position size in USD, 1e30-scaled.
    /// @param leverage Leverage multiplier in 1e4-scaled basis points. Max 3x (VAULT-04).
    /// @param slippageBps Acceptable slippage in basis points.
    /// @return orderKey Unique order identifier for async event tracking.
    function openShort(
        string calldata market,
        uint256 sizeUsd,
        uint256 leverage,
        uint256 slippageBps
    ) external returns (bytes32 orderKey);

    /// @notice Closes (partially or fully) an existing perpetual position.
    /// @dev positionKey is the value emitted in the OrderExecuted event from the
    ///      corresponding openLong / openShort call. Not the venue-native key directly
    ///      (adapter re-maps as needed). Partial close: pass sizeUsd < full position size.
    ///      The adapter implementation handles venue-specific decrease-order creation and
    ///      fee mechanics. Journals publish on the resulting OrderExecuted event, not this
    ///      submission receipt (front-running mitigation, spec §9.1).
    /// @param positionKey The position identifier from the prior OrderExecuted event.
    /// @param sizeUsd USD amount to close, 1e30-scaled. Pass full position size to close entirely.
    /// @return orderKey Unique order identifier for async event tracking.
    function closePosition(
        bytes32 positionKey,
        uint256 sizeUsd
    ) external returns (bytes32 orderKey);

    // =========================================================================
    // Position value (NAV feed) — Chainlink-priced, venue-agnostic (D-03)
    // =========================================================================

    /// @notice Returns the current USDC value of all open positions held by `vault`.
    /// @dev CRITICAL (D-03 / CLAUDE.md §4 / spec §9.5): this function MUST use
    ///      Chainlink ETH/BTC/SOL USD mark prices with a staleness check. It MUST NOT
    ///      use the venue's internal price — using the venue's price would create a
    ///      circular NAV dependency on the traded venue (oracle-manipulation mitigation).
    ///
    ///      Value = collateral + pnlAfterFees (net position value per CLAUDE.md §4 —
    ///      use getPositionInfo net position value = collateralAmount + pnlAfterFees).
    ///      Collateral amount is in USDC (6 decimals); return value is in USDC (6 decimals).
    ///
    ///      Staleness check: if Chainlink updatedAt > 1 hour ago, implementation SHOULD
    ///      return the last known-good value to keep vault mint/burn live (CONTRACTS-08).
    ///
    ///      For MockPerps: reads stored {market, size, entry, collateral}; computes
    ///      pnl = (markPrice - entryPrice) * signedSize using Chainlink mark or
    ///      operator override price (D-11). Returns collateral + pnl.
    ///
    /// @param vault The mTokenVault address whose open positions to value.
    /// @return uint256 Total position value in USDC (6 decimals).
    function positionValueUSDC(address vault) external view returns (uint256);
}

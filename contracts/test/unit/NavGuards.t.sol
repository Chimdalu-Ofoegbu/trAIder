// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {Test} from "forge-std/Test.sol";

/// @title NavGuardsTest — NAV guard unit tests (CONTRACTS-07 / CONTRACTS-08 / D-03)
/// @notice STUB — Wave 0 scaffold. All tests are skipped (vm.skip) pending Wave 1 (03-04).
///
///         These unit tests validate:
///           1. positionValueUSDC returns 0 (no revert) when the vault has no open positions
///              and Chainlink prices are fresh — D-05 empty-set behavior.
///           2. The oracle staleness / sequencer downtime drain-settle path: when prices
///              are stale or sequencer is down, NAV calls return last-known-good (not revert)
///              so mint/burn stays live during circuit-breaker events (CONTRACTS-08).
///
///         Uses MockChainlinkAggregator and MockSequencerUptimeFeed for deterministic
///         price and uptime control without requiring a fork.
///
/// @dev Contract name matches 03-PATTERNS.md "NavGuardsTest" section.
///      Test names are the authoritative Wave 1 scaffold targets from 03-VALIDATION.md.
contract NavGuardsTest is Test {
    // =========================================================================
    // Unit tests — Wave 1 scaffolds (all skipped in Wave 0)
    // =========================================================================

    /// @notice positionValueUSDC returns 0 and does NOT revert when vault has no positions.
    /// @dev Wave 1 (03-04): deploy MockPerps with MockChainlinkAggregator feeds;
    ///      call positionValueUSDC on a vault address that has never opened a position;
    ///      assert result == 0 and no revert. This is the D-05 empty-set behavior gate.
    function test_positionValueUSDC_empty_no_revert() public {
        vm.skip(true);
    }

    /// @notice Stale oracle triggers last-known-good fallback; mint/burn stays live.
    /// @dev Wave 1 (03-04): set MockChainlinkAggregator.updatedAt to a stale timestamp
    ///      (> 1 hour ago). For GMXAdapter: assert positionValueUSDC returns cached value
    ///      rather than reverting (CONTRACTS-08 "last-known-good" contract).
    ///      For MockPerps: stale price DOES revert (D-03 strict enforcement) — document
    ///      the behavioral difference between MockPerps and GMXAdapter here.
    ///      Also test sequencer uptime feed integration:
    ///        - setDown() → drain call returns last-known-good
    ///        - setUp(block.timestamp) → grace period → returns last-known-good
    ///        - setUp(block.timestamp - 3601) → grace elapsed → uses fresh price.
    function test_oracle_stale_drain_settle() public {
        vm.skip(true);
    }
}

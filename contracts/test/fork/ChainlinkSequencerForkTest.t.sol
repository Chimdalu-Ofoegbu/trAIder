// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {Test} from "forge-std/Test.sol";

/// @title ChainlinkSequencerForkTest — L2 sequencer uptime fork tests (CONTRACTS-07 / D-03)
/// @notice STUB — Wave 0 scaffold. All tests are skipped (vm.skip) pending Wave 1 (03-04).
///
///         These fork tests validate the sequencer uptime guard against the LIVE
///         Chainlink L2 Sequencer Uptime Feed on Arbitrum One. They verify that:
///           1. When the sequencer is down (answer == 1), NAV calls revert.
///           2. When the sequencer is up but in the grace period (< 60 min since recovery),
///              NAV calls revert (grace period enforcement).
///           3. When the sequencer is up AND grace period has elapsed, NAV calls succeed.
///
///         Unit tests against MockSequencerUptimeFeed cover the same logic without a fork
///         (see contracts/test/unit/NavGuards.t.sol). These fork tests provide extra
///         confidence against the live feed shape and semantics on Arbitrum One.
///
/// @dev Contract name matches 03-PATTERNS.md "ChainlinkSequencerForkTest" section.
///      Test names are the authoritative Wave 1 scaffold targets from 03-VALIDATION.md.
contract ChainlinkSequencerForkTest is Test {
    // =========================================================================
    // Constants — Arbitrum One Chainlink sequencer uptime feed
    // =========================================================================

    /// @dev Chainlink L2 Sequencer Uptime Feed on Arbitrum One.
    ///      Source: https://docs.chain.link/data-feeds/l2-sequencer-feeds
    address constant SEQUENCER_FEED = 0xFdB631F5EE196F0ed6FAa767959853A9F217697D;

    // =========================================================================
    // Fork tests — Wave 1 scaffolds (all skipped in Wave 0)
    // =========================================================================

    /// @notice positionValueUSDC reverts when the Arbitrum sequencer is reported DOWN.
    /// @dev Wave 1 (03-04): fork at a block where SEQUENCER_FEED.latestRoundData() returns
    ///      answer == 1 (sequencer down), OR use vm.mockCall to inject a "down" response.
    ///      Assert that GMXAdapter.positionValueUSDC (and MockPerps.positionValueUSDC)
    ///      revert with a sequencer-down error. Fork block: default (353000000).
    function test_sequencer_down_reverts() public {
        vm.skip(true);
    }

    /// @notice positionValueUSDC reverts during the sequencer grace period (<60 min up).
    /// @dev Wave 1 (03-04): sequencer just came back online (startedAt = block.timestamp - 1800,
    ///      i.e., 30 min ago < 60 min grace). Assert NAV call reverts with grace-period error.
    ///      Use vm.mockCall to inject startedAt = block.timestamp - 1800 into SEQUENCER_FEED.
    ///      Fork block: default (353000000).
    function test_sequencer_grace_period_reverts() public {
        vm.skip(true);
    }

    /// @notice positionValueUSDC succeeds when sequencer is UP and grace period has elapsed.
    /// @dev Wave 1 (03-04): sequencer up for >60 min (startedAt = block.timestamp - 3601).
    ///      Assert NAV call returns a value >= 0 without reverting.
    ///      This is the happy-path gate — confirms the guard does NOT over-revert.
    ///      Fork block: default (353000000).
    function test_sequencer_grace_elapses_succeeds() public {
        vm.skip(true);
    }
}

// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

/// @title MockSequencerUptimeFeed (D-06/D-07) — test-only Chainlink L2 sequencer uptime feed mock
/// @notice Exposes `latestRoundData()` in Chainlink AggregatorV3Interface shape so
///         unit tests can control sequencer up/down state without a fork.
///
///         Chainlink L2 sequencer uptime feed semantics (per Chainlink docs):
///           - answer = 0 → sequencer IS UP (counterintuitive but correct)
///           - answer = 1 → sequencer IS DOWN
///           - updatedAt (index 3) = timestamp when the sequencer last came back online
///             (this is the GRACE CLOCK — MTokenVault._checkSequencer reads index 3)
///           - Consumers must check: answer == 0 AND block.timestamp - updatedAt >= GRACE_PERIOD
///             before accepting prices. If either fails → treat as sequencer down.
///
///         Default state: sequencer UP, recovery time = block.timestamp - 3601 (grace elapsed).
///         Use `setDown()` to put the sequencer down. Use `setUp(_startedAt)` to bring it back up.
///
/// @dev Shape matches Chainlink AggregatorV3Interface.latestRoundData() exactly.
///      CRITICAL: MTokenVault._checkSequencer destructures as:
///        (, int256 seqAnswer,, uint256 seqStartedAt,) = _latestRoundData(SEQUENCER_UPTIME_FEED)
///      The vault reads index 3 (updatedAt slot) as seqStartedAt — the grace-period clock.
///      This mock places the recovery timestamp at index 3 (updatedAt) to match that expectation.
///      NOT for deployment on any real network.
///
///      Used by:
///        - contracts/test/unit/NavGuards.t.sol (unit tests, no fork)
///        - contracts/test/01-MTokenVault.t.sol (sequencer up/down/grace unit tests)
///        - Sepolia deploy (03-07) as the operator-toggleable uptime feed
contract MockSequencerUptimeFeed {
    // =========================================================================
    // Constants
    // =========================================================================

    /// @dev Must match MTokenVault.SEQUENCER_GRACE_PERIOD (3600 seconds = 1 hour).
    uint256 private constant SEQUENCER_GRACE_PERIOD = 3_600;

    // =========================================================================
    // State
    // =========================================================================

    /// @notice Sequencer status: 0 = UP, 1 = DOWN (Chainlink convention).
    int256 public answer;

    /// @notice Timestamp when the sequencer last came back online (unix seconds).
    ///         Stored in the `updatedAt` slot (index 3) of latestRoundData() because
    ///         MTokenVault._checkSequencer reads index 3 as the grace-period clock.
    ///         Only meaningful when answer == 0 (sequencer up).
    ///         Consumers compare block.timestamp - startedAt >= GRACE_PERIOD.
    uint256 public startedAt;

    // =========================================================================
    // Constructor
    // =========================================================================

    /// @notice Deploys with sequencer UP and grace period already elapsed.
    /// @dev Default startedAt = block.timestamp - SEQUENCER_GRACE_PERIOD - 1 ensures
    ///      the 1-hour grace period (3600 seconds) has elapsed at any block.timestamp,
    ///      so tests that do NOT explicitly test the grace-period path pass without
    ///      additional setup. Grace-period test: call setUp(block.timestamp) instead.
    constructor() {
        answer = 0; // UP
        // slither-disable-next-line timestamp
        startedAt = block.timestamp - SEQUENCER_GRACE_PERIOD - 1;
    }

    // =========================================================================
    // Test helpers — setters
    // =========================================================================

    /// @notice Puts the sequencer DOWN.
    /// @dev Sets answer = 1. startedAt is left unchanged (irrelevant when down).
    ///      Consumers checking answer != 0 will treat this as sequencer down.
    function setDown() external {
        answer = 1;
    }

    /// @notice Brings the sequencer UP with a specific recovery timestamp.
    /// @dev Sets answer = 0 and records `_startedAt` as the recovery timestamp (placed
    ///      at the updatedAt slot — index 3 — in latestRoundData, matching the vault's
    ///      destructuring expectation).
    ///      Pass block.timestamp to simulate a JUST-recovered sequencer (grace not elapsed).
    ///      Pass block.timestamp - 3601 (or any value > 3600s ago) to simulate grace elapsed.
    /// @param _startedAt Unix timestamp when the sequencer came back online.
    function setUp(uint256 _startedAt) external {
        answer = 0;
        startedAt = _startedAt;
    }

    // =========================================================================
    // AggregatorV3Interface shape — latestRoundData
    // =========================================================================

    /// @notice Returns the latest round data in standard Chainlink shape.
    /// @dev Return-value positions follow what MTokenVault._checkSequencer expects.
    ///      The vault destructures: (, seqAnswer,, seqStartedAt,) — i.e., it reads
    ///      index 3 (the updatedAt slot) as the grace-period clock.
    ///
    ///      Positions:
    ///        [0] roundId        = 1 (plausible placeholder)
    ///        [1] answer         = 0 (up) or 1 (down)  ← vault reads this
    ///        [2] startedAt_slot = 0 (skipped by vault — placeholder)
    ///        [3] updatedAt_slot = startedAt  ← vault reads this as seqStartedAt (grace clock)
    ///        [4] answeredInRound = 1 (plausible placeholder)
    ///
    ///      For real Chainlink L2 sequencer feeds, the `updatedAt` field IS the
    ///      "time since sequencer recovery" timestamp; `startedAt` is the feed creation time
    ///      and is NOT the grace clock. This mock mirrors the real feed's semantics.
    /// @return roundId         Current round identifier (always 1 in mock).
    /// @return _answer         Sequencer status: 0 = UP, 1 = DOWN.
    /// @return startedAtSlot  Feed creation time placeholder (0 in mock; vault skips this).
    /// @return updatedAtSlot  Recovery timestamp (startedAt) — vault reads this as seqStartedAt.
    /// @return answeredInRound Round in which answer was computed (always 1 in mock).
    function latestRoundData()
        external
        view
        returns (uint80 roundId, int256 _answer, uint256 startedAtSlot, uint256 updatedAtSlot, uint80 answeredInRound)
    {
        roundId = 1;
        _answer = answer;
        startedAtSlot = 0; // placeholder — vault skips index 2
        updatedAtSlot = startedAt; // grace-period clock — vault reads index 3 as seqStartedAt
        answeredInRound = 1;
    }
}

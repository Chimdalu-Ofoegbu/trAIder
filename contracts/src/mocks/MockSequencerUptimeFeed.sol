// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

/// @title MockSequencerUptimeFeed — test-only Chainlink L2 sequencer uptime feed mock
/// @notice Exposes `latestRoundData()` in Chainlink AggregatorV3Interface shape so
///         unit tests can control sequencer up/down state without a fork.
///
///         Chainlink L2 sequencer uptime feed semantics (per Chainlink docs):
///           - answer = 0 → sequencer IS UP (counterintuitive but correct)
///           - answer = 1 → sequencer IS DOWN
///           - startedAt = timestamp when the sequencer last came back online
///           - Consumers must check: answer == 0 AND block.timestamp - startedAt >= GRACE_PERIOD
///             before accepting prices. If either fails → treat as sequencer down.
///
///         Default state: sequencer UP, startedAt = block.timestamp - 3601 (grace elapsed).
///         Use `setDown()` to put the sequencer down. Use `setUp(startedAt)` to bring it back up.
///
/// @dev Shape matches Chainlink AggregatorV3Interface.latestRoundData() exactly.
///      Only `answer` and `startedAt` are semantically meaningful — other return values
///      (roundId, updatedAt, answeredInRound) are plausible placeholders.
///      NOT for deployment on any real network.
contract MockSequencerUptimeFeed {
    // =========================================================================
    // State
    // =========================================================================

    /// @notice Sequencer status: 0 = UP, 1 = DOWN (Chainlink convention).
    int256 public answer;

    /// @notice Timestamp when the sequencer last came back online (unix seconds).
    ///         Only meaningful when answer == 0 (sequencer up).
    ///         Consumers compare block.timestamp - startedAt >= GRACE_PERIOD.
    uint256 public startedAt;

    // =========================================================================
    // Constructor
    // =========================================================================

    /// @notice Deploys with sequencer UP and grace period already elapsed.
    /// @dev Default startedAt = block.timestamp - 3601 ensures the 1-hour grace period
    ///      (3600 seconds) has elapsed at any block.timestamp, so tests that do NOT
    ///      explicitly test the grace-period path pass without additional setup.
    constructor() {
        answer = 0; // UP
        // slither-disable-next-line timestamp
        startedAt = block.timestamp - 3601;
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

    /// @notice Brings the sequencer UP with a specific `startedAt` timestamp.
    /// @dev Sets answer = 0 and records `_startedAt` as the recovery timestamp.
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
    /// @dev Return values follow Chainlink L2 sequencer feed semantics:
    ///      - roundId: always 1 (plausible placeholder)
    ///      - answer: 0 = UP, 1 = DOWN
    ///      - startedAt: recovery timestamp (relevant when answer == 0)
    ///      - updatedAt: mirrors startedAt (plausible placeholder)
    ///      - answeredInRound: mirrors roundId (plausible placeholder; no stale-round logic)
    /// @return roundId      Current round identifier (always 1 in mock).
    /// @return _answer      Sequencer status: 0 = UP, 1 = DOWN.
    /// @return _startedAt   Timestamp when sequencer came online (0 when down).
    /// @return updatedAt    Timestamp of last data update (mirrors startedAt).
    /// @return answeredInRound Round in which answer was computed (always 1 in mock).
    function latestRoundData()
        external
        view
        returns (uint80 roundId, int256 _answer, uint256 _startedAt, uint256 updatedAt, uint80 answeredInRound)
    {
        roundId = 1;
        _answer = answer;
        _startedAt = startedAt;
        updatedAt = startedAt;
        answeredInRound = 1;
    }
}

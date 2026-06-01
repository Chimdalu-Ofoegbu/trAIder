// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

/// @title MockChainlinkAggregator — test-only AggregatorV3Interface-shape mock (MOCK-01)
/// @notice Exposes `latestRoundData()` with settable answer and updatedAt so Foundry
///         tests can deterministically control mark prices and simulate Chainlink staleness
///         without a fork. NOT for deployment on any real network.
/// @dev Shape matches Chainlink AggregatorV3Interface:
///      latestRoundData() returns (uint80 roundId, int256 answer, uint256 startedAt,
///                                 uint256 updatedAt, uint80 answeredInRound)
///      The `answer` is in 8-decimal USD format (same as live Chainlink feeds).
contract MockChainlinkAggregator {
    // =========================================================================
    // Storage
    // =========================================================================

    /// @notice Current round data fields.
    uint80 public roundId;

    /// @notice Price answer in 8-decimal USD format (e.g., 300000000000 = $3,000.00).
    int256 public answer;

    /// @notice Timestamp when data was last updated. Tests manipulate this to simulate
    ///         staleness — set to block.timestamp for a fresh price, set to a past value
    ///         to trigger the MockPerps staleness revert.
    uint256 public updatedAt;

    // =========================================================================
    // Constructor
    // =========================================================================

    /// @param _answer Initial price answer in 8-decimal format.
    /// @param _updatedAt Initial updatedAt timestamp (use block.timestamp for fresh price).
    constructor(int256 _answer, uint256 _updatedAt) {
        answer = _answer;
        updatedAt = _updatedAt;
        roundId = 1;
    }

    // =========================================================================
    // AggregatorV3Interface shape — latestRoundData
    // =========================================================================

    /// @notice Returns the latest price data in standard Chainlink shape.
    /// @dev Only `answer` and `updatedAt` are meaningful in test context; the other
    ///      fields are returned as deterministic placeholder values.
    /// @return _roundId The current round identifier (monotonically incremented on set).
    /// @return _answer Price answer in 8-decimal USD.
    /// @return startedAt Round start timestamp (mirrors updatedAt in mock).
    /// @return _updatedAt Timestamp of last price update. Set old to trigger staleness revert.
    /// @return answeredInRound Same as roundId — no delayed-answer semantics in mock.
    function latestRoundData()
        external
        view
        returns (uint80 _roundId, int256 _answer, uint256 startedAt, uint256 _updatedAt, uint80 answeredInRound)
    {
        return (roundId, answer, updatedAt, updatedAt, roundId);
    }

    // =========================================================================
    // Test helpers — setters
    // =========================================================================

    /// @notice Updates the mark price and resets updatedAt to the current block timestamp.
    /// @dev Increments roundId to mimic a new round from a Chainlink heartbeat.
    /// @param _answer New price in 8-decimal format.
    function setPrice(int256 _answer) external {
        answer = _answer;
        updatedAt = block.timestamp;
        roundId++;
    }

    /// @notice Sets both price and updatedAt explicitly, used to simulate stale data.
    /// @param _answer New price in 8-decimal format.
    /// @param _updatedAt Explicit timestamp — set older than MAX_STALENESS to trigger revert.
    function setPriceAt(int256 _answer, uint256 _updatedAt) external {
        answer = _answer;
        updatedAt = _updatedAt;
        roundId++;
    }
}

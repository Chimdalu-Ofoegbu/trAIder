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

    // =========================================================================
    // CR-03 test helpers — stale-round simulation
    // =========================================================================

    /// @notice Overrides ALL round data fields to simulate a stale or incomplete round.
    /// @dev Use this to exercise the CR-03 guards in MockPerps._markPrice:
    ///      - Set answeredInRound < roundId to trigger "MockPerps: stale round"
    ///      - Set updatedAt = 0 to trigger "MockPerps: round not complete"
    ///      Default state (set by setPrice / setPriceAt / constructor) keeps
    ///      updatedAt != 0 and answeredInRound == roundId so existing tests pass.
    /// @param _roundId The round identifier to publish.
    /// @param _answer Price in 8-decimal format.
    /// @param _updatedAt Round completion timestamp (0 = incomplete round).
    /// @param _answeredInRound The round in which the answer was computed.
    ///        Set < _roundId to simulate a carried-over stale answer.
    function setStaleRound(uint80 _roundId, int256 _answer, uint256 _updatedAt, uint80 _answeredInRound) external {
        roundId = _roundId;
        answer = _answer;
        updatedAt = _updatedAt;
        _answeredInRoundOverride = _answeredInRound;
        _staleRoundActive = true;
    }

    /// @notice Resets stale-round override back to normal (answeredInRound == roundId).
    function clearStaleRound() external {
        _staleRoundActive = false;
    }

    // =========================================================================
    // Internal — stale-round override storage
    // =========================================================================

    /// @dev When _staleRoundActive is true, latestRoundData returns _answeredInRoundOverride
    ///      instead of roundId, allowing tests to trigger the answeredInRound < roundId path.
    bool private _staleRoundActive;
    uint80 private _answeredInRoundOverride;

    /// @notice Returns the latest price data in standard Chainlink shape.
    /// @dev Only `answer` and `updatedAt` are meaningful in normal test context; the other
    ///      fields are returned as deterministic placeholder values. When `_staleRoundActive`
    ///      is true (set via setStaleRound), `answeredInRound` returns `_answeredInRoundOverride`
    ///      instead of `roundId` so tests can exercise the CR-03 stale-round guards.
    /// @return _roundId The current round identifier.
    /// @return _answer Price answer in 8-decimal USD.
    /// @return startedAt Round start timestamp (mirrors updatedAt in mock).
    /// @return _updatedAt Timestamp of last price update. Set old to trigger staleness revert.
    /// @return answeredInRound The round in which the answer was computed.
    ///         Normally equals roundId; set < roundId via setStaleRound to trigger stale-round revert.
    function latestRoundData()
        external
        view
        returns (uint80 _roundId, int256 _answer, uint256 startedAt, uint256 _updatedAt, uint80 answeredInRound)
    {
        _roundId = roundId;
        _answer = answer;
        startedAt = updatedAt;
        _updatedAt = updatedAt;
        answeredInRound = _staleRoundActive ? _answeredInRoundOverride : roundId;
    }
}

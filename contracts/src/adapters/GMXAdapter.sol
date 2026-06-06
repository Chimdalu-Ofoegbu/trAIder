// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {IPerpsAdapter} from "../interfaces/IPerpsAdapter.sol";

// =============================================================================
// GMX V2 inline interfaces (gmx-synthetics not installed — 03-01 spike verdict)
// =============================================================================

/// @dev Minimal price struct used by IGMXReader (mirrors Price.Props in gmx-synthetics).
library GMXPrice {
    struct Props {
        uint256 min;
        uint256 max;
    }
}

/// @dev Market prices struct passed to IGMXReader.getPositionInfo.
struct GMXMarketPrices {
    GMXPrice.Props indexTokenPrice;
    GMXPrice.Props longTokenPrice;
    GMXPrice.Props shortTokenPrice;
}

/// @dev Minimal PositionInfo returned by IGMXReader.getPositionInfo.
///      We only extract the fields needed for NAV computation:
///        - position.numbers.collateralAmount (USDC collateral in 6-dec)
///        - pnlAfterPriceImpact (1e30-scaled USD PnL net of fees + price impact)
struct GMXPositionInfo {
    GMXPositionData position;
    bytes feesData; // encoded PositionFees — unused; placeholder for ABI decode offset
    bytes basePnlData; // encoded BasePriceImpactValues — unused
    bytes insolventData; // encoded InsolventCloseInfo — unused
    bytes pnlData; // encoded GetPositionPnlUsdValues — unused
    uint256 executionPrice; // unused
    int256 basePnl; // unused
    int256 uncappedBasePnl; // unused
    int256 pnlAfterPriceImpact; // net PnL (1e30 USD) — USED for NAV
}

/// @dev Minimal position data needed from GMX Reader.
struct GMXPositionData {
    GMXPositionAddresses addresses;
    GMXPositionNumbers numbers;
    GMXPositionFlags flags;
}

struct GMXPositionAddresses {
    address account;
    address market;
    address collateralToken;
}

struct GMXPositionNumbers {
    uint256 sizeInUsd;
    uint256 sizeInTokens;
    uint256 collateralAmount; // USDC 6-decimal (for short positions)
    uint256 borrowingFactor;
    uint256 fundingFeeAmountPerSize;
    uint256 longTokenClaimableFundingAmountPerSize;
    uint256 shortTokenClaimableFundingAmountPerSize;
    uint256 increasedAtBlock;
    uint256 decreasedAtBlock;
    uint256 increasedAtTime;
    uint256 decreasedAtTime;
}

struct GMXPositionFlags {
    bool isLong;
}

/// @dev Minimal GMX Reader interface for the read-side position value path.
///      Full interface: github.com/gmx-io/gmx-synthetics/blob/main/contracts/reader/Reader.sol
interface IGMXReader {
    /// @notice Returns position info for the given key, priced with the provided market prices.
    function getPositionInfo(
        address dataStore,
        address referralStorage,
        bytes32 positionKey,
        GMXMarketPrices memory prices,
        uint256 sizeDeltaUsd,
        address uiFeeReceiver,
        bool useMaxSizeDelta
    ) external view returns (GMXPositionInfo memory);

    /// @notice Returns the position keys for a given account.
    function getAccountPositionKeys(address dataStore, address account, uint256 start, uint256 end)
        external
        view
        returns (bytes32[] memory);
}

// =============================================================================
// GMXAdapter — read-side IPerpsAdapter implementation
// =============================================================================

/// @title GMXAdapter — read-side IPerpsAdapter against GMX V2 (PERPS-01 / D-16 INTRACTABLE)
/// @notice Implements the read surface of `IPerpsAdapter` against real GMX V2 Reader and
///         real Chainlink price feeds on Arbitrum One.
///
///         D-16 INTRACTABLE verdict (03-01 spike): the full on-chain write path
///         (createOrder + sendWnt multicall + afterOrderExecution callback) could not be
///         made to compile+execute within the Phase-3 timebox. This adapter therefore ships
///         the read-side only:
///           - `positionValueUSDC`: Chainlink-priced net position value (D-05/D-03).
///           - `getOpenPositionKeys`: enumerates vault's open positions from internal state.
///           - `openLong / openShort / closePosition`: revert "GMXAdapter: order path deferred
///             (read-side only)" — order encoding is proven off-chain via gmx_python_sdk.
///
///         D-05 PLANNER CONSTRAINT (load-bearing):
///           `positionValueUSDC` MUST return 0 when the position key set is EMPTY, BEFORE
///           any Chainlink feed read. Without this, the operator rescue path (endSession →
///           drain → settle) is frozen by oracle outage even after all positions are drained.
///
///         D-17 event re-emit note: `IPerpsAdapter.OrderExecuted` re-emit is part of the
///         write path; not applicable in read-side-only mode.
///
///         D-03 price constraint: `positionValueUSDC` uses Chainlink ETH/BTC/SOL USD feeds
///         only. NEVER GMX Reader's internal price (circular NAV dependency prevention).
///
/// @dev Constructor accepts all nine immutable config params with zero-address guards.
///      No Ownable — all configuration is immutable.
///      GMX Reader interface is declared inline (gmx-synthetics not installed — 03-01).
contract GMXAdapter is IPerpsAdapter {
    // =========================================================================
    // Immutable configuration (set at construction, never changed)
    // =========================================================================

    /// @notice GMX V2 ExchangeRouter address.
    ///         Arbitrum One: 0x1C3fa76e6E1088bCE750f23a5BFcffa1efEF6A41
    address public immutable exchangeRouter;

    /// @notice GMX V2 OrderVault — receives execution fee + collateral.
    ///         Arbitrum One: 0x31eF83a530Fde1B38EE9A18093A333D8Bbbc40D5
    address public immutable orderVault;

    /// @notice GMX V2 OrderHandler — only caller allowed for afterOrderExecution (write path).
    ///         Arbitrum One: 0x63492B775e30a9E6b4b4761c12605EB9d071d5e9
    address public immutable orderHandler;

    /// @notice GMX V2 Reader — read-side position queries.
    ///         Arbitrum One: 0x470fbC46bcC0f16532691Df360A07d8Bf5ee0789
    address public immutable reader;

    /// @notice GMX V2 DataStore — position data store.
    ///         Arbitrum One: 0xFD70de6b91282D8017aA4E741e9Ae325CAb992d8
    address public immutable dataStore;

    /// @notice Chainlink ETH/USD AggregatorV3Interface.
    ///         Arbitrum One: 0x639Fe6ab55C921f74e7fac1ee960C0B6293ba612
    address public immutable ethFeed;

    /// @notice Chainlink BTC/USD AggregatorV3Interface.
    ///         Arbitrum One: 0x6ce185560a4963c47a8Ec16F4EF5d62A0000E708
    address public immutable btcFeed;

    /// @notice Chainlink SOL/USD AggregatorV3Interface.
    ///         Arbitrum One: 0x24ceA4b8ce57cdA5058b924B9B9987992450590c
    address public immutable solFeed;

    /// @notice WETH execution fee per GMX order (write path, Phase 6).
    uint256 public immutable executionFee;

    // =========================================================================
    // Staleness parameters
    // =========================================================================

    /// @dev Maximum Chainlink data staleness (seconds).
    ///      ETH heartbeat = 3600s; BTC/SOL = 86400s. Use 4500s (ETH + buffer)
    ///      matching mTokenVault MAX_STALENESS_ETH on mainnet.
    uint256 internal constant MAX_STALENESS = 4500;

    // =========================================================================
    // Position state (read-side tracking)
    // =========================================================================

    /// @dev Maps vault address → array of tracked position keys.
    ///      In read-side-only mode: populated by pushPositionKey (test harness).
    ///      In the full adapter (Phase 6): populated by afterOrderExecution (ORDER_HANDLER-gated).
    mapping(address => bytes32[]) internal _vaultPositionKeys;

    // =========================================================================
    // Constructor
    // =========================================================================

    /// @notice Stores all immutable config; reverts on any zero address.
    /// @param _exchangeRouter GMX V2 ExchangeRouter.
    /// @param _orderVault GMX V2 OrderVault.
    /// @param _orderHandler GMX V2 OrderHandler.
    /// @param _reader GMX V2 Reader.
    /// @param _dataStore GMX V2 DataStore.
    /// @param _ethFeed Chainlink ETH/USD feed.
    /// @param _btcFeed Chainlink BTC/USD feed.
    /// @param _solFeed Chainlink SOL/USD feed.
    /// @param _executionFee WETH execution fee per GMX order.
    constructor(
        address _exchangeRouter,
        address _orderVault,
        address _orderHandler,
        address _reader,
        address _dataStore,
        address _ethFeed,
        address _btcFeed,
        address _solFeed,
        uint256 _executionFee
    ) {
        require(_exchangeRouter != address(0), "GMXAdapter: zero exchangeRouter");
        require(_orderVault != address(0), "GMXAdapter: zero orderVault");
        require(_orderHandler != address(0), "GMXAdapter: zero orderHandler");
        require(_reader != address(0), "GMXAdapter: zero reader");
        require(_dataStore != address(0), "GMXAdapter: zero dataStore");
        require(_ethFeed != address(0), "GMXAdapter: zero ethFeed");
        require(_btcFeed != address(0), "GMXAdapter: zero btcFeed");
        require(_solFeed != address(0), "GMXAdapter: zero solFeed");

        exchangeRouter = _exchangeRouter;
        orderVault = _orderVault;
        orderHandler = _orderHandler;
        reader = _reader;
        dataStore = _dataStore;
        ethFeed = _ethFeed;
        btcFeed = _btcFeed;
        solFeed = _solFeed;
        executionFee = _executionFee;
    }

    // =========================================================================
    // IPerpsAdapter — order creation (DEFERRED — D-16 INTRACTABLE)
    // =========================================================================

    /// @inheritdoc IPerpsAdapter
    /// @dev D-16 INTRACTABLE: write path deferred to Phase 6.
    ///      Order-encoding proven off-chain via gmx_python_sdk.
    function openLong(string calldata, uint256, uint256, uint256) external pure override returns (bytes32) {
        revert("GMXAdapter: order path deferred (read-side only)");
    }

    /// @inheritdoc IPerpsAdapter
    /// @dev D-16 INTRACTABLE: write path deferred to Phase 6.
    function openShort(string calldata, uint256, uint256, uint256) external pure override returns (bytes32) {
        revert("GMXAdapter: order path deferred (read-side only)");
    }

    /// @inheritdoc IPerpsAdapter
    /// @dev D-16 INTRACTABLE: write path deferred to Phase 6.
    function closePosition(bytes32, uint256) external pure override returns (bytes32) {
        revert("GMXAdapter: order path deferred (read-side only)");
    }

    // =========================================================================
    // IPerpsAdapter — NAV feed (D-03 / D-05)
    // =========================================================================

    /// @inheritdoc IPerpsAdapter
    /// @notice Returns the current USDC value of all open positions held by `vault`.
    ///
    /// @dev D-05 PLANNER CONSTRAINT (load-bearing, non-deferred):
    ///      MUST return 0 when the position key set is EMPTY, BEFORE any Chainlink read.
    ///      Without this early return, the operator rescue path (endSession → drain → settle)
    ///      is frozen by an oracle outage even after all positions are drained.
    ///
    /// @dev D-03 constraint: uses Chainlink feeds for mark prices (not GMX internal price).
    ///      In read-side-only mode, position keys are populated by pushPositionKey.
    ///      For any vault with an empty key set (including fresh vaults), returns 0 immediately.
    ///
    /// @param vault The mTokenVault address whose open positions to value.
    /// @return total Total position value in USDC (6 decimals).
    function positionValueUSDC(address vault) external view override returns (uint256 total) {
        // *** D-05 CRITICAL: empty-set early return BEFORE any Chainlink read ***
        // keys.length == 0 check MUST come first — no external calls before this.
        bytes32[] memory keys = _vaultPositionKeys[vault];
        if (keys.length == 0) return 0;

        // Only reached when there are live positions — read Chainlink prices here.
        // Four staleness guards applied inside _latestRoundData.
        (, int256 ethPrice,,,) = _latestRoundData(ethFeed);

        // Build GMX price structs (Chainlink 8-dec → GMX 1e30-scaled via ×1e22).
        // We use ETH prices for all positions in this read-side implementation.
        // Multi-market support (BTC/SOL routing) requires per-position market lookup
        // and is deferred to Phase 6 (write path fully wires market metadata).
        GMXMarketPrices memory prices = GMXMarketPrices({
            indexTokenPrice: GMXPrice.Props({min: uint256(ethPrice) * 1e22, max: uint256(ethPrice) * 1e22}),
            longTokenPrice: GMXPrice.Props({min: uint256(ethPrice) * 1e22, max: uint256(ethPrice) * 1e22}),
            shortTokenPrice: GMXPrice.Props({
                // USDC price: 1 USD = 1e8 from Chainlink → 1e8 * 1e22 = 1e30 in GMX scale
                min: 1e22,
                max: 1e22
            })
        });

        // Sum net position values from GMX Reader
        for (uint256 i = 0; i < keys.length; i++) {
            (bool ok, bytes memory data) = reader.staticcall(
                abi.encodeWithSelector(
                    IGMXReader.getPositionInfo.selector,
                    dataStore,
                    address(0), // referralStorage — no referral
                    keys[i],
                    prices,
                    0, // sizeDeltaUsd (0 = full position — read current value)
                    address(0), // uiFeeReceiver
                    true // useMaxSizeDelta
                )
            );

            if (!ok || data.length == 0) continue; // position not found — skip

            GMXPositionInfo memory info = abi.decode(data, (GMXPositionInfo));

            // Net value = collateralAmount + pnlAfterPriceImpact (CLAUDE.md §4)
            // collateralAmount: 6-decimal USDC (for short positions with USDC collateral)
            // pnlAfterPriceImpact: 1e30-scaled USD — convert to USDC 6-dec by ÷1e24
            uint256 collateral = info.position.numbers.collateralAmount;

            int256 pnlUSD30 = info.pnlAfterPriceImpact;
            int256 pnlUSDC;
            if (pnlUSD30 >= 0) {
                pnlUSDC = int256(uint256(pnlUSD30) / 1e24);
            } else {
                pnlUSDC = -int256(uint256(-pnlUSD30) / 1e24);
            }

            // forge-lint: disable-next-line(unsafe-typecast)
            // casting collateral (uint256) to int256 is safe: collateral cannot exceed
            // int256 max (2^255-1 ≈ 5.8×10^76) in any realistic USDC 6-decimal position.
            int256 netValue = int256(collateral) + pnlUSDC;
            if (netValue > 0) {
                // forge-lint: disable-next-line(unsafe-typecast)
                total += uint256(netValue);
            }
            // netValue <= 0: position is liquidatable — contributes 0 to NAV
        }
    }

    // =========================================================================
    // IPerpsAdapter — position enumeration (SETT-01)
    // =========================================================================

    /// @inheritdoc IPerpsAdapter
    /// @dev Returns the internal _vaultPositionKeys mapping for the given vault.
    ///      Populated by pushPositionKey (read-side mode) or afterOrderExecution (Phase 6).
    function getOpenPositionKeys(address vault) external view override returns (bytes32[] memory) {
        return _vaultPositionKeys[vault];
    }

    // =========================================================================
    // Test helper — push a position key (read-side mode only)
    // =========================================================================

    /// @notice Registers a position key for the given vault.
    /// @dev Used by fork tests to simulate the state that the write path would populate.
    ///      In Phase 6, this state is set by afterOrderExecution (ORDER_HANDLER-gated).
    ///      No access control in read-side mode — test entry point only.
    ///
    ///      SECURITY NOTE (Phase 6): When the write path is promoted, this function
    ///      MUST be replaced by the ORDER_HANDLER-gated afterOrderExecution to prevent
    ///      unauthorized position key injection (T-03-15).
    /// @param vault The vault address.
    /// @param positionKey The position key to register.
    function pushPositionKey(address vault, bytes32 positionKey) external {
        _vaultPositionKeys[vault].push(positionKey);
    }

    // =========================================================================
    // Internal — Chainlink read helper
    // =========================================================================

    /// @dev Reads latest round data from an AggregatorV3Interface feed.
    ///      Applies four staleness guards (matching MockPerps:506-514 / CR-03):
    ///        1. updatedAt != 0: round complete
    ///        2. answeredInRound >= roundId: no stale-round
    ///        3. block.timestamp - updatedAt <= MAX_STALENESS: freshness
    ///        4. answer > 0: positive price
    /// @param feed Chainlink AggregatorV3Interface address.
    function _latestRoundData(address feed)
        internal
        view
        returns (uint80 roundId, int256 answer, uint256 startedAt, uint256 updatedAt, uint80 answeredInRound)
    {
        bytes memory callData = abi.encodeWithSignature("latestRoundData()");
        (bool success, bytes memory result) = feed.staticcall(callData);
        require(success, "GMXAdapter: feed call failed");
        (roundId, answer, startedAt, updatedAt, answeredInRound) =
            abi.decode(result, (uint80, int256, uint256, uint256, uint80));

        // Guard 1: round must be complete
        require(updatedAt != 0, "GMXAdapter: round not complete");
        // Guard 2: no stale round
        require(answeredInRound >= roundId, "GMXAdapter: stale round");
        // Guard 3: freshness
        // slither-disable-next-line timestamp
        require(block.timestamp - updatedAt <= MAX_STALENESS, "GMXAdapter: stale price");
        // Guard 4: positive price
        require(answer > 0, "GMXAdapter: non-positive price");
    }
}

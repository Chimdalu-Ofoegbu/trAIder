// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {IPerpsAdapter} from "../interfaces/IPerpsAdapter.sol";

/// @title GMXAdapter (PERPS-01/D-16)
/// @notice Real-GMX adapter implementing IPerpsAdapter.
///
///         STUB — Wave 0 scaffold only. All write functions revert NotImplemented.
///         positionValueUSDC and getOpenPositionKeys return safe empty/zero values
///         so the stub satisfies the interface and passes D-05 empty-set checks trivially.
///
///         Full implementation scope decided by D-16 (GMX spike verdict):
///         VERDICT: INTRACTABLE — 03-05 will implement READ-SIDE ONLY:
///           - positionValueUSDC: real Chainlink-priced position valuation
///           - getOpenPositionKeys: query GMX Reader for vault's open positions
///           - openLong / openShort / closePosition: remain NotImplemented in Phase 3
///             (Python gmx_python_sdk provides the order-encoding proof off-chain)
///
/// @dev Constructor pattern per 03-PATTERNS.md. Immutable config fields stored with
///      zero-address guards. No storage slots used by stubs — gas neutral.
contract GMXAdapter is IPerpsAdapter {
    // =========================================================================
    // Immutable configuration (set at construction, never changed)
    // =========================================================================

    /// @notice GMX V2 ExchangeRouter address (Arbitrum One).
    address public immutable exchangeRouter;

    /// @notice GMX V2 OrderVault address — receives execution fee + collateral.
    address public immutable orderVault;

    /// @notice GMX V2 OrderHandler address — used to impersonate keeper in fork tests.
    address public immutable orderHandler;

    /// @notice GMX V2 Reader address — read-side position queries.
    address public immutable reader;

    /// @notice GMX V2 DataStore address — position data store.
    address public immutable dataStore;

    /// @notice Chainlink ETH/USD AggregatorV3Interface address (Arbitrum One).
    address public immutable ethFeed;

    /// @notice Chainlink BTC/USD AggregatorV3Interface address (Arbitrum One).
    address public immutable btcFeed;

    /// @notice Chainlink SOL/USD AggregatorV3Interface address (Arbitrum One).
    address public immutable solFeed;

    /// @notice Execution fee forwarded to GMX keeper (in WETH, wei units).
    uint256 public immutable executionFee;

    // =========================================================================
    // Constructor
    // =========================================================================

    /// @notice Stores all immutable config; reverts on any zero address.
    /// @dev All nine config fields required — no default addresses baked in.
    ///      This ensures the adapter is always explicitly configured at deployment.
    /// @param _exchangeRouter GMX V2 ExchangeRouter.
    /// @param _orderVault GMX V2 OrderVault.
    /// @param _orderHandler GMX V2 OrderHandler (for fork-test impersonation).
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
    // IPerpsAdapter — order creation (STUB — reverts NotImplemented)
    // =========================================================================

    /// @inheritdoc IPerpsAdapter
    /// @dev STUB — not implemented in Wave 0.
    ///      D-16 INTRACTABLE verdict: write path moves to Python gmx_python_sdk proof.
    function openLong(string calldata, uint256, uint256, uint256) external override returns (bytes32) {
        revert("GMXAdapter: not implemented");
    }

    /// @inheritdoc IPerpsAdapter
    /// @dev STUB — not implemented in Wave 0.
    ///      D-16 INTRACTABLE verdict: write path moves to Python gmx_python_sdk proof.
    function openShort(string calldata, uint256, uint256, uint256) external override returns (bytes32) {
        revert("GMXAdapter: not implemented");
    }

    /// @inheritdoc IPerpsAdapter
    /// @dev STUB — not implemented in Wave 0.
    ///      D-16 INTRACTABLE verdict: write path moves to Python gmx_python_sdk proof.
    function closePosition(bytes32, uint256) external override returns (bytes32) {
        revert("GMXAdapter: not implemented");
    }

    // =========================================================================
    // IPerpsAdapter — read-side (safe stub values for Wave 0)
    // =========================================================================

    /// @inheritdoc IPerpsAdapter
    /// @dev STUB — returns 0. Wave 0 safe default: no positions open.
    ///      03-05 implements real Chainlink-priced valuation against GMX Reader.
    ///      D-03: real implementation MUST use Chainlink prices (not GMX internal).
    function positionValueUSDC(address) external pure override returns (uint256) {
        return 0;
    }

    /// @inheritdoc IPerpsAdapter
    /// @dev STUB — returns empty array. Wave 0 safe default: no positions open.
    ///      03-05 implements real query against GMX Reader.getAccountPositions.
    ///      SETT-01: settlement drain finds empty set — trivially safe.
    function getOpenPositionKeys(address) external pure override returns (bytes32[] memory) {
        return new bytes32[](0);
    }
}

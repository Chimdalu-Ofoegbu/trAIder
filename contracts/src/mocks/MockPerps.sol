// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {Ownable} from "@openzeppelin/contracts/access/Ownable.sol";
import {IPerpsAdapter} from "../interfaces/IPerpsAdapter.sol";

/// @title MockPerps — GMX-shape mock perpetuals adapter (MOCK-01)
/// @notice Implements `IPerpsAdapter` for anvil + Sepolia + Robinhood Chain testnet.
///         Swap-equivalent with the future GMXAdapter: a single `PERPS_VENUE=mock`
///         environment variable at deploy time routes the vault to this contract (D-04).
///
///         Pricing: reads Chainlink ETH/BTC/SOL feeds by default. The deployer (owner)
///         may call `setMarkOverride(market, price, expiresAt)` to inject a fixture price
///         for tests and demos. The override auto-expires past `expiresAt` and falls back
///         to Chainlink so the live Sepolia/Robinhood demo tracks real markets (D-11).
///
///         Async execution: `openLong` / `openShort` / `closePosition` record a pending
///         order and return an `orderKey`. After `executionDelay` blocks have elapsed,
///         anyone may call `executeOrder(orderKey)` — mimicking a GMX keeper — which
///         finalizes the position and emits `OrderExecuted`. Default N=1 (local anvil),
///         N=3 for Sepolia deployments (D-13).
///
///         Auto-liquidation: if `collateral + pnl <= 0` at any settlement point the
///         position is wiped and `PositionLiquidated` is emitted, forcing the
///         SettlementContract path (D-12).
///
///         NAV pricing: `positionValueUSDC(vault)` is Chainlink-priced with a 1-hour
///         staleness revert (D-03). It MUST NOT use venue-internal prices (CLAUDE.md §4).
///         Returns collateral + pnlAfterFees where fees = 0 for the mock.
///
/// @dev Markets: "ETH", "BTC", "SOL" — mapped to three Chainlink aggregator addresses
///      at construction. Feed answer assumed 8-decimal USD (standard Chainlink format).
///      Position sizes are tracked in USD (1e30-scaled, matching GMX V2 precision).
///      collateral is in USDC (6 decimals). positionValueUSDC returns USDC (6 decimals).
contract MockPerps is IPerpsAdapter, Ownable {
    // =========================================================================
    // Constants
    // =========================================================================

    /// @notice Maximum age of a Chainlink price before positionValueUSDC reverts (D-03).
    uint256 public constant MAX_STALENESS = 1 hours;

    /// @notice Chainlink feed answer decimals (standard: 8).
    uint256 private constant FEED_DECIMALS = 8;

    /// @notice GMX V2 USD precision — sizeUsd is 1e30-scaled.
    uint256 private constant USD_PRECISION = 1e30;

    /// @notice USDC decimal precision (6 decimals).
    uint256 private constant USDC_PRECISION = 1e6;

    /// @notice PnL scaling denominator: entry × 1e24. See _computePnl derivation.
    /// @dev 1e24 = 1e30 (usd precision) / 1e6 (usdc precision).
    uint256 private constant PNL_ENTRY_SCALE = 1e24;

    // =========================================================================
    // Structs
    // =========================================================================

    /// @dev One open perpetuals position held by a vault.
    struct Position {
        /// @dev Market identifier — "ETH", "BTC", or "SOL".
        string market;
        /// @dev Signed size: positive = long, negative = short. Stored in 1e30 USD units.
        ///      Negative implies a short position (signedSize < 0 → PnL when mark < entry).
        int256 signedSize;
        /// @dev Entry price in 8-decimal USD (Chainlink format).
        int256 entryPrice;
        /// @dev Collateral in USDC (6 decimal units).
        uint256 collateral;
        /// @dev Vault address that owns this position. Used for per-vault aggregation.
        address vault;
        /// @dev True when the position has been closed / liquidated.
        bool closed;
    }

    /// @dev Pending asynchronous order awaiting keeper execution.
    struct PendingOrder {
        /// @dev The position this order will finalize (open) or close.
        bytes32 positionKey;
        /// @dev Block number at/after which `executeOrder` may be called (D-13).
        uint256 executeAfterBlock;
        /// @dev Vault that submitted the order.
        address vault;
        /// @dev True for a close order; false for an open order.
        bool isClose;
        /// @dev True once executed.
        bool executed;
    }

    /// @dev Per-market deployer price override (D-11).
    struct PriceOverride {
        /// @dev Price in 8-decimal USD format.
        int256 price;
        /// @dev Unix timestamp after which this override is ignored (falls back to Chainlink).
        uint256 expiresAt;
    }

    // =========================================================================
    // State
    // =========================================================================

    /// @notice Chainlink aggregator addresses keyed by market string.
    mapping(bytes32 => address) public feeds;

    /// @notice Active positions keyed by positionKey.
    mapping(bytes32 => Position) public positions;

    /// @notice Pending orders awaiting keeper execution.
    mapping(bytes32 => PendingOrder) public pendingOrders;

    /// @notice Per-vault list of open positionKeys (for positionValueUSDC aggregation).
    mapping(address => bytes32[]) public vaultPositionKeys;

    /// @notice Deployer-only mark overrides (D-11).
    mapping(bytes32 => PriceOverride) public markOverrides;

    /// @notice Number of blocks between order creation and keeper execution (D-13).
    /// @dev Default 1 (local anvil). Set to 3 for Sepolia deployments.
    uint16 public executionDelay;

    /// @dev Monotonic nonce for unique key generation.
    uint256 private _nonce;

    // =========================================================================
    // Events
    // =========================================================================

    /// @notice Emitted when a pending order is created (open or close).
    /// @dev Orchestrator parses this event from the transaction receipt to recover
    ///      the orderKey deterministically — avoids brute-force nonce derivation (CR-01).
    ///      Both open and close orders emit this event so all pending orders are recoverable.
    /// @param orderKey  Unique order key returned by openLong/openShort/closePosition.
    /// @param positionKey The position this order will open or close.
    /// @param vault The mTokenVault address that submitted the order (msg.sender).
    event OrderCreated(bytes32 indexed orderKey, bytes32 indexed positionKey, address indexed vault);

    /// @notice Emitted when a position is auto-liquidated (collateral + pnl <= 0).
    /// @param positionKey The liquidated position key.
    /// @param vault The vault that held the position.
    event PositionLiquidated(bytes32 indexed positionKey, address indexed vault);

    // =========================================================================
    // Constructor
    // =========================================================================

    /// @notice Deploys MockPerps with three Chainlink feed addresses.
    /// @dev On Sepolia / Robinhood Chain: pass live Chainlink aggregator addresses.
    ///      For local anvil tests: pass MockChainlinkAggregator contract addresses.
    ///      Constructor wires ETH / BTC / SOL feeds. executionDelay defaults to 1.
    /// @param ethFeed Chainlink ETH/USD AggregatorV3Interface-compatible address.
    /// @param btcFeed Chainlink BTC/USD AggregatorV3Interface-compatible address.
    /// @param solFeed Chainlink SOL/USD AggregatorV3Interface-compatible address.
    constructor(address ethFeed, address btcFeed, address solFeed) Ownable(msg.sender) {
        require(ethFeed != address(0), "MockPerps: zero ETH feed");
        require(btcFeed != address(0), "MockPerps: zero BTC feed");
        require(solFeed != address(0), "MockPerps: zero SOL feed");

        feeds[keccak256("ETH")] = ethFeed;
        feeds[keccak256("BTC")] = btcFeed;
        feeds[keccak256("SOL")] = solFeed;

        executionDelay = 1; // Default N=1 for local anvil (D-13)
    }

    // =========================================================================
    // IPerpsAdapter — order creation (D-01 / D-02)
    // =========================================================================

    /// @notice Opens a long perpetual position for the calling vault.
    /// @dev Records position with positive signedSize (long). Schedules OrderExecuted
    ///      emission after `executionDelay` blocks (D-13). Does NOT emit OrderExecuted
    ///      in this transaction — caller must poll/subscribe and call `executeOrder`.
    /// @param market Venue-agnostic market identifier ("ETH", "BTC", or "SOL").
    /// @param sizeUsd Position size in USD, 1e30-scaled.
    /// @param leverage Leverage multiplier in 1e4-scaled basis points (e.g., 30000 = 3x).
    ///        Vault enforces the 3x cap (VAULT-04) before calling this.
    /// @param slippageBps Acceptable slippage in basis points. Ignored in mock (no impact).
    /// @return orderKey Unique order identifier for async event tracking.
    function openLong(string calldata market, uint256 sizeUsd, uint256 leverage, uint256 slippageBps)
        external
        override
        returns (bytes32 orderKey)
    {
        return _openPosition(market, sizeUsd, leverage, slippageBps, true);
    }

    /// @notice Opens a short perpetual position for the calling vault.
    /// @dev See openLong NatSpec — identical params, signedSize stored as negative.
    /// @param market Venue-agnostic market identifier ("ETH", "BTC", or "SOL").
    /// @param sizeUsd Position size in USD, 1e30-scaled.
    /// @param leverage Leverage multiplier in 1e4-scaled basis points. Max 3x (VAULT-04).
    /// @param slippageBps Acceptable slippage in basis points. Ignored in mock.
    /// @return orderKey Unique order identifier for async event tracking.
    function openShort(string calldata market, uint256 sizeUsd, uint256 leverage, uint256 slippageBps)
        external
        override
        returns (bytes32 orderKey)
    {
        return _openPosition(market, sizeUsd, leverage, slippageBps, false);
    }

    /// @notice Closes (partially or fully) an existing perpetual position.
    /// @dev positionKey must refer to a non-closed position owned by msg.sender vault.
    ///      Schedules OrderExecuted after `executionDelay` blocks. Partial close not
    ///      implemented in mock (sizeUsd ignored — always fully closes in Phase 0).
    /// @param positionKey The position identifier from the prior OrderExecuted event.
    /// @param sizeUsd USD amount to close, 1e30-scaled. Mock closes fully regardless.
    /// @return orderKey Unique order identifier for async event tracking.
    function closePosition(bytes32 positionKey, uint256 sizeUsd) external override returns (bytes32 orderKey) {
        Position storage pos = positions[positionKey];
        require(pos.vault != address(0), "MockPerps: position not found");
        require(!pos.closed, "MockPerps: position already closed");
        require(pos.vault == msg.sender, "MockPerps: caller not position vault");

        // Suppress unused param warning — partial close is Phase 3 scope.
        sizeUsd;

        orderKey = _freshKey();
        pendingOrders[orderKey] = PendingOrder({
            positionKey: positionKey,
            executeAfterBlock: block.number + executionDelay,
            vault: msg.sender,
            isClose: true,
            executed: false
        });
        // CR-01: emit OrderCreated so orchestrator can recover orderKey from tx receipt
        emit OrderCreated(orderKey, positionKey, msg.sender);
    }

    // =========================================================================
    // Keeper execution — async settlement (D-02 / D-13)
    // =========================================================================

    /// @notice Finalizes a pending order after `executionDelay` blocks have elapsed.
    /// @dev Callable by ANYONE (mimics GMX keeper permissionlessness, D-13). Reverts
    ///      if called before `executeAfterBlock`. Emits `OrderExecuted` on success.
    ///      For open orders: the position is already recorded; this just emits the event
    ///      that the orchestrator / journal waits for before publishing.
    ///      For close orders: closes the position (sets closed=true) then emits event.
    ///      Auto-liquidation is checked on close: if collateral + pnl <= 0 at settlement,
    ///      `PositionLiquidated` is emitted instead and position is wiped (D-12).
    /// @param orderKey The pending order key returned by openLong / openShort / closePosition.
    function executeOrder(bytes32 orderKey) external {
        PendingOrder storage order = pendingOrders[orderKey];
        require(order.vault != address(0), "MockPerps: order not found");
        require(!order.executed, "MockPerps: order already executed");
        // D-13: block delay enforcement — premature execution is tampering
        require(block.number >= order.executeAfterBlock, "MockPerps: too early");

        order.executed = true;
        bytes32 positionKey = order.positionKey;
        address vault = order.vault;

        if (order.isClose) {
            Position storage pos = positions[positionKey];
            require(!pos.closed, "MockPerps: position already closed");

            // Check liquidation before closing (D-12)
            int256 pnl = _computePnl(positionKey);
            int256 netValue = int256(pos.collateral) + pnl;

            if (netValue <= 0) {
                // Auto-liquidation: collateral + pnl <= 0 → wipe position
                pos.closed = true;
                emit PositionLiquidated(positionKey, vault);
            } else {
                pos.closed = true;
                emit OrderExecuted(orderKey, vault, positionKey);
            }
        } else {
            // Open order finalization: position already stored; emit event for orchestrator
            emit OrderExecuted(orderKey, vault, positionKey);
        }
    }

    /// @notice Triggers liquidation check for an open position outside of an executeOrder call.
    /// @dev Callable by anyone. Checks current mark price; if collateral + pnl <= 0, closes
    ///      position and emits PositionLiquidated (D-12). No-op if position is healthy or closed.
    /// @param positionKey The position to check.
    function checkLiquidation(bytes32 positionKey) external {
        Position storage pos = positions[positionKey];
        if (pos.vault == address(0) || pos.closed) return;

        int256 pnl = _computePnl(positionKey);
        int256 netValue = int256(pos.collateral) + pnl;

        if (netValue <= 0) {
            pos.closed = true;
            emit PositionLiquidated(positionKey, pos.vault);
        }
    }

    // =========================================================================
    // IPerpsAdapter — position enumeration (SETT-01)
    // =========================================================================

    /// @notice Returns all open (non-closed) position keys held by `vault`.
    /// @dev SettlementContract.endSession uses this to enumerate positions for the
    ///      in-contract drain (SETT-01). Filters out closed positions so the
    ///      settlement loop does not attempt to re-close already-closed keys.
    /// @param vault The mTokenVault address whose open position keys to return.
    /// @return keys Array of open (non-closed) position keys for the vault.
    function getOpenPositionKeys(address vault) external view override returns (bytes32[] memory keys) {
        bytes32[] storage allKeys = vaultPositionKeys[vault];
        uint256 len = allKeys.length;

        // First pass: count open positions
        uint256 openCount;
        for (uint256 i = 0; i < len; i++) {
            if (!positions[allKeys[i]].closed) openCount++;
        }

        // Second pass: populate result array
        keys = new bytes32[](openCount);
        uint256 idx;
        for (uint256 i = 0; i < len; i++) {
            if (!positions[allKeys[i]].closed) {
                keys[idx++] = allKeys[i];
            }
        }
    }

    // =========================================================================
    // IPerpsAdapter — NAV feed (D-03)
    // =========================================================================

    /// @notice Returns the current USDC value of all open positions held by `vault`.
    /// @dev CRITICAL (D-03 / CLAUDE.md §4): uses Chainlink mark prices ONLY. MUST NOT
    ///      use venue-internal prices. Value = collateral + pnlAfterFees (fees = 0 mock).
    ///      Staleness check: reverts if any feed's updatedAt is older than MAX_STALENESS,
    ///      unless an unexpired deployer override is active for that market.
    ///      Returns USDC 6-decimal value.
    /// @param vault The mTokenVault address whose open positions to value.
    /// @return total Total position value in USDC (6 decimals).
    function positionValueUSDC(address vault) external view override returns (uint256 total) {
        bytes32[] storage keys = vaultPositionKeys[vault];
        uint256 len = keys.length;
        for (uint256 i = 0; i < len; i++) {
            bytes32 key = keys[i];
            Position storage pos = positions[key];
            if (pos.closed) continue;

            int256 pnl = _computePnl(key);
            int256 netValue = int256(pos.collateral) + pnl;
            if (netValue > 0) {
                // casting to 'uint256' is safe because netValue > 0 (checked above)
                // and collateral is uint256 so netValue cannot exceed int256 max.
                // forge-lint: disable-next-line(unsafe-typecast)
                total += uint256(netValue);
            }
            // If netValue <= 0 the position contributes 0 to NAV (liquidatable)
        }
    }

    // =========================================================================
    // Admin — deployer-only controls (D-11)
    // =========================================================================

    /// @notice Sets a deployer-only mark price override for a market.
    /// @dev Override auto-expires at `expiresAt` (unix timestamp). After expiry,
    ///      `_markPrice` falls back to Chainlink. Used by test fixtures and demos.
    ///      On Sepolia / Robinhood Chain deploys, leave override expired so the live
    ///      demo tracks real Chainlink marks (D-11, T-0-mock mitigation).
    ///      Emits no event — state is observable via `markOverrides` getter.
    /// @param market Market string ("ETH", "BTC", or "SOL").
    /// @param price Override price in 8-decimal USD.
    /// @param expiresAt Unix timestamp after which this override is ignored.
    function setMarkOverride(string calldata market, int256 price, uint256 expiresAt) external onlyOwner {
        require(price > 0, "MockPerps: price must be positive");
        markOverrides[keccak256(bytes(market))] = PriceOverride({price: price, expiresAt: expiresAt});
    }

    /// @notice Updates the async execution delay (D-13).
    /// @dev Default N=1 for local anvil. Set N=3 for Sepolia/Robinhood deployments.
    ///      Change takes effect for all orders created AFTER this call.
    /// @param delay New execution delay in blocks.
    function setExecutionDelay(uint16 delay) external onlyOwner {
        executionDelay = delay;
    }

    // =========================================================================
    // Internal — key generation, price, PnL
    // =========================================================================

    /// @dev Opens a new position (long or short), records it, creates a pending order.
    /// @param market Market identifier string.
    /// @param sizeUsd Size in 1e30 USD units.
    /// @param leverage Leverage in 1e4-scaled bps (30000 = 3x).
    /// @param slippageBps Ignored by mock (no price impact).
    /// @param isLong True for long (positive signedSize), false for short (negative).
    /// @return orderKey Fresh order key for async tracking.
    function _openPosition(string calldata market, uint256 sizeUsd, uint256 leverage, uint256 slippageBps, bool isLong)
        internal
        returns (bytes32 orderKey)
    {
        require(sizeUsd > 0, "MockPerps: zero size");
        require(leverage > 0, "MockPerps: zero leverage");
        bytes32 marketKey = keccak256(bytes(market));
        require(feeds[marketKey] != address(0), "MockPerps: unsupported market");

        // Suppress unused param warning — slippage has no effect in mock.
        slippageBps;

        // Entry price from current mark (Chainlink or override).
        int256 entryPrice = _markPrice(market);

        // Collateral implied from size + leverage: collateral = sizeUsd / leverage
        // leverage is 1e4-scaled (30000 = 3x), so:
        //   collateral_usd_1e30 = sizeUsd * 10000 / leverage
        //   collateral_usdc = collateral_usd_1e30 / 1e30 * 1e6
        uint256 collateralRaw = (sizeUsd * 10_000) / leverage;
        uint256 collateralUsdc = collateralRaw / (USD_PRECISION / USDC_PRECISION);

        // signedSize: positive for long, negative for short. Stored in 1e30 USD units.
        // casting to 'int256' is safe because sizeUsd <= type(uint128).max in any realistic
        // trade scenario and GMX V2 uses 1e30-scaled USD which fits in int256.
        // forge-lint: disable-next-line(unsafe-typecast)
        int256 signedSize = isLong ? int256(sizeUsd) : -int256(sizeUsd);

        bytes32 positionKey = _freshKey();
        positions[positionKey] = Position({
            market: market,
            signedSize: signedSize,
            entryPrice: entryPrice,
            collateral: collateralUsdc,
            vault: msg.sender,
            closed: false
        });
        vaultPositionKeys[msg.sender].push(positionKey);

        orderKey = _freshKey();
        pendingOrders[orderKey] = PendingOrder({
            positionKey: positionKey,
            executeAfterBlock: block.number + executionDelay,
            vault: msg.sender,
            isClose: false,
            executed: false
        });
        // CR-01: emit OrderCreated so orchestrator can recover orderKey from tx receipt
        emit OrderCreated(orderKey, positionKey, msg.sender);
    }

    /// @dev Computes PnL for an open position using the financially-correct formula.
    ///      PnL (USDC 6-dec) = signedSize × (mark - entry) / (entry × 1e24)
    ///      where signedSize is 1e30-scaled USD notional and mark/entry are 8-decimal USD.
    ///
    ///      Derivation:
    ///        pnl_usd = price_delta_usd × (size_usd / entry_usd)
    ///                = (mark - entry)/1e8 × (signedSize/1e30) / (entry/1e8)
    ///                = (mark - entry) × signedSize / (entry × 1e30)
    ///        pnl_usdc_6dec = pnl_usd × 1e6
    ///                      = (mark - entry) × signedSize × 1e6 / (entry × 1e30)
    ///                      = (mark - entry) × signedSize / (entry × 1e24)
    ///
    ///      For a long (signedSize > 0): pnl > 0 when mark > entry (profit).
    ///      For a short (signedSize < 0): pnl < 0 when mark > entry (loss). Correct.
    ///      Uses the same `_markPrice` path (override-first, Chainlink fallback, staleness check).
    ///      Stale prices revert (D-03 enforcement during view call).
    /// @param positionKey The position to value.
    /// @return pnl Signed PnL in USDC (6 decimals). Positive = profit, negative = loss.
    function _computePnl(bytes32 positionKey) internal view returns (int256 pnl) {
        Position storage pos = positions[positionKey];
        // _markPrice may revert on staleness — intentional (D-03 enforcement)
        int256 mark = _markPrice(pos.market);
        int256 entry = pos.entryPrice;
        // CR-04: safe degradation — if entry is somehow 0 (corrupt storage), return 0 PnL
        // rather than reverting and permanently bricking positionValueUSDC/totalAssets.
        // _openPosition sets entryPrice from _markPrice which requires answer > 0, so this
        // guard is defensive. A revert here would DoS the vault permanently with no recovery.
        if (entry <= 0) return 0;

        // pnl_usdc = signedSize * (mark - entry) / (entry * 1e24)
        // Division is safe: entry > 0 (asserted), 1e24 > 0 always.
        int256 priceDelta = mark - entry;
        // Intermediate: priceDelta * signedSize may overflow for very large positions,
        // but at realistic trade sizes (sizeUsd <= 1e12 * 1e30 = 1e42) and typical
        // price deltas (< 1e12 for any real asset), product stays within int256.
        int256 numerator = priceDelta * pos.signedSize;
        // scaleFactor = entry * 1e24
        int256 denominator = entry * int256(1e24);
        pnl = numerator / denominator;
    }

    /// @dev Returns the effective mark price for a market.
    ///      Priority: (1) unexpired deployer override → (2) Chainlink with staleness check.
    ///      Reverts if Chainlink price is stale and no valid override exists (D-03).
    ///      Reverts if market feed is not registered.
    /// @param market Market string ("ETH", "BTC", or "SOL").
    /// @return price Mark price in 8-decimal USD.
    function _markPrice(string memory market) internal view returns (int256 price) {
        bytes32 marketKey = keccak256(bytes(market));

        // Check for unexpired deployer override (D-11)
        PriceOverride storage ov = markOverrides[marketKey];
        // solhint-disable-next-line not-rely-on-time
        if (ov.price > 0 && block.timestamp <= ov.expiresAt) {
            return ov.price;
        }

        // Fall back to Chainlink (D-03)
        address feed = feeds[marketKey];
        require(feed != address(0), "MockPerps: no feed for market");

        (uint80 roundId, int256 answer,, uint256 updatedAt, uint80 answeredInRound) = _latestRoundData(feed);
        // CR-03: canonical Chainlink stale-round guards (in addition to MAX_STALENESS check)
        require(updatedAt != 0, "MockPerps: round not complete");
        require(answeredInRound >= roundId, "MockPerps: stale round");
        // Staleness check: revert if price is older than MAX_STALENESS (D-03)
        // slither-disable-next-line timestamp
        require(block.timestamp - updatedAt <= MAX_STALENESS, "MockPerps: stale price");
        require(answer > 0, "MockPerps: non-positive price");
        return answer;
    }

    /// @dev Thin wrapper around AggregatorV3Interface.latestRoundData for interface compat.
    function _latestRoundData(address feed)
        internal
        view
        returns (uint80 roundId, int256 answer, uint256 startedAt, uint256 updatedAt, uint80 answeredInRound)
    {
        // Call-level assembly not needed; use low-level staticcall for interface safety
        // since MockChainlinkAggregator + real Chainlink have the same shape.
        bytes memory data = abi.encodeWithSignature("latestRoundData()");
        (bool success, bytes memory result) = feed.staticcall(data);
        require(success, "MockPerps: feed call failed");
        (roundId, answer, startedAt, updatedAt, answeredInRound) =
            abi.decode(result, (uint80, int256, uint256, uint256, uint80));
    }

    /// @dev Generates a fresh unique key by hashing msg.sender + block + nonce.
    function _freshKey() internal returns (bytes32) {
        return keccak256(abi.encodePacked(msg.sender, block.number, _nonce++));
    }
}

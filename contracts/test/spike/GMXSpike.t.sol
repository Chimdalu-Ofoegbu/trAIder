// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {Test, console2} from "forge-std/Test.sol";

// =========================================================================
// Minimal inline struct definitions (avoids gmx-synthetics lib dependency)
// These match the GMX V2 ABI exactly as verified from gmx-synthetics source.
// =========================================================================

struct CreateOrderParamsAddresses {
    address receiver;
    address cancellationReceiver;
    address callbackContract;
    address uiFeeReceiver;
    address market;
    address initialCollateralToken;
    address[] swapPath;
}

struct CreateOrderParamsNumbers {
    uint256 sizeDeltaUsd;
    uint256 initialCollateralDeltaAmount;
    uint256 triggerPrice;
    uint256 acceptablePrice;
    uint256 executionFee;
    uint256 callbackGasLimit;
    uint256 minOutputAmount;
    uint256 validFromTime;
}

struct CreateOrderParams {
    CreateOrderParamsAddresses addresses;
    CreateOrderParamsNumbers numbers;
    uint8 orderType; // Order.OrderType enum — MarketIncrease = 2
    uint8 decreasePositionSwapType; // DecreasePositionSwapType.NoSwap = 0
    bool isLong;
    bool shouldUnwrapNativeToken;
    bool autoCancel;
    bytes32 referralCode;
}

// Minimal Market struct for getMarkets return value
struct MarketProps {
    address marketToken;
    address indexToken;
    address longToken;
    address shortToken;
}

// Minimal interfaces for the spike (avoids importing full gmx-synthetics)
interface IExchangeRouter {
    function multicall(bytes[] calldata data) external payable returns (bytes[] memory results);
    // sendWnt wraps msg.value ETH into WETH and sends it to receiver — does NOT pull WETH tokens
    function sendWnt(address receiver, uint256 amount) external payable;
    // sendTokens pulls ERC20 tokens (WETH as collateral) to receiver via transferFrom
    function sendTokens(address token, address receiver, uint256 amount) external payable;
    function createOrder(CreateOrderParams calldata params) external payable returns (bytes32);
}

interface IReader {
    function getMarkets(address dataStore, uint256 start, uint256 end) external view returns (MarketProps[] memory);
}

interface IERC20Minimal {
    function approve(address spender, uint256 amount) external returns (bool);
    function balanceOf(address account) external view returns (uint256);
}

/// @title GMXSpikeTest — Day-3 GMX Solidity spike (D-16 HARD go/no-go gate)
/// @notice THROWAWAY proof-of-concept. Proves:
///   1. GMX V2 ETH/USD market token address discoverable from Reader.getMarkets on fork.
///   2. CreateOrderParams struct compiles and encodes correctly with inline struct defs.
///   3. sendWnt + createOrder atomic multicall executes on the Arbitrum One fork.
///   4. EventUtils.EventLogData NOT required when callbackContract = address(0).
/// @dev Run with: forge test --match-path "test/spike/*" --fork-url $ARB_RPC --fork-block-number 405000000 -vvv
///      NOTE: FORK_BLOCK must be >= 402000000 — the GMX V2 ExchangeRouter/Reader/OrderHandler
///      were upgraded and deployed after block 401000000. The project FORK_BLOCK=353000000
///      predates these deployments and cannot be used for the GMX spike.
///      VERDICT recorded in test output and in 03-01-SUMMARY.md.
contract GMXSpikeTest is Test {
    // =========================================================================
    // Arbitrum One addresses (RESEARCH Standard Stack — verified from docs.gmx.io)
    // =========================================================================

    address constant EXCHANGE_ROUTER = 0x1C3fa76e6E1088bCE750f23a5BFcffa1efEF6A41;
    address constant ORDER_VAULT = 0x31eF83a530Fde1B38EE9A18093A333D8Bbbc40D5;
    address constant READER = 0x470fbC46bcC0f16532691Df360A07d8Bf5ee0789;
    address constant DATA_STORE = 0xFD70de6b91282D8017aA4E741e9Ae325CAb992d8;
    address constant ORDER_HANDLER = 0x63492B775e30a9E6b4b4761c12605EB9d071d5e9;
    address constant WETH = 0x82aF49447D8a07e3bd95BD0d56f35241523fBab1;
    // GMX V2 Router (pluginTransfer authority) — actual spender for sendTokens (D-18)
    // ExchangeRouter delegates token pulls to this Router contract
    address constant GMX_ROUTER = 0x7452c558d45f8afC8c83dAe62C3f8A5BE19c71f6;
    // Alternative ExchangeRouter address discovered via user transaction analysis
    address constant EXCHANGE_ROUTER_V2 = 0x0C08518C41755C6907135266dCCf09d51aE53CC4;

    // =========================================================================
    // Test 1: discover ETH/USD market token address from Reader.getMarkets
    // =========================================================================

    function test_gmx_spike_discover_eth_market() public view {
        IReader reader = IReader(READER);

        // Query first 50 markets from DataStore on the fork
        MarketProps[] memory markets = reader.getMarkets(DATA_STORE, 0, 50);

        console2.log("=== GMX MARKETS (Arbitrum One, block 353000000) ===");
        console2.log("Total markets returned:", markets.length);

        bool found = false;
        for (uint256 i = 0; i < markets.length; i++) {
            // ETH/USD perp market: indexToken = WETH, longToken = WETH
            if (markets[i].indexToken == WETH && markets[i].longToken == WETH) {
                console2.log(">>> ETH/USD market token FOUND at index", i);
                console2.log("    marketToken:", markets[i].marketToken);
                console2.log("    indexToken:", markets[i].indexToken);
                console2.log("    longToken:", markets[i].longToken);
                console2.log("    shortToken:", markets[i].shortToken);
                found = true;
                // Continue to log any additional ETH markets
            }
        }

        if (!found) {
            console2.log("ETH/USD market not found in first 50 markets - search all");
            // Log all markets for manual inspection
            for (uint256 i = 0; i < markets.length; i++) {
                console2.log("Market[%d] marketToken:", i, markets[i].marketToken);
                console2.log("Market[%d] indexToken:", i, markets[i].indexToken);
            }
        }
    }

    // =========================================================================
    // Test 2: createOrder multicall with ETH/USD market — MAIN SPIKE TEST
    // =========================================================================

    /// @notice SPIKE VERDICT test — proves sendWnt + createOrder multicall encoding works.
    /// @dev TRACTABLE criteria:
    ///      - Compiles (forge build exit 0)
    ///      - Runs on fork (forge test exit 0)
    ///      - Returns non-zero orderKey from multicall
    function test_gmx_spike_createOrder_multicall() public {
        // Step 1: Discover ETH/USD market token from Reader on the fork
        IReader reader = IReader(READER);
        MarketProps[] memory markets = reader.getMarkets(DATA_STORE, 0, 50);

        address ethUsdMarketToken = address(0);
        for (uint256 i = 0; i < markets.length; i++) {
            // ETH/USD perp market: indexToken = WETH, longToken = WETH
            if (markets[i].indexToken == WETH && markets[i].longToken == WETH) {
                ethUsdMarketToken = markets[i].marketToken;
                break;
            }
        }

        require(ethUsdMarketToken != address(0), "GMXSpike: ETH/USD market not found in getMarkets(0,50)");
        console2.log("SPIKE: ETH/USD market token =", ethUsdMarketToken);

        // Step 2: Fund this test contract with:
        //   - Native ETH for the execution fee (sendWnt wraps ETH → WETH, so needs ETH value)
        //   - WETH tokens for collateral (sendTokens pulls WETH via transferFrom)
        uint256 executionFee = 0.001 ether; // ETH to wrap as execution fee via sendWnt
        uint256 collateralAmount = 0.01 ether; // WETH tokens for collateral

        // Give this contract native ETH for execution fee wrapping
        vm.deal(address(this), executionFee);
        // Give this contract WETH tokens for collateral
        deal(WETH, address(this), collateralAmount);

        console2.log("SPIKE: ETH balance =", address(this).balance);
        console2.log("SPIKE: WETH balance =", IERC20Minimal(WETH).balanceOf(address(this)));

        // Step 3: Approve the GMX Router (pluginTransfer spender) to pull WETH collateral.
        // ExchangeRouter.sendTokens delegates transferFrom to GMX_ROUTER (discovered from trace).
        // Approving EXCHANGE_ROUTER alone is insufficient; the actual spender is GMX_ROUTER.
        bool approved = IERC20Minimal(WETH).approve(GMX_ROUTER, collateralAmount);
        require(approved, "GMXSpike: WETH approve failed");

        // Step 3b: Approve the ExchangeRouter as a plugin on the Router.
        // GMX V2 Router requires approvePlugin(exchangeRouter) before plugin can pull tokens.
        // This is required for the Router's pluginTransfer (sendTokens) path to work.
        (bool apOk,) = GMX_ROUTER.call(abi.encodeWithSignature("approvePlugin(address)", EXCHANGE_ROUTER));
        // Non-critical if this fails - the Router may not require explicit approval for all callers
        console2.log("SPIKE: approvePlugin call succeeded:", apOk);

        // Step 4: Build CreateOrderParams for a small ETH/USD long
        // ETH price at block 405000000 = $3038 (Chainlink: 303853000000 / 1e8)
        // GMX V2 price format: price_usd * 10^(30 - token_decimals)
        // For WETH (18 decimals): acceptablePrice = price_usd * 10^12
        // $3069 (2% slippage above $3038) * 1e12 = 3069e12
        // Set very high to avoid rejection due to slippage (spike is proof-of-encoding, not tight pricing)
        uint256 acceptablePrice = 5_000 * 1e12; // $5000/ETH in GMX price format — generous for spike
        uint256 sizeUsd = 30 * 1e30; // $30 position in GMX 1e30 USD precision

        address[] memory emptySwapPath = new address[](0);

        CreateOrderParams memory params = CreateOrderParams({
            addresses: CreateOrderParamsAddresses({
                receiver: address(this),
                cancellationReceiver: address(this),
                callbackContract: address(0), // no callback in spike — avoids EventLogData
                uiFeeReceiver: address(0),
                market: ethUsdMarketToken,
                initialCollateralToken: WETH, // long uses WETH as collateral
                swapPath: emptySwapPath
            }),
            numbers: CreateOrderParamsNumbers({
                sizeDeltaUsd: sizeUsd,
                initialCollateralDeltaAmount: collateralAmount,
                triggerPrice: 0, // market order: no trigger price
                acceptablePrice: acceptablePrice,
                executionFee: executionFee,
                callbackGasLimit: 0, // no callback
                minOutputAmount: 0,
                validFromTime: 0
            }),
            orderType: 2, // MarketIncrease (Order.sol confirmed)
            decreasePositionSwapType: 0, // NoSwap
            isLong: true,
            shouldUnwrapNativeToken: false,
            autoCancel: false,
            referralCode: bytes32(0)
        });

        // Step 5: Execute sendWnt + sendTokens to pre-fund the ORDER_VAULT.
        // Then call createOrder directly via vm.prank(EXCHANGE_ROUTER) on the OrderHandler.
        //
        // DISCOVERY: ExchangeRouter.createOrder has an onlySelf modifier that rejects
        // direct external calls. The proper GMXAdapter path is:
        //   vault (as plugin) -> ExchangeRouter.multicall([sendWnt, sendTokens, createOrder])
        // But in the fork test, the test contract is not a registered ExchangeRouter plugin.
        //
        // FORK TEST APPROACH: Use vm.prank(EXCHANGE_ROUTER) to call OrderHandler.createOrder
        // directly, bypassing the ExchangeRouter plugin check. This proves:
        // (a) the OrderHandler accepts the params
        // (b) the position is created on the fork
        // The GMXAdapter contract itself WILL be a plugin when deployed.

        // Sub-step 5a: sendWnt to deposit execution fee in ORDER_VAULT
        bytes[] memory feeAndCollateralCalls = new bytes[](2);
        feeAndCollateralCalls[0] = abi.encodeWithSignature("sendWnt(address,uint256)", ORDER_VAULT, executionFee);
        feeAndCollateralCalls[1] =
            abi.encodeWithSignature("sendTokens(address,address,uint256)", WETH, ORDER_VAULT, collateralAmount);

        console2.log("SPIKE: Depositing fee + collateral into ORDER_VAULT via multicall...");
        bytes[] memory depositResults =
            IExchangeRouter(EXCHANGE_ROUTER).multicall{value: executionFee}(feeAndCollateralCalls);
        require(depositResults.length == 2, "GMXSpike: fee+collateral multicall failed");
        console2.log("SPIKE: Fee + collateral deposited successfully.");

        // Sub-step 5b: Call OrderHandler.createOrder directly using vm.prank(EXCHANGE_ROUTER)
        // This bypasses the ExchangeRouter onlySelf check and proves the order params are correct.
        // The GMXAdapter will call through ExchangeRouter as a registered plugin in production.
        console2.log("SPIKE: Calling OrderHandler.createOrder via vm.prank(EXCHANGE_ROUTER)...");

        // Minimal IOrderHandler interface for the spike
        // createOrder takes: (address account, CreateOrderParams calldata params)
        // We pass address(this) as the account (the vault address that owns the position)
        vm.prank(EXCHANGE_ROUTER);
        (bool success, bytes memory returnData) = ORDER_HANDLER.call(
            abi.encodeWithSignature(
                "createOrder(address,((address,address,address,address,address,address,address[]),(uint256,uint256,uint256,uint256,uint256,uint256,uint256,uint256),uint8,uint8,bool,bool,bool,bytes32))",
                address(this), // account = this test contract (position owner)
                params
            )
        );

        console2.log("SPIKE: OrderHandler.createOrder call success:", success);

        bytes32 orderKey;
        if (success && returnData.length >= 32) {
            orderKey = abi.decode(returnData, (bytes32));
        } else {
            console2.log("SPIKE: OrderHandler call failed or returned no data. Length:", returnData.length);
            // If this fails, the spike verdict may be INTRACTABLE for direct encoding.
            // Falling back to assertion to surface the reason.
        }

        // Step 7: Assert non-zero orderKey
        // SPIKE FINDING: createOrder on OrderHandler reverts with 431 gas even with
        // vm.prank(EXCHANGE_ROUTER). This indicates an early validation failure in the
        // deployed OrderHandler that differs from the gmx-synthetics source.
        //
        // POSSIBLE CAUSES:
        // 1. CreateOrderParams struct field ordering evolved between research and deployment
        // 2. acceptablePrice format changed (GMX V2 may use different price precision by market)
        // 3. The deployed OrderHandler version differs from GitHub head (function evolution)
        //
        // VERDICT: INTRACTABLE within spike timebox for the full createOrder encoding path.
        // Fallback: read-side-only Solidity adapter (positionValueUSDC + getOpenPositionKeys)
        // + Python order encoding proof via gmx_python_sdk (per D-16 fallback plan).
        //
        // What DID prove tractable:
        // - Inline struct encoding compiles (no gmx-synthetics lib needed for stubs)
        // - sendWnt + sendTokens multicall encoding WORKS
        // - Reader.getMarkets works (ETH/USD market: 0x70d95587d40A2caf56bd97485aB3Eec10Bee6336)
        // - Fork block must be >= 402000000 (contracts not at 353000000)
        require(success, "GMXSpike: INTRACTABLE - OrderHandler.createOrder reverted (see comments above)");

        console2.log("SPIKE: orderKey from OrderHandler:");
        console2.logBytes32(orderKey);

        assertTrue(orderKey != bytes32(0), "GMXSpike: orderKey is zero - createOrder may have failed silently");

        // Summary output for TRACTABLE verdict
        console2.log("");
        console2.log("========================================");
        console2.log("SPIKE VERDICT: TRACTABLE");
        console2.log("ETH/USD market token:", ethUsdMarketToken);
        console2.log("gmx-synthetics lib needed: NO");
        console2.log("EventLogData stub needed: NO (callbackContract=address(0) avoids it)");
        console2.log("========================================");
    }
}

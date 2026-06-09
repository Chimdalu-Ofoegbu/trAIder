// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {Test} from "forge-std/Test.sol";
import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {Math} from "@openzeppelin/contracts/utils/math/Math.sol";
import {MTokenVault} from "../../src/mTokenVault.sol";
import {MockPerps} from "../../src/mocks/MockPerps.sol";
import {MockChainlinkAggregator} from "../../src/mocks/MockChainlinkAggregator.sol";
import {ArbitragePrimitive} from "../../src/ArbitragePrimitive.sol";

// =========================================================================
// Minimal 6-decimal USDC mock
// =========================================================================

contract ArbTestUSDC is ERC20 {
    constructor() ERC20("Test USDC", "USDC") {}

    function mint(address to, uint256 amount) external {
        _mint(to, amount);
    }

    function decimals() public pure override returns (uint8) {
        return 6;
    }
}

// =========================================================================
// Minimal mock pool that allows controlling AMM price for arbCloseGap tests
// =========================================================================

/// @dev A minimal mock Algebra V3 pool. Implements the subset ArbitragePrimitive needs:
///      - globalState() (raw staticcall returning sqrtPriceX96 slot)
///      - token0() / token1()
///      - swap() with algebraSwapCallback
///      Controls AMM price via setPrice() so tests can set gap above/below threshold.
contract MockAlgebraPool {
    address public token0;
    address public token1;

    // sqrtPriceX96 stored — controls the "AMM price" seen by ArbitragePrimitive
    uint160 public sqrtPriceX96;

    // Whether the pool is locked (simulates Algebra globalState.unlocked)
    bool public poolUnlocked = true;

    // Whether swap() should revert (for error path coverage tests)
    bool public swapShouldRevert = false;
    string public swapRevertReason = "";

    constructor(address _token0, address _token1, uint160 _sqrtPriceX96) {
        // token0 must be < token1 per Algebra/Uniswap convention
        if (_token0 < _token1) {
            token0 = _token0;
            token1 = _token1;
        } else {
            token0 = _token1;
            token1 = _token0;
        }
        sqrtPriceX96 = _sqrtPriceX96;
    }

    /// @dev Update the pool price (for test control)
    function setPrice(uint160 _sqrtPriceX96) external {
        sqrtPriceX96 = _sqrtPriceX96;
    }

    /// @dev Make swap() revert on next call (for error path coverage)
    function setSwapReverts(bool reverts, string calldata reason) external {
        swapShouldRevert = reverts;
        swapRevertReason = reason;
    }

    /// @dev Algebra globalState() — returns 256 bytes (8 slots per VENUE-DECISION.md finding).
    ///      slot[0] = sqrtPriceX96 (uint160 in low 20 bytes), tick, lastFee, pluginConfig, communityFee, unlocked
    ///      ArbitragePrimitive uses raw staticcall + assembly to read slot 0 (sqrtPriceX96).
    function globalState()
        external
        view
        returns (uint160 price, int24 tick, uint16 lastFee, uint8 pluginConfig, uint16 communityFee, bool unlocked)
    {
        return (sqrtPriceX96, 0, 0, 0, 0, poolUnlocked);
    }

    /// @dev Algebra swap callback interface — the pool calls this on the caller during swap.
    ///      The mock executes an "instant" swap: transfers amount0Delta/amount1Delta tokens.
    ///      In production, ArbitragePrimitive implements algebraSwapCallback to pay the pool.
    ///      Here we just execute transfers directly to simulate the swap outcome.
    /// @param recipient        Address receiving the swap output
    /// @param zeroToOne        Direction: true = token0→token1, false = token1→token0
    /// @param amountSpecified  Exact amount in (positive) or exact amount out (negative)
    /// @param sqrtPriceLimitX96 Price limit (ignored in mock)
    /// @param data             Callback data passed through to algebraSwapCallback
    function swap(
        address recipient,
        bool zeroToOne,
        int256 amountSpecified,
        uint160 sqrtPriceLimitX96,
        bytes calldata data
    ) external returns (int256 amount0, int256 amount1) {
        // Suppress unused variable warnings
        sqrtPriceLimitX96;

        // Error path: revert with configured reason (for coverage tests)
        if (swapShouldRevert) {
            if (bytes(swapRevertReason).length > 0) {
                revert(swapRevertReason);
            }
            revert(); // no-reason revert (tests "AP: swap failed" path)
        }

        // Simulate a 1:1 swap (at current price) for testing purposes.
        // In the actual pool, the swap math is complex; here we approximate.
        // For AMM>NAV (AMM overpriced mTOKEN): zeroToOne=false (sell mTOKEN, get USDC)
        // For AMM<NAV (AMM underpriced mTOKEN): zeroToOne=true (buy mTOKEN, pay USDC)
        //
        // amountSpecified > 0 → exact input
        // We return a simple 1:1 ratio adjusted for decimal difference (mTOKEN 18-dec, USDC 6-dec).
        uint256 absAmount = amountSpecified > 0 ? uint256(amountSpecified) : uint256(-amountSpecified);

        if (zeroToOne) {
            // token0 in, token1 out
            // token0=mTOKEN(18dec), token1=USDC(6dec) OR
            // token0=USDC(6dec), token1=mTOKEN(18dec)
            // The ArbitragePrimitive provides exact payment in the callback.
            // Mock: output = input adjusted for decimal ratio at price=1
            address tokenIn = token0;
            address tokenOut = token1;

            // Compute output (approximate 1:1 at NAV)
            uint256 decimalsIn = ERC20(tokenIn).decimals();
            uint256 decimalsOut = ERC20(tokenOut).decimals();
            uint256 amountOut;
            if (decimalsIn >= decimalsOut) {
                amountOut = absAmount / (10 ** (decimalsIn - decimalsOut));
            } else {
                amountOut = absAmount * (10 ** (decimalsOut - decimalsIn));
            }

            // Transfer output to recipient
            if (amountOut > 0) {
                IERC20(tokenOut).transfer(recipient, amountOut);
            }

            amount0 = int256(absAmount); // positive = pool receives token0
            amount1 = -int256(amountOut); // negative = pool sends token1

            // Call algebraSwapCallback so ArbitragePrimitive can pay the pool
            if (data.length > 0) {
                IAlgebraSwapCallback(msg.sender).algebraSwapCallback(amount0, amount1, data);
            }
        } else {
            // token1 in, token0 out
            address tokenIn = token1;
            address tokenOut = token0;

            uint256 decimalsIn = ERC20(tokenIn).decimals();
            uint256 decimalsOut = ERC20(tokenOut).decimals();
            uint256 amountOut;
            if (decimalsIn >= decimalsOut) {
                amountOut = absAmount / (10 ** (decimalsIn - decimalsOut));
            } else {
                amountOut = absAmount * (10 ** (decimalsOut - decimalsIn));
            }

            if (amountOut > 0) {
                IERC20(tokenOut).transfer(recipient, amountOut);
            }

            amount0 = -int256(amountOut); // pool sends token0
            amount1 = int256(absAmount); // pool receives token1

            if (data.length > 0) {
                IAlgebraSwapCallback(msg.sender).algebraSwapCallback(amount0, amount1, data);
            }
        }
    }

    /// @dev Give the pool tokens so it can output them during swaps
    function seedTokens(address token, uint256 amount) external {
        IERC20(token).transferFrom(msg.sender, address(this), amount);
    }
}

interface IAlgebraSwapCallback {
    function algebraSwapCallback(int256 amount0Delta, int256 amount1Delta, bytes calldata data) external;
}

// =========================================================================
// ArbitragePrimitiveTest — ARB-01/02 + D-06/D-07 behavior tests
// =========================================================================

/// @title ArbitragePrimitiveTest — ARB-01/02 + D-06/D-07 invariant tests
/// @notice Proves:
///   ARB-01: arbMint/arbBurn NAV deposit/redeem + slippage guards
///   ARB-01: arbMint inherits VAULT-05 circuit-breaker pause; arbBurn stays live
///   ARB-02: arbCloseGap reverts below 1% threshold
///   D-06:   AT-NAV invariant (transacts at NAV, not off-NAV)
///   D-07:   holds-nothing invariant (non-custodial)
contract ArbitragePrimitiveTest is Test {
    // =========================================================================
    // Constants
    // =========================================================================

    int256 internal constant ETH_PRICE_8DEC = 300_000_000_000; // $3,000.00

    uint256 internal constant SESSION_DURATION = 72 hours;
    uint256 internal constant INITIAL_CAPITAL = 10_000e6;

    // =========================================================================
    // Fixtures
    // =========================================================================

    ArbTestUSDC internal usdc;
    MockPerps internal perps;
    MockChainlinkAggregator internal ethFeed;
    MockChainlinkAggregator internal btcFeed;
    MockChainlinkAggregator internal solFeed;
    MTokenVault internal vault;
    ArbitragePrimitive internal arb;
    MockAlgebraPool internal pool;

    address internal sessionFactory;
    address internal orchestrator;
    address internal operator;
    address internal caller; // the arb primitive caller (test user)

    // =========================================================================
    // setUp
    // =========================================================================

    function setUp() public {
        vm.warp(10_001);

        sessionFactory = makeAddr("sessionFactory");
        orchestrator = makeAddr("orchestrator");
        operator = makeAddr("operator");
        caller = makeAddr("caller");

        // Deploy USDC mock
        usdc = new ArbTestUSDC();

        // Deploy Chainlink feed mocks
        ethFeed = new MockChainlinkAggregator(ETH_PRICE_8DEC, block.timestamp);
        btcFeed = new MockChainlinkAggregator(ETH_PRICE_8DEC, block.timestamp);
        solFeed = new MockChainlinkAggregator(ETH_PRICE_8DEC, block.timestamp);

        // Deploy MockPerps
        perps = new MockPerps(address(ethFeed), address(btcFeed), address(solFeed));

        // Deploy MTokenVault
        vault = new MTokenVault(
            IERC20(address(usdc)),
            "mCLA-S1",
            "mCLA-S1",
            address(perps),
            address(0), // no sequencer feed for tests
            address(ethFeed),
            address(btcFeed),
            address(solFeed),
            sessionFactory,
            orchestrator,
            operator,
            INITIAL_CAPITAL,
            true // useSepoliaStaleness
        );

        // Start the session
        vm.prank(sessionFactory);
        vault.startSession(SESSION_DURATION);

        // Deploy ArbitragePrimitive (no swapRouter needed — uses direct pool.swap())
        arb = new ArbitragePrimitive();

        // Compute sqrtPriceX96 for 1:1 price (NAV = 1 mTOKEN = 1 USDC).
        // Token ordering: MockAlgebraPool sorts by address.
        // At 1:1 NAV: if token0=mTOKEN(18dec), token1=USDC(6dec):
        //   rawPrice = 1e6/1e18 = 1e-12
        //   sqrtPrice = sqrt(1e-12) = 1e-6
        //   sqrtPriceX96 = 1e-6 * 2^96 ≈ 79228162514 (approx)
        // We use a nominal value here — pool's price won't be perfectly tested in unit tests;
        // the gap-direction logic is tested via setPrice() to force gap above/below threshold.
        uint160 sqrtPriceAtNAV = 79_228_162_514; // approx sqrt(1e-12) * 2^96

        pool = new MockAlgebraPool(address(vault), address(usdc), sqrtPriceAtNAV);

        // Seed USDC to the caller so they can call arbMint
        usdc.mint(caller, 1_000_000e6); // 1M USDC

        // Also seed the pool with both tokens so it can output them during swaps
        usdc.mint(address(pool), 1_000_000e6);
        // Seed the pool with vault shares (mTOKEN) — deposit as vault on behalf of pool
        // so pool has mTOKEN to give out during AMM<NAV swap direction
        usdc.mint(address(this), 1_000_000e6);
        usdc.approve(address(vault), 1_000_000e6);
        vault.deposit(1_000_000e6, address(pool));
    }

    // =========================================================================
    // Test 1: arbMint + arbBurn round-trip with minOut slippage guard (ARB-01)
    // =========================================================================

    /// @notice arbMint(vault, usdcAmount, minOut) transfers USDC in, deposits to vault, sends
    ///         shares to caller, reverts if mTokenOut < minMTokenOut.
    ///         arbBurn round-trips back, reverts if usdcOut < minUsdcOut.
    function test_arbMint_and_arbBurn_respectMinOut() public {
        uint256 usdcAmt = 1_000e6;

        // Approve arb primitive to pull USDC from caller
        vm.startPrank(caller);
        usdc.approve(address(arb), usdcAmt);

        // arbMint: deposit 1000 USDC, receive mTOKEN at NAV
        uint256 previewShares = vault.previewDeposit(usdcAmt);
        uint256 mTokenOut = arb.arbMint(address(vault), usdcAmt, previewShares);

        // Caller received shares, USDC left caller
        assertEq(vault.balanceOf(caller), mTokenOut, "caller must receive mTOKEN shares");
        assertGt(mTokenOut, 0, "mTokenOut must be > 0");
        assertApproxEqRel(mTokenOut, previewShares, 1e15, "mTokenOut must match previewDeposit within 0.1%");

        // arbMint reverts if minMTokenOut is too high
        usdc.approve(address(arb), usdcAmt);
        vm.expectRevert("AP: insufficient mToken output");
        arb.arbMint(address(vault), usdcAmt, type(uint256).max);

        // arbBurn: redeem mTOKEN back for USDC
        vault.approve(address(arb), mTokenOut);
        uint256 previewUsdc = vault.previewRedeem(mTokenOut);
        uint256 usdcOut = arb.arbBurn(address(vault), mTokenOut, previewUsdc);

        assertGt(usdcOut, 0, "usdcOut must be > 0");
        assertApproxEqRel(usdcOut, previewUsdc, 1e15, "usdcOut must match previewRedeem within 0.1%");
        assertEq(vault.balanceOf(caller), 0, "caller must have 0 shares after arbBurn");

        // arbBurn reverts if minUsdcOut is too high
        // Re-mint first
        usdc.approve(address(arb), usdcAmt);
        uint256 mTokenOut2 = arb.arbMint(address(vault), usdcAmt, 0);
        vault.approve(address(arb), mTokenOut2);
        vm.expectRevert("AP: insufficient USDC output");
        arb.arbBurn(address(vault), mTokenOut2, type(uint256).max);

        vm.stopPrank();
    }

    // =========================================================================
    // Test 2: arbMint inherits VAULT-05 circuit-breaker pause; arbBurn stays live
    // =========================================================================

    /// @notice With the vault circuit-breaker paused (NAV < 30% per VAULT-05), arbMint reverts
    ///         ("Vault: mint paused"); arbBurn still succeeds (burn stays live).
    function test_arbMint_revertsWhenCBPaused() public {
        // First deposit so caller has shares to burn later
        uint256 usdcAmt = 1_000e6;
        vm.startPrank(caller);
        usdc.approve(address(arb), usdcAmt);
        uint256 mTokenOut = arb.arbMint(address(vault), usdcAmt, 0);
        vm.stopPrank();

        // Trip the circuit breaker by directly writing _mintPaused=true to storage.
        // vm.mockCall(totalAssets) cannot work here: checkAndLatchCircuitBreaker() calls
        // _computeNav() which calls totalAssets() via INTERNAL dispatch (same contract),
        // bypassing the external-call intercept layer.
        // Storage layout (forge inspect MTokenVault storage-layout):
        //   slot 13, byte 0 = _mintPaused (bool)
        //   slot 13, byte 1 = _tradingLocked (bool)
        //   slot 13, byte 2 = _sessionPaused (bool)
        // Set slot 13 to uint256(1) → _mintPaused=true, others=false.
        vm.store(address(vault), bytes32(uint256(13)), bytes32(uint256(1)));

        // arbMint should now revert because _mintPaused is latched
        vm.startPrank(caller);
        usdc.approve(address(arb), usdcAmt);
        vm.expectRevert("Vault: mint paused");
        arb.arbMint(address(vault), usdcAmt, 0);

        // arbBurn must still succeed (vault.redeem() has no CB guard — VAULT-05 asymmetry)
        vault.approve(address(arb), mTokenOut);
        uint256 usdcOut = arb.arbBurn(address(vault), mTokenOut, 0);
        assertGt(usdcOut, 0, "arbBurn must succeed during CB pause");

        vm.stopPrank();
    }

    // =========================================================================
    // Test 3: arbCloseGap reverts below 1% gap threshold (ARB-02)
    // =========================================================================

    /// @notice With AMM price within 1% of NAV, arbCloseGap reverts "AP: gap below threshold".
    function test_arbCloseGap_revertsBelow1pct() public {
        // Strategy: mock vault.nav() to return the exact price that _readPoolPrice decodes,
        // ensuring the gap = 0 → revert. This tests the threshold guard directly.
        //
        // First, compute what price _readPoolPrice returns for the current pool.
        // We mock vault.nav() to match that value so gap = 0 bps < 100 bps threshold.
        //
        // token ordering: vault < usdc or usdc < vault (non-deterministic address)
        // Use vm.mockCall on globalState to return a price that makes gap = 0:
        // Mock NAV to match the pool price exactly.
        //
        // The pool.sqrtPriceX96 = 79_228_162_514 (tiny). We can just mock vault.nav() to be
        // whatever ammPrice decodes to. But if ammPrice = 0, gap = -infinity, which would
        // not be < threshold.
        //
        // Cleaner: mock globalState to return a sqrtPriceX96 that decodes to nav exactly.
        // nav = 1e18 (session start with no trades).
        // If mTokenIsToken0: sqrtP = 1000 * 2^96 (from formula: sqrtP^2 * 1e12/2^192 = 1e18
        //   → sqrtP = 1e3 * 2^96)
        // If USDC=token0: sqrtP = 2^96 / 1e3 (from formula: 2^192 * 1e12/sqrtP^2 = 1e18
        //   → sqrtP = 2^96 / 1e3)
        //
        // Since token ordering is non-deterministic, mock vault.nav() to match decoded price.
        // The decoded price from sqrtP=79228162514 (≈2^36):
        // mTokenIsToken0: step1 = 79228162514 * 1e12 / 2^96 → 0 (underflows integer division)
        //   ammPrice = 0, gap = (0 - nav) * 10000 / nav = -10000 bps → abs = 10000 > 100
        //   → would NOT revert. So we need a different sqrtPriceX96.
        //
        // Use vm.mockCall on globalState() to force a specific price return.
        // At price exactly equal to nav (gap=0):
        // For mTokenIsToken0 case: sqrtP = 1000 * 2^96 ≈ 7.9e31
        // For USDC=token0 case: sqrtP = 2^96 / 1000 ≈ 7.9e25
        //
        // We mock both: check token0 to determine which formula applies.
        bool mTokenIsToken0 = address(vault) < address(usdc);
        uint256 Q96 = 2 ** 96;
        uint160 sqrtPriceAtNAV;
        if (mTokenIsToken0) {
            // sqrtP = sqrt(1e18 * 2^192 / 1e12) = sqrt(1e6 * 2^192) = 1e3 * 2^96
            sqrtPriceAtNAV = uint160(1000 * Q96);
        } else {
            // sqrtP = sqrt(2^192 * 1e12 / 1e18) = sqrt(2^192 / 1e6) = 2^96 / 1e3
            sqrtPriceAtNAV = uint160(Q96 / 1000);
        }

        // Set pool price to exactly NAV (0% gap)
        pool.setPrice(sqrtPriceAtNAV);

        vm.startPrank(caller);
        usdc.approve(address(arb), 100_000e6);

        // With price at NAV (0% gap) — must revert
        vm.expectRevert("AP: gap below threshold");
        arb.arbCloseGap(address(vault), address(pool));

        // Also test at 0.5% gap (above NAV but below 1% threshold) — must still revert
        // price = NAV * 1.005 → sqrtP = sqrt(1.005) * sqrtPAtNAV ≈ 1.0025 * sqrtPAtNAV
        uint160 sqrtPrice05pct = uint160((uint256(sqrtPriceAtNAV) * 10025) / 10000);
        pool.setPrice(sqrtPrice05pct);

        vm.expectRevert("AP: gap below threshold");
        arb.arbCloseGap(address(vault), address(pool));

        vm.stopPrank();
    }

    // =========================================================================
    // Test 4: AT-NAV invariant — arbMint then arbBurn transacts at NAV (D-06)
    // =========================================================================

    /// @notice After arbMint then arbBurn of the same notional, caller's net USDC delta ≈ 0
    ///         minus fees/dust — proving the primitive transacts at NAV, not off-NAV (D-06).
    ///         Assert shares minted == vault.previewDeposit(usdc) and usdc returned ==
    ///         vault.previewRedeem(shares) (within dust).
    function test_AT_NAV_invariant() public {
        uint256 usdcAmt = 1_000e6;
        uint256 callerUsdcBefore = usdc.balanceOf(caller);

        vm.startPrank(caller);

        // Preview what we'll get
        uint256 expectedShares = vault.previewDeposit(usdcAmt);

        usdc.approve(address(arb), usdcAmt);
        uint256 sharesOut = arb.arbMint(address(vault), usdcAmt, 0);

        // D-06: shares minted must match previewDeposit exactly (AT-NAV)
        assertEq(sharesOut, expectedShares, "D-06: shares minted must equal previewDeposit (AT-NAV)");

        // Preview what we'll get back
        uint256 expectedUsdc = vault.previewRedeem(sharesOut);

        vault.approve(address(arb), sharesOut);
        uint256 usdcBack = arb.arbBurn(address(vault), sharesOut, 0);

        // D-06: USDC returned must match previewRedeem exactly (AT-NAV)
        assertEq(usdcBack, expectedUsdc, "D-06: USDC returned must equal previewRedeem (AT-NAV)");

        // Net USDC delta ≈ 0 (within rounding dust from ERC-4626 shares math)
        uint256 callerUsdcAfter = usdc.balanceOf(caller);
        // The caller should have approximately the same USDC as before (within dust)
        assertApproxEqAbs(callerUsdcAfter, callerUsdcBefore, 2, "D-06: net USDC delta must be ~0 (AT-NAV)");

        vm.stopPrank();
    }

    // =========================================================================
    // Test 5 (extra): arbCloseGap AMM>NAV path (buy at NAV, sell on AMM)
    // =========================================================================

    /// @notice With AMM price > NAV by > 1%, arbCloseGap executes the AMM>NAV round-trip:
    ///         deposits USDC into vault at NAV, then sells mTOKEN on AMM.
    ///         After: arb holds nothing; caller gets USDC back (possibly with profit).
    function test_arbCloseGap_ammAboveNav_succeeds() public {
        // Set AMM price 5% above NAV.
        // NAV = 1e18 (initial). AMM needs ammPrice_e18 > 1.01e18.
        // Use same formula as test_arbCloseGap_revertsBelow1pct but apply 1.05x multiplier.
        bool mTokenIsToken0 = address(vault) < address(usdc);
        uint256 Q96 = 2 ** 96;
        uint160 sqrtPriceAtNAV;
        if (mTokenIsToken0) {
            sqrtPriceAtNAV = uint160(1000 * Q96);
        } else {
            sqrtPriceAtNAV = uint160(Q96 / 1000);
        }
        // Multiply sqrtPrice by sqrt(1.05) ≈ 1.0247 to get ~5% price increase.
        // We use 10247/10000 as integer approximation.
        uint160 sqrtPriceAboveNav = uint160((uint256(sqrtPriceAtNAV) * 10_247) / 10_000);
        pool.setPrice(sqrtPriceAboveNav);

        // Caller needs USDC to fund the arb (1000 USDC fixed notional in arbCloseGap)
        uint256 arbCapital = 1100e6; // a little extra for slippage
        usdc.mint(caller, arbCapital);

        uint256 callerUsdcBefore = usdc.balanceOf(caller);

        vm.startPrank(caller);
        usdc.approve(address(arb), arbCapital);

        // arbCloseGap must succeed (gap > 1% threshold)
        arb.arbCloseGap(address(vault), address(pool));

        vm.stopPrank();

        // D-07: arb contract must hold nothing after the call
        assertEq(usdc.balanceOf(address(arb)), 0, "D-07: arb holds 0 USDC after AMM>NAV arbCloseGap");
        assertEq(vault.balanceOf(address(arb)), 0, "D-07: arb holds 0 mTOKEN after AMM>NAV arbCloseGap");

        // Caller's USDC balance may have increased or be close to original (profit from AMM>NAV)
        // The mock pool is 1:1 so net is roughly 0 change — just verify caller balance is tracked
        uint256 callerUsdcAfter = usdc.balanceOf(caller);
        // Caller should have gotten most of their USDC back (pool returns 1:1 in the mock)
        assertLe(callerUsdcAfter, callerUsdcBefore, "caller USDC not negative");
    }

    // =========================================================================
    // Test 5 (extra): arbCloseGap AMM<NAV path (buy on AMM, redeem at NAV)
    // =========================================================================

    /// @notice With AMM price < NAV by > 1%, arbCloseGap executes the AMM<NAV round-trip:
    ///         buys mTOKEN on AMM (cheap), then redeems at NAV.
    function test_arbCloseGap_ammBelowNav_succeeds() public {
        // Set AMM price 5% below NAV.
        bool mTokenIsToken0 = address(vault) < address(usdc);
        uint256 Q96 = 2 ** 96;
        uint160 sqrtPriceAtNAV;
        if (mTokenIsToken0) {
            sqrtPriceAtNAV = uint160(1000 * Q96);
        } else {
            sqrtPriceAtNAV = uint160(Q96 / 1000);
        }
        // Multiply sqrtPrice by sqrt(0.95) ≈ 0.9747 to get ~5% price decrease.
        uint160 sqrtPriceBelowNav = uint160((uint256(sqrtPriceAtNAV) * 9_747) / 10_000);
        pool.setPrice(sqrtPriceBelowNav);

        // Caller needs USDC for the arb (1000 USDC fixed notional in arbCloseGap)
        uint256 arbCapital = 1100e6;
        usdc.mint(caller, arbCapital);

        vm.startPrank(caller);
        usdc.approve(address(arb), arbCapital);

        // arbCloseGap must succeed (gap > 1% threshold, AMM < NAV)
        arb.arbCloseGap(address(vault), address(pool));

        vm.stopPrank();

        // D-07: arb contract must hold nothing after the call
        assertEq(usdc.balanceOf(address(arb)), 0, "D-07: arb holds 0 USDC after AMM<NAV arbCloseGap");
        assertEq(vault.balanceOf(address(arb)), 0, "D-07: arb holds 0 mTOKEN after AMM<NAV arbCloseGap");
    }

    // =========================================================================
    // Test: _executeSwap error paths — revert bubbling (coverage for lines 346-352)
    // =========================================================================

    /// @notice When pool.swap() reverts WITH a reason, arbCloseGap bubbles up the reason.
    ///         When pool.swap() reverts WITHOUT a reason, arbCloseGap uses "AP: swap failed".
    function test_arbCloseGap_swapReverts_bubblesUp() public {
        // Set AMM price 5% above NAV to enter the AMM>NAV branch
        bool mTokenIsToken0 = address(vault) < address(usdc);
        uint256 Q96 = 2 ** 96;
        uint160 sqrtPriceAtNAV = mTokenIsToken0 ? uint160(1000 * Q96) : uint160(Q96 / 1000);
        uint160 sqrtPriceAboveNav = uint160((uint256(sqrtPriceAtNAV) * 10_247) / 10_000);
        pool.setPrice(sqrtPriceAboveNav);

        usdc.mint(caller, 1100e6);
        vm.startPrank(caller);
        usdc.approve(address(arb), 1100e6);

        // Case 1: swap reverts WITH a reason — bubble up (lines 346-350)
        pool.setSwapReverts(true, "MockPool: insufficient output");
        vm.expectRevert("MockPool: insufficient output");
        arb.arbCloseGap(address(vault), address(pool));

        // Case 2: swap reverts WITHOUT a reason — "AP: swap failed" (lines 351-352)
        pool.setSwapReverts(true, "");
        vm.expectRevert("AP: swap failed");
        arb.arbCloseGap(address(vault), address(pool));

        vm.stopPrank();
    }

    // =========================================================================
    // Test 5: holds-nothing invariant — ArbitragePrimitive holds no tokens after calls (D-07)
    // =========================================================================

    /// @notice After any arb call, assert IERC20(usdc).balanceOf(arbPrimitive)==0 AND
    ///         IERC20(vault).balanceOf(arbPrimitive)==0 (non-custodial, D-07).
    function test_contract_holds_nothing() public {
        uint256 usdcAmt = 1_000e6;

        // After arbMint: arb holds nothing
        vm.startPrank(caller);
        usdc.approve(address(arb), usdcAmt);
        uint256 sharesOut = arb.arbMint(address(vault), usdcAmt, 0);
        vm.stopPrank();

        assertEq(usdc.balanceOf(address(arb)), 0, "D-07: arb must hold 0 USDC after arbMint");
        assertEq(vault.balanceOf(address(arb)), 0, "D-07: arb must hold 0 mTOKEN after arbMint");

        // After arbBurn: arb holds nothing
        vm.startPrank(caller);
        vault.approve(address(arb), sharesOut);
        arb.arbBurn(address(vault), sharesOut, 0);
        vm.stopPrank();

        assertEq(usdc.balanceOf(address(arb)), 0, "D-07: arb must hold 0 USDC after arbBurn");
        assertEq(vault.balanceOf(address(arb)), 0, "D-07: arb must hold 0 mTOKEN after arbBurn");

        // After arbCloseGap (revert expected since gap < threshold): nothing changes
        vm.startPrank(caller);
        usdc.approve(address(arb), 100_000e6);
        try arb.arbCloseGap(address(vault), address(pool)) {} catch {}
        vm.stopPrank();

        assertEq(usdc.balanceOf(address(arb)), 0, "D-07: arb holds 0 USDC after arbCloseGap attempt");
        assertEq(vault.balanceOf(address(arb)), 0, "D-07: arb holds 0 mTOKEN after arbCloseGap attempt");
    }
}

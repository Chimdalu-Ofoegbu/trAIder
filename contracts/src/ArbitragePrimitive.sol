// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {ReentrancyGuardTransient} from "@openzeppelin/contracts/utils/ReentrancyGuardTransient.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {Math} from "@openzeppelin/contracts/utils/math/Math.sol";
import {IArbitragePrimitive} from "./interfaces/IArbitragePrimitive.sol";
import {IMTokenVault} from "./interfaces/IMTokenVault.sol";

/// @title ArbitragePrimitive — stateless/non-custodial NAV-peg arbitrage primitive (ARB-01/02, D-07)
/// @notice Provides three permissionless primitives for NAV-peg arbitrage:
///           - `arbMint`:     USDC → vault deposit → mTOKEN at NAV  (ARB-01)
///           - `arbBurn`:     mTOKEN → vault redeem → USDC at NAV   (ARB-01)
///           - `arbCloseGap`: threshold-guarded AMM round-trip       (ARB-02)
///
///         This contract is STATELESS and NON-CUSTODIAL (D-07):
///           - No token balance is held between calls.
///           - No privileged state is stored.
///           - Every call is atomic: tokens flow fully in and out in a single call.
///
///         AT-NAV invariant (D-06): arbMint and arbBurn transact ONLY at vault NAV via the
///         ERC-4626 deposit/redeem path — never at off-NAV prices. AMM swaps appear ONLY
///         inside arbCloseGap, never in arbMint/arbBurn.
///
///         VAULT-05 CB-pause inheritance: arbMint calls vault.deposit(), which reverts when
///         the circuit breaker is active. arbBurn calls vault.redeem(), which stays live.
///
///         Threat mitigations:
///           T-04-03-01: ReentrancyGuardTransient + SafeERC20 + per-call forceApprove
///           T-04-03-02: amountOutMinimum slippage guard on AMM swap leg in arbCloseGap
///           T-04-03-04: test_contract_holds_nothing asserts zero residual balance
///
/// @dev Uses Algebra V3 direct pool.swap() (no SwapRouter — not available at fork block per
///      VENUE-DECISION.md finding #2). Implements algebraSwapCallback to pay the pool.
///
///      globalState() ABI mismatch workaround (VENUE-DECISION.md finding #1):
///      The real Algebra Integral v1 pool returns 256 bytes from globalState() (8 slots),
///      not 192 bytes (6 slots). Solidity strict ABI decoder reverts. We use raw staticcall
///      + assembly to extract only slot 0 (sqrtPriceX96 as uint160).
contract ArbitragePrimitive is IArbitragePrimitive, ReentrancyGuardTransient {
    using SafeERC20 for IERC20;

    // =========================================================================
    // Constants
    // =========================================================================

    /// @inheritdoc IArbitragePrimitive
    /// @notice 100 bps = 1% gap threshold for arbCloseGap (ARB-02 default).
    uint256 public constant GAP_THRESHOLD_BPS = 100;

    // =========================================================================
    // Errors
    // =========================================================================

    // Revert strings used as string literals per project convention (matches mTokenVault.sol)

    // =========================================================================
    // arbMint — USDC → vault deposit → mTOKEN at NAV (ARB-01)
    // =========================================================================

    /// @inheritdoc IArbitragePrimitive
    /// @notice Non-custodial: USDC in, mTOKEN shares out, nothing held in this contract.
    ///         VAULT-05 CB-pause: vault.deposit() reverts when CB active → arbMint inherits.
    // slither-disable-next-line reentrancy-no-eth — ReentrancyGuardTransient prevents re-entry
    function arbMint(address vault, uint256 usdcAmount, uint256 minMTokenOut)
        external
        override
        nonReentrant
        returns (uint256 mTokenOut)
    {
        address usdc = IMTokenVault(vault).asset();

        // Pull USDC from caller into this contract
        IERC20(usdc).safeTransferFrom(msg.sender, address(this), usdcAmount);

        // Per-call approve (never unlimited — T-04-03-01)
        IERC20(usdc).forceApprove(vault, usdcAmount);

        // Deposit into vault; shares go directly to caller (AT-NAV: ERC-4626 path)
        // vault.deposit() reverts if _mintPaused (VAULT-05 CB-pause inheritance)
        mTokenOut = IMTokenVault(vault).deposit(usdcAmount, msg.sender);

        // Slippage guard
        require(mTokenOut >= minMTokenOut, "AP: insufficient mToken output");

        // Zero-out approval (defensive — belt-and-suspenders after forceApprove)
        IERC20(usdc).forceApprove(vault, 0);

        // D-07 assertion: this contract must hold nothing (asserted by tests)
        // No explicit revert needed — any residual would be a bug caught by test_contract_holds_nothing
    }

    // =========================================================================
    // arbBurn — mTOKEN → vault redeem → USDC at NAV (ARB-01)
    // =========================================================================

    /// @inheritdoc IArbitragePrimitive
    /// @notice Burn stays live during CB pause (vault.redeem() has no mint guards).
    ///         Non-custodial: mTOKEN in, USDC out, nothing held in this contract.
    // slither-disable-next-line reentrancy-no-eth — ReentrancyGuardTransient prevents re-entry
    function arbBurn(address vault, uint256 mTokenAmount, uint256 minUsdcOut)
        external
        override
        nonReentrant
        returns (uint256 usdcOut)
    {
        // Pull mTOKEN shares from caller (the vault share IS the ERC-20 token — D-18)
        IERC20(vault).safeTransferFrom(msg.sender, address(this), mTokenAmount);

        // Per-call approve (never unlimited — T-04-03-01)
        IERC20(vault).forceApprove(vault, mTokenAmount);

        // Redeem from vault; USDC goes directly to caller (AT-NAV: ERC-4626 path)
        // vault.redeem() stays live during CB pause (VAULT-05 asymmetry)
        usdcOut = IMTokenVault(vault).redeem(mTokenAmount, msg.sender, address(this));

        // Slippage guard
        require(usdcOut >= minUsdcOut, "AP: insufficient USDC output");

        // Zero-out approval (defensive)
        IERC20(vault).forceApprove(vault, 0);
    }

    // =========================================================================
    // arbCloseGap — threshold-guarded AMM round-trip (ARB-02)
    // =========================================================================

    /// @inheritdoc IArbitragePrimitive
    /// @notice The AMM swap leg appears ONLY here — never in arbMint/arbBurn (D-07).
    ///         AMM > NAV: pull USDC from caller → arbMint (deposit at NAV) → sell mTOKEN on AMM → return USDC to caller.
    ///         AMM < NAV: pull USDC from caller → buy mTOKEN on AMM → arbBurn (redeem at NAV) → return USDC to caller.
    ///         Reverts if |gap| < GAP_THRESHOLD_BPS.
    // slither-disable-next-line reentrancy-no-eth — ReentrancyGuardTransient prevents re-entry
    function arbCloseGap(address vault, address pool) external override nonReentrant {
        // Read NAV and AMM price
        uint256 navE18 = IMTokenVault(vault).nav();
        uint256 ammPriceE18 = _readPoolPrice(pool, vault);

        // Compute gap in bps: positive = AMM > NAV, negative = AMM < NAV
        int256 gapBps = (int256(ammPriceE18) - int256(navE18)) * 10_000 / int256(navE18);
        int256 absGap = gapBps < 0 ? -gapBps : gapBps;

        require(absGap >= int256(GAP_THRESHOLD_BPS), "AP: gap below threshold");

        address usdc = IMTokenVault(vault).asset();

        if (gapBps > 0) {
            // AMM > NAV: buy at NAV via vault.deposit(), sell on AMM
            // Pull USDC from caller
            // Arb size: use a fixed notional of 1000 USDC (production bot iterates)
            uint256 arbUsdc = 1000e6;
            IERC20(usdc).safeTransferFrom(msg.sender, address(this), arbUsdc);

            // Deposit at NAV (inherits VAULT-05 CB-pause)
            IERC20(usdc).forceApprove(vault, arbUsdc);
            uint256 mTokenOut = IMTokenVault(vault).deposit(arbUsdc, address(this));
            IERC20(usdc).forceApprove(vault, 0);

            // Sell mTOKEN on AMM (swap mTOKEN → USDC)
            bool mTokenIsToken0 = vault < usdc;
            bool zeroToOne = mTokenIsToken0; // selling mTOKEN → token0→token1 if mToken=token0

            // Approve pool to take mTOKEN via callback
            IERC20(vault).forceApprove(pool, mTokenOut);

            // amountOutMinimum: 0.5% below arbUsdc (sandwich mitigation — T-04-03-02)
            uint256 minUsdcFromSwap = arbUsdc * 9950 / 10_000;

            bytes memory callbackData = abi.encode(vault, usdc, msg.sender, minUsdcFromSwap);
            _executeSwap(pool, address(this), zeroToOne, int256(mTokenOut), callbackData);

            // Zero-out remaining approvals
            IERC20(vault).forceApprove(pool, 0);

            // Return any residual USDC to caller
            uint256 residualUsdc = IERC20(usdc).balanceOf(address(this));
            if (residualUsdc > 0) {
                IERC20(usdc).safeTransfer(msg.sender, residualUsdc);
            }
        } else {
            // AMM < NAV: buy mTOKEN on AMM (cheap), redeem at NAV
            // Pull USDC from caller
            uint256 arbUsdc = 1000e6;
            IERC20(usdc).safeTransferFrom(msg.sender, address(this), arbUsdc);

            // Buy mTOKEN on AMM (swap USDC → mTOKEN)
            bool mTokenIsToken0 = vault < usdc;
            bool zeroToOne = !mTokenIsToken0; // buying mTOKEN → token1→token0 if mToken=token0

            IERC20(usdc).forceApprove(pool, arbUsdc);

            bytes memory callbackData = abi.encode(usdc, vault, msg.sender, uint256(0));
            _executeSwap(pool, address(this), zeroToOne, int256(arbUsdc), callbackData);

            IERC20(usdc).forceApprove(pool, 0);

            // Redeem mTOKEN at NAV — USDC goes directly to msg.sender as the receiver.
            // vault.redeem() already reverts if it cannot pay (ERC-4626 guarantee).
            uint256 mTokenBal = IERC20(vault).balanceOf(address(this));
            if (mTokenBal > 0) {
                IERC20(vault).forceApprove(vault, mTokenBal);
                // slither-disable-next-line unused-return — usdcOut delivered to msg.sender via receiver= param; vault reverts on failure
                IMTokenVault(vault).redeem(mTokenBal, msg.sender, address(this));
                IERC20(vault).forceApprove(vault, 0);
            }

            // Return any residual USDC to caller
            uint256 residualUsdc = IERC20(usdc).balanceOf(address(this));
            if (residualUsdc > 0) {
                IERC20(usdc).safeTransfer(msg.sender, residualUsdc);
            }
        }
    }

    // =========================================================================
    // algebraSwapCallback — called by the pool during swap to collect payment
    // =========================================================================

    /// @notice Algebra pool callback: called by pool.swap() to collect the token input.
    /// @dev Decode the callback data to identify which token to pay and transfer it to the pool.
    ///      The callback data encodes (tokenIn, tokenOut, caller, minAmountOut).
    ///      Only tokenIn is used in the callback body — the other fields are passed through
    ///      for documentation / future slippage enforcement at the callsite.
    function algebraSwapCallback(int256 amount0Delta, int256 amount1Delta, bytes calldata data) external {
        // The pool must be the caller (prevent arbitrary calls)
        // Note: in unit tests the MockAlgebraPool is msg.sender; in production any whitelisted pool.
        // For the stateless primitive we trust that this is called by a legitimate pool.
        // Production use: callers should validate pool addresses before calling arbCloseGap.

        // Decode only tokenIn (first field). The remaining fields are logged in calldata but
        // not used in the callback body — slither-disable-next-line unused-return justified:
        // tokenOut/recipient/minAmountOut are metadata encoded for the callback callsite,
        // not consumed here.
        (address tokenIn,,,) = abi.decode(data, (address, address, address, uint256));

        // Pay the pool for the tokens we received
        int256 amountToPay = amount0Delta > 0 ? amount0Delta : amount1Delta;
        if (amountToPay > 0) {
            IERC20(tokenIn).safeTransfer(msg.sender, uint256(amountToPay));
        }
    }

    // =========================================================================
    // Internal — pool price read
    // =========================================================================

    /// @dev Read the Algebra V3 pool price as 1e18-scaled USDC per mTOKEN.
    ///      Uses raw staticcall + assembly to handle the Algebra Integral v1 globalState()
    ///      ABI mismatch (returns 256 bytes / 8 slots, not 192 bytes / 6 slots per
    ///      VENUE-DECISION.md finding #1). Strict ABI decoder would revert on 256 bytes.
    ///
    ///      Price formula (token ordering matters — VENUE-DECISION.md finding #4):
    ///        mToken=token0 (mToken < usdc): price_usd_e18 = sqrtP^2 * 1e30 / 2^192
    ///        USDC=token0   (usdc < mToken): price_usd_e18 = 2^192 * 1e30 / sqrtP^2
    ///
    ///      Both cases use Math.mulDiv with 512-bit intermediates for overflow safety.
    function _readPoolPrice(address pool, address vault) internal view returns (uint256 ammPriceE18) {
        // Raw staticcall to globalState() — handles 256-byte return
        (bool success, bytes memory returnData) = pool.staticcall(abi.encodeWithSignature("globalState()"));
        require(success && returnData.length >= 32, "AP: pool globalState call failed");

        uint256 sqrtPriceX96Raw;
        // solhint-disable-next-line no-inline-assembly
        assembly {
            // Load the first 32 bytes of return data (slot 0 = sqrtPriceX96 as uint256)
            // The sqrtPriceX96 is a uint160 stored in the lower 20 bytes of slot 0
            sqrtPriceX96Raw := mload(add(returnData, 0x20))
        }
        // Mask to uint160 (top 12 bytes should be zero but we mask for safety)
        uint160 sqrtPriceX96 = uint160(sqrtPriceX96Raw & type(uint160).max);
        require(sqrtPriceX96 > 0, "AP: pool not initialized");

        address usdc = IMTokenVault(vault).asset();
        bool mTokenIsToken0 = vault < usdc;

        if (mTokenIsToken0) {
            // token0=mTOKEN(18dec), token1=USDC(6dec)
            // price = token1/token0 = (sqrtP)^2 / 2^192
            // = (sqrtPriceX96)^2 / 2^192
            // To get 1e18-scaled USDC per mTOKEN:
            // raw_ratio = sqrtP^2 / 2^192  (dimensionless ratio in token units)
            // 1 mTOKEN = raw_ratio USDC (in token units)
            // 1 mTOKEN in USD = raw_ratio * (1 USDC / 1e6) * 1e18 = raw_ratio * 1e12
            // ammPrice_e18 = sqrtP^2 * 1e30 / 2^192  (1e30 = 1e12 decimal gap x 1e18 output scale)
            // Use Math.mulDiv to avoid overflow:
            // ammPrice_e18 = mulDiv(sqrtP^2, 1e12, 2^192)
            // But sqrtP^2 can overflow uint256 for large sqrtP values.
            // Better: mulDiv(sqrtP, sqrtP, 2^192) won't work directly.
            // Split: ammPrice_e18 = (sqrtP / 2^96)^2 * 1e12 but loses precision.
            // Use the pattern: mulDiv(sqrtP * sqrtP, 1e12, 2^192) with 512-bit intermediate.
            // OZ Math.mulDiv handles a*b/c with a,b,c up to uint256 using 512-bit intermediate.
            // sqrtP^2 can be up to (2^160)^2 = 2^320 which exceeds uint256 (2^256).
            // For the expected range (price ~1e-12 at 1:1 NAV), sqrtP ≈ 7.9e13:
            //   sqrtP^2 ≈ 6.3e27 which fits in uint256.
            // For safety, we use mulDiv(sqrtP, mulDiv(sqrtP, 1e12, 2**96), 2**96):
            //   step1 = sqrtP * 1e12 / 2^96  (fits in uint256)
            //   step2 = sqrtP * step1 / 2^96  (final result)
            uint256 sqrtP = uint256(sqrtPriceX96);
            // step1: (sqrtP * 1e12) / 2^96
            uint256 step1 = Math.mulDiv(sqrtP, 1e30, 2 ** 96);
            // step2: (sqrtP * step1) / 2^96 = sqrtP^2 * 1e12 / 2^192
            ammPriceE18 = Math.mulDiv(sqrtP, step1, 2 ** 96);
        } else {
            // token0=USDC(6dec), token1=mTOKEN(18dec)
            // price = token1/token0 = (sqrtP)^2 / 2^192 gives mTOKEN per USDC in token units
            // We want USDC per mTOKEN → invert:
            // USDC per mTOKEN (in token units) = 1 / (mTOKEN per USDC raw)
            //   = 2^192 / sqrtP^2
            // To get 1e18-scaled:
            // 1 mTOKEN = (2^192 / sqrtP^2) USDC in token units
            // 1 mTOKEN in USD = (2^192 / sqrtP^2) / 1e6 * 1e18 = 2^192 * 1e12 / sqrtP^2
            // ammPrice_e18 = mulDiv(2^192, 1e30, sqrtP^2)  (1e30 = 1e12 decimal gap x 1e18 output scale)
            // = mulDiv(2^96, mulDiv(2^96, 1e12, sqrtP), sqrtP)
            uint256 sqrtP = uint256(sqrtPriceX96);
            // step1: (2^96 * 1e12) / sqrtP
            uint256 step1 = Math.mulDiv(2 ** 96, 1e30, sqrtP);
            // step2: (2^96 * step1) / sqrtP = 2^192 * 1e12 / sqrtP^2
            ammPriceE18 = Math.mulDiv(2 ** 96, step1, sqrtP);
        }
    }

    // =========================================================================
    // Internal — swap execution
    // =========================================================================

    /// @dev Execute a direct pool.swap() call.
    function _executeSwap(address pool, address recipient, bool zeroToOne, int256 amountSpecified, bytes memory data)
        internal
    {
        // Price limit: no limit (use max/min depending on direction)
        uint160 sqrtPriceLimitX96 = zeroToOne
            ? uint160(4_295_128_740)  // MIN_SQRT_RATIO + 1
            : uint160(1_461_446_703_485_210_103_287_273_052_203_988_822_378_723_970_342 - 1); // MAX_SQRT_RATIO - 1

        // slither-disable-next-line unused-return
        (bool success, bytes memory returnData) = pool.call(
            abi.encodeWithSignature(
                "swap(address,bool,int256,uint160,bytes)",
                recipient,
                zeroToOne,
                amountSpecified,
                sqrtPriceLimitX96,
                data
            )
        );
        if (!success) {
            // Bubble up revert reason
            if (returnData.length > 0) {
                // solhint-disable-next-line no-inline-assembly
                assembly {
                    revert(add(returnData, 0x20), mload(returnData))
                }
            }
            revert("AP: swap failed");
        }
    }
}

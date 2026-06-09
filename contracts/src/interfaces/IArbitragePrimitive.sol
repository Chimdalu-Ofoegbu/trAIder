// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

/// @title IArbitragePrimitive — stateless NAV-peg arbitrage primitive interface (ARB-01/02, D-07)
/// @notice Declares the three externally-callable arb operations.
///         ArbitragePrimitive is NON-CUSTODIAL: every call transfers tokens atomically in/out.
///         No funds are held between calls (D-07).
interface IArbitragePrimitive {
    // =========================================================================
    // Constants
    // =========================================================================

    /// @notice Minimum gap in basis points required for arbCloseGap to fire (ARB-02).
    ///         Default: 100 bps = 1% of NAV. Below this, arbCloseGap reverts.
    function GAP_THRESHOLD_BPS() external view returns (uint256);

    // =========================================================================
    // Core operations
    // =========================================================================

    /// @notice Deposit USDC into the vault and receive mTOKEN shares at NAV (ARB-01).
    /// @dev Pulls `usdcAmount` from caller, deposits into `vault`, sends shares to caller.
    ///      Inherits VAULT-05 circuit-breaker pause: if vault.deposit() reverts, so does this.
    ///      Slippage guard: reverts if mTokenOut < minMTokenOut ("AP: insufficient mToken output").
    ///      Non-custodial: no funds held after this call (D-07).
    /// @param vault        The mTokenVault address (ERC-4626).
    /// @param usdcAmount   Amount of USDC to deposit (6 decimals).
    /// @param minMTokenOut Minimum acceptable mTOKEN shares out (slippage guard).
    /// @return mTokenOut   Actual mTOKEN shares minted to the caller.
    function arbMint(address vault, uint256 usdcAmount, uint256 minMTokenOut) external returns (uint256 mTokenOut);

    /// @notice Redeem mTOKEN shares from the vault and receive USDC at NAV (ARB-01).
    /// @dev Pulls `mTokenAmount` from caller, redeems from `vault`, sends USDC to caller.
    ///      Burn path stays live even during circuit-breaker pause (VAULT-05 asymmetry).
    ///      Slippage guard: reverts if usdcOut < minUsdcOut ("AP: insufficient USDC output").
    ///      Non-custodial: no funds held after this call (D-07).
    /// @param vault         The mTokenVault address (ERC-4626).
    /// @param mTokenAmount  Amount of mTOKEN shares to redeem (18 decimals).
    /// @param minUsdcOut    Minimum acceptable USDC out (slippage guard).
    /// @return usdcOut      Actual USDC returned to the caller.
    function arbBurn(address vault, uint256 mTokenAmount, uint256 minUsdcOut) external returns (uint256 usdcOut);

    /// @notice Execute a peg-closing arbitrage round-trip against the AMM (ARB-02).
    /// @dev Reads NAV from vault and pool price from the Algebra V3 pool.
    ///      If gap < GAP_THRESHOLD_BPS: reverts "AP: gap below threshold".
    ///      AMM > NAV: arbMint (buy shares at NAV) → sell on AMM.
    ///      AMM < NAV: buy on AMM → arbBurn (sell shares at NAV).
    ///      The AMM swap is ONLY in this function (never in arbMint/arbBurn — D-07).
    ///      AMM > NAV direction inherits VAULT-05: reverts if vault.deposit() reverts.
    ///      AMM < NAV direction stays live (burn path active during circuit breaker).
    ///      Non-custodial: no funds held after this call (D-07).
    /// @param vault  The mTokenVault address.
    /// @param pool   The Algebra V3 pool address (mTOKEN/USDC).
    function arbCloseGap(address vault, address pool) external;
}

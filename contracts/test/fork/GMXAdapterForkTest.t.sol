// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {Test} from "forge-std/Test.sol";

/// @title GMXAdapterForkTest — real-GMX fork tests for GMXAdapter read path (PERPS-01 / D-02)
/// @notice STUB — Wave 0 scaffold. All tests are skipped (vm.skip) pending Wave 2 (03-05).
///
///         D-16 INTRACTABLE verdict: full on-chain write path (createOrder + sendWnt multicall)
///         is NOT implemented in Phase 3 Solidity. These fork tests cover only the READ SIDE:
///           - positionValueUSDC: Chainlink-priced valuation against GMX Reader positions
///           - getOpenPositionKeys: enumerate vault positions from GMX Reader
///
///         Fork block: MUST be >= 402000000. GMX V2 ExchangeRouter/Reader/OrderHandler were
///         redeployed after block ~401000000 — at block 353000000 (old FORK_BLOCK) those
///         contracts have NO code. Tests use block 405000000 (verified in GMX spike, 03-01).
///
///         FORK_BLOCK supersession: Decision 00-00 pinned FORK_BLOCK=353000000 for Phase 1-2
///         general-purpose tests. That block is SUPERSEDED for GMX fork tests specifically.
///         Non-GMX fork tests continue to use 353000000.
///
/// @dev Contract name matches 03-PATTERNS.md "GMXAdapterForkTest" section exactly.
///      Test names are the authoritative Wave 2 scaffold targets from 03-VALIDATION.md.
contract GMXAdapterForkTest is Test {
    // =========================================================================
    // Constants — verified GMX V2 Arbitrum One addresses (post-402000000 deploy)
    // =========================================================================

    /// @dev GMX V2 OrderHandler — used to impersonate keeper in fork tests.
    address constant ORDER_HANDLER = 0x63492B775e30a9E6b4b4761c12605EB9d071d5e9;

    /// @dev WETH on Arbitrum One (execution fee buffer currency).
    address constant WETH = 0x82aF49447D8a07e3bd95BD0d56f35241523fBab1;

    // =========================================================================
    // Fork tests — Wave 2 scaffolds (all skipped in Wave 0)
    // =========================================================================

    /// @notice Full GMX open-long round trip (create order → keeper execute → OrderExecuted).
    /// @dev Wave 2 (03-05): encode CreateOrderParams + sendWnt multicall; impersonate
    ///      ORDER_HANDLER to call afterOrderExecution; assert OrderExecuted event emitted
    ///      with non-zero positionKey. Fork block: >= 402000000.
    function test_gmx_full_round_trip() public {
        vm.skip(true);
    }

    /// @notice Full GMX close-position round trip (open → close → verify USDC returned).
    /// @dev Wave 2 (03-05): follows test_gmx_full_round_trip open; encodes decrease order;
    ///      asserts collateral returned to vault. Fork block: >= 402000000.
    function test_gmx_close_round_trip() public {
        vm.skip(true);
    }

    /// @notice WETH execution buffer keeps keeper alive across 50 orders.
    /// @dev Wave 2 (03-05): assert adapter's WETH balance covers 50 × executionFee;
    ///      keeper execution does not fail due to insufficient fee. Fork block: >= 402000000.
    function test_weth_buffer_execution() public {
        vm.skip(true);
    }

    /// @notice positionValueUSDC returns 0 when vault has no open positions.
    /// @dev Wave 2 (03-05): call GMXAdapter.positionValueUSDC(vault) with a fresh vault
    ///      address that has never submitted a GMX order; assert result == 0.
    ///      This is the D-05 empty-set behavior. Fork block: >= 402000000.
    function test_positionValueUSDC_empty_no_revert() public {
        vm.skip(true);
    }
}

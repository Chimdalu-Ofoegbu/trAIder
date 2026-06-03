// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {Test} from "forge-std/Test.sol";
import {IMTokenVault} from "../src/interfaces/IMTokenVault.sol";
import {IPerpsAdapter} from "../src/interfaces/IPerpsAdapter.sol";
import {IPerformanceOracle} from "../src/interfaces/IPerformanceOracle.sol";

// =============================================================================
// Compile-only implementability proof (00-01, IFACE-01 + IFACE-02)
// =============================================================================
// These stub contracts do NOT test runtime behavior — they prove that the
// combined interface surface (IERC4626 + IMTokenVault extensions +
// IPerpsAdapter + events) is implementable by a single concrete contract.
// Value: if either interface is malformed (e.g., conflicting signatures,
// missing struct definitions), this file fails to COMPILE — the failing
// compilation is the signal. Runtime assertions are trivial.
//
// Phase 1 (mTokenVault) and Phase 3 (GMXAdapter / MockPerps) provide real
// implementations. These stubs are intentionally minimal.
// =============================================================================

/// @dev Minimal vault stub — implements every method required by IMTokenVault
///      (which includes all of IERC4626, IERC20, IERC20Metadata) plus the
///      trAIder-specific extensions. All bodies revert with "stub" or return
///      trivial zero values. This is a COMPILE PROOF, not a functional contract.
contract _VaultStub is IMTokenVault {
    // -------------------------------------------------------------------------
    // IERC20Metadata
    // -------------------------------------------------------------------------
    function name() external pure returns (string memory) {
        return "";
    }

    function symbol() external pure returns (string memory) {
        return "";
    }

    function decimals() external pure returns (uint8) {
        return 18;
    }

    // -------------------------------------------------------------------------
    // IERC20
    // -------------------------------------------------------------------------
    function totalSupply() external pure returns (uint256) {
        return 0;
    }

    function balanceOf(address) external pure returns (uint256) {
        return 0;
    }

    function allowance(address, address) external pure returns (uint256) {
        return 0;
    }

    function approve(address, uint256) external pure returns (bool) {
        return false;
    }

    function transfer(address, uint256) external pure returns (bool) {
        return false;
    }

    function transferFrom(address, address, uint256) external pure returns (bool) {
        return false;
    }

    // -------------------------------------------------------------------------
    // IERC4626 — asset info
    // -------------------------------------------------------------------------
    function asset() external pure returns (address) {
        return address(0);
    }

    function totalAssets() external pure returns (uint256) {
        return 0;
    }

    // -------------------------------------------------------------------------
    // IERC4626 — conversion
    // -------------------------------------------------------------------------
    function convertToShares(uint256) external pure returns (uint256) {
        return 0;
    }

    function convertToAssets(uint256) external pure returns (uint256) {
        return 0;
    }

    // -------------------------------------------------------------------------
    // IERC4626 — deposit
    // -------------------------------------------------------------------------
    function maxDeposit(address) external pure returns (uint256) {
        return 0;
    }

    function previewDeposit(uint256) external pure returns (uint256) {
        return 0;
    }

    function deposit(uint256, address) external pure returns (uint256) {
        return 0;
    }

    // -------------------------------------------------------------------------
    // IERC4626 — mint
    // -------------------------------------------------------------------------
    function maxMint(address) external pure returns (uint256) {
        return 0;
    }

    function previewMint(uint256) external pure returns (uint256) {
        return 0;
    }

    function mint(uint256, address) external pure returns (uint256) {
        return 0;
    }

    // -------------------------------------------------------------------------
    // IERC4626 — withdraw
    // -------------------------------------------------------------------------
    function maxWithdraw(address) external pure returns (uint256) {
        return 0;
    }

    function previewWithdraw(uint256) external pure returns (uint256) {
        return 0;
    }

    function withdraw(uint256, address, address) external pure returns (uint256) {
        return 0;
    }

    // -------------------------------------------------------------------------
    // IERC4626 — redeem
    // -------------------------------------------------------------------------
    function maxRedeem(address) external pure returns (uint256) {
        return 0;
    }

    function previewRedeem(uint256) external pure returns (uint256) {
        return 0;
    }

    function redeem(uint256, address, address) external pure returns (uint256) {
        return 0;
    }

    // -------------------------------------------------------------------------
    // IMTokenVault — trAIder extensions
    // -------------------------------------------------------------------------
    function nav() external pure returns (uint256) {
        return 0;
    }

    function getStats() external pure returns (IPerformanceOracle.VaultStats memory stats) {
        // Return zero-value struct — compile proof only.
        return stats;
    }

    function startSession(uint256) external pure {
        revert("stub");
    }

    function endSession() external pure {
        revert("stub");
    }
}

/// @dev Minimal perps adapter stub — implements every method required by
///      IPerpsAdapter. All bodies return zero values or revert. COMPILE PROOF only.
contract _PerpsStub is IPerpsAdapter {
    function openLong(string calldata, uint256, uint256, uint256) external pure returns (bytes32) {
        return bytes32(0);
    }

    function openShort(string calldata, uint256, uint256, uint256) external pure returns (bytes32) {
        return bytes32(0);
    }

    function closePosition(bytes32, uint256) external pure returns (bytes32) {
        return bytes32(0);
    }

    function positionValueUSDC(address) external pure returns (uint256) {
        return 0;
    }

    function getOpenPositionKeys(address) external pure returns (bytes32[] memory) {
        return new bytes32[](0);
    }
}

// =============================================================================
// Test contract
// =============================================================================

/// @title InterfacesTest — compile-only implementability proof for IFACE-01 + IFACE-02
/// @notice The real value of this test is that it COMPILES. If either interface has
///         an error (missing method, conflicting signature, undefined struct), the
///         compilation fails and the problem is immediately surfaced. The runtime
///         assertion (`assertGt(code.length, 0)`) is a sanity check that the stub
///         contracts were actually deployed (not optimized away).
contract InterfacesTest is Test {
    /// @notice Instantiates both stub contracts and verifies they have bytecode.
    /// @dev test_FunctionName_Condition_Expected naming convention (D-15).
    ///      The assertion target is address.code.length to confirm contract deployment.
    function test_Interfaces_Compile_Succeeds() public {
        _VaultStub vault = new _VaultStub();
        _PerpsStub perps = new _PerpsStub();

        assertGt(address(vault).code.length, 0, "VaultStub has no bytecode");
        assertGt(address(perps).code.length, 0, "PerpsStub has no bytecode");
    }
}

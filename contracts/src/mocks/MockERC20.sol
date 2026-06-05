// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";

/// @title MockERC20 — minimal mintable ERC-20 for integration test USDC (Plan 02-06).
/// @notice Mimics USDC: 6 decimals, freely mintable (test/anvil use only).
///         Used as the USDC_ADDRESS in the anvil deploy fixture so the deploy script
///         has a real ERC-20 at a known address rather than relying on anvil_setStorageAt
///         slot-patching on an external proxy.
/// @dev DO NOT deploy on mainnet or any production chain.
contract MockERC20 is ERC20 {
    uint8 private immutable _dec;

    constructor(string memory name_, string memory symbol_, uint8 decimals_) ERC20(name_, symbol_) {
        _dec = decimals_;
    }

    function decimals() public view override returns (uint8) {
        return _dec;
    }

    /// @notice Permissionless mint — anvil integration tests only.
    function mint(address to, uint256 amount) external {
        _mint(to, amount);
    }
}

// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {Test} from "forge-std/Test.sol";
import {JournalRegistry} from "../src/JournalRegistry.sol";

/// @title JournalRegistryTest — JREG-01 gate: record/emit, duplicate-revert, auth-gate, zero-arg revert
/// @notice Proves JournalRegistry satisfies all JREG-01 invariants:
///         1. recordJournal stores {ipfsCid, operatorSig, timestamp} and emits JournalRecorded.
///         2. Duplicate tradeHash reverts (chain-layer idempotency).
///         3. Unauthorized caller reverts (registered-vault/owner gate).
///         4. Zero tradeHash and zero ipfsCid reverts.
///         5. registerVault is owner-only.
/// @dev Naming convention: test_FunctionName_Condition_Expected (D-15).
contract JournalRegistryTest is Test {
    // =========================================================================
    // Test fixtures
    // =========================================================================

    JournalRegistry internal registry;

    /// @dev Authorized vault address registered in setUp.
    address internal vault;

    /// @dev Non-authorized stranger address.
    address internal stranger;

    /// @dev Sample trade hash for record tests.
    bytes32 internal tradeHash;

    /// @dev Sample IPFS CID (bytes32-packed CIDv1) for record tests.
    bytes32 internal cid;

    /// @dev Sample operator signature bytes.
    bytes internal sig;

    // =========================================================================
    // Setup
    // =========================================================================

    function setUp() public {
        // Deploy JournalRegistry; this test contract is the owner.
        registry = new JournalRegistry();

        // Register a vault address.
        vault = makeAddr("vault");
        registry.registerVault(vault);

        stranger = makeAddr("stranger");

        // Sample fixtures.
        tradeHash = keccak256("trade-1");
        cid = keccak256("cid-1");
        sig = hex"abcd";
    }

    // =========================================================================
    // Test 1: recordJournal stores entry and emits JournalRecorded
    // =========================================================================

    /// @notice Proves that a registered vault can record a journal entry, and that
    ///         JournalRecorded is emitted with the correct indexed arguments.
    function test_JournalRegistry_RecordEmits() public {
        vm.expectEmit(true, true, true, false);
        emit JournalRegistry.JournalRecorded(tradeHash, cid, vault);

        vm.prank(vault);
        registry.recordJournal(tradeHash, cid, sig);

        // Verify dedup flag set.
        assertTrue(registry.registered(tradeHash), "registered should be true after record");

        // Verify stored entry.
        (bytes32 storedCid,,) = registry.journals(tradeHash);
        assertEq(storedCid, cid, "stored ipfsCid should match");
    }

    // =========================================================================
    // Test 2: Duplicate tradeHash reverts
    // =========================================================================

    /// @notice Proves that recording the same tradeHash twice reverts with the
    ///         chain-layer idempotency guard (JREG-01).
    function test_JournalRegistry_DuplicateReverts() public {
        // First record succeeds.
        vm.prank(vault);
        registry.recordJournal(tradeHash, cid, sig);

        // Second record with identical tradeHash must revert.
        vm.prank(vault);
        vm.expectRevert("JournalRegistry: duplicate tradeHash");
        registry.recordJournal(tradeHash, cid, sig);
    }

    // =========================================================================
    // Test 3: Unauthorized caller reverts
    // =========================================================================

    /// @notice Proves that an address that is neither a registered vault nor the
    ///         owner cannot record a journal entry.
    function test_JournalRegistry_Unauthorized_Reverts() public {
        vm.prank(stranger);
        vm.expectRevert("JournalRegistry: unauthorized");
        registry.recordJournal(tradeHash, cid, sig);
    }

    // =========================================================================
    // Test 4: Zero-argument reverts
    // =========================================================================

    /// @notice Proves that zero tradeHash and zero ipfsCid both revert, preventing
    ///         null/poison attestations (T-1-journalzero mitigation).
    function test_JournalRegistry_ZeroArgs_Revert() public {
        vm.startPrank(vault);

        vm.expectRevert("JournalRegistry: zero tradeHash");
        registry.recordJournal(bytes32(0), cid, sig);

        vm.expectRevert("JournalRegistry: zero ipfsCid");
        registry.recordJournal(tradeHash, bytes32(0), sig);

        vm.stopPrank();
    }

    // =========================================================================
    // Test 5: registerVault is owner-only
    // =========================================================================

    /// @notice Proves that a non-owner cannot register a vault (OZ Ownable gate).
    function test_JournalRegistry_RegisterVault_OnlyOwner() public {
        address newVault = makeAddr("newVault");
        vm.prank(stranger);
        vm.expectRevert(); // OZ OwnableUnauthorizedAccount custom error
        registry.registerVault(newVault);
    }
}

// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {Test} from "forge-std/Test.sol";
import {MessageHashUtils} from "@openzeppelin/contracts/utils/cryptography/MessageHashUtils.sol";
import {JournalRegistry} from "../../src/JournalRegistry.sol";

/// @title JournalRegistryEcrecoverTest — operator signature ecrecover gate (JOURNAL-03 / D-10)
/// @notice Proves the JournalRegistry ecrecover gate implemented in Phase 3 (03-03):
///           - A valid EIP-191 personal_sign signature from OPERATOR_JOURNAL_KEY accepts
///             the journal entry (registered[tradeHash] == true).
///           - A signature from any other key reverts with "JournalRegistry: invalid operator sig".
///
///         Uses Foundry's vm.sign cheatcode for in-test signature generation against a
///         known private key — no external signer required.
///
///         Signed message layout (matches JournalRegistry.recordJournal exactly):
///           packed  = keccak256(abi.encodePacked(tradeHash, ipfsCid))  // 64 raw bytes
///           ethHash = "\x19Ethereum Signed Message:\n32" ++ packed     // EIP-191
///           sig     = vm.sign(privKey, ethHash)                        // (v, r, s) -> abi.encodePacked(r,s,v)
///
///         Python JournalPublisher (03-06) MUST reproduce this exact layout:
///           packed   = keccak256(tradeHash_bytes32 ++ ipfsCid_bytes32)  // abi.encodePacked
///           eth_hash = encode_defunct(primitive=packed)                 // EIP-191
///           sig      = eth_account.sign_message(eth_hash, private_key) // .signature bytes
///
/// @dev Contract name matches 03-PATTERNS.md "JournalRegistryEcrecoverTest" section.
///      Test names are the authoritative JOURNAL-03 validation targets from 03-VALIDATION.md.
///      Naming convention: test_FunctionName_Condition_Expected (D-15).
contract JournalRegistryEcrecoverTest is Test {
    // =========================================================================
    // Fixtures
    // =========================================================================

    JournalRegistry internal registry;

    /// @dev Operator-journal private key (test-only; Foundry well-known key space).
    uint256 internal constant OPERATOR_PRIV_KEY = 0xA11CE;

    /// @dev Operator-journal address derived from OPERATOR_PRIV_KEY.
    address internal operatorJournalKey;

    /// @dev Authorized vault address used to call recordJournal.
    address internal vault;

    /// @dev Sample trade hash.
    bytes32 internal tradeHash;

    /// @dev Sample IPFS CID (bytes32-packed CIDv1).
    bytes32 internal cid;

    // =========================================================================
    // Setup
    // =========================================================================

    function setUp() public {
        // Derive operator-journal address from the test private key.
        operatorJournalKey = vm.addr(OPERATOR_PRIV_KEY);

        // Deploy registry with the operator-journal key.
        registry = new JournalRegistry(operatorJournalKey);

        // Register a vault so it can call recordJournal.
        vault = makeAddr("vault");
        registry.registerVault(vault);

        // Sample fixtures.
        tradeHash = keccak256("trade-1");
        cid = keccak256("cid-1");
    }

    // =========================================================================
    // Test 1: JOURNAL-03 — valid sig from OPERATOR_JOURNAL_KEY accepts
    // =========================================================================

    /// @notice recordJournal accepts a valid operator-journal EIP-191 signature and stores the entry.
    /// @dev Proves JOURNAL-03: "operator-journal key signs {tradeHash, ipfsCid} onchain accepted."
    ///
    ///      Signed message construction (must match contract exactly):
    ///        packed  = keccak256(abi.encodePacked(tradeHash, cid))
    ///        ethHash = MessageHashUtils.toEthSignedMessageHash(packed)  // EIP-191
    ///        sig     = vm.sign(OPERATOR_PRIV_KEY, ethHash)              // -> (v, r, s)
    ///        bytes   = abi.encodePacked(r, s, v)                        // 65 bytes
    function test_journal_correct_sig_accepts() public {
        // Build EIP-191 hash matching the contract's recordJournal hash construction.
        bytes32 packed = keccak256(abi.encodePacked(tradeHash, cid));
        bytes32 ethHash = MessageHashUtils.toEthSignedMessageHash(packed);

        // Sign with the operator-journal private key.
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(OPERATOR_PRIV_KEY, ethHash);
        // Note: OZ ECDSA.recover expects (r, s, v) ordering — NOT (v, r, s).
        bytes memory validSig = abi.encodePacked(r, s, v);

        // Call as a registered vault — should NOT revert.
        vm.prank(vault);
        registry.recordJournal(tradeHash, cid, validSig);

        // Entry must be marked as registered (JREG-01 dedup flag).
        assertTrue(registry.registered(tradeHash), "tradeHash should be registered after valid sig");

        // Stored CID must match.
        (bytes32 storedCid,,) = registry.journals(tradeHash);
        assertEq(storedCid, cid, "stored ipfsCid should match");
    }

    // =========================================================================
    // Test 2: JOURNAL-03 — wrong-key sig reverts
    // =========================================================================

    /// @notice recordJournal reverts when the signature is from a different key than OPERATOR_JOURNAL_KEY.
    /// @dev Proves JOURNAL-03: "wrong operator key rejected onchain."
    ///
    ///      Uses a different private key (0xB0B != 0xA11CE) so ECDSA.recover returns
    ///      a signer != OPERATOR_JOURNAL_KEY, triggering the revert.
    function test_journal_wrong_sig_reverts() public {
        // Build the same EIP-191 hash.
        bytes32 packed = keccak256(abi.encodePacked(tradeHash, cid));
        bytes32 ethHash = MessageHashUtils.toEthSignedMessageHash(packed);

        // Sign with a DIFFERENT private key — wrong signer.
        uint256 wrongKey = 0xB0B;
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(wrongKey, ethHash);
        bytes memory badSig = abi.encodePacked(r, s, v);

        // Call as a registered vault — must revert with the ecrecover gate error string.
        vm.prank(vault);
        vm.expectRevert("JournalRegistry: invalid operator sig");
        registry.recordJournal(tradeHash, cid, badSig);
    }
}

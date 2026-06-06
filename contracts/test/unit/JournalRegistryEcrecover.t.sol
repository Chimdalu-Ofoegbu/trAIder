// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {Test} from "forge-std/Test.sol";

/// @title JournalRegistryEcrecoverTest — operator signature ecrecover gate (JOURNAL-01 / D-17)
/// @notice STUB — Wave 0 scaffold. All tests are skipped (vm.skip) pending Wave 1 (03-03).
///
///         These unit tests validate the JournalRegistry ecrecover gate that was deferred
///         in Phase 1 (Phase 1 Decision 01-03: operatorSig stored verbatim, ecrecover gating
///         deferred to Phase 3). Wave 1 plan 03-03 implements the full ecrecover path.
///
///         The gate must enforce:
///           - A valid EIP-191 personal_sign signature from OPERATOR_JOURNAL_KEY accepts
///             the journal entry and emits JournalPublished.
///           - A signature from any other key reverts with an unauthorized error.
///
///         Uses Foundry's vm.sign cheatcode for in-test signature generation against a
///         known private key — no external signer required.
///
/// @dev Contract name matches 03-PATTERNS.md "JournalRegistryEcrecoverTest" section.
///      Test names are the authoritative Wave 1 scaffold targets from 03-VALIDATION.md.
contract JournalRegistryEcrecoverTest is Test {
    // =========================================================================
    // Unit tests — Wave 1 scaffolds (all skipped in Wave 0)
    // =========================================================================

    /// @notice recordJournal accepts a valid operator signature and emits JournalPublished.
    /// @dev Wave 1 (03-03): deploy JournalRegistry with a test operator key; call vm.sign
    ///      to produce a valid EIP-191 signature over (tradeHash, ipfsCid); call
    ///      recordJournal with the signature; assert JournalPublished event emitted.
    function test_journal_correct_sig_accepts() public {
        vm.skip(true);
    }

    /// @notice recordJournal reverts when the signature is from the wrong key.
    /// @dev Wave 1 (03-03): use vm.sign with a DIFFERENT private key to produce a
    ///      signature; call recordJournal; assert revert with "JournalRegistry: bad sig"
    ///      (or equivalent). Confirms the ecrecover gate rejects unauthorized callers.
    function test_journal_wrong_sig_reverts() public {
        vm.skip(true);
    }
}

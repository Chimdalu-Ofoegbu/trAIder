// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {Ownable} from "@openzeppelin/contracts/access/Ownable.sol";

/// @title JournalRegistry — per-trade IPFS CID + operator signature registry (JREG-01)
/// @notice Stores exactly one `{ipfsCid, operatorSig, timestamp}` entry per `tradeHash`.
///         Rejects duplicate `tradeHash` writes — providing chain-layer idempotency for
///         the orchestrator's `(vault, order_key)` journal publication path (JOURNAL-04).
///
///         Only registered session vaults or the owner (operator) may call `recordJournal`.
///         Vault registration is performed by SessionFactory via `registerVault` (Plan 06).
///
///         No audit-payload bytes are stored on-chain — only the IPFS CID, the
///         EIP-191 operator-journal signature, and the block timestamp (JREG-01).
///
///         Phase 1: operator-sig is stored verbatim. Full `ecrecover` gating against a
///         stored `OPERATOR_JOURNAL_KEY` is deferred to Phase 3 hardening.
/// @dev `ipfsCid` is a CIDv1 base32 multihash packed into 32 bytes. The exact encoding
///      is documented in Phase 3's JournalPublisher spec.
contract JournalRegistry is Ownable {
    // =========================================================================
    // Structs
    // =========================================================================

    /// @dev Per-trade journal entry stored on-chain (JREG-01).
    struct JournalEntry {
        /// @dev IPFS CIDv1 packed as bytes32 (no payload bytes on-chain).
        bytes32 ipfsCid;
        /// @dev EIP-191 personal_sign of keccak256(tradeHash ++ ipfsCid) by the operator-journal key.
        ///      Stored verbatim in Phase 1; ecrecover gating deferred to Phase 3.
        bytes operatorSig;
        /// @dev Block timestamp at record time. uint32 is valid until year 2106.
        uint32 timestamp;
    }

    // =========================================================================
    // State
    // =========================================================================

    /// @notice Journal entries keyed by tradeHash.
    mapping(bytes32 => JournalEntry) public journals;

    /// @notice Dedup guard: true once a tradeHash has been recorded (JREG-01 idempotency).
    mapping(bytes32 => bool) public registered;

    /// @notice Addresses authorized to call recordJournal (session vault contracts).
    ///         Set exclusively by the owner via registerVault.
    mapping(address => bool) public authorizedVaults;

    // =========================================================================
    // Events
    // =========================================================================

    /// @notice Emitted when a journal entry is recorded.
    /// @param tradeHash  keccak256 identifier of the on-chain trade (from OrderExecuted).
    /// @param ipfsCid    IPFS CID of the journal payload (bytes32-packed CIDv1).
    /// @param caller     Address that submitted the entry (registered vault or owner).
    event JournalRecorded(bytes32 indexed tradeHash, bytes32 indexed ipfsCid, address indexed caller);

    // =========================================================================
    // Constructor
    // =========================================================================

    /// @dev Transfers ownership to msg.sender (the deployer / SessionFactory).
    constructor() Ownable(msg.sender) {}

    // =========================================================================
    // Mutators
    // =========================================================================

    /// @notice Authorizes a session vault to record journal entries.
    /// @dev Called by SessionFactory during `createSession` (Plan 06). Owner-only.
    /// @param vault Session vault address to authorize. Must be non-zero.
    function registerVault(address vault) external onlyOwner {
        require(vault != address(0), "JournalRegistry: zero vault");
        authorizedVaults[vault] = true;
    }

    /// @notice Records one `{ipfsCid, operatorSig, timestamp}` entry for `tradeHash`.
    /// @dev Reverts if `tradeHash` is already registered (chain-layer idempotency, JREG-01).
    ///      Only registered vaults or the owner may call this function.
    ///      No payload bytes are stored — only the CID, signature, and block timestamp.
    /// @param tradeHash  Unique identifier for the trade (keccak256 of on-chain order data).
    ///                   Must be non-zero.
    /// @param ipfsCid    bytes32-packed IPFS CIDv1 of the journal payload. Must be non-zero.
    /// @param operatorSig EIP-191 personal_sign by the operator-journal key. Stored verbatim.
    function recordJournal(bytes32 tradeHash, bytes32 ipfsCid, bytes calldata operatorSig) external {
        require(authorizedVaults[msg.sender] || msg.sender == owner(), "JournalRegistry: unauthorized");
        require(tradeHash != bytes32(0), "JournalRegistry: zero tradeHash");
        require(ipfsCid != bytes32(0), "JournalRegistry: zero ipfsCid");
        require(!registered[tradeHash], "JournalRegistry: duplicate tradeHash");

        registered[tradeHash] = true;
        journals[tradeHash] =
            JournalEntry({ipfsCid: ipfsCid, operatorSig: operatorSig, timestamp: uint32(block.timestamp)});
        emit JournalRecorded(tradeHash, ipfsCid, msg.sender);
    }
}

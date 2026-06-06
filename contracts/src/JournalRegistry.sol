// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {Ownable} from "@openzeppelin/contracts/access/Ownable.sol";
import {ECDSA} from "@openzeppelin/contracts/utils/cryptography/ECDSA.sol";
import {MessageHashUtils} from "@openzeppelin/contracts/utils/cryptography/MessageHashUtils.sol";

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
///         Phase 3 (D-10): `recordJournal` verifies the operator-journal signature via
///         `ECDSA.recover` against the immutable `OPERATOR_JOURNAL_KEY`. Any entry not
///         signed by the operator-journal key is rejected on-chain.
///
///         Signed message layout (EIP-191 personal_sign):
///           packed    = keccak256(abi.encodePacked(tradeHash, ipfsCid))
///           ethHash   = MessageHashUtils.toEthSignedMessageHash(packed)
///           signer    = ECDSA.recover(ethHash, operatorSig)
///           (signer must equal OPERATOR_JOURNAL_KEY)
///
///         The JournalPublisher (Python, Plan 03-06) MUST reproduce this exactly:
///           packed = keccak256(tradeHash_bytes32 ++ ipfsCid_bytes32)  // abi.encodePacked
///           eth_hash = eth_account.messages.encode_defunct(primitive=packed)
///           sig = eth_account.sign_message(eth_hash, private_key)
///
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
        /// @dev EIP-191 personal_sign of keccak256(abi.encodePacked(tradeHash, ipfsCid))
        ///      by the operator-journal key. Verified on-chain via ecrecover (D-10).
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

    /// @notice Operator-journal key. recordJournal verifies the EIP-191 sig against this (D-10).
    /// @dev Immutable — set at deploy time. Rotation requires redeploy (no governance attack surface).
    address public immutable OPERATOR_JOURNAL_KEY;

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
    ///      Sets the immutable OPERATOR_JOURNAL_KEY used by the ecrecover gate (D-10).
    /// @param operatorJournalKey_ The operator-journal EOA address. Must be non-zero.
    constructor(address operatorJournalKey_) Ownable(msg.sender) {
        require(operatorJournalKey_ != address(0), "JournalRegistry: zero operator key");
        OPERATOR_JOURNAL_KEY = operatorJournalKey_;
    }

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
    ///
    ///      Check order:
    ///        1. authorized (msg.sender is registered vault or owner)
    ///        2. zero tradeHash guard
    ///        3. zero ipfsCid guard
    ///        4. duplicate tradeHash guard
    ///        5. ecrecover gate: operatorSig must recover to OPERATOR_JOURNAL_KEY (D-10)
    ///        6. store + emit
    ///
    ///      Signed message layout (MUST match JournalPublisher Python implementation exactly):
    ///        packed  = keccak256(abi.encodePacked(tradeHash, ipfsCid))  // 64 raw bytes packed
    ///        ethHash = "\x19Ethereum Signed Message:\n32" ++ packed     // EIP-191
    ///        signer  = ecrecover(ethHash, v, r, s)
    ///
    /// @param tradeHash  Unique identifier for the trade (keccak256 of on-chain order data).
    ///                   Must be non-zero.
    /// @param ipfsCid    bytes32-packed IPFS CIDv1 of the journal payload. Must be non-zero.
    /// @param operatorSig EIP-191 personal_sign by the operator-journal key. Verified on-chain.
    function recordJournal(bytes32 tradeHash, bytes32 ipfsCid, bytes calldata operatorSig) external {
        require(authorizedVaults[msg.sender] || msg.sender == owner(), "JournalRegistry: unauthorized");
        require(tradeHash != bytes32(0), "JournalRegistry: zero tradeHash");
        require(ipfsCid != bytes32(0), "JournalRegistry: zero ipfsCid");
        require(!registered[tradeHash], "JournalRegistry: duplicate tradeHash");

        // D-10: verify operator-journal EIP-191 signature against immutable OPERATOR_JOURNAL_KEY.
        // OZ v5 uses MessageHashUtils (not ECDSA.toEthSignedMessageHash — that was v4-only).
        // OZ ECDSA.recover rejects malleable (high-s) sigs and zero-address results (T-03-09).
        bytes32 packed = keccak256(abi.encodePacked(tradeHash, ipfsCid));
        bytes32 ethHash = MessageHashUtils.toEthSignedMessageHash(packed);
        address signer = ECDSA.recover(ethHash, operatorSig);
        require(signer == OPERATOR_JOURNAL_KEY, "JournalRegistry: invalid operator sig");

        registered[tradeHash] = true;
        journals[tradeHash] =
            JournalEntry({ipfsCid: ipfsCid, operatorSig: operatorSig, timestamp: uint32(block.timestamp)});
        emit JournalRecorded(tradeHash, ipfsCid, msg.sender);
    }
}

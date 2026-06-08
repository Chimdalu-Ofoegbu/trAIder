"""
orchestrator.journal.publisher — JournalPublisher: dual-pin + ecrecover attestation (JOURNAL-01).

Orchestrates the full journal publish pipeline (D-08 state machine):
  1. Pin to Pinata (primary IPFS, GATES the onchain record) → CID.
  2. Transition DB state: pending_pin → pinned_primary.
  3. Encode CID → bytes32 (see cid_to_bytes32 docstring for the encoding decision).
  4. EIP-191 personal_sign the packed(tradeHash, ipfsCid) tuple with the operator journal key.
  5. Call JournalRegistry.recordJournal onchain — attests the IPFS CID + sig.
  6. Transition DB state: pinned_primary → recorded (onchain_tx stored).
  7. asyncio.create_task: backfill Filebase async, non-blocking.
     Filebase failure → WARNING alert + DB unchanged (no crash, D-08 / T-03-21).

Pin scope (D-09): this function handles TRADE entries only (onchain record).
  Hold/malformed cycles are pinned by the cycle path without an onchain registry entry.

Security (T-03-22): operator_journal_private_key is raw bytes from env; never logged.
  On-chain recordJournal requires the vault to be registered in SessionFactory/JournalRegistry.

CID → bytes32 encoding decision (D-06 / Phase-5 verifier contract):
  Pinata returns a CIDv1 base32 string (e.g. "bafybei...").
  bytes32 onchain = sha256 digest extracted from the CIDv1 multihash (32 raw bytes).
  Encoding: base32-decode CID → strip multibase prefix → strip CID version varint →
            strip content type varint → strip multihash codec varint → strip hash length varint
            → 32 bytes sha256 digest.
  Reversal (Phase-5 verifier): reconstruct the full CIDv1 from the digest as
    CID(version=1, codec=dag-cbor, multihash=sha2-256:<digest>).
  Rationale: the sha256 digest IS the content fingerprint; it is 32 bytes and reversible.
  See: https://github.com/multiformats/cid (CIDv1 structure)

Pattern reference: 03-PATTERNS.md "publisher.py" section.
"""

from __future__ import annotations

import asyncio
import base64
import logging
from typing import Any

from eth_account import Account
from eth_account.messages import encode_defunct
from web3 import Web3

from orchestrator.alerts.sink import AlertSeverity, send_alert
from orchestrator.journal.ipfs import pin_to_pinata, pin_to_storacha_backup
from orchestrator.state.db import update_journal_state

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# CID → bytes32 encoding (reversible — Phase-5 verifier)
# ---------------------------------------------------------------------------


def cid_to_bytes32(cid: str) -> bytes:
    """Extract the 32-byte SHA-256 digest from a CIDv1 base32 string.

    CIDv1 layout (multiformats spec):
      <multibase-prefix> <version-varint> <codec-varint> <multihash>
    where:
      <multihash> = <hash-func-varint> <digest-length-varint> <digest-bytes>

    For a sha2-256 CIDv1:
      - multibase prefix 'b' (base32lower, stripped before decoding)
      - version varint  = 0x01  (1 byte)
      - codec varint    = varies (dag-pb=0x70, raw=0x55, dag-cbor=0x71, etc.) — 1 byte for typical values
      - hash func varint = 0x12 (sha2-256, 1 byte)
      - digest length   = 0x20 (32, 1 byte)
      - digest bytes    = 32 bytes

    This function strips the 4 leading varint bytes and returns the 32-byte digest.
    Works for standard CIDv1 where version, codec, hash-func, and length are all
    single-byte varints (values < 128), which covers all Pinata-returned CIDv1s.

    The Phase-5 verifier reconstructs the full CIDv1 from the digest by prepending:
      b'\\x01\\x55\\x12\\x20' + digest  → base32-encode → prepend 'b'
    (or the appropriate codec prefix for the stored content type).

    Args:
        cid: CIDv1 base32 string as returned by Pinata (e.g. "bafybeig...").

    Returns:
        32 bytes — the SHA-256 digest embedded in the CID.

    Raises:
        ValueError: If the CID cannot be decoded or the digest is not 32 bytes.
    """
    if not cid:
        raise ValueError("cid_to_bytes32: CID string is empty")

    # Strip multibase prefix (CIDv1 base32 always starts with 'b' or 'B')
    raw_encoded = cid[1:] if cid[0] in ("b", "B") else cid

    # base32 decode (no padding — add padding if needed)
    padding = (8 - len(raw_encoded) % 8) % 8
    raw_encoded_padded = raw_encoded.upper() + "=" * padding
    try:
        decoded = base64.b32decode(raw_encoded_padded)
    except Exception as exc:
        raise ValueError(f"cid_to_bytes32: base32 decode failed for CID={cid!r}: {exc}") from exc

    # Strip: version varint (1 byte) + codec varint (1 byte for values < 128)
    #       + hash-func varint (1 byte, 0x12=sha2-256) + digest-length varint (1 byte, 0x20=32)
    # Total: 4 bytes header, then the 32-byte SHA-256 digest
    if len(decoded) < 36:
        raise ValueError(
            f"cid_to_bytes32: decoded CID too short ({len(decoded)} bytes) for CID={cid!r}"
        )

    digest = decoded[4:36]
    if len(digest) != 32:
        raise ValueError(
            f"cid_to_bytes32: expected 32-byte digest, got {len(digest)} for CID={cid!r}"
        )
    return digest


# ---------------------------------------------------------------------------
# sign_journal_entry — EIP-191 personal_sign (03-03 cross-plan contract)
# ---------------------------------------------------------------------------


def sign_journal_entry(
    trade_hash: bytes,
    ipfs_cid: bytes,
    private_key: bytes,
) -> bytes:
    """Produce an EIP-191 personal_sign signature over (tradeHash, ipfsCid).

    This MUST reproduce the on-chain ecrecover construction from 03-03 BYTE-EXACTLY.
    The signature is passed to JournalRegistry.recordJournal for ecrecover gating.

    Cross-plan contract (03-03-SUMMARY.md "CRITICAL: Signed Message Layout for 03-06"):

      Solidity (on-chain):
        bytes32 packed = keccak256(abi.encodePacked(tradeHash, ipfsCid));
        //   tradeHash = bytes32  (32 bytes, no ABI padding)
        //   ipfsCid   = bytes32  (32 bytes, no ABI padding)
        //   Total input: 64 bytes concatenated directly
        bytes32 ethHash = MessageHashUtils.toEthSignedMessageHash(packed);
        //   = keccak256("\\x19Ethereum Signed Message:\\n32" ++ packed)
        address signer = ECDSA.recover(ethHash, operatorSig);
        require(signer == OPERATOR_JOURNAL_KEY, "JournalRegistry: invalid operator sig");

      Python (here):
        packed = trade_hash + ipfs_cid   # 64 bytes raw concatenation (NO padding)
        packed_hash = Web3.keccak(packed)  # keccak256 of 64 bytes
        signable = encode_defunct(primitive=packed_hash)  # EIP-191 prefix
        signed = Account.sign_message(signable, private_key=private_key)
        return signed.signature  # 65 bytes: r(32) + s(32) + v(1)

    Args:
        trade_hash:  32 raw bytes: keccak256 of the canonical trade payload (bytes32).
        ipfs_cid:    32 raw bytes: sha256 digest extracted from the IPFS CIDv1 (bytes32).
        private_key: Raw 32-byte private key (from env; never logged).

    Returns:
        65-byte EIP-191 personal_sign signature: r(32) + s(32) + v(1).

    Raises:
        ValueError: If trade_hash or ipfs_cid are not exactly 32 bytes.
    """
    if len(trade_hash) != 32:
        raise ValueError(f"sign_journal_entry: trade_hash must be 32 bytes, got {len(trade_hash)}")
    if len(ipfs_cid) != 32:
        raise ValueError(f"sign_journal_entry: ipfs_cid must be 32 bytes, got {len(ipfs_cid)}")

    # Step 1: abi.encodePacked(tradeHash, ipfsCid) — raw concatenation, NO padding
    packed = trade_hash + ipfs_cid  # 64 bytes

    # Step 2: keccak256(packed) — matches Solidity keccak256(abi.encodePacked(...))
    packed_hash = Web3.keccak(packed)

    # Step 3: EIP-191 personal_sign prefix (MessageHashUtils.toEthSignedMessageHash)
    # encode_defunct(primitive=bytes32_hash) prepends "\x19Ethereum Signed Message:\n32"
    signable = encode_defunct(primitive=packed_hash)

    # Step 4: sign
    signed = Account.sign_message(signable, private_key=private_key)

    # Return raw 65 bytes: r(32) + s(32) + v(1) = abi.encodePacked(r, s, v)
    return bytes(signed.signature)


# ---------------------------------------------------------------------------
# _normalize_trade_hash — hex string → 32 bytes
# ---------------------------------------------------------------------------


def _hex_to_bytes32(hex_str: str) -> bytes:
    """Convert a 0x-prefixed hex string to exactly 32 bytes."""
    h = hex_str.removeprefix("0x")
    b = bytes.fromhex(h)
    if len(b) < 32:
        b = b.zfill(32 - len(b)) + b  # left-pad
    return b[:32]


# ---------------------------------------------------------------------------
# _backfill_filebase — async non-blocking Filebase backup (D-08 / T-03-21)
# ---------------------------------------------------------------------------


async def _backfill_filebase(
    payload: dict,
    filebase_access_key: str,
    filebase_secret_key: str,
    db_session: Any,
    vault_address: str,
    order_key: str,
    telegram_bot_token: str | None = None,
    telegram_chat_id: str | None = None,
) -> None:
    """Pin payload to Filebase backup asynchronously. Non-blocking (D-08).

    Called via asyncio.create_task — failure LOGS + ALERTS but never raises
    into the caller. A Filebase outage leaves web3_storage_cid NULL in the DB
    and the primary journal record (Pinata-confirmed, onchain) intact.

    Args:
        payload:              Journal entry dict to pin.
        filebase_access_key:  Filebase S3 access key (FILEBASE_ACCESS_KEY from env).
        filebase_secret_key:  Filebase S3 secret key (FILEBASE_SECRET_KEY from env).
        db_session:           AsyncSession for updating web3_storage_cid on success.
        vault_address:        DB row key (part of UNIQUE).
        order_key:            DB row key (part of UNIQUE).
        telegram_bot_token:   Optional Telegram bot token for WARNING alert.
        telegram_chat_id:     Optional Telegram chat ID for WARNING alert.
    """
    try:
        backup_cid = await pin_to_storacha_backup(payload, filebase_access_key, filebase_secret_key)
        await update_journal_state(
            db_session,
            vault_address=vault_address,
            order_key=order_key,
            new_state="pinned_backup",
            web3_storage_cid=backup_cid,
        )
        logger.info(
            "publisher: Filebase backup confirmed CID=%s vault=%s order_key=%s",
            backup_cid,
            vault_address[:10],
            order_key[:10],
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "publisher: Filebase backup failed (non-fatal, primary record intact): %s",
            exc,
        )
        await send_alert(
            f"Filebase backup pin failed for order_key={order_key[:10]}: {exc}",
            AlertSeverity.WARNING,
            context={"vault_address": vault_address, "order_key": order_key},
            telegram_bot_token=telegram_bot_token,
            telegram_chat_id=telegram_chat_id,
        )


# ---------------------------------------------------------------------------
# publish_journal_entry — main entry point (JOURNAL-01 / D-08 / PERPS-02)
# ---------------------------------------------------------------------------


async def publish_journal_entry(
    web3: Any,
    journal_registry: Any,
    db_session: Any,
    *,
    vault_address: str,
    trade_hash: str,
    order_key: str,
    payload: dict,
    operator_journal_private_key: bytes,
    pinata_jwt: str,
    storacha_api_key: str | None = None,
    filebase_access_key: str | None = None,
    filebase_secret_key: str | None = None,
    operator_journal_key_address: str | None = None,
    telegram_bot_token: str | None = None,
    telegram_chat_id: str | None = None,
) -> None:
    """Publish a trade journal entry: pin to Pinata, sign, attest onchain, backfill Filebase.

    D-08 state machine (JOURNAL-01):
      1. pin_to_pinata → cid (raises on failure — entry stays pending_pin, retried)
      2. update_journal_state -> pinned_primary  (cid stored in pinata_cid column)
      3. sign_journal_entry(trade_hash_b32, cid_b32, key)
      4. journal_registry.recordJournal(trade_hash_b32, cid_b32, sig).transact(...)
      5. update_journal_state -> recorded  (onchain_tx stored)
      6. asyncio.create_task(_backfill_filebase(...))  — non-blocking, never blocks step 5

    D-09: called for TRADE entries only (onchain recordJournal is trade-only).
    PERPS-02: called ONLY from keeper_monitor after OrderExecuted — never from driver.

    Filebase credentials: pass ``filebase_access_key`` + ``filebase_secret_key`` (SigV4).
    The legacy ``storacha_api_key`` parameter is accepted for backwards compatibility but
    is ignored when the new keys are provided.  If only ``storacha_api_key`` is set (old
    callers), the backup is skipped with a WARNING so deployments fail loudly rather than
    silently pinning with broken Bearer auth.

    Args:
        web3:                           AsyncWeb3 instance.
        journal_registry:               Bound JournalRegistry contract instance.
        db_session:                     AsyncSession for DB state transitions.
        vault_address:                  ERC-4626 vault address (hex string).
        trade_hash:                     0x-prefixed 32-byte hex string.
        order_key:                      0x-prefixed 32-byte hex string.
        payload:                        Full journal entry dict to pin.
        operator_journal_private_key:   Raw 32-byte private key (from env; never logged).
        pinata_jwt:                     Pinata V3 JWT (from env; never logged).
        storacha_api_key:               Deprecated — ignored. Use filebase_access_key + secret_key.
        filebase_access_key:            Filebase S3 access key (FILEBASE_ACCESS_KEY env var).
        filebase_secret_key:            Filebase S3 secret key (FILEBASE_SECRET_KEY env var).
        operator_journal_key_address:   Hex address to use in transact({from: ...}).
                                        If None, derived from operator_journal_private_key.
        telegram_bot_token:             Optional Telegram bot token for Filebase WARNING.
        telegram_chat_id:               Optional Telegram chat ID for Filebase WARNING.

    Returns:
        None. Raises on Pinata failure or onchain recordJournal failure (gates the record).

    Raises:
        httpx.HTTPStatusError: Pinata pin failed — entry stays pending_pin.
        Exception: Any web3 tx failure in recordJournal.
    """
    # Derive operator key address if not provided
    if operator_journal_key_address is None:
        operator_journal_key_address = Account.from_key(operator_journal_private_key).address

    # ── Step 1: Pin to Pinata (GATES the onchain record — D-08) ─────────────
    logger.info(
        "publisher: pinning to Pinata vault=%s order_key=%s",
        vault_address[:10],
        order_key[:10],
    )
    cid = await pin_to_pinata(payload, pinata_jwt)
    # Pinata failure raises → entry stays pending_pin, retried by reconcile

    # ── Step 2: DB transition: pending_pin → pinned_primary ──────────────────
    await update_journal_state(
        db_session,
        vault_address=vault_address,
        order_key=order_key,
        new_state="pinned_primary",
        pinata_cid=cid,
    )

    # ── Step 3: Encode CID → bytes32 + sign ─────────────────────────────────
    cid_bytes32 = cid_to_bytes32(cid)  # 32-byte SHA-256 digest from CIDv1
    trade_hash_bytes32 = _hex_to_bytes32(trade_hash)

    sig = sign_journal_entry(trade_hash_bytes32, cid_bytes32, operator_journal_private_key)
    sig_hex = "0x" + sig.hex()

    logger.debug(
        "publisher: signed entry trade_hash=%s cid=%s sig=%s...",
        trade_hash[:10],
        cid[:20],
        sig_hex[:10],
    )

    # ── Step 4: Onchain recordJournal (ecrecover gate in JournalRegistry) ────
    logger.info(
        "publisher: recording onchain vault=%s order_key=%s",
        vault_address[:10],
        order_key[:10],
    )
    tx_hash = await journal_registry.functions.recordJournal(
        trade_hash_bytes32,
        cid_bytes32,
        sig,
    ).transact({"from": operator_journal_key_address})

    # Normalize tx hash to 0x-prefixed string
    if hasattr(tx_hash, "hex"):
        tx_hex = tx_hash.hex()
    else:
        tx_hex = str(tx_hash)
    if not tx_hex.startswith("0x"):
        tx_hex = "0x" + tx_hex

    # GAP #8 fix: await the receipt BEFORE transitioning DB state.
    # transact() returns the tx hash immediately (not mined). If we transition
    # to 'recorded' before the tx mines and it reverts (status==0), the DB
    # falsely shows 'recorded' with no valid onchain attestation.
    # Fix: wait for the receipt, check status==1, only then mark 'recorded'.
    # On revert (status==0): keep state at 'pinned_primary', log ERROR + alert.
    logger.info(
        "publisher: awaiting recordJournal receipt tx=%s vault=%s",
        tx_hex[:12],
        vault_address[:10],
    )
    try:
        record_receipt = await web3.eth.wait_for_transaction_receipt(tx_hex, timeout=30)
    except Exception as receipt_exc:  # noqa: BLE001
        # Timeout or unexpected error — keep state at pinned_primary for retry
        logger.error(
            "publisher: recordJournal receipt wait failed for tx=%s vault=%s order_key=%s: %s",
            tx_hex[:12],
            vault_address[:10],
            order_key[:10],
            receipt_exc,
        )
        await send_alert(
            f"recordJournal receipt wait failed for order_key={order_key[:10]} tx={tx_hex[:12]}: "
            f"{receipt_exc}. State kept at pinned_primary for reconcile retry.",
            AlertSeverity.WARNING,
            context={
                "vault_address": vault_address,
                "order_key": order_key,
                "tx": tx_hex,
                "error": str(receipt_exc),
            },
            telegram_bot_token=telegram_bot_token,
            telegram_chat_id=telegram_chat_id,
        )
        return  # Do NOT advance to 'recorded' — reconcile will retry

    if record_receipt.get("status") == 0:
        # Transaction reverted on-chain — keep at pinned_primary, log + alert
        logger.error(
            "publisher: recordJournal REVERTED (status=0) tx=%s vault=%s order_key=%s — "
            "state kept at pinned_primary for reconcile retry",
            tx_hex[:12],
            vault_address[:10],
            order_key[:10],
        )
        await send_alert(
            f"recordJournal REVERTED for order_key={order_key[:10]} tx={tx_hex[:12]}. "
            "State kept at pinned_primary. Check ecrecover gate / registry registration.",
            AlertSeverity.CRITICAL,
            context={
                "vault_address": vault_address,
                "order_key": order_key,
                "tx": tx_hex,
                "receipt_status": 0,
            },
            telegram_bot_token=telegram_bot_token,
            telegram_chat_id=telegram_chat_id,
        )
        return  # Do NOT advance to 'recorded'

    # ── Step 5: DB transition: pinned_primary → recorded (only on status==1) ──
    await update_journal_state(
        db_session,
        vault_address=vault_address,
        order_key=order_key,
        new_state="recorded",
        onchain_tx=tx_hex,
    )
    logger.info(
        "publisher: journal recorded onchain tx=%s vault=%s",
        tx_hex[:12],
        vault_address[:10],
    )

    # ── Step 6: Async Filebase backfill — non-blocking (D-08 / T-03-21) ─────
    if filebase_access_key and filebase_secret_key:
        asyncio.create_task(
            _backfill_filebase(
                payload,
                filebase_access_key,
                filebase_secret_key,
                db_session,
                vault_address,
                order_key,
                telegram_bot_token=telegram_bot_token,
                telegram_chat_id=telegram_chat_id,
            )
        )
    elif storacha_api_key:
        # Legacy path: storacha_api_key alone cannot authenticate to Filebase SigV4.
        # Warn loudly rather than silently failing with a 403 at pin time.
        logger.warning(
            "publisher: storacha_api_key set but FILEBASE_ACCESS_KEY/SECRET_KEY are missing — "
            "Filebase backup SKIPPED. Set FILEBASE_ACCESS_KEY + FILEBASE_SECRET_KEY in .env."
        )
    else:
        logger.debug("publisher: Filebase creds not provided — skipping Filebase backfill")

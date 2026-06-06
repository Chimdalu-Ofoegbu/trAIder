"""
orchestrator.journal.publisher — JournalPublisher: dual-pin + ecrecover attestation (JOURNAL-01).

Orchestrates the full journal publish pipeline:
  1. Serialize trade payload to canonical JSON.
  2. Pin to Pinata (primary IPFS) → CID.
  3. Pin to backup provider (Storacha or Filebase) → CID.
  4. Assert primary and backup CIDs match (dual-pin parity check, JOURNAL-02).
  5. EIP-191 personal_sign the (tradeHash, ipfsCid) tuple with the operator journal key.
  6. Call JournalRegistry.recordJournal on-chain — attests the IPFS CID and signature.
  7. Persist journal entry to Postgres (db.py).

All functions are STUBS in Wave 0 — bodies raise NotImplementedError.
Full implementation lands in Wave 2 (03-06).

Security note (T-03-02): operator_journal_private_key is bytes from env; never logged.
The on-chain recordJournal call requires the vault to be registered in SessionFactory.

Pattern reference: 03-PATTERNS.md "publisher.py" section.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def publish_journal_entry(
    web3: object,
    journal_registry: object,
    db_session: object,
    *,
    vault_address: str,
    trade_hash: str,
    order_key: str,
    payload: dict,
    operator_journal_private_key: bytes,
    pinata_jwt: str,
    storacha_api_key: str | None,
) -> None:
    """Publish a trade journal entry: dual-pin to IPFS, sign, attest on-chain, persist to DB.

    Args:
        web3:                        AsyncWeb3 instance (for on-chain attestation).
        journal_registry:            Bound JournalRegistry contract instance.
        db_session:                  Async SQLAlchemy session (for DB persistence).
        vault_address:               Hex address of the mTokenVault that traded.
        trade_hash:                  32-byte hex string: keccak256(trade payload).
        order_key:                   32-byte hex string: GMX/MockPerps order key.
        payload:                     Full trade payload dict to pin to IPFS.
        operator_journal_private_key: Raw private key bytes for EIP-191 signing.
        pinata_jwt:                  Pinata V3 API JWT (read from env; never logged).
        storacha_api_key:            Backup provider API key, or None if unavailable.

    Returns:
        None. Raises on any pipeline step failure.

    Raises:
        NotImplementedError: Wave 0 stub — implemented in 03-06.
        ValueError: If CIDs from primary and backup do not match (dual-pin parity failure).
        httpx.HTTPStatusError: If either IPFS pin call fails.
    """
    raise NotImplementedError("publish_journal_entry: implemented in Wave 2 (03-06)")


def sign_journal_entry(
    trade_hash: bytes,
    ipfs_cid: bytes,
    private_key: bytes,
) -> bytes:
    """Produce an EIP-191 personal_sign signature over (tradeHash, ipfsCid).

    The signature is passed to JournalRegistry.recordJournal for ecrecover gating.
    Signer must be OPERATOR_JOURNAL_KEY registered in the SessionFactory / JournalRegistry.

    Args:
        trade_hash:  32 raw bytes: keccak256 of the canonical trade payload JSON.
        ipfs_cid:    Raw bytes of the IPFS CID string (UTF-8 encoded).
        private_key: Raw 32-byte private key (from env; never logged).

    Returns:
        65-byte EIP-191 personal_sign signature (v, r, s concatenated).

    Raises:
        NotImplementedError: Wave 0 stub — implemented in 03-06.
    """
    raise NotImplementedError("sign_journal_entry: implemented in Wave 2 (03-06)")

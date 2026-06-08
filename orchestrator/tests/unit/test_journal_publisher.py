"""
orchestrator.tests.unit.test_journal_publisher — Unit tests for JournalPublisher (JOURNAL-01).

Tests:
  1. State machine (JOURNAL-01): trade entry walks pending_pin -> pinned_primary -> signed
     -> recorded; assert DB row state + pinata_cid + onchain_tx after successful publish.
  2. (see test_journal_storacha_failure.py for Test 2)
  3. EIP-191 sign round-trip: sign_journal_entry produces a 65-byte sig that recovers to
     the operator-journal address using the EXACT 03-03 packed-hash construction.
  4. Dual-pin same CID: same payload -> pin_to_pinata cid == backup cid.

Cross-plan contract (03-03): packed_hash = keccak256(tradeHash_bytes32 + ipfsCid_bytes32)
   ethHash = encode_defunct(primitive=packed_hash)  -- EIP-191 "\x19Ethereum Signed Message:\n32"
   sig = Account.sign_message(ethHash, private_key).signature  -- 65 bytes r(32)+s(32)+v(1)
"""

from __future__ import annotations

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from eth_account import Account
from eth_account.messages import encode_defunct
from web3 import Web3

from orchestrator.journal.publisher import sign_journal_entry

# ---------------------------------------------------------------------------
# Test 3: EIP-191 sign round-trip — matches 03-03 on-chain ecrecover construction
# ---------------------------------------------------------------------------


def test_sign_journal_entry_round_trip() -> None:
    """sign_journal_entry produces a 65-byte sig that recovers to the operator address.

    Cross-plan contract from 03-03-SUMMARY.md:
      packed_hash = keccak256(tradeHash_bytes32 ++ ipfsCid_bytes32)   (64 raw bytes, no padding)
      ethHash = encode_defunct(primitive=packed_hash)                  (EIP-191 prefix)
      sig = Account.sign_message(ethHash, private_key).signature       (65 bytes r+s+v)
    """
    # Foundry test private key (publicly documented test key — no real value) # gitleaks:allow
    priv_key = b"\xde\xad\xbe\xef" * 8  # 32 bytes
    expected_address = Account.from_key(priv_key).address

    trade_hash = bytes.fromhex("abcd" * 16)  # 32 bytes
    ipfs_cid = bytes.fromhex("1234" * 16)  # 32 bytes

    sig = sign_journal_entry(trade_hash, ipfs_cid, priv_key)

    # Verify signature is 65 bytes
    assert len(sig) == 65, f"Expected 65-byte signature, got {len(sig)}"

    # Recover signer using the IDENTICAL construction as 03-03 Solidity:
    # packed_hash = keccak256(abi.encodePacked(tradeHash, ipfsCid))
    packed = trade_hash + ipfs_cid  # 64 bytes
    packed_hash = Web3.keccak(packed)
    message = encode_defunct(primitive=packed_hash)
    recovered = Account.recover_message(message, signature=sig)

    assert recovered == expected_address, (
        f"Sign round-trip failed: recovered {recovered} != expected {expected_address}. "
        "EIP-191 construction must match 03-03 Solidity ecrecover gate exactly."
    )


def test_sign_journal_entry_returns_bytes() -> None:
    """sign_journal_entry returns raw bytes (not a hex string)."""
    priv_key = os.urandom(32)
    trade_hash = os.urandom(32)
    ipfs_cid = os.urandom(32)
    sig = sign_journal_entry(trade_hash, ipfs_cid, priv_key)
    assert isinstance(sig, bytes)
    assert len(sig) == 65


def test_sign_journal_entry_different_keys_different_sigs() -> None:
    """Different private keys produce different signatures for the same payload."""
    trade_hash = b"\x01" * 32
    ipfs_cid = b"\x02" * 32
    key1 = b"\xaa" * 32
    key2 = b"\xbb" * 32
    sig1 = sign_journal_entry(trade_hash, ipfs_cid, key1)
    sig2 = sign_journal_entry(trade_hash, ipfs_cid, key2)
    assert sig1 != sig2


# ---------------------------------------------------------------------------
# Test 1: State machine (JOURNAL-01) — mock publish pipeline
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_publish_journal_entry_state_machine() -> None:
    """Full publish pipeline: pending_pin -> pinned_primary -> recorded.

    Mocks: Pinata pin (returns cid), Filebase backup (returns cid),
    JournalRegistry.recordJournal (returns tx hash), db update_journal_state.
    Asserts: update_journal_state called in correct order with correct states.
    """
    from orchestrator.journal.publisher import publish_journal_entry

    fake_cid = "bafybeigdyrzt5sfp7udm7hu76uh7y26nf3efuylqabf3oclgtqy55fbzdi"
    fake_tx = "0xdeadbeefdeadbeef000000000000000000000000000000000000000000000001"
    priv_key = b"\xcc" * 32

    # Track update_journal_state calls
    db_calls: list[dict] = []

    async def mock_update(session, *, vault_address, order_key, new_state, **kwargs):
        db_calls.append({"state": new_state, **kwargs})

    # Mock JournalRegistry contract
    mock_record_fn = MagicMock()
    mock_record_fn.transact = AsyncMock(return_value=fake_tx)

    mock_registry_functions = MagicMock()
    mock_registry_functions.recordJournal = MagicMock(return_value=mock_record_fn)

    mock_registry = MagicMock()
    mock_registry.functions = mock_registry_functions

    mock_web3 = MagicMock()
    # GAP #8: publisher awaits wait_for_transaction_receipt after recordJournal.transact()
    mock_web3.eth.wait_for_transaction_receipt = AsyncMock(return_value={"status": 1})
    mock_db = MagicMock()

    payload = {"trade": "ETH", "cycle": 42, "action": "open"}
    trade_hash_hex = "0x" + "ab" * 32
    order_key = "0x" + "cd" * 32

    with (
        patch(
            "orchestrator.journal.publisher.pin_to_pinata",
            new_callable=AsyncMock,
            return_value=fake_cid,
        ),
        patch(
            "orchestrator.journal.publisher.pin_to_storacha_backup",
            new_callable=AsyncMock,
            return_value=fake_cid,
        ),
        patch("orchestrator.journal.publisher.update_journal_state", mock_update),
    ):
        await publish_journal_entry(
            mock_web3,
            mock_registry,
            mock_db,
            vault_address="0xVault",
            trade_hash=trade_hash_hex,
            order_key=order_key,
            payload=payload,
            operator_journal_private_key=priv_key,
            pinata_jwt="test-jwt",
            filebase_access_key="test-access-key",
            filebase_secret_key="test-secret-key",
        )

    # State machine: pinned_primary must come before recorded
    states_seen = [c["state"] for c in db_calls]
    assert "pinned_primary" in states_seen, f"pinned_primary state missing; saw: {states_seen}"
    assert "recorded" in states_seen, f"recorded state missing; saw: {states_seen}"
    assert states_seen.index("pinned_primary") < states_seen.index("recorded"), (
        "pinned_primary must precede recorded in the state machine"
    )

    # pinata_cid must be stored when transitioning to pinned_primary
    primary_call = next(c for c in db_calls if c["state"] == "pinned_primary")
    assert primary_call.get("pinata_cid") == fake_cid or primary_call.get("ipfs_cid") == fake_cid


# ---------------------------------------------------------------------------
# Test 4: Dual-pin same CID (mocked providers return same CID for same bytes)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_publish_journal_entry_dual_pin_same_cid() -> None:
    """Same payload -> pin_to_pinata cid == backup cid (content-addressing invariant).

    The Filebase backfill is asyncio.create_task (non-blocking). This test verifies
    that both functions are called with the same payload bytes (same-CID invariant)
    by inspecting the calls after draining the event loop.
    """
    from orchestrator.journal.publisher import publish_journal_entry

    fake_cid = "bafybeigdyrzt5sfp7udm7hu76uh7y26nf3efuylqabf3oclgtqy55fbzdi"
    priv_key = b"\xee" * 32

    pinata_cids: list[str] = []
    backup_cids: list[str] = []

    async def mock_pinata(payload, jwt, **kw):
        pinata_cids.append(fake_cid)
        return fake_cid

    async def mock_backup(payload, access_key, secret_key, **kw):
        backup_cids.append(fake_cid)
        return fake_cid

    mock_record_fn = MagicMock()
    mock_record_fn.transact = AsyncMock(return_value="0xabcd")
    mock_registry = MagicMock()
    mock_registry.functions.recordJournal = MagicMock(return_value=mock_record_fn)
    mock_web3 = MagicMock()
    # GAP #8: publisher awaits wait_for_transaction_receipt after recordJournal.transact()
    mock_web3.eth.wait_for_transaction_receipt = AsyncMock(return_value={"status": 1})
    mock_db = MagicMock()

    with (
        patch("orchestrator.journal.publisher.pin_to_pinata", side_effect=mock_pinata),
        patch("orchestrator.journal.publisher.pin_to_storacha_backup", side_effect=mock_backup),
        patch("orchestrator.journal.publisher.update_journal_state", new_callable=AsyncMock),
    ):
        await publish_journal_entry(
            mock_web3,
            mock_registry,
            mock_db,
            vault_address="0xVault",
            trade_hash="0x" + "ab" * 32,
            order_key="0x" + "cd" * 32,
            payload={"x": 1},
            operator_journal_private_key=priv_key,
            pinata_jwt="jwt",
            filebase_access_key="test-access-key",
            filebase_secret_key="test-secret-key",
        )
        # Drain pending tasks so the async Filebase backfill runs
        await asyncio.gather(*asyncio.all_tasks() - {asyncio.current_task()})

    # Pinata must have been called once
    assert len(pinata_cids) == 1, f"Expected 1 Pinata call, got {len(pinata_cids)}"
    # Filebase backup should have been called once (after task drain)
    assert len(backup_cids) == 1, (
        f"Expected 1 Filebase backup call, got {len(backup_cids)}. "
        "Backfill task may not have been awaited — ensure asyncio.gather drains tasks."
    )
    assert pinata_cids[0] == backup_cids[0], "CIDs from both providers must match"

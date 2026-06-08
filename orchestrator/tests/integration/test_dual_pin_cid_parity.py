"""
orchestrator.tests.integration.test_dual_pin_cid_parity — CI regression guard for dual-pin
CID parity (D-08-fix / JOURNAL-02).

This test re-runs the determinism check that was proven offline:
  - Pin the same canonical buffer to Pinata AND Filebase IPFS RPC.
  - Assert both return byte-identical raw CIDv1 strings.
  - Assert single-block invariant (payload < 262144 bytes).

Rationale: any future change to the serialization path, Filebase RPC params, or pinning
config that breaks CID parity between providers will fail this test in CI, preventing a
regression to the Qm… / bafkrei… split seen before D-08-fix.

Skip behavior (EXPLICIT-DEFER): skips cleanly when PINATA_JWT and/or FILEBASE credentials
are absent — the test only runs live in CI environments with secrets configured.

References:
  - D-08-fix: proved offline that Filebase IPFS RPC add (cid-version=1, raw-leaves=true)
    with Bearer token base64(ACCESS:SECRET:BUCKET) returns the same raw CIDv1 as Pinata.
  - Proven example: 127-byte payload → bafkreihsc5kbzkkoshidvd4ntx3focrmeegoke5w34ja33sh6kmfgkbfs4
    from both Pinata and Filebase RPC.
  - JOURNAL-02 same-bytes invariant: json.dumps(payload, sort_keys=True).encode()
"""

from __future__ import annotations

import json
import logging
import os

import pytest

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Credential guards — skip cleanly when absent (EXPLICIT-DEFER)
# ---------------------------------------------------------------------------

_PINATA_JWT = os.environ.get("PINATA_JWT", "")  # gitleaks:allow
_FILEBASE_ACCESS_KEY = os.environ.get("FILEBASE_ACCESS_KEY", "")  # gitleaks:allow
_FILEBASE_SECRET_KEY = os.environ.get("FILEBASE_SECRET_KEY", "")  # gitleaks:allow
_FILEBASE_BUCKET = os.environ.get("FILEBASE_BUCKET", "traider-journals")

_HAVE_ALL_CREDS = bool(_PINATA_JWT and _FILEBASE_ACCESS_KEY and _FILEBASE_SECRET_KEY)

_SKIP_REASON = (
    "EXPLICIT-DEFER: PINATA_JWT and/or FILEBASE_ACCESS_KEY/FILEBASE_SECRET_KEY not set. "
    "Set all three credentials to run the live dual-pin CID parity regression guard. "
    "This test ensures Pinata and Filebase IPFS RPC return identical raw CIDv1 for the "
    "same payload — run live before any change to serialization or pinning config (D-08-fix)."
)

# Single-block limit — payloads >= this would be chunked differently by each provider,
# breaking CID parity.  Journal payloads are always single-block (< 1 KB typical).
_SINGLE_BLOCK_MAX_BYTES = 262144


# ---------------------------------------------------------------------------
# Test 1: Unit-level determinism check (no live credentials needed)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_single_block_guard_raises_on_large_payload() -> None:
    """pin_to_storacha_backup raises ValueError for payloads >= 262144 bytes.

    This guard prevents multi-block payloads from silently producing different CIDs
    on Pinata vs Filebase due to different chunking algorithms (D-08-fix invariant).
    """
    from orchestrator.journal.ipfs import pin_to_storacha_backup

    # Generate a payload that serializes to >= 262144 bytes
    large_payload = {"data": "x" * _SINGLE_BLOCK_MAX_BYTES}
    assert len(json.dumps(large_payload, sort_keys=True).encode()) >= _SINGLE_BLOCK_MAX_BYTES

    with pytest.raises(ValueError, match="single-block limit"):
        await pin_to_storacha_backup(large_payload, "fake-access", "fake-secret", bucket="test")


# ---------------------------------------------------------------------------
# Test 2: Live CID parity regression guard (requires credentials)
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.skipif(not _HAVE_ALL_CREDS, reason=_SKIP_REASON)
@pytest.mark.asyncio
async def test_pinata_filebase_rpc_same_cid() -> None:
    """Pinata and Filebase IPFS RPC return byte-identical CIDv1 for the same payload.

    CI regression guard (D-08-fix): pins a fixed small payload to both providers and
    asserts CID equality.  Fails if either provider changes its CID format or if the
    serialization diverges.

    Single-block assertion: payload must be < 262144 bytes (enforced by pin_to_storacha_backup
    and double-checked here for explicit test documentation).
    """
    from orchestrator.journal.ipfs import pin_to_pinata, pin_to_storacha_backup

    # Fixed reproducible payload (ts omitted — deterministic content for the regression guard)
    payload = {
        "test": "dual_pin_cid_parity_regression",
        "source": "03-08-ci-guard",
        "variant": "D-08-fix-rpc-add",
    }
    canonical_bytes = json.dumps(payload, sort_keys=True).encode()

    # Single-block assertion
    assert len(canonical_bytes) < _SINGLE_BLOCK_MAX_BYTES, (
        f"Regression guard payload is {len(canonical_bytes)} bytes — exceeds single-block "
        f"limit {_SINGLE_BLOCK_MAX_BYTES}. Reduce payload."
    )
    logger.info(
        "test_pinata_filebase_rpc_same_cid: payload=%d bytes (single-block OK)",
        len(canonical_bytes),
    )

    # Pin to Pinata
    pinata_cid = await pin_to_pinata(payload, _PINATA_JWT)
    assert pinata_cid, "Pinata returned empty CID"
    assert pinata_cid.startswith("baf"), f"Pinata must return CIDv1 (baf…), got {pinata_cid!r}"
    logger.info("test_pinata_filebase_rpc_same_cid: Pinata CID=%s", pinata_cid)

    # Pin to Filebase via IPFS RPC add (cid-version=1, raw-leaves=true)
    filebase_cid = await pin_to_storacha_backup(
        payload, _FILEBASE_ACCESS_KEY, _FILEBASE_SECRET_KEY, bucket=_FILEBASE_BUCKET
    )
    assert filebase_cid, "Filebase RPC returned empty CID"
    assert filebase_cid.startswith("baf"), (
        f"Filebase RPC must return CIDv1 (baf…), got {filebase_cid!r}. "
        "If you see Qm…, the old S3 PutObject path is active instead of RPC add."
    )
    logger.info("test_pinata_filebase_rpc_same_cid: Filebase CID=%s", filebase_cid)

    # THE PARITY ASSERTION — both CIDs must be byte-identical
    assert pinata_cid == filebase_cid, (
        f"CID PARITY FAILURE (D-08-fix regression):\n"
        f"  Pinata   CID: {pinata_cid}\n"
        f"  Filebase CID: {filebase_cid}\n"
        f"  Payload bytes ({len(canonical_bytes)}B): {canonical_bytes[:200]!r}\n"
        "Both providers must return the same raw CIDv1 for the same sorted-JSON bytes. "
        "Check: (1) Filebase using IPFS RPC add cid-version=1+raw-leaves=true? "
        "(2) Both using json.dumps(sort_keys=True).encode()? "
        "(3) Payload is single-block (<262144 bytes)?"
    )
    logger.info(
        "test_pinata_filebase_rpc_same_cid: PASS — identical CID=%s from both providers",
        pinata_cid,
    )

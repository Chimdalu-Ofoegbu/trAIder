"""
orchestrator.tests.integration.test_dual_pin_same_cid — Dual-pin CID parity (JOURNAL-02).

Live integration test: pin a small test payload to Pinata AND Filebase backup.
Assert both return identical raw CIDv1 (bafkrei…) strings (content-addressed — same bytes
= same CID).

Requires PINATA_JWT and FILEBASE_ACCESS_KEY + FILEBASE_SECRET_KEY + FILEBASE_BUCKET in env.
Skips cleanly if env vars are not set (EXPLICIT-DEFER per host_infra_facts).

D-08-fix (dual-pin CID unification): the old Filebase S3 PutObject path returned dag-pb
CIDv0 (Qm…) which differed from Pinata's raw CIDv1 (bafkrei…) for the same payload.
The new Filebase IPFS RPC add path (cid-version=1, raw-leaves=true) returns the same raw
CIDv1 — proven offline for single-block payloads.

This test proves the dual-pin CID-equality assumption before the publisher relies on it.
It also serves as the CI regression guard: any serialization or config change that breaks
CID parity between Pinata and Filebase will fail this test.
"""

from __future__ import annotations

import json
import os

import pytest

from orchestrator.journal.ipfs import fetch_from_gateway, pin_to_pinata, pin_to_storacha_backup

# ---------------------------------------------------------------------------
# Credential guards — skip if not set (EXPLICIT-DEFER)
# ---------------------------------------------------------------------------

PINATA_JWT = os.environ.get("PINATA_JWT", "")  # gitleaks:allow — reading env var, not a real secret
FILEBASE_ACCESS_KEY = os.environ.get("FILEBASE_ACCESS_KEY", "")  # gitleaks:allow
FILEBASE_SECRET_KEY = os.environ.get("FILEBASE_SECRET_KEY", "")  # gitleaks:allow
FILEBASE_BUCKET = os.environ.get("FILEBASE_BUCKET", "traider-journals")

_LIVE_CREDS_AVAILABLE = bool(PINATA_JWT and FILEBASE_ACCESS_KEY and FILEBASE_SECRET_KEY)

_SKIP_REASON = (
    "EXPLICIT-DEFER: PINATA_JWT and/or FILEBASE_ACCESS_KEY/FILEBASE_SECRET_KEY not set in env. "
    "Set all three to run the live dual-pin CID parity integration test (TEST-03 requirement). "
    "Filebase now uses IPFS RPC add (cid-version=1, raw-leaves=true) with Bearer token "
    "base64(ACCESS:SECRET:BUCKET) — same raw CIDv1 as Pinata for single-block payloads. "
    "Unit-level same-bytes invariant is proven in test_alert_sink.py Test 4."
)


@pytest.mark.integration
@pytest.mark.skipif(not _LIVE_CREDS_AVAILABLE, reason=_SKIP_REASON)
@pytest.mark.asyncio
async def test_dual_pin_same_cid() -> None:
    """Pinata and Filebase return identical raw CIDv1 for the same sorted-JSON payload.

    D-08-fix CI regression guard: any change to serialization or Filebase pinning config
    that breaks CID parity between Pinata and Filebase will fail here.

    Uses a minimal test payload to minimize pin cost. The test payload includes
    a timestamp to avoid content-dedup on Pinata returning a cached CID.

    Single-block assertion: payload < 262144 bytes — multi-block would break CID parity.
    """
    import time

    payload = {
        "test": "dual_pin_cid_parity",
        "ts": int(time.time()),
        "source": "03-08-integration-test",
    }

    # Verify single-block invariant before pinning
    canonical_bytes = json.dumps(payload, sort_keys=True).encode()
    assert len(canonical_bytes) < 262144, (
        f"Test payload exceeds single-block limit: {len(canonical_bytes)} bytes. "
        "Reduce payload size to preserve CID parity (JOURNAL-02)."
    )

    # Pin to Pinata (primary) — returns raw CIDv1 (bafkrei… or bafy… for raw codec)
    pinata_cid = await pin_to_pinata(payload, PINATA_JWT)
    assert pinata_cid, "Pinata returned empty CID"
    assert pinata_cid.startswith("baf"), f"Expected CIDv1 (baf…), got {pinata_cid}"

    # Pin to Filebase backup via IPFS RPC add (cid-version=1, raw-leaves=true)
    # → must return the SAME raw CIDv1 as Pinata for the same bytes (D-08-fix)
    filebase_cid = await pin_to_storacha_backup(
        payload, FILEBASE_ACCESS_KEY, FILEBASE_SECRET_KEY, bucket=FILEBASE_BUCKET
    )
    assert filebase_cid, "Filebase returned empty CID"
    assert filebase_cid.startswith("baf"), (
        f"Expected raw CIDv1 (baf…) from Filebase RPC add, got {filebase_cid!r}. "
        "Old S3 PutObject returned Qm… (dag-pb CIDv0) — if you see Qm…, the RPC fix is not active."
    )

    # The two CIDs must match exactly (same bytes → same content address)
    assert pinata_cid == filebase_cid, (
        f"JOURNAL-02 violation (D-08-fix): Pinata CID {pinata_cid} != Filebase CID {filebase_cid}. "
        "Both providers must receive json.dumps(sort_keys=True) bytes AND both must use "
        "cid-version=1+raw-leaves=true to produce the same raw CIDv1."
    )


@pytest.mark.integration
@pytest.mark.skipif(not bool(PINATA_JWT), reason="EXPLICIT-DEFER: PINATA_JWT not set in env")
@pytest.mark.asyncio
async def test_gateway_fetch() -> None:
    """Pin a payload to Pinata then fetch it back from the gateway.

    Proves the CID is fetchable from the Pinata public gateway — a TEST-03
    requirement: 'journal dual-pinned + onchain-recorded + CID-fetchable from
    BOTH gateways'.
    """
    import time

    payload = {
        "test": "gateway_fetch_assertion",
        "ts": int(time.time()),
    }

    # Canonical serialization
    canonical_bytes = json.dumps(payload, sort_keys=True).encode()

    cid = await pin_to_pinata(payload, PINATA_JWT)
    assert cid, "Pinata returned empty CID"

    # Fetch back from the public Pinata gateway
    fetched = await fetch_from_gateway(cid)
    fetched_bytes = json.dumps(fetched, sort_keys=True).encode()

    assert fetched_bytes == canonical_bytes, (
        f"Gateway fetch returned different content.\n"
        f"Expected: {canonical_bytes!r}\n"
        f"Got:      {fetched_bytes!r}"
    )

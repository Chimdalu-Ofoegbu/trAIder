"""
orchestrator.tests.integration.test_dual_pin_same_cid — Dual-pin CID parity (JOURNAL-02).

Live integration test: pin a small test payload to Pinata AND Filebase backup.
Assert both return identical CIDs (content-addressed → same content = same CID).

Requires PINATA_JWT and FILEBASE_ACCESS_KEY + FILEBASE_SECRET_KEY + FILEBASE_BUCKET in env.
Skips cleanly if env vars are not set (EXPLICIT-DEFER per host_infra_facts).

Note: the old FILEBASE_API_KEY Bearer-auth is BROKEN (Filebase S3 requires SigV4).
Use FILEBASE_ACCESS_KEY + FILEBASE_SECRET_KEY instead.

This test proves the dual-pin CID-equality assumption before the publisher relies on it.
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
    "Set all three to run the live dual-pin integration test (TEST-03 requirement). "
    "Use FILEBASE_ACCESS_KEY + FILEBASE_SECRET_KEY (SigV4) — NOT FILEBASE_API_KEY (Bearer/broken). "
    "Unit-level same-bytes invariant is proven in test_alert_sink.py Test 4."
)


@pytest.mark.integration
@pytest.mark.skipif(not _LIVE_CREDS_AVAILABLE, reason=_SKIP_REASON)
@pytest.mark.asyncio
async def test_dual_pin_same_cid() -> None:
    """Pinata and Filebase return identical CIDs for the same sorted-JSON payload.

    Uses a minimal test payload to minimize pin cost. The test payload includes
    a timestamp to avoid content-dedup on Pinata returning a cached CID.
    """
    import time

    payload = {
        "test": "dual_pin_cid_parity",
        "ts": int(time.time()),
        "source": "03-06-integration-test",
    }

    # Pin to Pinata (primary)
    pinata_cid = await pin_to_pinata(payload, PINATA_JWT)
    assert pinata_cid, "Pinata returned empty CID"
    assert pinata_cid.startswith("bafy"), f"Expected CIDv1, got {pinata_cid}"

    # Pin to Filebase backup (same sorted bytes → same CID)
    filebase_cid = await pin_to_storacha_backup(
        payload, FILEBASE_ACCESS_KEY, FILEBASE_SECRET_KEY, bucket=FILEBASE_BUCKET
    )
    assert filebase_cid, "Filebase returned empty CID"

    # The two CIDs must match (same bytes → same content address)
    assert pinata_cid == filebase_cid, (
        f"JOURNAL-02 violation: Pinata CID {pinata_cid} != Filebase CID {filebase_cid}. "
        "Both providers must receive json.dumps(sort_keys=True) bytes."
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

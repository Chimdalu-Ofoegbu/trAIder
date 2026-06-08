"""
orchestrator.tests.integration.test_filebase_live_pin — Live Filebase S3 SigV4 smoke test.

Proves the full Filebase backup round-trip BEFORE the 30-min operator gate run:
  1. PUT a tiny JSON payload to Filebase via SigV4 (boto3 → asyncio.to_thread).
  2. Assert the response contains a CID in the x-amz-meta-cid header.
  3. Fetch the CID from an independent IPFS gateway (ipfs.filebase.io) and assert
     the bytes round-trip exactly (JOURNAL-02 same-bytes invariant).

Skip conditions (EXPLICIT-DEFER):
  - FILEBASE_ACCESS_KEY not set.
  - FILEBASE_SECRET_KEY not set.
  Both must be present for the test to run live. The operator sets them after this fix
  and runs `uv run --project orchestrator pytest tests/integration/test_filebase_live_pin.py -v`
  to confirm the SigV4 fix is working end-to-end before the gate session.

Security note (T-03-22): keys are read from env only. They are never logged verbatim;
only SET/NOT SET status is logged.

References:
  - orchestrator/src/orchestrator/journal/ipfs.py — pin_to_storacha_backup (SigV4 impl)
  - docs/STORACHA-PROBE.md — Filebase backup selection decision
  - 03-08 gap fix: Filebase Bearer auth was broken; this test proves the SigV4 fix
"""

from __future__ import annotations

import json
import logging
import os
import time

import pytest

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Credentials + skip guard
# ---------------------------------------------------------------------------

_FILEBASE_ACCESS_KEY = os.environ.get("FILEBASE_ACCESS_KEY", "")  # gitleaks:allow
_FILEBASE_SECRET_KEY = os.environ.get("FILEBASE_SECRET_KEY", "")  # gitleaks:allow
_FILEBASE_BUCKET = os.environ.get("FILEBASE_BUCKET", "traider-journals")

# Independent IPFS gateway for the round-trip fetch — distinct from Pinata (D-04)
_FILEBASE_IPFS_GATEWAY = "https://ipfs.filebase.io/ipfs"

_SKIP_REASON = (
    "EXPLICIT-DEFER: FILEBASE_ACCESS_KEY and/or FILEBASE_SECRET_KEY not set. "
    "Set both env vars (from the Filebase dashboard → Access Keys) and re-run this test "
    "to prove the SigV4 fix end-to-end before the operator gate session. "
    "Note: use FILEBASE_ACCESS_KEY + FILEBASE_SECRET_KEY — NOT the old FILEBASE_API_KEY "
    "(Bearer auth returns 403 SignatureDoesNotMatch on Filebase S3)."
)

_HAVE_CREDS = bool(_FILEBASE_ACCESS_KEY) and bool(_FILEBASE_SECRET_KEY)


# ---------------------------------------------------------------------------
# Smoke test: PUT → CID → round-trip fetch
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(not _HAVE_CREDS, reason=_SKIP_REASON)
async def test_filebase_live_pin_round_trip() -> None:
    """Live Filebase S3 SigV4 smoke test: PUT payload, get CID, fetch from IPFS gateway.

    Asserts:
    1. pin_to_storacha_backup returns a non-empty CID string.
    2. CID is fetched from the Filebase IPFS gateway within 30s.
    3. Fetched bytes match the original canonical serialization (JOURNAL-02 round-trip).

    Skips cleanly when FILEBASE_ACCESS_KEY + FILEBASE_SECRET_KEY are absent (EXPLICIT-DEFER).
    """
    from orchestrator.journal.ipfs import fetch_from_gateway, pin_to_storacha_backup

    logger.info(
        "test_filebase_live_pin: FILEBASE_ACCESS_KEY=%s FILEBASE_SECRET_KEY=%s bucket=%s",
        "SET" if _FILEBASE_ACCESS_KEY else "NOT SET",
        "SET" if _FILEBASE_SECRET_KEY else "NOT SET",
        _FILEBASE_BUCKET,
    )

    # Build a small but realistic payload (mimics a journal entry)
    payload = {
        "test": "filebase_live_smoke",
        "ts": int(time.time()),
        "bucket": _FILEBASE_BUCKET,
        "note": "SigV4 fix smoke test — 03-08 gap fix",
    }
    canonical_bytes = json.dumps(payload, sort_keys=True).encode()

    # ── Step 1: PIN ──────────────────────────────────────────────────────────
    t0 = time.monotonic()
    cid = await pin_to_storacha_backup(
        payload,
        _FILEBASE_ACCESS_KEY,
        _FILEBASE_SECRET_KEY,
        bucket=_FILEBASE_BUCKET,
    )
    pin_elapsed = time.monotonic() - t0

    assert cid, "pin_to_storacha_backup returned an empty CID string"
    assert len(cid) > 10, f"CID looks too short to be valid: {cid!r}"
    logger.info("test_filebase_live_pin: PIN OK — CID=%s (%.2fs)", cid, pin_elapsed)

    # ── Step 2: FETCH from independent IPFS gateway ──────────────────────────
    t1 = time.monotonic()
    try:
        fetched = await fetch_from_gateway(cid, _FILEBASE_IPFS_GATEWAY)
        fetch_elapsed = time.monotonic() - t1
    except Exception as exc:
        # IPFS propagation can take a few seconds — give context
        raise AssertionError(
            f"fetch_from_gateway failed for CID={cid} from gateway={_FILEBASE_IPFS_GATEWAY}. "
            f"Elapsed={time.monotonic() - t1:.1f}s. Error: {exc}\n"
            "If this is a propagation delay, wait ~5s and retry. "
            "If it's a 404, check that the Filebase bucket is IPFS-enabled in the dashboard."
        ) from exc

    logger.info(
        "test_filebase_live_pin: FETCH OK — CID=%s gateway=%s (%.2fs)",
        cid,
        _FILEBASE_IPFS_GATEWAY,
        fetch_elapsed,
    )

    # ── Step 3: ROUND-TRIP ASSERT (JOURNAL-02 same-bytes invariant) ──────────
    fetched_bytes = json.dumps(fetched, sort_keys=True).encode()

    assert fetched_bytes == canonical_bytes, (
        f"Round-trip FAILED for CID={cid}:\n"
        f"  Uploaded bytes ({len(canonical_bytes)}B): {canonical_bytes[:200]!r}\n"
        f"  Fetched  bytes ({len(fetched_bytes)}B):  {fetched_bytes[:200]!r}\n"
        "Canonical serialization must be json.dumps(payload, sort_keys=True).encode() "
        "for the same-CID invariant (JOURNAL-02) to hold."
    )

    logger.info(
        "test_filebase_live_pin: ROUND-TRIP PASS — CID=%s bytes=%d pin=%.2fs fetch=%.2fs",
        cid,
        len(canonical_bytes),
        pin_elapsed,
        fetch_elapsed,
    )


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(not _HAVE_CREDS, reason=_SKIP_REASON)
async def test_filebase_live_pin_is_idempotent() -> None:
    """Same payload pinned twice produces the same CID (content-addressed idempotency).

    Proves the deterministic-key design: the S3 object key is sha256(content).hexdigest()
    so uploading the same bytes twice is a no-op (idempotent S3 PUT) and the CID
    returned by Filebase matches both times.
    """
    from orchestrator.journal.ipfs import pin_to_storacha_backup

    payload = {"idempotency_test": True, "marker": "03-08-gap-fix"}

    cid1 = await pin_to_storacha_backup(
        payload,
        _FILEBASE_ACCESS_KEY,
        _FILEBASE_SECRET_KEY,
        bucket=_FILEBASE_BUCKET,
    )
    cid2 = await pin_to_storacha_backup(
        payload,
        _FILEBASE_ACCESS_KEY,
        _FILEBASE_SECRET_KEY,
        bucket=_FILEBASE_BUCKET,
    )

    assert cid1 == cid2, (
        f"Idempotent double-pin returned different CIDs: cid1={cid1!r} cid2={cid2!r}. "
        "Content-addressed uploads of the same bytes must produce the same CID."
    )
    logger.info("test_filebase_live_pin_is_idempotent: PASS — CID=%s (both calls)", cid1)

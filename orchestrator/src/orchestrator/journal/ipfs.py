"""
orchestrator.journal.ipfs — Pinata primary + Filebase backup IPFS HTTP clients (JOURNAL-02).

Exposes three async functions:
  pin_to_pinata         : Pin a JSON payload to IPFS via Pinata V3 API. Returns CID string.
  pin_to_storacha_backup: Pin a JSON payload via Filebase S3-compatible API. Returns CID string.
                          Named "storacha" for API compatibility; implementation uses Filebase
                          per docs/STORACHA-PROBE.md Wave-0 decision (legacy web3.storage down
                          + w3up UCAN-gated → Filebase S3-compatible selected as backup).
                          Uses AWS Signature V4 (boto3) — Filebase S3 endpoint does NOT accept
                          Bearer auth; SigV4 with FILEBASE_ACCESS_KEY + FILEBASE_SECRET_KEY is
                          required.
  fetch_from_gateway    : Fetch a pinned payload by CID from an IPFS gateway. Returns dict.

Same-bytes invariant (JOURNAL-02): both pin functions serialize payloads with
  json.dumps(payload, sort_keys=True).encode()
so identical content produces identical CIDs on both providers (content addressing).

Security note (T-03-22): callers must pass JWT / API keys from env only.
These functions NEVER log the key values — only HTTP status codes and CIDs are logged.

Pattern references:
  03-RESEARCH.md Pattern 7: Pinata V3 multipart upload endpoint
  03-RESEARCH.md Pattern 8 Option B: Filebase S3-compatible IPFS
  docs/STORACHA-PROBE.md: backup provider selection decision (Wave 0, 03-01)
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pinata V3 primary pin
# ---------------------------------------------------------------------------

_PINATA_UPLOAD_URL = "https://uploads.pinata.cloud/v3/files"
_DEFAULT_GATEWAY = "https://gateway.pinata.cloud/ipfs"


async def pin_to_pinata(
    payload: dict,
    jwt: str,
    *,
    gateway_url: str = _DEFAULT_GATEWAY,
) -> str:
    """Pin ``payload`` to IPFS via Pinata V3 API and return the resulting CID.

    The payload is serialized as ``json.dumps(payload, sort_keys=True).encode()``
    — deterministic canonical bytes so both providers return the same CID for the
    same logical payload (JOURNAL-02 same-bytes-same-CID invariant).

    Args:
        payload:     JSON-serializable dict to pin (trade journal entry).
        jwt:         Pinata API JWT (read from env; never logged).
        gateway_url: IPFS gateway base URL (unused in this function — present for
                     callers who need the gateway after pinning).

    Returns:
        CID string (e.g. ``"bafybeig..."``) after successful pin.

    Raises:
        httpx.HTTPStatusError: On non-2xx Pinata API response.
        ValueError: If payload is not JSON-serializable.
    """
    content = json.dumps(payload, sort_keys=True).encode()
    logger.debug("pin_to_pinata: posting %d bytes to Pinata V3", len(content))
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            _PINATA_UPLOAD_URL,
            headers={"Authorization": f"Bearer {jwt}"},
            files={"file": ("journal.json", content, "application/json")},
            data={"network": "public"},
            timeout=30,
        )
    resp.raise_for_status()
    cid = resp.json()["data"]["cid"]
    logger.info("pin_to_pinata: pinned CID=%s", cid)
    return cid


# ---------------------------------------------------------------------------
# Filebase backup pin (named storacha_backup for API compatibility — D-08)
# ---------------------------------------------------------------------------

# Filebase S3-compatible endpoint.  Note: .io domain (not .com — .com returns 301).
_FILEBASE_S3_ENDPOINT = "https://s3.filebase.io"
_FILEBASE_REGION = "auto"


def _put_to_filebase_sync(
    content: bytes,
    access_key: str,
    secret_key: str,
    bucket: str,
    key: str,
) -> str:
    """Synchronous boto3 S3 put_object call to Filebase IPFS bucket.

    Wrapped in asyncio.to_thread by the async caller so the event loop is never blocked.

    Returns:
        CID string from the ``x-amz-meta-cid`` response header.

    Raises:
        ValueError: If the response header is missing (bucket is not IPFS-enabled).
        botocore.exceptions.ClientError: On S3-level errors (auth, bucket not found, etc.).
    """
    import boto3  # import inside function — boto3 is a sync library

    s3 = boto3.client(
        "s3",
        endpoint_url=_FILEBASE_S3_ENDPOINT,
        region_name=_FILEBASE_REGION,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
    )
    resp = s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=content,
        ContentType="application/json",
    )
    # Filebase IPFS-enabled buckets echo the IPFS CID in x-amz-meta-cid
    cid = resp.get("ResponseMetadata", {}).get("HTTPHeaders", {}).get("x-amz-meta-cid")
    if not cid:
        raise ValueError(
            "pin_to_storacha_backup: Filebase response missing x-amz-meta-cid header. "
            "Is the bucket IPFS-enabled? (enable IPFS in the Filebase dashboard for this bucket)"
        )
    return cid


async def pin_to_storacha_backup(
    payload: dict,
    access_key: str,
    secret_key: str,
    *,
    bucket: str = "traider-journals",
) -> str:
    """Pin ``payload`` to the Filebase S3-compatible IPFS backup and return CID.

    Named ``pin_to_storacha_backup`` for API compatibility with the stub (03-01),
    but the implementation uses Filebase per docs/STORACHA-PROBE.md decision:
    - Legacy api.web3.storage is in maintenance (503).
    - w3up Storacha requires UCAN capability delegation (no simple Python Bearer path).
    - Filebase exposes a standard S3-compatible endpoint with AWS Signature V4 auth.

    Auth: AWS Signature V4 via boto3.  Filebase does NOT accept Bearer tokens —
    they return 403 SignatureDoesNotMatch.  Use FILEBASE_ACCESS_KEY + FILEBASE_SECRET_KEY.

    Same-bytes invariant (JOURNAL-02): uses ``json.dumps(payload, sort_keys=True).encode()``
    so the resulting CID matches pin_to_pinata for the same payload.

    The CID is returned from the ``x-amz-meta-cid`` response header (Filebase IPFS
    bucket behaviour: Filebase computes and returns the IPFS CID for the uploaded object).

    Non-blocking contract (D-08): boto3 is synchronous — the call is wrapped in
    ``asyncio.to_thread`` so the event loop is never blocked.

    Args:
        payload:    JSON-serializable dict to pin (trade journal entry).
        access_key: Filebase S3 access key (``FILEBASE_ACCESS_KEY`` from env; never logged).
        secret_key: Filebase S3 secret key (``FILEBASE_SECRET_KEY`` from env; never logged).
        bucket:     Filebase IPFS bucket name (``FILEBASE_BUCKET`` from env; default shown).

    Returns:
        CID string after successful pin.

    Raises:
        ValueError: If response has no ``x-amz-meta-cid`` header (bucket not IPFS-enabled).
        botocore.exceptions.ClientError: On S3-level errors (auth failure, bucket not found).
    """
    content = json.dumps(payload, sort_keys=True).encode()
    # Deterministic key from content hash — uploads are idempotent for the same payload
    key = hashlib.sha256(content).hexdigest() + ".json"

    logger.debug(
        "pin_to_storacha_backup: PUT %d bytes to Filebase bucket=%s key=%s...",
        len(content),
        bucket,
        key[:16],
    )

    # boto3 is synchronous — run in a thread to preserve the non-blocking contract (D-08)
    cid = await asyncio.to_thread(
        _put_to_filebase_sync,
        content,
        access_key,
        secret_key,
        bucket,
        key,
    )
    logger.info("pin_to_storacha_backup: pinned CID=%s (Filebase SigV4)", cid)
    return cid


# ---------------------------------------------------------------------------
# Gateway fetch — CID-fetchable assertion (TEST-03 / JOURNAL-02)
# ---------------------------------------------------------------------------


async def fetch_from_gateway(
    cid: str,
    gateway: str = _DEFAULT_GATEWAY,
) -> dict:
    """Fetch and deserialize a JSON payload from an IPFS gateway by CID.

    Used by the Verifier CLI (JOURNAL-03) to retrieve the original journal entry
    for replay validation. Also used by integration tests to verify dual-pin CID
    parity and gateway accessibility (TEST-03 CID-fetchable assertion).

    Args:
        cid:     IPFS content identifier (CIDv1 or CIDv0 string).
        gateway: IPFS HTTP gateway base URL (no trailing slash).
                 Default: Pinata public gateway.

    Returns:
        Deserialized JSON dict of the pinned payload.

    Raises:
        httpx.HTTPStatusError: On non-2xx gateway response.
        json.JSONDecodeError: If gateway returns non-JSON body.
    """
    url = f"{gateway}/{cid}"
    logger.debug("fetch_from_gateway: GET %s", url)
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, timeout=30, follow_redirects=True)
    resp.raise_for_status()
    data = resp.json()
    logger.debug("fetch_from_gateway: fetched %d keys from CID=%s", len(data), cid)
    return data

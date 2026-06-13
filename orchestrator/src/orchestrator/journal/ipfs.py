"""
orchestrator.journal.ipfs — Pinata primary + Filebase backup IPFS HTTP clients (JOURNAL-02).

Exposes three async functions:
  pin_to_pinata         : Pin a JSON payload to IPFS via Pinata V3 API. Returns CID string.
  pin_to_storacha_backup: Pin a JSON payload via Filebase IPFS RPC API. Returns CID string.
                          Named "storacha" for API compatibility; implementation uses Filebase
                          per docs/STORACHA-PROBE.md Wave-0 decision (legacy web3.storage down
                          + w3up UCAN-gated → Filebase selected as backup).
                          Uses Filebase IPFS RPC add endpoint with Bearer token auth:
                            POST https://rpc.filebase.io/api/v0/add
                              ?cid-version=1&raw-leaves=true
                            Authorization: Bearer base64(ACCESS_KEY:SECRET_KEY:BUCKET)
                          This produces raw CIDv1 (bafkrei…) — IDENTICAL to Pinata's CID for
                          the same payload (proven offline: same 127-byte test payload gives
                          the exact same bafkrei… hash from both providers).
                          The S3 PutObject path (old impl) yielded dag-pb CIDv0 (Qm…) which
                          differed from Pinata's raw CIDv1 — replaced by RPC add (D-08-fix).
  fetch_from_gateway    : Fetch a pinned payload by CID from an IPFS gateway. Returns dict.

Same-bytes invariant (JOURNAL-02): both pin functions serialize payloads with
  json.dumps(payload, sort_keys=True).encode()
so identical content produces identical CIDs on both providers (content addressing).
Single-block assertion: journal payloads MUST be < 262144 bytes; multi-block chunking
would break CID parity between providers (different tree structures → different root CIDs).

Security note (T-03-22): callers must pass JWT / API keys from env only.
These functions NEVER log the key values — only HTTP status codes and CIDs are logged.

Gotcha (D-08-fix): Pinata raw CIDv1 (bafkrei) vs Filebase S3 dag-pb CIDv0 (Qm) differed
for the same payload; resolved by pinning Filebase via RPC add cid-version=1 raw-leaves=true
(token=base64(access:secret:bucket)) → identical raw CIDv1; deterministic because journal
payloads are single-block (< 262144 bytes).

Pattern references:
  03-RESEARCH.md Pattern 7: Pinata V3 multipart upload endpoint
  03-RESEARCH.md Pattern 8 Option B: Filebase IPFS RPC add (replaces S3 PutObject)
  docs/STORACHA-PROBE.md: backup provider selection decision (Wave 0, 03-01)
"""

from __future__ import annotations

import base64
import json
import logging
import re
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pinata V3 primary pin
# ---------------------------------------------------------------------------

_PINATA_UPLOAD_URL = "https://uploads.pinata.cloud/v3/files"
_DEFAULT_GATEWAY = "https://gateway.pinata.cloud/ipfs"

# SEC (SSRF hardening): fetch_from_gateway accepts a gateway base URL + CID. Restrict both so
# neither a caller-supplied gateway nor a crafted CID/redirect can point the fetch at an
# internal/metadata endpoint. Only https + these known IPFS gateway hosts are allowed.
_ALLOWED_GATEWAY_HOSTS = frozenset(
    {
        "gateway.pinata.cloud",
        "ipfs.filebase.io",
        "ipfs.io",
        "dweb.link",
        "cloudflare-ipfs.com",
    }
)
# A CID is base32/base58 alphanumerics only — no '/', '.', ':' so it cannot inject a path,
# host, or scheme into the request URL.
_CID_RE = re.compile(r"^[A-Za-z0-9]{8,128}$")


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
        CID string (e.g. ``"bafkrei..."``) after successful pin.

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
# Filebase IPFS RPC backup pin (named storacha_backup for API compatibility — D-08)
# ---------------------------------------------------------------------------

# Filebase IPFS RPC endpoint — produces raw CIDv1 matching Pinata (not S3 PutObject
# which yields dag-pb CIDv0 — different format).  Proven offline to return identical
# bafkrei… CIDs for the same payload as Pinata when cid-version=1 + raw-leaves=true.
_FILEBASE_RPC_URL = "https://rpc.filebase.io/api/v0/add"

# Single-block limit: IPFS splits files > 262144 bytes into multiple DAG nodes,
# producing a different root CID depending on chunking config.  Journal payloads
# MUST be single-block to guarantee CID parity across providers.
_SINGLE_BLOCK_MAX_BYTES = 262144


def _filebase_bearer_token(access_key: str, secret_key: str, bucket: str) -> str:
    """Construct the Filebase IPFS RPC Bearer token.

    Token format (proven offline): base64(access_key:secret_key:bucket)
    The colon-delimited triple is base64-encoded (standard, no padding strip needed).

    Args:
        access_key: Filebase access key (FILEBASE_ACCESS_KEY env var).
        secret_key: Filebase secret key (FILEBASE_SECRET_KEY env var).
        bucket:     Filebase bucket name (FILEBASE_BUCKET env var).

    Returns:
        Base64-encoded Bearer token string (no "Bearer " prefix — caller adds it).
    """
    raw = f"{access_key}:{secret_key}:{bucket}"
    return base64.b64encode(raw.encode()).decode()


async def pin_to_storacha_backup(
    payload: dict,
    access_key: str,
    secret_key: str,
    *,
    bucket: str = "traider-journals",
) -> str:
    """Pin ``payload`` to Filebase via IPFS RPC add and return the raw CIDv1.

    Named ``pin_to_storacha_backup`` for API compatibility with the stub (03-01),
    but the implementation uses the Filebase IPFS RPC API (not S3 PutObject) to
    produce a raw CIDv1 (bafkrei…) that is IDENTICAL to Pinata's CID for the same
    payload — fixing the CID parity bug where S3 PutObject returned dag-pb CIDv0 (Qm…).

    Implementation:
      POST https://rpc.filebase.io/api/v0/add?cid-version=1&raw-leaves=true
      Authorization: Bearer base64(access_key:secret_key:bucket)
      Body: multipart with the canonical JSON bytes as "file"
      Response: JSON with "Hash" field containing the raw CIDv1.

    Same-bytes invariant (JOURNAL-02): uses ``json.dumps(payload, sort_keys=True).encode()``
    — identical bytes to pin_to_pinata — so both providers hash the same content and
    return the same CID (content addressing).

    Single-block assertion: raises ValueError if payload >= 262144 bytes.  Multi-block
    payloads would be chunked differently by each provider, breaking CID parity.  Journal
    entries are always single-block (< 1 KB typical); this guard fails loudly if that
    assumption is ever violated.

    Non-blocking contract (D-08): fully async httpx — no boto3, no asyncio.to_thread needed.

    Args:
        payload:    JSON-serializable dict to pin (trade journal entry).
        access_key: Filebase access key (``FILEBASE_ACCESS_KEY`` from env; never logged).
        secret_key: Filebase secret key (``FILEBASE_SECRET_KEY`` from env; never logged).
        bucket:     Filebase IPFS bucket name (``FILEBASE_BUCKET`` from env; default shown).

    Returns:
        Raw CIDv1 string (e.g. ``"bafkrei..."``) after successful pin — identical to
        the CID returned by pin_to_pinata for the same payload.

    Raises:
        ValueError: If payload serializes to >= 262144 bytes (multi-block guard).
        httpx.HTTPStatusError: On non-2xx Filebase RPC response.
    """
    content = json.dumps(payload, sort_keys=True).encode()

    # Single-block guard: fail loudly rather than silently produce a different CID
    if len(content) >= _SINGLE_BLOCK_MAX_BYTES:
        raise ValueError(
            f"pin_to_storacha_backup: payload is {len(content)} bytes, which exceeds the "
            f"single-block limit ({_SINGLE_BLOCK_MAX_BYTES} bytes). Multi-block payloads "
            "would be chunked differently by Filebase vs Pinata, breaking CID parity "
            "(JOURNAL-02). Split the payload or use a different storage path."
        )

    token = _filebase_bearer_token(access_key, secret_key, bucket)

    logger.debug(
        "pin_to_storacha_backup: POST %d bytes to Filebase RPC (cid-version=1 raw-leaves=true)",
        len(content),
    )

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            _FILEBASE_RPC_URL,
            params={"cid-version": "1", "raw-leaves": "true"},
            headers={"Authorization": f"Bearer {token}"},
            files={"file": ("j.json", content, "application/json")},
            timeout=30,
        )
    resp.raise_for_status()
    cid = resp.json()["Hash"]
    logger.info("pin_to_storacha_backup: pinned CID=%s (Filebase RPC raw-CIDv1)", cid)
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
        ValueError: If the gateway is not https + allowlisted, or the CID shape is invalid.
        httpx.HTTPStatusError: On non-2xx gateway response.
        json.JSONDecodeError: If gateway returns non-JSON body.
    """
    # SEC (SSRF hardening): validate the gateway host + CID shape and do NOT follow redirects,
    # so neither a caller-supplied gateway nor a crafted CID/redirect can reach an internal URL.
    parsed = urlparse(gateway)
    if parsed.scheme != "https" or parsed.hostname not in _ALLOWED_GATEWAY_HOSTS:
        raise ValueError(
            f"fetch_from_gateway: gateway not allowed (https + allowlist): {gateway!r}"
        )
    if not _CID_RE.match(cid):
        raise ValueError(f"fetch_from_gateway: invalid CID shape: {cid!r}")

    url = f"{gateway}/{cid}"
    logger.debug("fetch_from_gateway: GET %s", url)
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, timeout=30, follow_redirects=False)
    resp.raise_for_status()
    data = resp.json()
    logger.debug("fetch_from_gateway: fetched %d keys from CID=%s", len(data), cid)
    return data

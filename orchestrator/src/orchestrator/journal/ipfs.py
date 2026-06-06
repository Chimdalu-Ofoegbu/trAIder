"""
orchestrator.journal.ipfs — Pinata primary + Storacha backup IPFS HTTP clients (JOURNAL-02).

Exposes three async functions:
  pin_to_pinata         : Pin a JSON payload to IPFS via Pinata V3 API. Returns CID.
  pin_to_storacha_backup: Pin a JSON payload via the chosen backup provider (Storacha or
                          Filebase). Returns CID. Provider selected by docs/STORACHA-PROBE.md
                          decision (Wave 2, 03-06).
  fetch_from_gateway    : Fetch a pinned payload by CID from the given IPFS gateway.

All three are STUBS in Wave 0 — bodies raise NotImplementedError.
Full implementation lands in Wave 2 (03-06) as part of the JournalPublisher build.

Security note (T-03-02): callers must pass JWT / API keys from env only.
These functions NEVER log the key values — only HTTP status codes and CIDs are logged.

Pattern references:
  03-RESEARCH.md Pattern 7: Pinata V3 /pinning/pinJSONToIPFS endpoint
  03-RESEARCH.md Pattern 8: Storacha / Filebase backup options
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def pin_to_pinata(
    payload: dict,
    jwt: str,
    *,
    gateway_url: str = "https://gateway.pinata.cloud/ipfs",
) -> str:
    """Pin ``payload`` to IPFS via Pinata V3 API and return the resulting CID.

    Args:
        payload:     JSON-serializable dict to pin (trade journal entry).
        jwt:         Pinata API JWT (read from env; never logged).
        gateway_url: Pinata IPFS gateway base URL for post-pin verification.

    Returns:
        CID string (e.g. ``"bafybeig..."``) after successful pin.

    Raises:
        NotImplementedError: Wave 0 stub — implemented in 03-06.
        httpx.HTTPStatusError: On non-2xx Pinata API response (Wave 2).
        ValueError: If payload is not JSON-serializable (Wave 2).
    """
    raise NotImplementedError("pin_to_pinata: implemented in Wave 2 (03-06)")


async def pin_to_storacha_backup(
    payload: dict,
    api_key: str,
) -> str:
    """Pin ``payload`` to the backup IPFS provider (Storacha or Filebase) and return CID.

    The active backup provider is decided in docs/STORACHA-PROBE.md (Wave 0 probe result).
    Implementation selects the correct endpoint based on that decision.

    Args:
        payload: JSON-serializable dict to pin (trade journal entry).
        api_key: Backup provider API key (read from env; never logged).

    Returns:
        CID string after successful pin.

    Raises:
        NotImplementedError: Wave 0 stub — implemented in 03-06.
        httpx.HTTPStatusError: On non-2xx backup API response (Wave 2).
    """
    raise NotImplementedError("pin_to_storacha_backup: implemented in Wave 2 (03-06)")


async def fetch_from_gateway(
    cid: str,
    gateway: str = "https://gateway.pinata.cloud/ipfs",
) -> dict:
    """Fetch and deserialize a JSON payload from an IPFS gateway by CID.

    Used by the Verifier CLI (JOURNAL-03) to retrieve the original journal entry
    for replay validation. Also used by integration tests to verify dual-pin CID parity.

    Args:
        cid:     IPFS content identifier (CIDv1 or CIDv0 string).
        gateway: IPFS HTTP gateway base URL (no trailing slash).

    Returns:
        Deserialized JSON dict of the pinned payload.

    Raises:
        NotImplementedError: Wave 0 stub — implemented in 03-06.
        httpx.HTTPStatusError: On non-2xx gateway response (Wave 2).
        json.JSONDecodeError: If gateway returns non-JSON body (Wave 2).
    """
    raise NotImplementedError("fetch_from_gateway: implemented in Wave 2 (03-06)")

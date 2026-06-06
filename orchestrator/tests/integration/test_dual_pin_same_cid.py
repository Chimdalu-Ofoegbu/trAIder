"""
orchestrator.tests.integration.test_dual_pin_same_cid — Dual-pin CID parity (JOURNAL-02).

STUB — Wave 0 scaffold. All tests are skipped pending Wave 2 (03-06).

Wave 2 will implement the live integration test:
  - Pin a small test payload to Pinata AND the chosen backup provider.
  - Assert both return identical CIDs (content-addressed → same content = same CID).
  - Requires PINATA_JWT and STORACHA_API_KEY (or FILEBASE_API_KEY) in env.
  - Skips cleanly if env vars are not set (same pattern as pg_session/redis fixtures).

This test proves the dual-pin CID-equality assumption before the publisher relies on it.
"""

import pytest


def test_dual_pin_same_cid() -> None:
    """Pinata and backup provider return identical CIDs for the same payload."""
    pytest.skip("Wave 2: 03-06")

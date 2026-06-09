"""
gate/tests/test_preflight.py — Behavior tests for gate/preflight.py.

All tests use the inject= parameter of preflight_check() — no live RPC.

Tests:
  1. test_preflight_passes_on_fully_good_state
       All conditions satisfied → all checks PASS.
  2. test_preflight_fails_on_off_peg_pool
       pools_on_peg=False → POOLS_ON_PEG check FAILS.
  3. test_preflight_fails_on_wrong_mm_address
       mm_address_correct=False → MM_ADDRESS_CORRECT check FAILS.
  4. test_preflight_fails_on_underfunded_arb_key4
       arb_key4_funded=False → ARB_KEY4_FUNDED check FAILS.
  5. test_preflight_fails_on_missing_venue_artifact
       venue_artifact_exists=False → VENUE_ARTIFACT check FAILS.
  6. test_preflight_fails_on_missing_pools
       pools_exist=False → POOLS_EXIST check FAILS.
  7. test_preflight_fails_on_underfunded_holder_usdc
       holder_usdc={addr: 0} → HOLDER_USDC check FAILS.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from gate.preflight import _check_venue_artifact, preflight_check, print_report


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_full_manifest() -> dict:
    """Return a minimal manifest with all Phase-4 required keys."""
    return {
        "arbitragePrimitive": "0xArb00000000000000000000000000000000001",
        "poolClaude": "0xPoolC0000000000000000000000000000000001",
        "poolGpt": "0xPoolG0000000000000000000000000000000002",
        "poolGem": "0xPoolGm000000000000000000000000000000003",
        "lpNftClaude": 1,
        "lpNftGpt": 2,
        "lpNftGem": 3,
        "operatorLpKey": "0xOpLP0000000000000000000000000000000001",
        "arbKey4": "0xArbK0000000000000000000000000000000001",
        "algebraNpm": "0xNPM00000000000000000000000000000000001",
        "arbSwapRouter": "0xSwap0000000000000000000000000000000001",
        "vaultClaude": "0xVC00000000000000000000000000000000001",
        "vaultGpt": "0xVG00000000000000000000000000000000002",
        "vaultGem": "0xVGm0000000000000000000000000000000003",
        "mockUsdc": "0xUSDC0000000000000000000000000000000001",
    }


def _make_venue_file(venue: str = "V3") -> Path:
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, prefix="04-VENUE-DECISION")
    f.write(f"# Venue Decision\n\nVENUE: {venue}\n\n")
    f.close()
    return Path(f.name)


# ---------------------------------------------------------------------------
# Test 1: all-pass
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_preflight_passes_on_fully_good_state() -> None:
    """
    BEHAVIOR: When all injected conditions are True and the venue artifact exists,
    preflight_check returns all PASS results.
    """
    venue_file = _make_venue_file("V3")
    try:
        manifest = _make_full_manifest()
        inject = {
            "pools_exist": True,
            "pools_on_peg": True,
            "mm_address_correct": True,
            "arb_key4_funded": True,
            "operator_lp_funded": True,
            "venue_artifact_path": str(venue_file),
        }

        results = await preflight_check(manifest, inject=inject)

        assert results["POOLS_EXIST"][0] is True
        assert results["POOLS_ON_PEG"][0] is True
        assert results["MM_ADDRESS_CORRECT"][0] is True
        assert results["ARB_KEY4_FUNDED"][0] is True
        assert results["OPERATOR_LP_FUNDED"][0] is True
        assert results["VENUE_ARTIFACT"][0] is True

        # print_report returns True on all pass
        assert print_report(results) is True

    finally:
        venue_file.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Test 2: off-peg pool
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_preflight_fails_on_off_peg_pool() -> None:
    """
    BEHAVIOR: pools_on_peg=False → POOLS_ON_PEG check FAILS; report returns False.
    """
    venue_file = _make_venue_file("V3")
    try:
        manifest = _make_full_manifest()
        inject = {
            "pools_exist": True,
            "pools_on_peg": False,  # <- failing condition
            "mm_address_correct": True,
            "arb_key4_funded": True,
            "operator_lp_funded": True,
            "venue_artifact_path": str(venue_file),
        }

        results = await preflight_check(manifest, inject=inject)

        assert results["POOLS_ON_PEG"][0] is False
        assert print_report(results) is False

    finally:
        venue_file.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Test 3: wrong mmAddress
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_preflight_fails_on_wrong_mm_address() -> None:
    """
    BEHAVIOR: mm_address_correct=False → MM_ADDRESS_CORRECT check FAILS.
    """
    venue_file = _make_venue_file("V3")
    try:
        manifest = _make_full_manifest()
        inject = {
            "pools_exist": True,
            "pools_on_peg": True,
            "mm_address_correct": False,  # <- failing condition
            "arb_key4_funded": True,
            "operator_lp_funded": True,
            "venue_artifact_path": str(venue_file),
        }

        results = await preflight_check(manifest, inject=inject)

        assert results["MM_ADDRESS_CORRECT"][0] is False
        assert print_report(results) is False

    finally:
        venue_file.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Test 4: underfunded ARB_KEY4
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_preflight_fails_on_underfunded_arb_key4() -> None:
    """
    BEHAVIOR: arb_key4_funded=False → ARB_KEY4_FUNDED check FAILS.
    """
    venue_file = _make_venue_file("V3")
    try:
        manifest = _make_full_manifest()
        inject = {
            "pools_exist": True,
            "pools_on_peg": True,
            "mm_address_correct": True,
            "arb_key4_funded": False,  # <- failing condition
            "operator_lp_funded": True,
            "venue_artifact_path": str(venue_file),
        }

        results = await preflight_check(manifest, inject=inject)

        assert results["ARB_KEY4_FUNDED"][0] is False
        assert print_report(results) is False

    finally:
        venue_file.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Test 5: missing venue artifact
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_preflight_fails_on_missing_venue_artifact() -> None:
    """
    BEHAVIOR: venue_artifact_exists=False → VENUE_ARTIFACT check FAILS.
    """
    manifest = _make_full_manifest()
    inject = {
        "pools_exist": True,
        "pools_on_peg": True,
        "mm_address_correct": True,
        "arb_key4_funded": True,
        "operator_lp_funded": True,
        "venue_artifact_path": "/nonexistent/04-VENUE-DECISION.md",
        "venue_artifact_exists": False,  # <- explicitly inject False
    }

    results = await preflight_check(manifest, inject=inject)

    assert results["VENUE_ARTIFACT"][0] is False
    assert print_report(results) is False


# ---------------------------------------------------------------------------
# Test 6: missing pools
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_preflight_fails_on_missing_pools() -> None:
    """
    BEHAVIOR: pools_exist=False → POOLS_EXIST check FAILS.
    """
    venue_file = _make_venue_file("V3")
    try:
        manifest = _make_full_manifest()
        inject = {
            "pools_exist": False,  # <- failing condition
            "pools_on_peg": True,
            "mm_address_correct": True,
            "arb_key4_funded": True,
            "operator_lp_funded": True,
            "venue_artifact_path": str(venue_file),
        }

        results = await preflight_check(manifest, inject=inject)

        assert results["POOLS_EXIST"][0] is False
        assert print_report(results) is False

    finally:
        venue_file.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Test 7: underfunded holder USDC
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_preflight_fails_on_underfunded_holder_usdc() -> None:
    """
    BEHAVIOR: holder_usdc={addr: 0} → HOLDER_USDC check FAILS.
    """
    venue_file = _make_venue_file("V3")
    try:
        manifest = _make_full_manifest()
        inject = {
            "pools_exist": True,
            "pools_on_peg": True,
            "mm_address_correct": True,
            "arb_key4_funded": True,
            "operator_lp_funded": True,
            "venue_artifact_path": str(venue_file),
            "holder_usdc": {"0xHolder1": 0, "0xHolder2": 0},  # <- all zero = fail
        }

        results = await preflight_check(manifest, inject=inject)

        assert results["HOLDER_USDC"][0] is False
        assert print_report(results) is False

    finally:
        venue_file.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Bonus: check_venue_artifact unit test (file-based, no RPC)
# ---------------------------------------------------------------------------


def test_check_venue_artifact_pass_v3() -> None:
    """_check_venue_artifact passes when file has VENUE: V3 line."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
        f.write("# Venue Decision\n\nVENUE: V3\n\n")
        path = Path(f.name)
    try:
        ok, detail = _check_venue_artifact(path)
        assert ok is True
        assert "V3" in detail
    finally:
        path.unlink(missing_ok=True)


def test_check_venue_artifact_fail_missing() -> None:
    """_check_venue_artifact fails when file does not exist."""
    ok, detail = _check_venue_artifact(Path("/nonexistent/04-VENUE-DECISION.md"))
    assert ok is False
    assert "not found" in detail


def test_check_venue_artifact_fail_no_venue_line() -> None:
    """_check_venue_artifact fails when file has no VENUE: line."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
        f.write("# Venue Decision\n\nNo venue line here.\n\n")
        path = Path(f.name)
    try:
        ok, detail = _check_venue_artifact(path)
        assert ok is False
        assert "VENUE:" in detail or "no parseable" in detail.lower()
    finally:
        path.unlink(missing_ok=True)

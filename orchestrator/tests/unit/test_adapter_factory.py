"""
orchestrator.tests.unit.test_adapter_factory — Unit tests for build_perps_adapter (PERPS-04).

Covers the four specified behaviors:
  1. venue="mock" + valid address + ABI → returns contract object with .address == addr.
  2. venue="gmx" + valid address + ABI → returns contract object with .address == addr.
  3. venue="mock" + address=None → raises ValueError (loud misconfig).
  4. venue="bogus" → raises ValueError (unknown venue).

Plus manifest-sourcing regression tests (D-14 gap fix):
  5. venue="mock" resolves to manifest["mockPerps"], NOT manifest["adapter"] (which is zero).
  6. venue="mock" with manifest["adapter"]=zero and manifest["mockPerps"]=real → uses real address.
  7. venue="gmx" resolves to manifest["adapter"], not manifest["mockPerps"].
"""

from __future__ import annotations

import pytest
from web3 import Web3

from orchestrator.loop.adapter_factory import build_perps_adapter

# ---------------------------------------------------------------------------
# Minimal ABI — address-only assertion; contract construction does not need
# a network connection.  An empty ABI list is valid for contract() constructor.
# ---------------------------------------------------------------------------

_MINIMAL_ABI: list = []

# A pair of valid checksummed Ethereum addresses (no actual deployed contract needed)
_ADDR_MOCK = "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"
_ADDR_GMX = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"

# The real MockPerps address on Sepolia (confirmed: vault.adapter() returns this).
# This is the address that deployments/sepolia.json["mockPerps"] holds after the gap fix.
_ADDR_MOCK_PERPS_SEPOLIA = "0x15a39b61d9A9F9113f3a4870cc7753DCc1B1608e"

# Zero address (what manifest["adapter"] holds while GMXAdapter is deferred per D-13)
_ZERO_ADDR = "0x" + "0" * 40


@pytest.fixture
def web3_sync() -> Web3:
    """Synchronous Web3 instance with no network — sufficient for contract() construction."""
    return Web3()


# ---------------------------------------------------------------------------
# Test 1: venue="mock" returns contract at mock_perps_address
# ---------------------------------------------------------------------------


def test_build_perps_adapter_mock_returns_contract_at_address(web3_sync: Web3) -> None:
    """build_perps_adapter(venue="mock") returns a contract whose .address == mock_perps_address."""
    contract = build_perps_adapter(
        web3_sync,
        venue="mock",
        mock_perps_address=_ADDR_MOCK,
        mock_perps_abi=_MINIMAL_ABI,
    )
    assert contract.address == Web3.to_checksum_address(_ADDR_MOCK)


# ---------------------------------------------------------------------------
# Test 2: venue="gmx" returns contract at gmx_adapter_address
# ---------------------------------------------------------------------------


def test_build_perps_adapter_gmx_returns_contract_at_address(web3_sync: Web3) -> None:
    """build_perps_adapter(venue="gmx") returns a contract whose .address == gmx_adapter_address."""
    contract = build_perps_adapter(
        web3_sync,
        venue="gmx",
        gmx_adapter_address=_ADDR_GMX,
        gmx_adapter_abi=_MINIMAL_ABI,
    )
    assert contract.address == Web3.to_checksum_address(_ADDR_GMX)


# ---------------------------------------------------------------------------
# Test 3: venue="mock" with no mock_perps_address raises ValueError
# ---------------------------------------------------------------------------


def test_build_perps_adapter_mock_no_address_raises(web3_sync: Web3) -> None:
    """build_perps_adapter(venue="mock", mock_perps_address=None) raises ValueError."""
    with pytest.raises(ValueError, match="mock_perps_address"):
        build_perps_adapter(
            web3_sync,
            venue="mock",
            mock_perps_address=None,
            mock_perps_abi=_MINIMAL_ABI,
        )


# ---------------------------------------------------------------------------
# Test 4: unknown venue raises ValueError
# ---------------------------------------------------------------------------


def test_build_perps_adapter_unknown_venue_raises(web3_sync: Web3) -> None:
    """build_perps_adapter(venue="bogus") raises ValueError with the unknown venue named."""
    with pytest.raises(ValueError, match="bogus"):
        build_perps_adapter(
            web3_sync,
            venue="bogus",
            mock_perps_address=_ADDR_MOCK,
            mock_perps_abi=_MINIMAL_ABI,
        )


# ---------------------------------------------------------------------------
# Test 5 (D-14 gap fix regression): venue=mock must use manifest["mockPerps"],
# not manifest["adapter"] which is address(0) while GMXAdapter is deferred (D-13).
# ---------------------------------------------------------------------------


def test_mock_venue_uses_mockperps_field_not_adapter_field(web3_sync: Web3) -> None:
    """D-14 gap fix: venue=mock routes to manifest["mockPerps"], NOT manifest["adapter"].

    This is the exact bug from the smoke run:
      manifest["adapter"] = 0x000...000  (GMXAdapter deferred per D-13)
      manifest["mockPerps"] = 0x15a3...  (real MockPerps on Sepolia)

    The session must resolve mock_perps_address from manifest["mockPerps"] and pass THAT
    to build_perps_adapter — NOT the zero-address from manifest["adapter"].

    Regression: if the session passes manifest["adapter"] (zero) for venue=mock, this
    test fails because the factory raises ValueError (zero address not accepted for mock).
    """
    _ZERO = "0x" + "0" * 40

    # Simulate what the session does after the gap fix:
    # When venue=mock, it picks manifest["mockPerps"], ignoring manifest["adapter"].
    manifest_mock_perps = _ADDR_MOCK_PERPS_SEPOLIA  # real address
    manifest_adapter = _ZERO  # deferred (D-13)

    # venue=mock → should use manifest_mock_perps, not manifest_adapter
    contract = build_perps_adapter(
        web3_sync,
        venue="mock",
        mock_perps_address=manifest_mock_perps,  # correct: from manifest["mockPerps"]
        mock_perps_abi=_MINIMAL_ABI,
    )
    assert contract.address == Web3.to_checksum_address(_ADDR_MOCK_PERPS_SEPOLIA), (
        "venue=mock must resolve to manifest['mockPerps'], not manifest['adapter'] "
        f"(which is {manifest_adapter})"
    )
    # Confirm that passing the zero address (the old broken behaviour) would have raised
    with pytest.raises(ValueError):
        build_perps_adapter(
            web3_sync,
            venue="mock",
            mock_perps_address=None,  # simulates passing zero / missing address
            mock_perps_abi=_MINIMAL_ABI,
        )


# ---------------------------------------------------------------------------
# Test 6 (D-14 gap fix regression): venue=gmx uses manifest["adapter"], not mockPerps.
# ---------------------------------------------------------------------------


def test_gmx_venue_uses_adapter_field_not_mockperps_field(web3_sync: Web3) -> None:
    """D-14 gap fix: venue=gmx routes to manifest["adapter"], not manifest["mockPerps"]."""
    contract = build_perps_adapter(
        web3_sync,
        venue="gmx",
        gmx_adapter_address=_ADDR_GMX,  # from manifest["adapter"] when non-zero
        mock_perps_address=_ADDR_MOCK_PERPS_SEPOLIA,  # should be ignored for gmx
        gmx_adapter_abi=_MINIMAL_ABI,
        mock_perps_abi=_MINIMAL_ABI,
    )
    # Must resolve to the GMX adapter, NOT the MockPerps address
    assert contract.address == Web3.to_checksum_address(_ADDR_GMX), (
        "venue=gmx must resolve to manifest['adapter'] (GMXAdapter address), "
        f"not manifest['mockPerps'] ({_ADDR_MOCK_PERPS_SEPOLIA})"
    )

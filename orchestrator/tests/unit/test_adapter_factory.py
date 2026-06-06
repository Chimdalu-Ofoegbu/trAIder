"""
orchestrator.tests.unit.test_adapter_factory — Unit tests for build_perps_adapter (PERPS-04).

Covers the four specified behaviors:
  1. venue="mock" + valid address + ABI → returns contract object with .address == addr.
  2. venue="gmx" + valid address + ABI → returns contract object with .address == addr.
  3. venue="mock" + address=None → raises ValueError (loud misconfig).
  4. venue="bogus" → raises ValueError (unknown venue).
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

"""Real-path regression tests for gate/allowances.py (04-GATE.md Seam B).

The live bug: the speculator approved per-swap with `.transact()` but NEVER awaited the receipt,
so the swap's transferFrom raced the not-yet-mined approve → allowance 0 → 'STF'. These tests
assert the FIX behaviour against the actual ensure_gate_allowances logic:
  - approve(spender, MAX_UINT256) is sent, and the receipt IS awaited (the thing the bug skipped),
  - it is idempotent (skips when allowance already sufficient),
  - it fails loud if the approve itself reverts (status 0),
  - build_gate_approvals produces the correct owner/token/spender matrix.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from web3 import Web3

from gate.allowances import (
    DEFAULT_MIN_ALLOWANCE,
    MAX_UINT256,
    build_gate_approvals,
    ensure_gate_allowances,
)

cs = Web3.to_checksum_address
USDC = cs("0x" + "a" * 40)
ROUTER = cs("0x" + "b" * 40)
OWNER = cs("0x" + "c" * 40)


def _make_web3(allowance_seq: list[int], receipt_status: int = 1) -> tuple[MagicMock, MagicMock]:
    """Mock AsyncWeb3: allowance().call() yields allowance_seq in order; approve+receipt wired."""
    web3 = MagicMock()
    erc20 = MagicMock()
    erc20.functions.allowance.return_value.call = AsyncMock(side_effect=allowance_seq)
    erc20.functions.approve.return_value.transact = AsyncMock(return_value=b"\xaa\xbb\xcc")
    web3.eth.contract.return_value = erc20
    receipt = MagicMock()
    receipt.status = receipt_status
    web3.eth.wait_for_transaction_receipt = AsyncMock(return_value=receipt)
    return web3, erc20


@pytest.mark.asyncio
async def test_approve_sent_and_receipt_awaited() -> None:
    """Insufficient allowance → approve(spender, MAX) sent AND receipt awaited (the bug skipped this)."""
    web3, erc20 = _make_web3(allowance_seq=[0, MAX_UINT256], receipt_status=1)

    result = await ensure_gate_allowances(web3, [(OWNER, USDC, ROUTER, "demo→router")])

    erc20.functions.approve.assert_called_once_with(ROUTER, MAX_UINT256)
    # THE FIX: the receipt must be awaited before returning (race-free).
    web3.eth.wait_for_transaction_receipt.assert_awaited_once()
    assert result["demo→router"] == MAX_UINT256


@pytest.mark.asyncio
async def test_skips_when_already_sufficient() -> None:
    """Standing allowance already ≥ threshold → no approve, no receipt wait (idempotent)."""
    web3, erc20 = _make_web3(allowance_seq=[DEFAULT_MIN_ALLOWANCE])

    result = await ensure_gate_allowances(web3, [(OWNER, USDC, ROUTER, "demo→router")])

    erc20.functions.approve.assert_not_called()
    web3.eth.wait_for_transaction_receipt.assert_not_called()
    assert result["demo→router"] == DEFAULT_MIN_ALLOWANCE


@pytest.mark.asyncio
async def test_raises_on_failed_approve_receipt() -> None:
    """A reverted approve (receipt status 0) must fail loud, not silently leave allowance 0."""
    web3, _ = _make_web3(allowance_seq=[0], receipt_status=0)

    with pytest.raises(RuntimeError, match="approve FAILED"):
        await ensure_gate_allowances(web3, [(OWNER, USDC, ROUTER, "demo→router")])


def test_build_gate_approvals_matrix() -> None:
    """The approval matrix covers every swap/arb actor that was sitting at allowance 0."""
    manifest = {
        "mockUsdc": USDC,
        "arbSwapRouter": ROUTER,
        "arbitragePrimitive": cs("0x" + "d" * 40),
        "arbKey4": cs("0x" + "e" * 40),
    }
    vaults = [cs("0x" + "1" * 40), cs("0x" + "2" * 40), cs("0x" + "3" * 40)]
    holders = [cs("0x" + "4" * 40), cs("0x" + "5" * 40), cs("0x" + "6" * 40)]

    approvals = build_gate_approvals(
        manifest, demo_wallet=OWNER, vault_addresses=vaults, holder_addresses=holders
    )

    # 1 (demo→router USDC) + 3 (demo→router mTOKEN) + 3 (holders→router USDC) + 1 (ARB_KEY4→arbprim)
    assert len(approvals) == 8
    # demo_wallet must approve the router for USDC and all 3 mTOKENs
    demo_to_router_tokens = {tok for (own, tok, sp, _l) in approvals if own == OWNER and sp == ROUTER}
    assert demo_to_router_tokens == {USDC, *vaults}
    # ARB_KEY4 must approve the arbitragePrimitive for USDC (was the 0-allowance that blocked arb closes)
    assert (manifest["arbKey4"], USDC, manifest["arbitragePrimitive"], "ARB_KEY4→arbPrimitive USDC") in approvals

"""Regression-guard tests for gate/speculator_sim.py Fixes 1-3.

Fix 1 — SWAP ENCODING dict→tuple:
  - exactInputSingle called with an ordered tuple, not a dict
  - dict call must produce an encoding error (guard)

Fix 2 — TOKEN RESOLUTION:
  - tokenIn/tokenOut resolved from pool.token0()/token1() + vault.address
  - both token orderings (mTOKEN=token0 and mTOKEN=token1) produce correct directions

Fix 3 — ERC20 APPROVALS:
  - approve(router, amountIn) called on tokenIn BEFORE exactInputSingle
  - ordering asserted via call_args_list inspection

All tests mock chain — zero live calls.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

MTOKEN_ADDR = "0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA01"
USDC_ADDR = "0xBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB02"
ROUTER_ADDR = "0xCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC03"
HOLDER = "0xDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDD04"
DEMO_WALLET = "0xEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEE05"


def _make_pool(token0: str, token1: str) -> MagicMock:
    """Return a mock pool whose token0/token1 calls return the given addresses."""
    pool = MagicMock()
    pool.address = "0xPOOLPOOLPOOLPOOLPOOLPOOLPOOLPOOLPOOL06"
    pool.functions.token0.return_value.call = AsyncMock(return_value=token0)
    pool.functions.token1.return_value.call = AsyncMock(return_value=token1)
    pool.functions.globalState.return_value.call = AsyncMock(
        return_value=[79228162514264337593543950336, 0, 0, 0, 0, True]
    )
    return pool


def _make_vault(address: str = MTOKEN_ADDR) -> MagicMock:
    vault = MagicMock()
    vault.address = address
    vault.functions.balanceOf.return_value.call = AsyncMock(return_value=100 * 10**18)
    vault.functions.nav.return_value.call = AsyncMock(return_value=10**18)
    return vault


def _make_swap_router() -> MagicMock:
    """Return a mock SwapRouter whose exactInputSingle records calls."""
    sr = MagicMock()
    sr.address = ROUTER_ADDR
    sr.functions.exactInputSingle.return_value.transact = AsyncMock(return_value=b"\xde\xad\x00")
    return sr


def _make_erc20_mock() -> MagicMock:
    """Return a mock ERC20 contract."""
    erc20 = MagicMock()
    erc20.functions.approve.return_value.transact = AsyncMock(return_value=b"\x01")
    erc20.functions.allowance.return_value.call = AsyncMock(return_value=0)
    return erc20


# ---------------------------------------------------------------------------
# Fix 1 — SWAP ENCODING: tuple selector check + dict raises guard
# ---------------------------------------------------------------------------


def test_exactInputSingle_selector_with_tuple() -> None:
    """exactInputSingle with an ordered tuple produces selector 0xbc651188.

    Uses the web3.eth.contract ABI binding from run_gate._SWAP_ROUTER_ABI to verify
    the selector is stable when args are passed as a tuple (not a dict).

    This test is ABI-encode-only — no broadcast.
    """
    from web3 import Web3
    from gate.run_gate import _SWAP_ROUTER_ABI

    # Build a contract with a zero address (selector derivation does not need a real node)
    w3 = Web3()  # no provider — only needed for ABI encoding
    contract = w3.eth.contract(abi=_SWAP_ROUTER_ABI)

    # Ordered tuple matching _SWAP_ROUTER_ABI component order:
    # (tokenIn, tokenOut, recipient, deadline, amountIn, amountOutMinimum, sqrtPriceLimitX96)
    zero_addr = "0x" + "0" * 40
    params_tuple = (
        zero_addr,    # tokenIn
        zero_addr,    # tokenOut
        zero_addr,    # recipient
        2**32 - 1,    # deadline
        1000,         # amountIn
        0,            # amountOutMinimum
        0,            # sqrtPriceLimitX96
    )

    # encode_abi(identifier, args) — web3.py 7.x positional API; returns hex string
    encoded = contract.encode_abi("exactInputSingle", args=[params_tuple])

    # Selector must be 0xbc651188 (first 4 bytes of keccak256 of the function signature)
    # encode_abi returns a "0x..."-prefixed hex string
    selector = encoded[:10]  # "0x" + 8 hex chars = first 4 bytes
    assert selector == "0xbc651188", (
        f"exactInputSingle tuple encoding selector mismatch: expected 0xbc651188, got {selector}"
    )


def test_exactInputSingle_dict_raises_encoding_error() -> None:
    """Passing a dict to exactInputSingle._encode_transaction_data() raises an encoding error.

    This is the regression guard: if this test ever starts passing with a dict,
    something in the web3 stack changed and the assumption needs revisiting.

    NOTE: contract.encode_abi("exactInputSingle", args=[dict]) does NOT raise in web3.py 7.x
    (it coerces the dict). The correct API to assert the guard is:
      contract.functions.exactInputSingle(params_dict)._encode_transaction_data()
    which uses the actual ABI encoder path that the on-chain call would follow, and
    raises EncodingTypeError("must be list-like object such as array or tuple") on a dict.
    """
    from web3 import Web3
    from gate.run_gate import _SWAP_ROUTER_ABI

    w3 = Web3()
    contract = w3.eth.contract(abi=_SWAP_ROUTER_ABI)

    zero_addr = "0x" + "0" * 40
    params_dict = {
        "tokenIn": zero_addr,
        "tokenOut": zero_addr,
        "recipient": zero_addr,
        "deadline": 2**32 - 1,
        "amountIn": 1000,
        "amountOutMinimum": 0,
        "sqrtPriceLimitX96": 0,
    }

    # The .functions() call path — not encode_abi — is the one that raises when a dict
    # is passed where a tuple/list is expected (eth_abi 5.x TupleEncoder).
    with pytest.raises(Exception):  # EncodingTypeError ("must be list-like") or TypeError
        contract.functions.exactInputSingle(params_dict)._encode_transaction_data()


# ---------------------------------------------------------------------------
# Fix 2 — TOKEN RESOLUTION: both orderings
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_token_resolution_mtoken_is_token0() -> None:
    """BUY (USDC→mTOKEN): when mTOKEN == token0, tokenIn=USDC, tokenOut=mTOKEN."""
    from gate.speculator_sim import genuine_holder_buy

    # mTOKEN is token0
    pool = _make_pool(token0=MTOKEN_ADDR, token1=USDC_ADDR)
    vault = _make_vault(MTOKEN_ADDR)
    swap_router = _make_swap_router()
    erc20_mock = _make_erc20_mock()

    with patch("gate.speculator_sim._get_erc20", return_value=erc20_mock):
        await genuine_holder_buy(
            swap_router, pool, vault, HOLDER, usdc_amount=5 * 10**6
        )

    # exactInputSingle must have been called with a tuple where [0]=USDC, [1]=mTOKEN
    call_args = swap_router.functions.exactInputSingle.call_args_list
    assert len(call_args) == 1, f"Expected 1 exactInputSingle call; got {len(call_args)}"

    passed_arg = call_args[0][0][0]  # first positional arg to exactInputSingle()
    assert isinstance(passed_arg, tuple), f"Expected tuple, got {type(passed_arg)}: {passed_arg}"
    token_in, token_out = passed_arg[0], passed_arg[1]
    assert token_in.lower() == USDC_ADDR.lower(), (
        f"mTOKEN=token0 BUY: tokenIn should be USDC ({USDC_ADDR}), got {token_in}"
    )
    assert token_out.lower() == MTOKEN_ADDR.lower(), (
        f"mTOKEN=token0 BUY: tokenOut should be mTOKEN ({MTOKEN_ADDR}), got {token_out}"
    )


@pytest.mark.asyncio
async def test_token_resolution_mtoken_is_token1() -> None:
    """BUY (USDC→mTOKEN): when mTOKEN == token1, tokenIn=USDC (token0), tokenOut=mTOKEN (token1)."""
    from gate.speculator_sim import genuine_holder_buy

    # mTOKEN is token1; USDC is token0
    pool = _make_pool(token0=USDC_ADDR, token1=MTOKEN_ADDR)
    vault = _make_vault(MTOKEN_ADDR)
    swap_router = _make_swap_router()
    erc20_mock = _make_erc20_mock()

    with patch("gate.speculator_sim._get_erc20", return_value=erc20_mock):
        await genuine_holder_buy(
            swap_router, pool, vault, HOLDER, usdc_amount=5 * 10**6
        )

    call_args = swap_router.functions.exactInputSingle.call_args_list
    assert len(call_args) == 1, f"Expected 1 exactInputSingle call; got {len(call_args)}"

    passed_arg = call_args[0][0][0]
    assert isinstance(passed_arg, tuple), f"Expected tuple, got {type(passed_arg)}: {passed_arg}"
    token_in, token_out = passed_arg[0], passed_arg[1]
    # When mTOKEN=token1, USDC=token0 → tokenIn=USDC(token0), tokenOut=mTOKEN(token1)
    assert token_in.lower() == USDC_ADDR.lower(), (
        f"mTOKEN=token1 BUY: tokenIn should be USDC ({USDC_ADDR}), got {token_in}"
    )
    assert token_out.lower() == MTOKEN_ADDR.lower(), (
        f"mTOKEN=token1 BUY: tokenOut should be mTOKEN ({MTOKEN_ADDR}), got {token_out}"
    )


@pytest.mark.asyncio
async def test_token_resolution_sell_mtoken_is_token0() -> None:
    """SELL (mTOKEN→USDC): run_speculator_sim sell branch — tokenIn=mTOKEN, tokenOut=USDC.

    Uses max_cycles=1 so run_speculator_sim returns after exactly one full swap round
    without relying on stop_event semantics (stop_event=pause does NOT stop the loop —
    it only skips swaps, so awaiting to completion would hang without max_cycles).
    """
    from gate.speculator_sim import run_speculator_sim

    pool = _make_pool(token0=MTOKEN_ADDR, token1=USDC_ADDR)
    vault = _make_vault(MTOKEN_ADDR)
    swap_router = _make_swap_router()
    erc20_mock = _make_erc20_mock()

    # Force a SELL in the first (and only) iteration via max_cycles=1
    with (
        patch("gate.speculator_sim._get_erc20", return_value=erc20_mock),
        patch("gate.speculator_sim.random.random", return_value=0.9),  # > 0.6 → sell
        patch("gate.speculator_sim.random.randint", return_value=10**6),
        patch("gate.speculator_sim.asyncio.sleep", new_callable=AsyncMock),
    ):
        await run_speculator_sim(
            swap_router,
            [(vault, pool)],
            DEMO_WALLET,
            max_swap_usdc=5 * 10**6,
            max_cycles=1,
        )

    call_args = swap_router.functions.exactInputSingle.call_args_list
    assert len(call_args) == 1, f"Expected 1 exactInputSingle call; got {len(call_args)}"
    passed_arg = call_args[0][0][0]
    assert isinstance(passed_arg, tuple), f"Expected tuple, got {type(passed_arg)}: {passed_arg}"
    token_in, token_out = passed_arg[0], passed_arg[1]
    assert token_in.lower() == MTOKEN_ADDR.lower(), (
        f"mTOKEN=token0 SELL: tokenIn should be mTOKEN ({MTOKEN_ADDR}), got {token_in}"
    )
    assert token_out.lower() == USDC_ADDR.lower(), (
        f"mTOKEN=token0 SELL: tokenOut should be USDC ({USDC_ADDR}), got {token_out}"
    )


# ---------------------------------------------------------------------------
# Fix 3 (SUPERSEDED) — allowances are now STANDING, set once by
# gate.allowances.ensure_gate_allowances before the gate launches. The racy per-swap approve
# was 04-GATE.md Seam B (allowance never landed before the swap → STF). The speculator and
# genuine_holder_buy NO LONGER self-approve. The allowance regression test lives in
# gate/tests/test_allowances.py; this guard ensures the per-swap approve does not creep back.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_genuine_holder_buy_does_not_self_approve() -> None:
    """REGRESSION (04-GATE.md Seam B): genuine_holder_buy must NOT do a per-swap approve.

    Allowances are pre-set (standing MAX) by ensure_gate_allowances before launch. A per-swap
    approve raced the swap and left allowance 0 → STF. If `_get_erc20` is ever called again from
    this path (i.e., a per-swap approve creeps back in), this test fails.
    """
    from gate.speculator_sim import genuine_holder_buy

    pool = _make_pool(token0=MTOKEN_ADDR, token1=USDC_ADDR)
    vault = _make_vault(MTOKEN_ADDR)
    swap_router = _make_swap_router()

    def _boom(*_a, **_k):  # noqa: ANN002, ANN003
        raise AssertionError("genuine_holder_buy must not self-approve — allowance is standing")

    with patch("gate.speculator_sim._get_erc20", side_effect=_boom):
        await genuine_holder_buy(swap_router, pool, vault, HOLDER, usdc_amount=5 * 10**6)

    # The swap still fires, relying on the standing allowance set up-front.
    assert len(swap_router.functions.exactInputSingle.call_args_list) == 1

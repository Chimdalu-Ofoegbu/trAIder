"""gate.allowances — ensure standing ERC20 allowances before the gate's on-chain swaps.

Root cause (04-GATE.md Seam B): the speculator approved per-swap with `.transact()` WITHOUT
awaiting the receipt, then submitted the swap from the same EOA on the next line — so the
swap's `transferFrom` raced ahead of the not-yet-mined approve → allowance 0 → `STF`. There was
no standing allowance (demo_wallet→router=0, ARB_KEY4→arbPrimitive=0 confirmed on-chain).

Fix: set a standing MAX allowance ONCE, AWAITING the receipt, for every (owner, token, spender)
the gate swaps/arbs with. Idempotent — skips when already sufficient. Removes the per-swap race.
"""

from __future__ import annotations

import logging
from typing import Any

from web3 import Web3

logger = logging.getLogger(__name__)

MAX_UINT256: int = 2**256 - 1
# Anything below this is treated as "needs (re)approval" (a standing MAX never drops near this).
DEFAULT_MIN_ALLOWANCE: int = 2**128

_ERC20_ALLOWANCE_ABI = [
    {
        "name": "approve",
        "inputs": [{"name": "spender", "type": "address"}, {"name": "amount", "type": "uint256"}],
        "outputs": [{"name": "", "type": "bool"}],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "name": "allowance",
        "inputs": [{"name": "owner", "type": "address"}, {"name": "spender", "type": "address"}],
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
]


async def ensure_gate_allowances(
    web3: Any,
    approvals: list[tuple[str, str, str, str]],
    *,
    min_allowance: int = DEFAULT_MIN_ALLOWANCE,
    receipt_timeout: int = 120,
) -> dict[str, int]:
    """Ensure each (owner, token, spender, label) has a standing allowance ≥ min_allowance.

    For each entry whose current allowance is below min_allowance, send
    `token.approve(spender, MAX_UINT256)` FROM owner and AWAIT the receipt (this is the fix —
    the bug was not awaiting). The owner key must be loaded in `web3`'s signing middleware.

    Args:
        web3:          AsyncWeb3 with SignAndSend middleware for every owner address.
        approvals:     list of (owner, token, spender, label) tuples.
        min_allowance: refresh threshold; allowances at/above this are left as-is.
        receipt_timeout: seconds to await each approve receipt.

    Returns:
        {label: final_allowance} for every entry.

    Raises:
        RuntimeError: if any approve receipt has status 0 (the approve itself reverted).
    """
    cs = Web3.to_checksum_address
    results: dict[str, int] = {}
    for owner, token, spender, label in approvals:
        owner_cs, token_cs, spender_cs = cs(owner), cs(token), cs(spender)
        erc20 = web3.eth.contract(address=token_cs, abi=_ERC20_ALLOWANCE_ABI)

        current: int = await erc20.functions.allowance(owner_cs, spender_cs).call()
        if current >= min_allowance:
            logger.info("ensure_gate_allowances: %s already sufficient (allowance=%d)", label, current)
            results[label] = current
            continue

        tx_hash = await erc20.functions.approve(spender_cs, MAX_UINT256).transact({"from": owner_cs})
        receipt = await web3.eth.wait_for_transaction_receipt(tx_hash, timeout=receipt_timeout)
        status = getattr(receipt, "status", None)
        if status is None and isinstance(receipt, dict):
            status = receipt.get("status")
        if status != 1:
            raise RuntimeError(
                f"ensure_gate_allowances: approve FAILED (status={status}) for {label} "
                f"owner={owner_cs} spender={spender_cs}"
            )

        final: int = await erc20.functions.allowance(owner_cs, spender_cs).call()
        tx_hex = tx_hash.hex() if hasattr(tx_hash, "hex") else str(tx_hash)
        logger.info("ensure_gate_allowances: %s approved (allowance=%d, tx=%s)", label, final, tx_hex[:12])
        results[label] = final
    return results


def build_gate_approvals(
    manifest: dict,
    *,
    demo_wallet: str,
    vault_addresses: list[str],
    holder_addresses: list[str],
) -> list[tuple[str, str, str, str]]:
    """Build the (owner, token, spender, label) approval matrix for a live gate run.

    - speculator demo_wallet → swapRouter: USDC + each mTOKEN (buys USDC→mTOKEN, sells mTOKEN→USDC)
    - each genuine holder  → swapRouter: USDC (genuine_holder_buy does USDC→mTOKEN)
    - ARB_KEY4             → arbitragePrimitive: USDC (arbCloseGap's arbMint leg pulls USDC)
    """
    usdc = manifest["mockUsdc"]
    router = manifest["arbSwapRouter"]
    arbprim = manifest["arbitragePrimitive"]
    arb_key4 = manifest["arbKey4"]

    approvals: list[tuple[str, str, str, str]] = [(demo_wallet, usdc, router, "demo_wallet→router USDC")]
    for name, vault in zip(("claude", "gpt", "gemini"), vault_addresses, strict=False):
        approvals.append((demo_wallet, vault, router, f"demo_wallet→router m{name}"))
    for h in holder_addresses:
        approvals.append((h, usdc, router, f"holder {h[:8]}→router USDC"))
    approvals.append((arb_key4, usdc, arbprim, "ARB_KEY4→arbPrimitive USDC"))
    return approvals

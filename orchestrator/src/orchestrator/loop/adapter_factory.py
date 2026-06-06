"""
orchestrator.loop.adapter_factory — Single PERPS_VENUE switch point (PERPS-04 / D-01 / D-04).

This module is the ONLY place in the orchestrator that names both MockPerps and GMXAdapter.
All other modules import the IPerpsAdapter contract interface only — they are venue-blind.

Venue selection is driven by the PERPS_VENUE environment variable at session start:
  "mock" → MockPerpsAdapter contract instance (Phase 0–2; live demo)
  "gmx"  → GMXAdapter contract instance (Phase 3+; real-GMX fork test only for Phase 3)

D-04 swap rule: switching venues = changing PERPS_VENUE and restarting. No governance
attack surface from a hot-swappable per-vault setter (D-03 restart-flip mechanism).

PERPS-04 / D-16 INTRACTABLE note: the GMXAdapter is READ-SIDE ONLY in Phase 3
(positionValueUSDC + getOpenPositionKeys). openLong/openShort/closePosition are
NotImplemented on the GMXAdapter until Phase 6 validates write-side encoding.

Caller convention (D-01): the caller reads os.environ["PERPS_VENUE"] and passes the
value here as `venue`. The factory is kept env-free so unit tests can inject any
venue string without patching the environment.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def build_perps_adapter(
    web3: Any,
    *,
    venue: str,
    mock_perps_address: str | None = None,
    gmx_adapter_address: str | None = None,
    mock_perps_abi: Any = None,
    gmx_adapter_abi: Any = None,
) -> Any:
    """Build and return the appropriate IPerpsAdapter contract instance.

    This is the single PERPS_VENUE switch point (PERPS-04). All callers (loop driver,
    settlement keeper, integration tests) call this factory instead of hard-coding
    venue-specific imports.

    Args:
        web3:                Web3 / AsyncWeb3 instance connected to the target chain.
        venue:               Venue identifier: "mock" or "gmx".
        mock_perps_address:  Deployed MockPerps contract address (required when venue="mock").
        gmx_adapter_address: Deployed GMXAdapter contract address (required when venue="gmx").
        mock_perps_abi:      MockPerps contract ABI (list of dicts; [] is valid for address-only use).
        gmx_adapter_abi:     GMXAdapter contract ABI (list of dicts; [] is valid for address-only use).

    Returns:
        A web3.py Contract instance implementing the IPerpsAdapter interface shape.
        The caller uses this object for read calls (getOpenPositionKeys, positionValueUSDC,
        pendingOrders) and event decoding (OrderCreated, OrderExecuted).

    Raises:
        ValueError: Unknown venue value — only "mock" and "gmx" are accepted.
        ValueError: Required address is None for the selected venue (misconfig fails loud).
    """
    if venue == "mock":
        if mock_perps_address is None:
            raise ValueError(
                "PERPS_VENUE=mock requires mock_perps_address+abi — "
                "set MOCK_PERPS_ADDRESS in your environment or pass mock_perps_address directly"
            )
        abi = mock_perps_abi if mock_perps_abi is not None else []
        contract = web3.eth.contract(address=mock_perps_address, abi=abi)
        logger.info(
            "adapter_factory: built MockPerps adapter at %s (PERPS_VENUE=mock)",
            mock_perps_address,
        )
        return contract

    if venue == "gmx":
        if gmx_adapter_address is None:
            raise ValueError(
                "PERPS_VENUE=gmx requires gmx_adapter_address+abi — "
                "set GMX_ADAPTER_ADDRESS in your environment or pass gmx_adapter_address directly"
            )
        abi = gmx_adapter_abi if gmx_adapter_abi is not None else []
        contract = web3.eth.contract(address=gmx_adapter_address, abi=abi)
        logger.info(
            "adapter_factory: built GMXAdapter (read-side, D-16 INTRACTABLE) at %s (PERPS_VENUE=gmx)",
            gmx_adapter_address,
        )
        return contract

    raise ValueError(
        f"unknown PERPS_VENUE: {venue!r} — accepted values are 'mock' and 'gmx'. "
        "Check PERPS_VENUE in your environment."
    )

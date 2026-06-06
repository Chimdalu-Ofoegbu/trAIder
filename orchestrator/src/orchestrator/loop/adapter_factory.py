"""
orchestrator.loop.adapter_factory — Single PERPS_VENUE switch point (PERPS-04 / D-01 / D-04).

This module is the ONLY place in the orchestrator that names both MockPerps and GMXAdapter.
All other modules import the IPerpsAdapter contract interface only — they are venue-blind.

Venue selection is driven by the PERPS_VENUE environment variable at session start:
  "mock" → MockPerpsAdapter contract instance (Phase 0–2; live demo)
  "gmx"  → GMXAdapter contract instance (Phase 3+; real-GMX fork test only for Phase 3)

D-04 swap rule: switching venues = changing PERPS_VENUE and redeploying. No governance
attack surface from a hot-swappable per-vault setter.

STUB in Wave 0 — raises NotImplementedError.
Wave 1 (03-02) implements:
  - "mock" path: instantiate MockPerps contract from address + ABI
  - "gmx" path: instantiate GMXAdapter from address + ABI (read-side only, D-16 INTRACTABLE)
  - Unknown venue: raise ValueError with clear message

Pattern reference: 03-PATTERNS.md "adapter_factory.py" section.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def build_perps_adapter(
    web3: object,
    *,
    venue: str,
    mock_perps_address: str | None = None,
    gmx_adapter_address: str | None = None,
    mock_perps_abi: object = None,
    gmx_adapter_abi: object = None,
) -> object:
    """Build and return the appropriate IPerpsAdapter contract instance.

    This is the single PERPS_VENUE switch point (PERPS-04). All callers (loop driver,
    settlement keeper, integration tests) call this factory instead of hard-coding
    venue-specific imports.

    Args:
        web3:                AsyncWeb3 instance connected to the target chain.
        venue:               Venue identifier: "mock" or "gmx".
        mock_perps_address:  Deployed MockPerps contract address (required when venue="mock").
        gmx_adapter_address: Deployed GMXAdapter contract address (required when venue="gmx").
        mock_perps_abi:      MockPerps contract ABI (list of dicts or None → load from file).
        gmx_adapter_abi:     GMXAdapter contract ABI (list of dicts or None → load from file).

    Returns:
        A web3.py Contract instance implementing the IPerpsAdapter interface shape.

    Raises:
        NotImplementedError: Wave 0 stub — implemented in Wave 1 (03-02).
        ValueError: Unknown venue value (Wave 1).
        ValueError: Required address is None for the selected venue (Wave 1).
    """
    raise NotImplementedError("build_perps_adapter: implemented in Wave 1 (03-02)")

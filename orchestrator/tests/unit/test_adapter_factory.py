"""
orchestrator.tests.unit.test_adapter_factory — Unit tests for build_perps_adapter (PERPS-04).

STUB — Wave 0 scaffold. All tests are skipped pending Wave 1 (03-02).

Wave 1 will implement tests covering:
  1. venue="mock" + valid address → returns MockPerps contract instance.
  2. venue="gmx" + valid address → returns GMXAdapter contract instance.
  3. venue="mock" + address=None → raises ValueError.
  4. venue="gmx" + address=None → raises ValueError.
  5. venue="unknown" → raises ValueError with clear message.
"""

import pytest


def test_adapter_factory_mock_vs_gmx() -> None:
    """build_perps_adapter routes "mock" and "gmx" to the correct contract instances."""
    pytest.skip("Wave 1: 03-02")

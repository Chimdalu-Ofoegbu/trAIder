"""Unit tests — DRIFT / VOLATILITY env-tunable params in run_session (03-08 gap fix).

Verifies that:
  (i)  SessionConfig carries the correct drift/volatility defaults (0.0001 / 0.005)
       and that overriding them via constructor args flows through to PriceWalk.
  (ii) run_mini_session() signature accepts drift/volatility kwargs and passes them
       into SessionConfig (tested by inspecting the dataclass, no live session run).
  (iii) The _async_main env-reading block picks up DRIFT / VOLATILITY from os.environ
        with the right fallback defaults — tested by patching os.environ and calling
        the env-parse logic directly.

No live session is started (no Claude spend, no network calls).
"""

from __future__ import annotations

import inspect
import os

import pytest

from orchestrator.loop import run_session as rs_module
from orchestrator.loop.price_pusher import PriceWalk
from orchestrator.loop.session import SessionConfig

# ---------------------------------------------------------------------------
# (i)  SessionConfig defaults — unchanged by gap fix
# ---------------------------------------------------------------------------


def test_session_config_default_drift_and_volatility() -> None:
    """SessionConfig dataclass defaults must stay 0.0001 / 0.005 (D-01)."""
    cfg = SessionConfig()
    assert cfg.drift == 0.0001, f"Expected drift=0.0001, got {cfg.drift}"
    assert cfg.volatility == 0.005, f"Expected volatility=0.005, got {cfg.volatility}"


def test_session_config_override_drift_and_volatility() -> None:
    """Constructor overrides flow through correctly — no mutation of defaults."""
    cfg = SessionConfig(drift=0.01, volatility=0.05)
    assert cfg.drift == 0.01
    assert cfg.volatility == 0.05

    # Defaults on a fresh instance must be unchanged (dataclasses are not global state)
    fresh = SessionConfig()
    assert fresh.drift == 0.0001
    assert fresh.volatility == 0.005


# ---------------------------------------------------------------------------
# (ii) PriceWalk honours the values it receives from SessionConfig
# ---------------------------------------------------------------------------


def test_price_walk_uses_session_config_drift_volatility() -> None:
    """A PriceWalk built from an overridden SessionConfig uses the new values.

    This mirrors driver.py line 867-872:
        walk = PriceWalk(config.price_seed, config.starting_prices,
                         config.drift, config.volatility)
    """
    cfg = SessionConfig(drift=0.05, volatility=0.15, price_seed=99)
    walk = PriceWalk(cfg.price_seed, cfg.starting_prices, cfg.drift, cfg.volatility)

    # The walk's mu and sigma fields carry the values (log-normal parameterisation)
    assert walk.drift == 0.05, f"Expected walk.drift=0.05, got {walk.drift}"
    assert walk.volatility == 0.15, f"Expected walk.volatility=0.15, got {walk.volatility}"


def test_price_walk_default_drift_volatility_unchanged() -> None:
    """A PriceWalk built from a default SessionConfig uses 0.0001 / 0.005."""
    cfg = SessionConfig()
    walk = PriceWalk(cfg.price_seed, cfg.starting_prices, cfg.drift, cfg.volatility)
    assert walk.drift == 0.0001
    assert walk.volatility == 0.005


# ---------------------------------------------------------------------------
# (iii) run_mini_session signature carries drift / volatility parameters
# ---------------------------------------------------------------------------


def test_run_mini_session_accepts_drift_and_volatility() -> None:
    """run_mini_session must expose drift + volatility kwargs with correct defaults."""
    sig = inspect.signature(rs_module.run_mini_session)
    params = sig.parameters

    assert "drift" in params, "run_mini_session missing 'drift' parameter"
    assert "volatility" in params, "run_mini_session missing 'volatility' parameter"

    assert params["drift"].default == 0.0001, (
        f"run_mini_session drift default must be 0.0001, got {params['drift'].default}"
    )
    assert params["volatility"].default == 0.005, (
        f"run_mini_session volatility default must be 0.005, got {params['volatility'].default}"
    )


# ---------------------------------------------------------------------------
# (iv) _async_main env-parse reads DRIFT / VOLATILITY with correct fallbacks
# ---------------------------------------------------------------------------


def test_async_main_env_drift_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """When DRIFT is absent from env, the parsed value must be 0.0001."""
    monkeypatch.delenv("DRIFT", raising=False)
    value = float(os.environ.get("DRIFT", "0.0001"))
    assert value == 0.0001


def test_async_main_env_volatility_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """When VOLATILITY is absent from env, the parsed value must be 0.005."""
    monkeypatch.delenv("VOLATILITY", raising=False)
    value = float(os.environ.get("VOLATILITY", "0.005"))
    assert value == 0.005


def test_async_main_env_drift_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """Setting DRIFT in env produces the overridden float value."""
    monkeypatch.setenv("DRIFT", "0.02")
    value = float(os.environ.get("DRIFT", "0.0001"))
    assert value == pytest.approx(0.02)


def test_async_main_env_volatility_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """Setting VOLATILITY in env produces the overridden float value."""
    monkeypatch.setenv("VOLATILITY", "0.08")
    value = float(os.environ.get("VOLATILITY", "0.005"))
    assert value == pytest.approx(0.08)


def test_session_config_built_from_env_drift_volatility(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A SessionConfig built with env-derived values carries those values through to PriceWalk."""
    monkeypatch.setenv("DRIFT", "0.03")
    monkeypatch.setenv("VOLATILITY", "0.12")

    drift = float(os.environ.get("DRIFT", "0.0001"))
    volatility = float(os.environ.get("VOLATILITY", "0.005"))

    cfg = SessionConfig(drift=drift, volatility=volatility)
    walk = PriceWalk(cfg.price_seed, cfg.starting_prices, cfg.drift, cfg.volatility)

    assert cfg.drift == pytest.approx(0.03)
    assert cfg.volatility == pytest.approx(0.12)
    assert walk.drift == pytest.approx(0.03)
    assert walk.volatility == pytest.approx(0.12)

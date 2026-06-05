"""Session configuration dataclass for the trading loop (D-01/D-11/D-13).

SessionConfig holds all per-session parameters that determine the behaviour of the
seeded price walk, the cadence of the trading loop, and the truthful time-remaining
countdown shown to the model each cycle.

NO web3 / provider SDK imports here — this is pure config.
"""

from __future__ import annotations

import dataclasses
import uuid


@dataclasses.dataclass
class SessionConfig:
    """Per-session configuration for the trAIder trading loop.

    Fields
    ------
    session_id : str
        UUIDv4 string.  Logged at session start and used as the DB FK anchor.
    session_key : str
        Human-readable short key (``sess-<8hex>``).  Used in log lines.
    session_duration_seconds : int
        D-11 — ACTUAL run length in seconds.  For acceptance tests ≈ 900–1200 s;
        demo ≈ 3–4 h (10 800–14 400 s).  NOT 72 h unless that really is the run.
    cadence_seconds : float
        ORCH-02 — interval between trading cycles.  Default 60 s; acceptance tests
        may lower to 1 s.
    execution_delay_cycles : int
        D-13 — number of cycles (blocks) the mock keeper waits before executing a
        submitted order.  Default 1.  Must be ≥ 1 in integration / restart-safety
        tests (D-14 guard in conftest.py).
    price_seed : int
        D-01 — PRNG seed for the PriceWalk.  Logged prominently at session start so
        any session is fully replayable from this value alone.
    drift : float
        D-01 — per-cycle log-normal drift fraction for the price walk.  Default 0.0001.
    volatility : float
        D-01 — per-cycle log-normal standard-deviation fraction.  Default 0.005.
    starting_prices : dict[str, float]
        D-01 — starting mark prices in USD for ETH, BTC, SOL.
    paused_poll_interval_seconds : float
        D-16 — back-off probe interval (seconds) while the model is in the paused
        state.  The status-probe coroutine sleeps this long between liveness checks.
    """

    session_id: str = dataclasses.field(default_factory=lambda: str(uuid.uuid4()))
    session_key: str = dataclasses.field(default_factory=lambda: f"sess-{uuid.uuid4().hex[:8]}")
    session_duration_seconds: int = 900  # D-11: ACTUAL run length; NOT 72h unless real
    cadence_seconds: float = 60.0  # ORCH-02 default; lower for acceptance tests
    execution_delay_cycles: int = 1  # D-13 default; >=1 required for restart-safety
    price_seed: int = 42  # D-01: log at session start for full replay
    drift: float = 0.0001  # per-cycle drift (log-normal mean)
    volatility: float = 0.005  # per-cycle std dev
    starting_prices: dict = dataclasses.field(
        default_factory=lambda: {"ETH": 3000.0, "BTC": 60000.0, "SOL": 150.0}
    )
    paused_poll_interval_seconds: float = 180.0  # D-16 back-off probe interval


def format_time_remaining(elapsed_seconds: float, total_seconds: int) -> str:
    """Return a truthful human-readable countdown string (D-11).

    Remaining time = max(0, total_seconds - elapsed_seconds), rendered as
    ``Hh Mm Ss``.  This is used for the ``{{time_remaining}}`` Jinja2
    placeholder in system.md and is journaled every cycle so the verifier
    can replay the exact prompt.

    NEVER emits "72 hours" unless ``total_seconds == 259200``.  The value is
    derived solely from the config — no fictional session duration is injected.

    Parameters
    ----------
    elapsed_seconds:
        Seconds elapsed since session start.
    total_seconds:
        ``SessionConfig.session_duration_seconds``.

    Returns
    -------
    str
        e.g. ``"0h 15m 0s"`` for a 900-second session at t=0, or
        ``"0h 0m 0s"`` once the session has ended.
    """
    remaining = max(0, int(total_seconds - elapsed_seconds))
    h, rem = divmod(remaining, 3600)
    m, s = divmod(rem, 60)
    return f"{h}h {m}m {s}s"

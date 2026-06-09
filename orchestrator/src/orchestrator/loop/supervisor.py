"""
Multi-model supervisor — wraps 3 independent asyncio.Tasks with per-task
exception boundary, auto-restart with exponential backoff, and Cut 2A flag.

D-12 design:
  - Each model loop is an independent supervised asyncio.Task.
  - A crash in one model NEVER propagates to siblings or the supervisor.
  - Auto-restart with exponential backoff (initial=5s, multiplier=2.0, max=120s).
  - Past threshold of consecutive crashes → ModelStatus.AUTO_PAUSED + CRITICAL alert.
  - Cut 2A = runtime config flag (ModelConfig.enabled=False or ModelStatus.FLAG_DISABLED)
    that OVERRIDES auto-restart; flag-disabled stays down permanently (D-12).
  - reconcile_pending_orders runs BEFORE a restarted loop resumes (D-12 safety-critical).
  - run_supervisor uses asyncio.gather(return_exceptions=True) — supervisor never crashes.

Cut 2A POLICY (when to cut a model) is deferred to Phase 6.
This module provides ONLY the flag + override + observability mechanics.

Reuses:
  - driver.run_session() (single-vault loop body) as the per-Task body
  - ARCH-X in-flight gate + reconcile_pending_orders from Phase 3
  - NonceManager from nonce_manager.py (shared EOA, one nonce lock)
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from orchestrator.alerts.sink import AlertSeverity

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# ModelStatus — per-model observability (D-12)
# ---------------------------------------------------------------------------


class ModelStatus(str, Enum):
    """Runtime status of a supervised model task.

    Values are plain strings for clean JSON serialization in status endpoints.
    """

    RUNNING = "running"
    AUTO_PAUSED = "auto_paused"  # crash-escalation threshold exceeded (D-12)
    FLAG_DISABLED = "flag_disabled"  # Cut 2A runtime flag: operator disabled (D-12)


# ---------------------------------------------------------------------------
# ModelConfig / ModelState — configuration + mutable runtime state
# ---------------------------------------------------------------------------


@dataclass
class ModelConfig:
    """Static configuration for one supervised model loop.

    Attributes:
        name:          Model identity: "claude" | "gpt" | "gemini".
        vault_address: The mTOKEN vault address this model trades against.
        enabled:       Cut 2A flag — False = FLAG_DISABLED; never starts/restarts.
    """

    name: str
    vault_address: str
    enabled: bool = True


@dataclass
class ModelState:
    """Mutable runtime state for one supervised model loop.

    Mutated by run_model_task. Readable by the observability layer.

    Attributes:
        status:                         Current ModelStatus enum value.
        restart_count:                  Total number of restarts since supervisor start.
        last_crash_time:                monotonic time of the most-recent crash (0.0 = never).
        consecutive_crashes_in_window:  Crash counter within the sliding window.
    """

    status: ModelStatus = ModelStatus.RUNNING
    restart_count: int = 0
    last_crash_time: float = 0.0
    consecutive_crashes_in_window: int = 0
    _backoff: float = field(default=0.0, repr=False)  # internal; reset per-task


# ---------------------------------------------------------------------------
# run_model_task — supervised single-model loop body (D-12)
# ---------------------------------------------------------------------------


async def run_model_task(  # noqa: PLR0913 (many params by design)
    config: ModelConfig,
    state: ModelState,
    shared_deps: dict[str, Any],
    max_consecutive_crashes: int = 5,
    backoff_initial: float = 5.0,
    backoff_multiplier: float = 2.0,
    backoff_max: float = 120.0,
    crash_window_seconds: float = 300.0,
) -> None:
    """Supervised task body for one model loop.

    Runs the driver body in an infinite loop, restarting on failure with
    exponential backoff. Terminates permanently if:
      - status is FLAG_DISABLED (Cut 2A flag) — never starts at all.
      - status becomes AUTO_PAUSED (crash threshold exceeded).

    D-12 safety-critical ordering on restart:
      1. Detect crash → increment counters → send ERROR alert.
      2. Check threshold → if exceeded: AUTO_PAUSED + CRITICAL alert + return.
      3. AWAIT reconcile_pending_orders BEFORE backoff sleep + restart.
      4. Backoff sleep.
      5. Restart body (back to top of loop).

    Args:
        config:                  Model identity + vault + Cut 2A flag.
        state:                   Mutable runtime state (shared with observability layer).
        shared_deps:             Dict containing:
                                   "driver_run_session": async callable(**kwargs) — the loop body.
                                   "reconcile_fn": async callable(**kwargs) — reconcile before restart.
                                   "alert_fn": async callable(str, severity) — operator alert sink.
        max_consecutive_crashes: Crash count within window before AUTO_PAUSED.
        backoff_initial:         First backoff delay in seconds.
        backoff_multiplier:      Exponential growth factor per crash.
        backoff_max:             Cap on backoff delay in seconds.
        crash_window_seconds:    Sliding window for consecutive crash counting.
    """
    # Cut 2A: if flag-disabled, never start
    if not config.enabled or state.status == ModelStatus.FLAG_DISABLED:
        state.status = ModelStatus.FLAG_DISABLED
        logger.info(
            "[%s] flag_disabled — not starting (Cut 2A)",
            config.name,
        )
        return

    driver_run_session = shared_deps["driver_run_session"]
    reconcile_fn = shared_deps["reconcile_fn"]
    alert_fn = shared_deps["alert_fn"]

    backoff = backoff_initial

    while True:
        # Check Cut 2A / AUTO_PAUSED at the top of each loop iteration
        if state.status == ModelStatus.FLAG_DISABLED:
            logger.info("[%s] flag_disabled — stopping loop", config.name)
            return
        if state.status == ModelStatus.AUTO_PAUSED:
            logger.info("[%s] auto_paused — not restarting", config.name)
            return

        try:
            state.status = ModelStatus.RUNNING
            await driver_run_session(
                vault_address=config.vault_address,
                provider=config.name,
                **{
                    k: v
                    for k, v in shared_deps.items()
                    if k not in ("driver_run_session", "reconcile_fn", "alert_fn")
                },
            )
            # Loop body returned normally — task is done
            return

        except asyncio.CancelledError:
            # Propagate cancellation — do NOT restart on explicit cancel
            raise

        except Exception as exc:
            now = time.monotonic()

            # Reset crash window if enough time has elapsed since last crash
            if now - state.last_crash_time > crash_window_seconds:
                state.consecutive_crashes_in_window = 0

            state.consecutive_crashes_in_window += 1
            state.last_crash_time = now
            state.restart_count += 1

            logger.error(
                "[%s] crashed (restart #%d, consecutive=%d): %s",
                config.name,
                state.restart_count,
                state.consecutive_crashes_in_window,
                exc,
            )
            await alert_fn(
                f"Model {config.name} crashed (restart #{state.restart_count}): {exc}",
                AlertSeverity.WARNING,
            )

            # Check auto-pause threshold
            if state.consecutive_crashes_in_window >= max_consecutive_crashes:
                state.status = ModelStatus.AUTO_PAUSED
                logger.critical(
                    "[%s] AUTO_PAUSED after %d consecutive crashes in %.0fs window",
                    config.name,
                    state.consecutive_crashes_in_window,
                    crash_window_seconds,
                )
                await alert_fn(
                    f"Model {config.name} AUTO_PAUSED after "
                    f"{max_consecutive_crashes} crashes in {crash_window_seconds}s window — "
                    f"operator intervention required (CRITICAL D-12)",
                    AlertSeverity.CRITICAL,
                )
                return

            # D-12 SAFETY-CRITICAL: reconcile BEFORE backoff sleep + restart
            # Ensures no double-submit or state drift on restart (T-04-05-03)
            try:
                await reconcile_fn(
                    vault_address=config.vault_address,
                    **{
                        k: v
                        for k, v in shared_deps.items()
                        if k not in ("driver_run_session", "reconcile_fn", "alert_fn")
                    },
                )
            except Exception as rec_exc:  # noqa: BLE001
                # Reconcile failure is logged but MUST NOT prevent restart
                logger.warning(
                    "[%s] reconcile_fn failed (non-fatal, will restart anyway): %s",
                    config.name,
                    rec_exc,
                )

            await asyncio.sleep(min(backoff, backoff_max))
            backoff = min(backoff * backoff_multiplier, backoff_max)


# ---------------------------------------------------------------------------
# run_supervisor — top-level 3-Task supervisor (D-12)
# ---------------------------------------------------------------------------


async def run_supervisor(
    model_configs: list[ModelConfig],
    shared_deps: dict[str, Any],
    max_consecutive_crashes: int = 5,
    backoff_initial: float = 5.0,
    backoff_multiplier: float = 2.0,
    backoff_max: float = 120.0,
    crash_window_seconds: float = 300.0,
) -> None:
    """Top-level supervisor: launch 3 Tasks, never propagate child crashes.

    Creates one asyncio.Task per enabled ModelConfig. Uses
    asyncio.gather(return_exceptions=True) so the supervisor NEVER crashes due
    to a child failure — it is the most-robust component (D-12).

    Flag-disabled models (Cut 2A) are simply not started.

    Args:
        model_configs:           List of ModelConfig (typically 3: claude, gpt, gemini).
        shared_deps:             Shared dependencies passed to each run_model_task.
        max_consecutive_crashes: Passed through to each task.
        backoff_initial:         Initial backoff delay (seconds).
        backoff_multiplier:      Exponential factor per crash.
        backoff_max:             Cap on backoff delay (seconds).
        crash_window_seconds:    Sliding window for consecutive crash counting.

    Returns:
        None. Child exceptions are swallowed via return_exceptions=True.
    """
    states = {cfg.name: ModelState() for cfg in model_configs}

    # Apply Cut 2A: mark disabled configs as FLAG_DISABLED immediately
    for cfg in model_configs:
        if not cfg.enabled:
            states[cfg.name].status = ModelStatus.FLAG_DISABLED

    tasks = [
        asyncio.create_task(
            run_model_task(
                config=cfg,
                state=states[cfg.name],
                shared_deps=shared_deps,
                max_consecutive_crashes=max_consecutive_crashes,
                backoff_initial=backoff_initial,
                backoff_multiplier=backoff_multiplier,
                backoff_max=backoff_max,
                crash_window_seconds=crash_window_seconds,
            ),
            name=f"trader-{cfg.name}",
        )
        for cfg in model_configs
        if cfg.enabled  # Skip flag-disabled models entirely
    ]

    if not tasks:
        logger.warning("run_supervisor: no enabled model tasks to supervise — all Cut 2A disabled")
        return

    # Supervisor never propagates child crashes (return_exceptions=True)
    await asyncio.gather(*tasks, return_exceptions=True)

    # Log final status for each model
    for name, state in states.items():
        logger.info(
            "run_supervisor: model=%s final_status=%s restart_count=%d",
            name,
            state.status.value,
            state.restart_count,
        )

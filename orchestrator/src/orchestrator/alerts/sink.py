"""
orchestrator.alerts.sink — Alert dispatch with severity tiering (ORCH-05).

AlertSeverity is FULLY IMPLEMENTED — it is a tiny enum consumed widely across the
orchestrator (loop driver, keeper_monitor, journal publisher, failure_tracker).
Having it fully implemented in Wave 0 avoids import failures in later waves.

send_alert is a STUB in Wave 0. Wave 2 (03-06) implements:
  - Telegram bot notifications (primary channel)
  - Structured log fallback (always active regardless of Telegram config)

Severity tiers:
  INFO     : Routine operational events (session start, trade executed, cycle heartbeat).
  WARNING  : Degraded-but-continuing states (stale price fallback, retry triggered,
             grace period active, single provider down).
  CRITICAL : Session-stopping or security-relevant events (both IPFS providers down,
             ecrecover gate failure, circuit breaker latched, DB write failure after retries).

Pattern reference: 03-PATTERNS.md "alerts/sink.py" section.
Analog: orchestrator.loop.failure_tracker severity-tier design.
"""

from __future__ import annotations

import logging
from enum import Enum

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# AlertSeverity — fully implemented (Wave 0)
# ---------------------------------------------------------------------------


class AlertSeverity(Enum):
    """Severity tier for operational alerts dispatched via send_alert.

    Tiers are intentionally coarse — three levels cover all trAIder alert needs
    without the maintenance overhead of a five-tier system.

    Values are plain strings so they serialize cleanly to JSON logs and Telegram
    messages without additional mapping.
    """

    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


# ---------------------------------------------------------------------------
# send_alert — Wave 0 stub
# ---------------------------------------------------------------------------


async def send_alert(
    message: str,
    severity: AlertSeverity,
    *,
    context: dict | None = None,
    telegram_bot_token: str | None = None,
    telegram_chat_id: str | None = None,
) -> None:
    """Dispatch an alert to the configured channel(s).

    Wave 0 stub — raises NotImplementedError. Implemented in Wave 2 (03-06).

    In the full implementation:
      - CRITICAL alerts always log at ERROR level regardless of Telegram availability.
      - WARNING alerts log at WARNING level.
      - INFO alerts log at INFO level.
      - If telegram_bot_token and telegram_chat_id are set, all severity tiers
        POST to Telegram (CRITICAL and WARNING include context dict if provided).
      - Telegram failure is swallowed and logged at WARNING — it must not crash the loop.

    Args:
        message:            Human-readable alert message.
        severity:           AlertSeverity tier (INFO / WARNING / CRITICAL).
        context:            Optional dict of structured context fields (trade_hash,
                            order_key, vault_address, etc.) to include in the alert body.
        telegram_bot_token: Telegram bot token (read from env; never logged verbatim).
        telegram_chat_id:   Telegram chat ID to post to.

    Raises:
        NotImplementedError: Wave 0 stub — implemented in 03-06.
    """
    raise NotImplementedError("send_alert: implemented in Wave 2 (03-06)")

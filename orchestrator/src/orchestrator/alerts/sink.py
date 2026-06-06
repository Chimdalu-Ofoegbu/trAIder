"""
orchestrator.alerts.sink — Pluggable operator alert sink (D-15).

Two-audience rule: operator alerts live HERE; viewer status lives on the viewer WS channel.
These two paths must NEVER be coupled. This module has zero viewer-channel imports.

Always-on: structured-log sink writes to the Python logging subsystem at the appropriate
level for every call — INFO/WARNING/CRITICAL maps to logging.INFO/WARNING/ERROR.

Config-gated: Telegram bot webhook is triggered at WARNING and CRITICAL only, when both
TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are provided. A Telegram delivery failure is
swallowed (logged as WARNING) so it can NEVER crash the trading loop — the log sink is
always the source of truth.

Event taxonomy (D-15):
  WARNING  : 1A latency breach (createOrder >30s); model paused after 3 API failures;
             journal pin failure (Pinata or Filebase); malformed-response streak.
  CRITICAL : Both IPFS providers failed; circuit breaker latched; DB write failed after
             retries; ecrecover gate failure on recordJournal.

Bot token / webhook URL are secrets: read from env by callers, passed as parameters,
NEVER committed or logged verbatim (T-03-22).

Pattern reference: 03-PATTERNS.md "alerts/sink.py" section.
Analog: orchestrator.loop.failure_tracker severity-tier design.
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# AlertSeverity — fully implemented (Wave 0 baseline, extended in Wave 2)
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
# _log_level_for — severity to Python logging level
# ---------------------------------------------------------------------------

_SEVERITY_TO_LOG_LEVEL = {
    AlertSeverity.INFO: logging.INFO,
    AlertSeverity.WARNING: logging.WARNING,
    AlertSeverity.CRITICAL: logging.ERROR,  # CRITICAL maps to logging.ERROR (highest available)
}


# ---------------------------------------------------------------------------
# send_alert — structured-log always + config-gated Telegram webhook (D-15)
# ---------------------------------------------------------------------------


async def send_alert(
    message: str,
    severity: AlertSeverity,
    *,
    context: dict[str, Any] | None = None,
    telegram_bot_token: str | None = None,
    telegram_chat_id: str | None = None,
) -> None:
    """Dispatch an alert to the configured channel(s).

    ALWAYS logs to the Python structured-log sink at the appropriate level.
    At WARNING/CRITICAL severity, also POSTs to Telegram if both token and chat_id
    are provided. Telegram failure is swallowed and logged as WARNING — it must
    never crash the trading loop (D-15 "logs always" guarantee).

    D-15 two-audience rule: this function has ZERO coupling to the viewer WS
    channel. Ops alerts and viewer status are two separate paths.

    Args:
        message:            Human-readable alert message.
        severity:           AlertSeverity tier (INFO / WARNING / CRITICAL).
        context:            Optional structured context fields (trade_hash, order_key,
                            vault_address, etc.). Included in the log record and, at
                            WARNING/CRITICAL, in the Telegram message body.
        telegram_bot_token: Telegram bot API token (read from env by caller; never
                            logged verbatim). None = Telegram disabled.
        telegram_chat_id:   Telegram chat ID to post to. None = Telegram disabled.

    Returns:
        None. Never raises (Telegram failure is swallowed).
    """
    # ── 1. Structured-log sink (always-on) ──────────────────────────────────
    level = _SEVERITY_TO_LOG_LEVEL.get(severity, logging.WARNING)
    ctx_str = f" ctx={context}" if context else ""
    logger.log(level, "[%s] %s%s", severity.value, message, ctx_str)

    # ── 2. Telegram webhook (config-gated: WARNING / CRITICAL only) ──────────
    if severity not in (AlertSeverity.WARNING, AlertSeverity.CRITICAL):
        return  # INFO: log only, no Telegram (D-15)

    if not telegram_bot_token or not telegram_chat_id:
        return  # Telegram not configured — silent no-op

    telegram_url = f"https://api.telegram.org/bot{telegram_bot_token}/sendMessage"
    text_body = f"[{severity.value}] {message}"
    if context:
        ctx_lines = "\n".join(f"  {k}: {v}" for k, v in context.items())
        text_body += f"\n{ctx_lines}"

    try:
        async with httpx.AsyncClient() as client:
            await client.post(
                telegram_url,
                json={"chat_id": telegram_chat_id, "text": text_body},
                timeout=10,
            )
    except Exception as exc:  # noqa: BLE001
        # Non-blocking per D-15: Telegram is best-effort; the log sink is the source of truth.
        logger.warning("alert sink: Telegram delivery failed (non-fatal): %s", exc)

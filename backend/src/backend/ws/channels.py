"""
backend.ws.channels — Redis channel topology + event-to-channel routing (D-23).

Frozen channel topology (IFACE-04):
  ws/vault/{vaultAddress}: NavTick, TradeEvent, JournalEvent, ModelStatus
  ws/global: ArbOpp, SessionEvent

No event appears on more than one channel (D-23 hard constraint).
Test in test_ws_models.py asserts no double-routing.

Usage:
  from backend.ws.channels import channel_for, VAULT_CHANNEL, GLOBAL_CHANNEL

  ch = channel_for("NavTick", vault_address="0xABCD")
  # -> "ws/vault/0xABCD"

  ch = channel_for("ArbOpp")
  # -> "ws/global"
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Channel name templates
# ---------------------------------------------------------------------------

VAULT_CHANNEL = "ws/vault/{vault_address}"
"""Template for per-vault channels. Format: VAULT_CHANNEL.format(vault_address=addr)."""

GLOBAL_CHANNEL = "ws/global"
"""Global channel carrying cross-vault events (ArbOpp, SessionEvent)."""

# ---------------------------------------------------------------------------
# Event -> channel routing table (D-23)
# ---------------------------------------------------------------------------

# This is the frozen routing contract for IFACE-04.
# No event type may appear in both lists (test asserts this invariant).
EVENT_CHANNEL: dict[str, list[str]] = {
    "vault_events": [
        "NavTick",
        "TradeEvent",
        "JournalEvent",
        "ModelStatus",
    ],
    "global_events": [
        "ArbOpp",
        "SessionEvent",
    ],
}

# Build reverse lookup: eventType -> channel kind ("vault" | "global")
_VAULT_EVENTS: frozenset[str] = frozenset(EVENT_CHANNEL["vault_events"])
_GLOBAL_EVENTS: frozenset[str] = frozenset(EVENT_CHANNEL["global_events"])

# Compile-time assertion: no event type appears in both buckets
_overlap = _VAULT_EVENTS & _GLOBAL_EVENTS
if _overlap:
    raise RuntimeError(
        f"BUG: event types {_overlap} appear in both vault and global channel buckets. "
        "This violates D-23: no event on more than one channel."
    )


# ---------------------------------------------------------------------------
# channel_for — the public routing helper
# ---------------------------------------------------------------------------


def channel_for(event_type: str, vault_address: str | None = None) -> str:
    """
    Return the Redis channel name for a given event type.

    Args:
        event_type: The event model name (e.g. "NavTick", "ArbOpp").
        vault_address: Required for vault-scoped events (NavTick, TradeEvent,
                       JournalEvent, ModelStatus). Ignored for global events.

    Returns:
        The Redis channel string to publish/subscribe on.

    Raises:
        ValueError: If event_type is unknown.
        ValueError: If a vault-scoped event is requested without a vault_address.
    """
    if event_type in _VAULT_EVENTS:
        if not vault_address:
            raise ValueError(
                f"channel_for('{event_type}') requires vault_address — "
                f"'{event_type}' is a vault-scoped event (D-23)."
            )
        return VAULT_CHANNEL.format(vault_address=vault_address)

    if event_type in _GLOBAL_EVENTS:
        return GLOBAL_CHANNEL

    raise ValueError(
        f"Unknown event type '{event_type}'. "
        f"Known vault events: {sorted(_VAULT_EVENTS)}. "
        f"Known global events: {sorted(_GLOBAL_EVENTS)}."
    )

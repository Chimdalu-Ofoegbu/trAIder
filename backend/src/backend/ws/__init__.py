"""
backend.ws — WebSocket event models, standard envelope, and channel topology.

Source of truth for the WS/Redis integration seam (IFACE-04, D-23/D-24/D-25/D-26/D-27).
"""

from backend.ws.channels import EVENT_CHANNEL, GLOBAL_CHANNEL, VAULT_CHANNEL, channel_for
from backend.ws.models import (
    ArbOpp,
    CurrentState,
    Envelope,
    JournalEvent,
    JournalStateEnum,
    ModelStatus,
    ModelStatusEnum,
    NavTick,
    SessionEvent,
    TradeEvent,
)

__all__ = [
    # Channels
    "GLOBAL_CHANNEL",
    "VAULT_CHANNEL",
    "EVENT_CHANNEL",
    "channel_for",
    # Models
    "ArbOpp",
    "CurrentState",
    "Envelope",
    "JournalEvent",
    "JournalStateEnum",
    "ModelStatus",
    "ModelStatusEnum",
    "NavTick",
    "SessionEvent",
    "TradeEvent",
]

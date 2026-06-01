"""
Tests for WebSocket Pydantic models, standard envelope, and channel topology (IFACE-04).

These tests freeze the WS contract:
  - Each event model serializes to JSON and round-trips via model_validate
  - Envelope wraps any payload with seq/serverTs/eventType; chainTs+blockNumber nullable
  - Channel router maps each eventType to EXACTLY one channel (D-23):
      NavTick/TradeEvent/JournalEvent/ModelStatus -> ws/vault/{addr}
      ArbOpp/SessionEvent -> ws/global
  - No eventType maps to two channels
"""

import pytest

from backend.ws.channels import (
    EVENT_CHANNEL,
    GLOBAL_CHANNEL,
    VAULT_CHANNEL,
    channel_for,
)
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

# ---------------------------------------------------------------------------
# NavTick
# ---------------------------------------------------------------------------


class TestNavTick:
    def test_serialize_round_trip(self) -> None:
        tick = NavTick(
            vault_address="0xABCD",
            nav_per_token_1e18="1000000000000000000",
            total_assets="10000000000",
            block_number=42,
            amm_price="999500000000000000",
        )
        dumped = tick.model_dump()
        restored = NavTick.model_validate(dumped)
        assert restored == tick

    def test_json_round_trip(self) -> None:
        tick = NavTick(
            vault_address="0xABCD",
            nav_per_token_1e18="1000000000000000000",
            total_assets="10000000000",
            block_number=42,
        )
        json_str = tick.model_dump_json()
        restored = NavTick.model_validate_json(json_str)
        assert restored == tick

    def test_amm_price_optional(self) -> None:
        tick = NavTick(
            vault_address="0xABCD",
            nav_per_token_1e18="1000000000000000000",
            total_assets="10000000000",
            block_number=42,
        )
        assert tick.amm_price is None


# ---------------------------------------------------------------------------
# TradeEvent
# ---------------------------------------------------------------------------


class TestTradeEvent:
    def test_serialize_round_trip(self) -> None:
        event = TradeEvent(
            vault_address="0xVAULT",
            order_key="0xORDERKEY",
            action="open",
            market="ETH",
            side="long",
            size_usd="5000.00",
            leverage=2.0,
            tx_hash="0xTXHASH",
            block_number=100,
        )
        restored = TradeEvent.model_validate(event.model_dump())
        assert restored == event

    def test_json_round_trip(self) -> None:
        event = TradeEvent(
            vault_address="0xVAULT",
            order_key="0xORDERKEY",
            action="close",
            market="BTC",
            side="short",
            size_usd="3000.00",
            leverage=1.5,
            tx_hash="0xTXHASH2",
            block_number=200,
        )
        restored = TradeEvent.model_validate_json(event.model_dump_json())
        assert restored == event

    def test_valid_markets(self) -> None:
        for market in ("ETH", "BTC", "SOL"):
            e = TradeEvent(
                vault_address="0xV",
                order_key="0xK",
                action="open",
                market=market,
                side="long",
                size_usd="1000",
                leverage=1.0,
                tx_hash="0xH",
                block_number=1,
            )
            assert e.market == market

    def test_valid_actions(self) -> None:
        for action in ("open", "close", "adjust"):
            e = TradeEvent(
                vault_address="0xV",
                order_key="0xK",
                action=action,
                market="ETH",
                side="long",
                size_usd="1000",
                leverage=1.0,
                tx_hash="0xH",
                block_number=1,
            )
            assert e.action == action


# ---------------------------------------------------------------------------
# JournalEvent
# ---------------------------------------------------------------------------


class TestJournalEvent:
    def test_serialize_round_trip(self) -> None:
        event = JournalEvent(
            vault_address="0xVAULT",
            trade_hash="0xTRADE",
            pinata_cid="QmABC",
            web3_storage_cid="bafy123",
            journal_state=JournalStateEnum.pinned_primary,
        )
        restored = JournalEvent.model_validate(event.model_dump())
        assert restored == event

    def test_json_round_trip(self) -> None:
        event = JournalEvent(
            vault_address="0xV",
            trade_hash="0xT",
            journal_state=JournalStateEnum.pending_pin,
        )
        restored = JournalEvent.model_validate_json(event.model_dump_json())
        assert restored == event

    def test_cids_optional(self) -> None:
        event = JournalEvent(
            vault_address="0xV",
            trade_hash="0xT",
            journal_state=JournalStateEnum.pending_pin,
        )
        assert event.pinata_cid is None
        assert event.web3_storage_cid is None

    def test_all_journal_states(self) -> None:
        states = [
            "pending_pin",
            "pinned_primary",
            "pinned_backup",
            "signed",
            "submitted",
            "recorded",
            "failed",
        ]
        for state in states:
            e = JournalEvent(
                vault_address="0xV",
                trade_hash="0xT",
                journal_state=state,
            )
            assert e.journal_state == state


# ---------------------------------------------------------------------------
# ModelStatus
# ---------------------------------------------------------------------------


class TestModelStatus:
    def test_serialize_round_trip(self) -> None:
        event = ModelStatus(
            vault_address="0xVAULT",
            model="claude-opus-4-7",
            status=ModelStatusEnum.active,
            consecutive_failures=0,
        )
        restored = ModelStatus.model_validate(event.model_dump())
        assert restored == event

    def test_json_round_trip(self) -> None:
        event = ModelStatus(
            vault_address="0xV",
            model="gpt-5.1",
            status=ModelStatusEnum.malformed,
            consecutive_failures=3,
        )
        restored = ModelStatus.model_validate_json(event.model_dump_json())
        assert restored == event

    def test_all_statuses(self) -> None:
        for status in ("active", "paused", "malformed"):
            e = ModelStatus(
                vault_address="0xV",
                model="test",
                status=status,
                consecutive_failures=0,
            )
            assert e.status == status


# ---------------------------------------------------------------------------
# ArbOpp
# ---------------------------------------------------------------------------


class TestArbOpp:
    def test_serialize_round_trip(self) -> None:
        event = ArbOpp(
            vault_address="0xVAULT",
            nav_price="1000000000000000000",
            amm_price="1010000000000000000",
            gap_bps=100,
            direction="mint",
        )
        restored = ArbOpp.model_validate(event.model_dump())
        assert restored == event

    def test_json_round_trip(self) -> None:
        event = ArbOpp(
            vault_address="0xV",
            nav_price="990000000000000000",
            amm_price="1000000000000000000",
            gap_bps=101,
            direction="burn",
        )
        restored = ArbOpp.model_validate_json(event.model_dump_json())
        assert restored == event

    def test_directions(self) -> None:
        for direction in ("mint", "burn"):
            e = ArbOpp(
                vault_address="0xV",
                nav_price="1e18",
                amm_price="1e18",
                gap_bps=0,
                direction=direction,
            )
            assert e.direction == direction


# ---------------------------------------------------------------------------
# SessionEvent
# ---------------------------------------------------------------------------


class TestSessionEvent:
    def test_serialize_round_trip(self) -> None:
        event = SessionEvent(
            session_id="session-001",
            kind="started",
        )
        restored = SessionEvent.model_validate(event.model_dump())
        assert restored == event

    def test_json_round_trip(self) -> None:
        event = SessionEvent(
            session_id="session-002",
            kind="hour_milestone",
            hour=24,
        )
        restored = SessionEvent.model_validate_json(event.model_dump_json())
        assert restored == event

    def test_all_kinds(self) -> None:
        for kind in ("started", "hour_milestone", "settling", "settled"):
            e = SessionEvent(session_id="s", kind=kind)
            assert e.kind == kind

    def test_hour_optional(self) -> None:
        e = SessionEvent(session_id="s", kind="settled")
        assert e.hour is None


# ---------------------------------------------------------------------------
# CurrentState
# ---------------------------------------------------------------------------


class TestCurrentState:
    def _make_nav_tick(self) -> NavTick:
        return NavTick(
            vault_address="0xV",
            nav_per_token_1e18="1000000000000000000",
            total_assets="10000000",
            block_number=1,
        )

    def _make_trade_event(self) -> TradeEvent:
        return TradeEvent(
            vault_address="0xV",
            order_key="0xK",
            action="open",
            market="ETH",
            side="long",
            size_usd="1000",
            leverage=1.0,
            tx_hash="0xH",
            block_number=1,
        )

    def _make_model_status(self) -> ModelStatus:
        return ModelStatus(
            vault_address="0xV",
            model="claude-opus-4-7",
            status=ModelStatusEnum.active,
            consecutive_failures=0,
        )

    def test_serialize_round_trip(self) -> None:
        state = CurrentState(
            vault_address="0xVAULT",
            latest_nav=self._make_nav_tick(),
            recent_trades=[self._make_trade_event()],
            latest_journal=None,
            model_status=self._make_model_status(),
            seq=5,
        )
        restored = CurrentState.model_validate(state.model_dump())
        assert restored == state

    def test_json_round_trip(self) -> None:
        state = CurrentState(
            vault_address="0xVAULT",
            latest_nav=self._make_nav_tick(),
            recent_trades=[],
            latest_journal=None,
            model_status=self._make_model_status(),
            seq=0,
        )
        restored = CurrentState.model_validate_json(state.model_dump_json())
        assert restored == state


# ---------------------------------------------------------------------------
# Envelope
# ---------------------------------------------------------------------------


class TestEnvelope:
    def test_wrap_nav_tick(self) -> None:
        tick = NavTick(
            vault_address="0xV",
            nav_per_token_1e18="1000000000000000000",
            total_assets="1000",
            block_number=1,
        )
        envelope = Envelope(
            seq=1,
            server_ts="2026-06-01T00:00:00Z",
            chain_ts="2026-06-01T00:00:00Z",
            block_number=1,
            event_type="NavTick",
            payload=tick.model_dump(),
        )
        assert envelope.seq == 1
        assert envelope.event_type == "NavTick"
        assert envelope.chain_ts == "2026-06-01T00:00:00Z"

    def test_nullable_chain_fields_for_operational_events(self) -> None:
        status = ModelStatus(
            vault_address="0xV",
            model="gpt-5.1",
            status=ModelStatusEnum.paused,
            consecutive_failures=1,
        )
        envelope = Envelope(
            seq=2,
            server_ts="2026-06-01T00:00:01Z",
            chain_ts=None,
            block_number=None,
            event_type="ModelStatus",
            payload=status.model_dump(),
            latest_block_number=9999,
            latest_block_ts="2026-06-01T00:00:00Z",
        )
        assert envelope.chain_ts is None
        assert envelope.block_number is None
        assert envelope.latest_block_number == 9999

    def test_json_round_trip(self) -> None:
        tick = NavTick(
            vault_address="0xV",
            nav_per_token_1e18="1e18",
            total_assets="1000",
            block_number=10,
        )
        envelope = Envelope(
            seq=3,
            server_ts="2026-06-01T00:00:03Z",
            chain_ts="2026-06-01T00:00:02Z",
            block_number=10,
            event_type="NavTick",
            payload=tick.model_dump(),
        )
        restored = Envelope.model_validate_json(envelope.model_dump_json())
        assert restored == envelope

    def test_envelope_shape_fields(self) -> None:
        envelope = Envelope(
            seq=0,
            server_ts="2026-06-01T00:00:00Z",
            chain_ts=None,
            block_number=None,
            event_type="SessionEvent",
            payload={"session_id": "s1", "kind": "started"},
        )
        dumped = envelope.model_dump()
        assert "seq" in dumped
        assert "server_ts" in dumped
        assert "chain_ts" in dumped
        assert "block_number" in dumped
        assert "event_type" in dumped
        assert "payload" in dumped


# ---------------------------------------------------------------------------
# Channel topology (D-23)
# ---------------------------------------------------------------------------


VAULT_EVENT_TYPES = {"NavTick", "TradeEvent", "JournalEvent", "ModelStatus"}
GLOBAL_EVENT_TYPES = {"ArbOpp", "SessionEvent"}
ALL_LIVE_EVENT_TYPES = VAULT_EVENT_TYPES | GLOBAL_EVENT_TYPES


class TestChannelTopology:
    def test_vault_events_route_to_vault_channel(self) -> None:
        vault_addr = "0xTESTVAULT"
        for event_type in VAULT_EVENT_TYPES:
            ch = channel_for(event_type, vault_address=vault_addr)
            assert ch == VAULT_CHANNEL.format(vault_address=vault_addr), (
                f"{event_type} should route to vault channel"
            )

    def test_global_events_route_to_global_channel(self) -> None:
        for event_type in GLOBAL_EVENT_TYPES:
            ch = channel_for(event_type)
            assert ch == GLOBAL_CHANNEL, f"{event_type} should route to global channel"

    def test_no_event_type_maps_to_two_channels(self) -> None:
        """D-23: no event may appear on more than one channel."""
        vault_events = set(EVENT_CHANNEL.get("vault_events", []))
        global_events = set(EVENT_CHANNEL.get("global_events", []))
        overlap = vault_events & global_events
        assert overlap == set(), f"Event types on multiple channels: {overlap}"

    def test_event_channel_routing_table_covers_all_live_types(self) -> None:
        """All 6 live event types must appear in exactly one channel bucket."""
        vault_events = set(EVENT_CHANNEL.get("vault_events", []))
        global_events = set(EVENT_CHANNEL.get("global_events", []))
        all_routed = vault_events | global_events
        assert ALL_LIVE_EVENT_TYPES == all_routed, (
            f"Missing: {ALL_LIVE_EVENT_TYPES - all_routed}, "
            f"Extra: {all_routed - ALL_LIVE_EVENT_TYPES}"
        )

    def test_channel_for_vault_event_without_address_raises(self) -> None:
        """Vault-scoped events require vault_address."""
        with pytest.raises((ValueError, KeyError, TypeError)):
            channel_for("NavTick", vault_address=None)

    def test_channel_for_global_event_ignores_vault_address(self) -> None:
        """Global events should not need vault_address."""
        ch = channel_for("ArbOpp", vault_address="0xSOMEVAULT")
        assert ch == GLOBAL_CHANNEL

    def test_channel_for_unknown_event_type_raises(self) -> None:
        """Unknown event types must raise, not silently misroute."""
        with pytest.raises((ValueError, KeyError)):
            channel_for("UnknownEvent")

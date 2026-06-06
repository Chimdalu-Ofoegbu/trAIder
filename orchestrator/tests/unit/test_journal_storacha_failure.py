"""
orchestrator.tests.unit.test_journal_storacha_failure — Filebase backup failure isolation.

Test 2 (JOURNAL-02): Pinata OK + Filebase backup raises -> recordJournal STILL called;
final state is 'recorded' with web3_storage_cid NULL/pending; publish does NOT raise.

This proves T-03-21 (D-08): a Filebase/Storacha outage NEVER halts journaling or the
trade loop. The Filebase backfill is an asyncio.create_task that logs + send_alert(WARNING)
on failure and never raises into the caller.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orchestrator.journal.publisher import publish_journal_entry


@pytest.mark.asyncio
async def test_filebase_failure_does_not_block_publish() -> None:
    """Filebase outage: Pinata pin succeeds, recordJournal fires, state=recorded, no raise.

    Proves:
    - publish_journal_entry completes without raising
    - update_journal_state reaches 'recorded' (Pinata-primary + onchain record intact)
    - Filebase failure is logged (non-fatal) not propagated
    """
    fake_cid = "bafybeigdyrzt5sfp7udm7hu76uh7y26nf3efuylqabf3oclgtqy55fbzdi"
    priv_key = b"\xaa" * 32

    db_calls: list[str] = []

    async def mock_update(session, *, vault_address, order_key, new_state, **kwargs):
        db_calls.append(new_state)

    mock_record_fn = MagicMock()
    mock_record_fn.transact = AsyncMock(return_value="0xtxhash")
    mock_registry = MagicMock()
    mock_registry.functions.recordJournal = MagicMock(return_value=mock_record_fn)
    mock_web3 = MagicMock()
    mock_db = MagicMock()

    # Pinata OK — Filebase RAISES
    async def mock_pinata_ok(payload, jwt, **kw):
        return fake_cid

    async def mock_filebase_fail(payload, api_key, **kw):
        raise RuntimeError("Filebase: connection refused (simulated outage)")

    with (
        patch("orchestrator.journal.publisher.pin_to_pinata", side_effect=mock_pinata_ok),
        patch(
            "orchestrator.journal.publisher.pin_to_storacha_backup",
            side_effect=mock_filebase_fail,
        ),
        patch("orchestrator.journal.publisher.update_journal_state", mock_update),
        patch(
            "orchestrator.journal.publisher.send_alert",
            new_callable=AsyncMock,
        ) as mock_alert,
    ):
        # Must NOT raise even though Filebase fails
        await publish_journal_entry(
            mock_web3,
            mock_registry,
            mock_db,
            vault_address="0xVault",
            trade_hash="0x" + "ab" * 32,
            order_key="0x" + "cd" * 32,
            payload={"trade": "ETH", "cycle": 1},
            operator_journal_private_key=priv_key,
            pinata_jwt="test-jwt",
            storacha_api_key="test-key",
        )
        # Drain pending asyncio tasks (the Filebase backfill create_task)
        await asyncio.gather(*asyncio.all_tasks() - {asyncio.current_task()})

    # State machine must still reach 'recorded'
    assert "pinned_primary" in db_calls, f"pinned_primary missing from DB calls: {db_calls}"
    assert "recorded" in db_calls, (
        f"recorded missing from DB calls (Filebase fail blocked it?): {db_calls}"
    )

    # recordJournal must have been called (Filebase failure must not block onchain record)
    mock_record_fn.transact.assert_called_once()

    # A WARNING alert must have been sent for the Filebase failure
    # (non-blocking alert — the only consequence of the outage)
    mock_alert.assert_called()
    alert_call_args = [str(c) for c in mock_alert.call_args_list]
    assert any(
        "WARNING" in a or "warning" in a.lower() or "WARNING" in str(mock_alert.call_args_list)
        for a in alert_call_args
    ), "Expected a WARNING alert for Filebase failure, but no WARNING was sent"


@pytest.mark.asyncio
async def test_filebase_failure_web3_storage_cid_remains_null() -> None:
    """When Filebase fails, web3_storage_cid column stays NULL (backup not confirmed)."""
    fake_cid = "bafybeigdyrzt5sfp7udm7hu76uh7y26nf3efuylqabf3oclgtqy55fbzdi"
    priv_key = b"\xbb" * 32

    web3_storage_cid_updates: list = []

    async def mock_update(session, *, vault_address, order_key, new_state, **kwargs):
        # Track whether web3_storage_cid was set
        web3_storage_cid_updates.append(kwargs.get("web3_storage_cid"))

    mock_record_fn = MagicMock()
    mock_record_fn.transact = AsyncMock(return_value="0xtxhash")
    mock_registry = MagicMock()
    mock_registry.functions.recordJournal = MagicMock(return_value=mock_record_fn)

    with (
        patch(
            "orchestrator.journal.publisher.pin_to_pinata",
            new_callable=AsyncMock,
            return_value=fake_cid,
        ),
        patch(
            "orchestrator.journal.publisher.pin_to_storacha_backup",
            side_effect=RuntimeError("Filebase outage"),
        ),
        patch("orchestrator.journal.publisher.update_journal_state", mock_update),
        patch("orchestrator.journal.publisher.send_alert", new_callable=AsyncMock),
    ):
        await publish_journal_entry(
            MagicMock(),
            mock_registry,
            MagicMock(),
            vault_address="0xVault",
            trade_hash="0x" + "ab" * 32,
            order_key="0x" + "cd" * 32,
            payload={"trade": "ETH"},
            operator_journal_private_key=priv_key,
            pinata_jwt="jwt",
            storacha_api_key="key",
        )
        # Drain pending asyncio tasks so backfill task completes
        await asyncio.gather(*asyncio.all_tasks() - {asyncio.current_task()})

    # web3_storage_cid should NOT have been set to a real value
    # (all updates should have web3_storage_cid = None or absent)
    cids_set = [c for c in web3_storage_cid_updates if c is not None]
    assert len(cids_set) == 0, (
        f"web3_storage_cid should remain NULL on Filebase failure, got {cids_set}"
    )

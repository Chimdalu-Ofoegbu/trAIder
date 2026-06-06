"""
orchestrator.tests.unit.test_alert_sink — Unit tests for D-15 alert sink (JOURNAL-01).

Tests:
  1. send_alert at INFO with no Telegram config -> logs only, NO httpx call.
  2. send_alert at WARNING with Telegram config -> posts to api.telegram.org;
     a Telegram POST failure is swallowed (logged, not raised).
  3. (ipfs) pin_to_pinata posts multipart to uploads.pinata.cloud/v3/files and
     returns resp.json()["data"]["cid"] (mock httpx).
  4. (ipfs) same json.dumps(sort_keys=True) bytes feed both pin functions ->
     identical content fed (same-bytes invariant for same-CID).
"""

from __future__ import annotations

import json
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orchestrator.alerts.sink import AlertSeverity, send_alert
from orchestrator.journal.ipfs import pin_to_pinata, pin_to_storacha_backup

# ---------------------------------------------------------------------------
# Test 1: INFO severity — logs only, no httpx call
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_alert_info_no_telegram_call(caplog: pytest.LogCaptureFixture) -> None:
    """INFO alert: ALWAYS logs; NEVER makes httpx call regardless of Telegram config."""
    with patch("orchestrator.alerts.sink.httpx") as mock_httpx:
        with caplog.at_level(logging.INFO, logger="orchestrator.alerts.sink"):
            await send_alert(
                "routine heartbeat",
                AlertSeverity.INFO,
                context={"cycle": 1},
                telegram_bot_token="fake-token",
                telegram_chat_id="fake-chat",
            )
    # httpx.AsyncClient should NOT have been called for INFO
    mock_httpx.AsyncClient.assert_not_called()
    # Log should contain the message
    assert any("routine heartbeat" in record.message for record in caplog.records)


# ---------------------------------------------------------------------------
# Test 2: WARNING with Telegram config — posts; Telegram failure swallowed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_alert_warning_telegram_failure_swallowed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """WARNING alert with Telegram config: posts to Telegram; POST failure is swallowed."""
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock(side_effect=Exception("connection refused"))

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(side_effect=Exception("connection refused"))

    with patch("orchestrator.alerts.sink.httpx.AsyncClient", return_value=mock_client):
        with caplog.at_level(logging.WARNING, logger="orchestrator.alerts.sink"):
            # Must NOT raise even though Telegram fails
            await send_alert(
                "latency breach: >30s",
                AlertSeverity.WARNING,
                context={"latency_ms": 32000},
                telegram_bot_token="tok123",
                telegram_chat_id="chat456",
            )

    # The warning message should appear in logs
    assert any("latency breach" in record.message for record in caplog.records)
    # A warning about Telegram failure should also be logged
    assert any("Telegram" in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_send_alert_warning_telegram_success() -> None:
    """WARNING alert: Telegram POST is attempted when both token and chat_id are set."""
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=mock_response)

    with patch("orchestrator.alerts.sink.httpx.AsyncClient", return_value=mock_client):
        await send_alert(
            "model paused after 3 failures",
            AlertSeverity.WARNING,
            telegram_bot_token="bot-token",
            telegram_chat_id="123456",
        )

    mock_client.post.assert_called_once()
    call_kwargs = mock_client.post.call_args
    # Must post to the Telegram API
    assert "api.telegram.org" in call_kwargs[0][0]
    assert "WARNING" in str(call_kwargs[1].get("json", {}).get("text", ""))


@pytest.mark.asyncio
async def test_send_alert_critical_logs_at_error(caplog: pytest.LogCaptureFixture) -> None:
    """CRITICAL alerts must log at ERROR level (not INFO/WARNING)."""
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=MagicMock())

    with patch("orchestrator.alerts.sink.httpx.AsyncClient", return_value=mock_client):
        with caplog.at_level(logging.ERROR, logger="orchestrator.alerts.sink"):
            await send_alert(
                "circuit breaker latched",
                AlertSeverity.CRITICAL,
                telegram_bot_token="tok",
                telegram_chat_id="chat",
            )

    error_records = [r for r in caplog.records if r.levelname == "ERROR"]
    assert len(error_records) >= 1
    assert any("circuit breaker" in r.message for r in error_records)


# ---------------------------------------------------------------------------
# D-15: assert NO ModelStatus coupling in sink (structural — grep on import)
# ---------------------------------------------------------------------------


def test_sink_has_no_model_status_coupling() -> None:
    """sink.py must NOT import or reference ModelStatus — two audiences, two channels."""
    import inspect

    import orchestrator.alerts.sink as sink_module

    source = inspect.getsource(sink_module)
    assert "ModelStatus" not in source, (
        "D-15 violation: alerts/sink.py must never reference the viewer WS ModelStatus channel"
    )


# ---------------------------------------------------------------------------
# Test 3 (ipfs): pin_to_pinata posts multipart and returns resp.json()["data"]["cid"]
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pin_to_pinata_posts_multipart_returns_cid() -> None:
    """pin_to_pinata: posts multipart to uploads.pinata.cloud/v3/files, returns cid."""
    fake_cid = "bafybeigdyrzt5sfp7udm7hu76uh7y26nf3efuylqabf3oclgtqy55fbzdi"
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json = MagicMock(return_value={"data": {"cid": fake_cid}})

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=mock_response)

    payload = {"trade": "ETH", "cycle": 1}
    with patch("orchestrator.journal.ipfs.httpx.AsyncClient", return_value=mock_client):
        cid = await pin_to_pinata(payload, "test-jwt")

    assert cid == fake_cid
    mock_client.post.assert_called_once()
    call_args = mock_client.post.call_args
    assert "uploads.pinata.cloud/v3/files" in call_args[0][0]
    # Check Authorization header
    assert "Bearer test-jwt" in str(call_args[1].get("headers", {}))


# ---------------------------------------------------------------------------
# Test 4 (ipfs): same sorted-JSON bytes fed to both pin functions (same-CID invariant)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_same_sorted_json_bytes_both_providers() -> None:
    """Both pin functions use json.dumps(sort_keys=True) — identical content bytes."""
    payload = {"z_field": "zzz", "a_field": "aaa", "cycle": 42}
    # Expected canonical bytes
    expected_bytes = json.dumps(payload, sort_keys=True).encode()

    captured_pinata: list[bytes] = []
    captured_filebase: list[bytes] = []

    fake_cid = "bafybeigdyrzt5sfp7udm7hu76uh7y26nf3efuylqabf3oclgtqy55fbzdi"

    # Mock for Pinata
    async def mock_pinata_post(url, *, headers, files, data, timeout):
        # Extract content bytes from multipart files arg
        captured_pinata.append(files["file"][1])
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json = MagicMock(return_value={"data": {"cid": fake_cid}})
        return mock_resp

    mock_pinata_client = AsyncMock()
    mock_pinata_client.__aenter__ = AsyncMock(return_value=mock_pinata_client)
    mock_pinata_client.__aexit__ = AsyncMock(return_value=False)
    mock_pinata_client.post = AsyncMock(side_effect=mock_pinata_post)

    # Mock for Filebase backup
    async def mock_filebase_put(url, *, headers, content, timeout):
        captured_filebase.append(content)
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.headers = {"x-amz-meta-cid": fake_cid}
        return mock_resp

    mock_filebase_client = AsyncMock()
    mock_filebase_client.__aenter__ = AsyncMock(return_value=mock_filebase_client)
    mock_filebase_client.__aexit__ = AsyncMock(return_value=False)
    mock_filebase_client.put = AsyncMock(side_effect=mock_filebase_put)

    with patch("orchestrator.journal.ipfs.httpx.AsyncClient") as mock_cls:
        # First call = Pinata, second call = Filebase
        mock_cls.side_effect = [mock_pinata_client, mock_filebase_client]
        await pin_to_pinata(payload, "jwt-x")
        await pin_to_storacha_backup(payload, "filebase-key", bucket="my-bucket")

    assert len(captured_pinata) == 1
    assert len(captured_filebase) == 1
    assert captured_pinata[0] == expected_bytes, "Pinata content must be sorted-JSON bytes"
    assert captured_filebase[0] == expected_bytes, "Filebase content must be sorted-JSON bytes"
    assert captured_pinata[0] == captured_filebase[0], "Both providers must receive identical bytes"

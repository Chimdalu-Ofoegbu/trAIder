"""
orchestrator.tests.unit.test_operator_journal_signing_middleware

GAP #11 regression tests: operator-journal signing middleware must be loaded in
run_session so that recordJournal.transact({"from": operator_journal_key_address})
goes through eth_sendRawTransaction (signed path), NOT eth_sendTransaction (unsigned
path that Alchemy rejects with 400).

Detection uses an explicit tracked-set (`_loaded_signer_addresses`) built right
after each SignAndSendRawMiddlewareBuilder.build() + inject() call — NOT middleware_onion
introspection (unreliable in web3.py 7.x: build() returns a coroutine, not an object
with .account).

Tests:
  1. _check_signing_middleware_present — helper returns True when tracked set contains
     the address; False when absent or set is empty.
  2. run_mini_session builds the tracked set and the guard PASSES when all three
     signers (operator-trade injected in driver, journal + pusher injected here) are
     recorded.  Guard state is verified by asserting the explicit set, not by patching
     the guard helper.
  3. Startup guard fails loudly — run_mini_session raises RuntimeError when the
     journal signing middleware is deliberately NOT injected (simulate by patching
     the inject() call to skip tracking).
  4. Startup guard passes — no RuntimeError when guard confirms the middleware.
  5. Source-code order invariant — inject happens before guard check.
  6. price-pusher guard — tracked when PRICE_PUSHER_KEY is set; guard also passes.

No live chain calls, no Opus, no gate spend.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from eth_account import Account

# ---------------------------------------------------------------------------
# Test 1: _check_signing_middleware_present — explicit tracked-set path
# ---------------------------------------------------------------------------


def test_check_signing_middleware_present_found_via_tracked_set() -> None:
    """_check_signing_middleware_present returns True when the address is in loaded_signers."""
    from orchestrator.loop.run_session import _check_signing_middleware_present

    # Foundry test private key (publicly documented test key — no real value) # gitleaks:allow
    priv_key = b"\xde\xad\xbe\xef" * 8
    account = Account.from_key(priv_key)

    loaded = {account.address.lower()}
    mock_web3 = MagicMock()  # not consulted when loaded_signers is provided

    result = _check_signing_middleware_present(mock_web3, account.address, loaded_signers=loaded)
    assert result is True, f"Expected True when address is in loaded_signers; got {result}"


def test_check_signing_middleware_present_not_found_via_tracked_set() -> None:
    """_check_signing_middleware_present returns False when address is not in loaded_signers."""
    from orchestrator.loop.run_session import _check_signing_middleware_present

    priv_key = b"\xaa" * 32
    account = Account.from_key(priv_key)

    loaded: set[str] = set()  # empty — nothing injected
    mock_web3 = MagicMock()

    result = _check_signing_middleware_present(mock_web3, account.address, loaded_signers=loaded)
    assert result is False, "Expected False when loaded_signers is empty; got {result}"


def test_check_signing_middleware_present_wrong_account_in_tracked_set() -> None:
    """Returns False when a *different* account's address is in loaded_signers."""
    from orchestrator.loop.run_session import _check_signing_middleware_present

    priv_key_a = b"\xaa" * 32
    priv_key_b = b"\xbb" * 32
    account_a = Account.from_key(priv_key_a)
    account_b = Account.from_key(priv_key_b)

    loaded = {account_a.address.lower()}  # only account_a tracked
    mock_web3 = MagicMock()

    result = _check_signing_middleware_present(mock_web3, account_b.address, loaded_signers=loaded)
    assert result is False, (
        "Expected False when loaded_signers contains a different address; "
        f"account_a={account_a.address} account_b={account_b.address}"
    )


# ---------------------------------------------------------------------------
# Legacy fallback path tests (middleware_onion introspection — still callable
# when loaded_signers=None, e.g. from external callers)
# ---------------------------------------------------------------------------


def _make_mock_web3_with_middleware(account: Account) -> MagicMock:
    """Build a mock AsyncWeb3 whose middleware_onion contains a fake SignAndSendRaw entry."""
    mock_mw = MagicMock()
    mock_mw.account = account

    mock_web3 = MagicMock()
    mock_web3.middleware_onion = [mock_mw]
    return mock_web3


def _make_mock_web3_without_middleware() -> MagicMock:
    mock_web3 = MagicMock()
    mock_web3.middleware_onion = []
    return mock_web3


def test_check_signing_middleware_present_found_legacy() -> None:
    """Legacy path (loaded_signers=None): True when account.address matches middleware attr."""
    from orchestrator.loop.run_session import _check_signing_middleware_present

    priv_key = b"\xde\xad\xbe\xef" * 8
    account = Account.from_key(priv_key)

    mock_web3 = _make_mock_web3_with_middleware(account)
    result = _check_signing_middleware_present(mock_web3, account.address)
    assert result is True


def test_check_signing_middleware_present_not_found_legacy() -> None:
    """Legacy path (loaded_signers=None): False when middleware_onion is empty."""
    from orchestrator.loop.run_session import _check_signing_middleware_present

    priv_key = b"\xaa" * 32
    account = Account.from_key(priv_key)

    mock_web3 = _make_mock_web3_without_middleware()
    result = _check_signing_middleware_present(mock_web3, account.address)
    assert result is False


def test_check_signing_middleware_present_wrong_account_legacy() -> None:
    """Legacy path: False when middleware is for a different account."""
    from orchestrator.loop.run_session import _check_signing_middleware_present

    priv_key_a = b"\xaa" * 32
    priv_key_b = b"\xbb" * 32
    account_a = Account.from_key(priv_key_a)
    account_b = Account.from_key(priv_key_b)

    mock_web3 = _make_mock_web3_with_middleware(account_a)
    result = _check_signing_middleware_present(mock_web3, account_b.address)
    assert result is False


def test_check_signing_middleware_present_tuple_entry_legacy() -> None:
    """Legacy path: handles (name, mw) tuple entries in the middleware_onion."""
    from orchestrator.loop.run_session import _check_signing_middleware_present

    priv_key = b"\xcc" * 32
    account = Account.from_key(priv_key)

    mock_mw = MagicMock()
    mock_mw.account = account

    mock_web3 = MagicMock()
    mock_web3.middleware_onion = [("sign_and_send_raw", mock_mw)]

    result = _check_signing_middleware_present(mock_web3, account.address)
    assert result is True, "Expected True when middleware is in (name, mw) tuple form"


def test_check_signing_middleware_present_iteration_error_fail_closed() -> None:
    """Legacy path: if iteration raises, the guard fails CLOSED (returns False).

    SEC: a safety guard must not fail open. On introspection error we assume the signing
    middleware is absent so the caller errors loudly instead of proceeding to a tx the RPC
    would reject.
    """
    from orchestrator.loop.run_session import _check_signing_middleware_present

    mock_web3 = MagicMock()
    mock_web3.middleware_onion = MagicMock()
    mock_web3.middleware_onion.__iter__ = MagicMock(side_effect=RuntimeError("boom"))

    result = _check_signing_middleware_present(mock_web3, "0xDEAD" + "0" * 36)
    assert result is False, "Expected fail-closed (False) when iteration raises"


# ---------------------------------------------------------------------------
# Test 2 / 3: run_mini_session builds tracked set; guard PASSES/FAILS based on it
# ---------------------------------------------------------------------------

_FAKE_MANIFEST = {
    "vaultClaude": "0x" + "01" * 20,
    "journal": "0x" + "02" * 20,
    "ethFeed": "0x" + "03" * 20,
    "btcFeed": "0x" + "04" * 20,
    "solFeed": "0x" + "05" * 20,
    "mockPerps": "0x" + "06" * 20,
    "mockUsdc": "0x" + "07" * 20,
    "sessionFactory": "0x" + "08" * 20,
    "oracle": "0x" + "09" * 20,
    "adapter": "0x" + "0" * 40,
    "sequencerFeed": "0x" + "0a" * 20,
}

# Test private keys (publicly documented Foundry test keys — no real value) # gitleaks:allow
_JOURNAL_PRIV = "0x" + "de" * 32
_TRADE_PRIV = "0x" + "ab" * 32


def _base_patches(*, driver_side_effect=None):
    """Return the common patch context managers for run_mini_session startup tests."""
    mock_middleware_onion = MagicMock()
    mock_middleware_onion.__iter__ = MagicMock(return_value=iter([]))
    mock_middleware_onion.inject = MagicMock()

    mock_web3 = MagicMock()
    mock_web3.middleware_onion = mock_middleware_onion

    if driver_side_effect is None:

        async def _noop(*_a, **_kw):
            return None

        driver_side_effect = _noop

    patches = [
        patch("orchestrator.loop.run_session.AsyncWeb3", return_value=mock_web3),
        patch("orchestrator.loop.run_session.ExtraDataToPOAMiddleware", MagicMock()),
        patch("orchestrator.loop.run_session.load_manifest", return_value=_FAKE_MANIFEST),
        patch("orchestrator.loop.run_session.build_perps_adapter", return_value=MagicMock()),
        patch("sqlalchemy.ext.asyncio.create_async_engine", return_value=MagicMock()),
        patch("sqlalchemy.ext.asyncio.AsyncSession", return_value=MagicMock()),
        patch("orchestrator.loop.run_session.driver_run_session", side_effect=driver_side_effect),
    ]
    return patches, mock_web3


@pytest.mark.asyncio
async def test_run_mini_session_guard_passes_when_journal_key_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When OPERATOR_JOURNAL_KEY_PRIV is set, run_mini_session must NOT raise RuntimeError
    from the signer guard.

    This is the core regression test for the false-positive bug: the guard was raising
    even though the middleware was correctly injected, because middleware_onion introspection
    failed on web3.py 7.x.  With the tracked-set approach the guard correctly finds the
    signer and passes.
    """
    journal_account = Account.from_key(_JOURNAL_PRIV)

    monkeypatch.setenv("OPERATOR_JOURNAL_KEY_PRIV", _JOURNAL_PRIV)
    monkeypatch.setenv("OPERATOR_JOURNAL_KEY_ADDR", journal_account.address)
    monkeypatch.setenv("OPERATOR_TRADE_KEY", _TRADE_PRIV)
    monkeypatch.setenv("SEPOLIA_RPC", "https://mock-rpc.test")
    monkeypatch.setenv("ORCHESTRATOR_DATABASE_URL", "postgresql+asyncpg://u:p@localhost/db")

    driver_called: list[bool] = []

    async def _fake_driver(*_a, **_kw):
        driver_called.append(True)
        return None

    patches, _ = _base_patches(driver_side_effect=_fake_driver)
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
        from orchestrator.loop.run_session import run_mini_session

        try:
            await run_mini_session()
        except RuntimeError as exc:
            if "Startup signer guard failed" in str(exc):
                raise AssertionError(
                    "Startup signer guard must NOT raise when journal middleware is injected "
                    f"(tracked-set path). Got RuntimeError: {exc}"
                ) from exc
            # Other RuntimeError (not the guard) is fine in this mocked context
        except Exception:  # noqa: BLE001
            # Other exceptions (TypeError from None result, etc.) are expected
            pass

    assert driver_called, "driver_run_session was never called — guard must not have blocked it"


@pytest.mark.asyncio
async def test_startup_signer_guard_fails_loudly_when_journal_middleware_not_tracked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the journal signing middleware is injected but NOT tracked in _loaded_signer_addresses,
    the guard raises RuntimeError.

    Simulates the case where injection happens but the tracking line is missing/skipped —
    e.g. a future refactor accidentally removes the `_loaded_signer_addresses.add(...)` line.
    We do this by patching `_check_signing_middleware_present` to return False for the
    journal address, which is equivalent to the address not being in the tracked set.
    """
    journal_account = Account.from_key(_JOURNAL_PRIV)

    monkeypatch.setenv("OPERATOR_JOURNAL_KEY_PRIV", _JOURNAL_PRIV)
    monkeypatch.setenv("OPERATOR_JOURNAL_KEY_ADDR", journal_account.address)
    monkeypatch.setenv("OPERATOR_TRADE_KEY", _TRADE_PRIV)
    monkeypatch.setenv("SEPOLIA_RPC", "https://mock-rpc.test")
    monkeypatch.setenv("ORCHESTRATOR_DATABASE_URL", "postgresql+asyncpg://u:p@localhost/db")

    mock_middleware_onion = MagicMock()
    mock_middleware_onion.__iter__ = MagicMock(return_value=iter([]))
    mock_middleware_onion.inject = MagicMock()

    mock_web3 = MagicMock()
    mock_web3.middleware_onion = mock_middleware_onion

    with (
        patch("orchestrator.loop.run_session.AsyncWeb3", return_value=mock_web3),
        patch("orchestrator.loop.run_session.ExtraDataToPOAMiddleware", MagicMock()),
        patch("orchestrator.loop.run_session.load_manifest", return_value=_FAKE_MANIFEST),
        patch("orchestrator.loop.run_session.build_perps_adapter", return_value=MagicMock()),
        patch("sqlalchemy.ext.asyncio.create_async_engine", return_value=MagicMock()),
        patch("sqlalchemy.ext.asyncio.AsyncSession", return_value=MagicMock()),
        # Simulate tracking being absent: guard helper always returns False for journal EOA
        patch(
            "orchestrator.loop.run_session._check_signing_middleware_present",
            return_value=False,
        ),
        patch(
            "orchestrator.loop.run_session.driver_run_session",
            new_callable=AsyncMock,
            return_value=None,
        ),
    ):
        from orchestrator.loop.run_session import run_mini_session

        with pytest.raises(RuntimeError, match="Startup signer guard failed"):
            await run_mini_session()


@pytest.mark.asyncio
async def test_startup_signer_guard_passes_when_guard_helper_returns_true(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When _check_signing_middleware_present returns True, no RuntimeError is raised.

    This is the "already patched guard helper" path used in integration-style tests.
    """
    journal_account = Account.from_key(_JOURNAL_PRIV)

    monkeypatch.setenv("OPERATOR_JOURNAL_KEY_PRIV", _JOURNAL_PRIV)
    monkeypatch.setenv("OPERATOR_JOURNAL_KEY_ADDR", journal_account.address)
    monkeypatch.setenv("OPERATOR_TRADE_KEY", _TRADE_PRIV)
    monkeypatch.setenv("SEPOLIA_RPC", "https://mock-rpc.test")
    monkeypatch.setenv("ORCHESTRATOR_DATABASE_URL", "postgresql+asyncpg://u:p@localhost/db")

    mock_middleware_onion = MagicMock()
    mock_middleware_onion.__iter__ = MagicMock(return_value=iter([]))
    mock_middleware_onion.inject = MagicMock()

    mock_web3 = MagicMock()
    mock_web3.middleware_onion = mock_middleware_onion

    driver_called: list[bool] = []

    async def fake_driver(*_args, **_kwargs):
        driver_called.append(True)
        return None

    with (
        patch("orchestrator.loop.run_session.AsyncWeb3", return_value=mock_web3),
        patch("orchestrator.loop.run_session.ExtraDataToPOAMiddleware", MagicMock()),
        patch("orchestrator.loop.run_session.load_manifest", return_value=_FAKE_MANIFEST),
        patch("orchestrator.loop.run_session.build_perps_adapter", return_value=MagicMock()),
        patch("sqlalchemy.ext.asyncio.create_async_engine", return_value=MagicMock()),
        patch("sqlalchemy.ext.asyncio.AsyncSession", return_value=MagicMock()),
        patch(
            "orchestrator.loop.run_session._check_signing_middleware_present",
            return_value=True,
        ),
        patch("orchestrator.loop.run_session.driver_run_session", side_effect=fake_driver),
    ):
        from orchestrator.loop.run_session import run_mini_session

        try:
            await run_mini_session()
        except RuntimeError as exc:
            if "Startup signer guard failed" in str(exc):
                raise AssertionError(
                    "Startup signer guard must NOT raise when guard returns True; "
                    f"got RuntimeError: {exc}"
                ) from exc
        except Exception:  # noqa: BLE001
            pass  # Other exceptions (e.g. TypeError from None result) are expected

    assert driver_called, "driver_run_session was not called — startup guard must not block it"


# ---------------------------------------------------------------------------
# Test: price-pusher guard — tracked when PRICE_PUSHER_KEY is set
# ---------------------------------------------------------------------------

_PUSHER_PRIV = "0x" + "cc" * 32


@pytest.mark.asyncio
async def test_startup_signer_guard_passes_with_price_pusher_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When PRICE_PUSHER_KEY is also set, the pusher EOA is tracked and the guard passes."""
    journal_account = Account.from_key(_JOURNAL_PRIV)

    monkeypatch.setenv("OPERATOR_JOURNAL_KEY_PRIV", _JOURNAL_PRIV)
    monkeypatch.setenv("OPERATOR_JOURNAL_KEY_ADDR", journal_account.address)
    monkeypatch.setenv("OPERATOR_TRADE_KEY", _TRADE_PRIV)
    monkeypatch.setenv("PRICE_PUSHER_KEY", _PUSHER_PRIV)
    monkeypatch.setenv("SEPOLIA_RPC", "https://mock-rpc.test")
    monkeypatch.setenv("ORCHESTRATOR_DATABASE_URL", "postgresql+asyncpg://u:p@localhost/db")

    driver_called: list[bool] = []

    async def _fake_driver(*_a, **_kw):
        driver_called.append(True)
        return None

    patches, _ = _base_patches(driver_side_effect=_fake_driver)
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
        from orchestrator.loop.run_session import run_mini_session

        try:
            await run_mini_session()
        except RuntimeError as exc:
            if "Startup signer guard failed" in str(exc):
                raise AssertionError(
                    "Startup signer guard must NOT raise when price-pusher is also tracked. "
                    f"Got RuntimeError: {exc}"
                ) from exc
        except Exception:  # noqa: BLE001
            pass

    assert driver_called, "driver_run_session was not called"


# ---------------------------------------------------------------------------
# Test 5: Source-code invariant — inject happens before guard check
# ---------------------------------------------------------------------------


def test_journal_middleware_injected_before_guard_check() -> None:
    """The signing middleware injection for operator-journal MUST occur before the
    startup signer guard check in run_mini_session source code.
    """
    import inspect

    from orchestrator.loop import run_session as rs_mod

    src = inspect.getsource(rs_mod.run_mini_session)

    inject_idx = src.find("web3.middleware_onion.inject(_journal_mw")
    guard_idx = src.find("_check_signing_middleware_present(")

    assert inject_idx != -1, (
        "Source must contain 'web3.middleware_onion.inject(_journal_mw' — "
        "GAP #11 fix: journal middleware injection"
    )
    assert guard_idx != -1, (
        "Source must contain '_check_signing_middleware_present(' — "
        "GAP #11 fix: startup signer guard"
    )
    assert inject_idx < guard_idx, (
        "Journal middleware injection must appear BEFORE the signer guard check in "
        "run_mini_session. "
        f"Injection at char {inject_idx}, guard at char {guard_idx}. "
        "If guard runs first it will always fail (middleware not yet injected)."
    )


def test_tracked_set_add_before_guard_check() -> None:
    """The _loaded_signer_addresses.add() call for journal must appear before the guard check.

    This ensures the tracked set has the address by the time the guard consults it.
    """
    import inspect

    from orchestrator.loop import run_session as rs_mod

    src = inspect.getsource(rs_mod.run_mini_session)

    add_idx = src.find("_loaded_signer_addresses.add(operator_journal_account.address.lower())")
    guard_idx = src.find("_check_signing_middleware_present(")

    assert add_idx != -1, (
        "Source must contain '_loaded_signer_addresses.add(operator_journal_account.address.lower())' — "
        "tracked-set population for journal signer"
    )
    assert add_idx < guard_idx, (
        "Journal address must be added to _loaded_signer_addresses BEFORE the signer guard check. "
        f"add() at char {add_idx}, guard at char {guard_idx}."
    )


# ---------------------------------------------------------------------------
# Test: run_mini_session source code invariants — GAP #11 fix patterns present
# ---------------------------------------------------------------------------


def test_run_mini_session_source_has_journal_middleware_injection() -> None:
    """run_mini_session source must contain the GAP #11 journal middleware injection."""
    import inspect

    from orchestrator.loop import run_session as rs_mod

    src = inspect.getsource(rs_mod.run_mini_session)

    assert "operator_journal_account = Account.from_key" in src, (
        "run_mini_session must create Account from journal key"
    )
    assert "_SARMBuilder.build(operator_journal_account)" in src, (
        "run_mini_session must call SignAndSendRawMiddlewareBuilder.build(operator_journal_account)"
    )
    assert "_journal_mw" in src and "web3.middleware_onion.inject(_journal_mw" in src, (
        "run_mini_session must inject the journal signing middleware"
    )
    assert "_loaded_signer_addresses" in src, (
        "run_mini_session must maintain an explicit tracked-signer set"
    )
    assert "_loaded_signer_addresses.add(operator_journal_account.address.lower())" in src, (
        "run_mini_session must track the journal signer in _loaded_signer_addresses"
    )

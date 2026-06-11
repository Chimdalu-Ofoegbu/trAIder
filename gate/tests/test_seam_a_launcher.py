"""
gate/tests/test_seam_a_launcher.py — Seam A real-path launcher tests (04-GATE item #4).

These are the tests the dry-run fake HID. They exercise the ACTUAL live launcher
(``build_live_shared_deps``) against the REAL ``driver.run_session`` signature, with NO
network / chain / LLM spend (mock web3 + patched driver). Two guarantees:

  (a) NONCE SAFETY under concurrency — 3 models sharing ONE NonceManager (plus the shared
      price-pusher) never get a duplicate nonce. This is the exact D-11 collision the
      shared-EOA design exists to prevent (web3's per-tx auto-nonce hands the same nonce to
      two concurrent .transact() calls → one silently replaces the other).

  (b) REAL-SIGNATURE compatibility — the launcher's call BINDS to the real
      driver.run_session signature. This is the exact gap the dry-run fake masked: the fake
      ``_fake_driver_run_session(*, vault_address, provider, **kwargs)`` swallowed every
      argument, so the wiring "passed" on mocks while the live path raised
      ``TypeError: unexpected keyword argument 'vault_address'`` (the gate-failure root).

Mock-masking guard (04-GATE): a real-path test in CI so this failure class cannot silently
return — green-on-mocks ≠ works-live.
"""

from __future__ import annotations

import asyncio
import inspect
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gate.run_gate import build_live_shared_deps
from orchestrator.loop.driver import run_session as _real_run_session
from orchestrator.loop.nonce_manager import submit_op_tx

# Real driver.run_session signature, captured ONCE before any patching. Assertion (b)
# binds the launcher's actual call against this — the check the dry-run fake bypassed.
_REAL_SIG = inspect.signature(_real_run_session)

# anvil account[0] — a valid key so Account.from_key works; never signs anything real here.
_TEST_TRADE_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"

# Real Sepolia addresses (deployments/sepolia.json) so the closure's per-vault lookup hits.
_VAULT_ADDRS = {
    "claude": "0xd755A69E5DeAC38890412e68Ea9a9b5A00d4153E",
    "gpt": "0x3B11463a85f5Ea513e62f5aF37dd66D09dc0c26e",
    "gemini": "0xA4eDE74F0992bFb3c034DE8ebF9CBD01E699e84f",
}

_MANIFEST = {
    "mockPerps": "0x8Dd2FBA5fC20BF5e8dd656e53c79b2E7BD6344E2",
    "ethFeed": "0xA3ADBA9c6AafF08411613974241a8699D22fC680",
    "btcFeed": "0x752B5cbd8431a671C73e9462A602146342A4E911",
    "solFeed": "0xf247f27B5bC715235A9EeDFB0751Af8b661b2F20",
}


def _make_mock_web3(nonce_record: list) -> MagicMock:
    """Mock AsyncWeb3 that seeds nonce 0 and records every nonce passed to .transact().

    Every operator-EOA write in the gate (3 models' trades via the patched driver, plus the
    shared price-pusher's setPrice) routes through ``submit_op_tx`` → ``.transact({"from":…,
    "nonce":N})``. Capturing N here lets the test prove there are no collisions.
    """
    web3 = MagicMock()
    # NonceManager seeds _local_nonce from this once, then increments locally under its lock.
    web3.eth.get_transaction_count = AsyncMock(return_value=0)
    web3.eth.wait_for_transaction_receipt = AsyncMock(
        return_value={"status": 1, "blockNumber": 1}
    )

    def _contract(*, address=None, abi=None):  # noqa: ANN001, ANN002
        contract = MagicMock()
        contract.address = address

        def _setprice(*_args, **_kwargs):  # noqa: ANN002, ANN003
            call = MagicMock()

            async def _transact(tx):  # noqa: ANN001
                nonce_record.append(tx.get("nonce"))
                return b"\x00" * 32  # HexBytes-like; .hex() works in push_price's debug log

            call.transact = _transact
            return call

        contract.functions.setPrice = _setprice
        return contract

    web3.eth.contract = _contract
    return web3


@pytest.mark.asyncio
async def test_three_models_share_one_nonce_manager_no_collision() -> None:
    """Real-path: 3 live model closures run concurrently through the SHARED NonceManager.

    Asserts (a) every operator-EOA write gets a UNIQUE, contiguous nonce (no D-11 collision),
    and (b) all 3 closures reach driver.run_session with arguments that BIND to its real
    signature (the wrapper the dry-run fake hid).
    """
    nonce_record: list = []
    web3 = _make_mock_web3(nonce_record)
    vaults_with_addrs = [
        (MagicMock(name="vaultClaude"), _VAULT_ADDRS["claude"]),
        (MagicMock(name="vaultGpt"), _VAULT_ADDRS["gpt"]),
        (MagicMock(name="vaultGem"), _VAULT_ADDRS["gemini"]),
    ]

    shared_deps, teardown = build_live_shared_deps(
        web3,
        _MANIFEST,
        vaults_with_addrs,
        trade_key=_TEST_TRADE_KEY,
        gate_duration=60,
        gate_cadence=3600.0,  # huge → shared pusher does one push round then idles (deterministic)
        gate_seed=42,
        db_url="postgresql+asyncpg://t:t@localhost/t",  # never connects (driver is patched)
    )
    closure = shared_deps["driver_run_session"]

    bind_ok: list = []

    async def _spy_run_session(*args, **kwargs):  # noqa: ANN002, ANN003
        # (b) Prove the launcher's actual call binds to the REAL driver.run_session signature.
        #     A TypeError here = the Seam A bug is back (raises out of gather → test fails).
        _REAL_SIG.bind(*args, **kwargs)
        bind_ok.append(kwargs.get("vault_contract"))
        nonce_mgr = kwargs["nonce_manager"]
        from_addr = kwargs["deployer_address"]
        # (a) Exercise the shared NonceManager the way the real driver + keeper do: several
        #     serialized operator writes, with explicit yields to force cross-task interleaving.
        for _ in range(4):
            await submit_op_tx(
                web3.eth.contract(address="0xaggregator", abi=[]).functions.setPrice(0),
                from_addr,
                nonce_manager=nonce_mgr,
            )
            await asyncio.sleep(0)
        return {"cycles": 1, "seed": 42, "session_id": "seam-a-smoke"}

    try:
        with patch("orchestrator.loop.driver.run_session", _spy_run_session):
            await asyncio.gather(
                closure(vault_address=_VAULT_ADDRS["claude"], provider="claude"),
                closure(vault_address=_VAULT_ADDRS["gpt"], provider="gpt"),
                closure(vault_address=_VAULT_ADDRS["gemini"], provider="gemini"),
            )
    finally:
        await teardown()

    # (b) all 3 models reached the real driver.run_session signature
    assert len(bind_ok) == 3, "all 3 model closures must reach driver.run_session"
    # the per-vault lookup wired the right vault contract into each call (not None)
    assert all(vc is not None for vc in bind_ok), f"vault_contract lookup failed: {bind_ok}"

    # (a) no duplicate nonce across all shared-EOA writes (3 models × 4 + shared pusher)
    assert None not in nonce_record, "a write went out with no explicit nonce"
    assert len(nonce_record) >= 12, f"expected ≥12 operator writes; got {len(nonce_record)}"
    assert len(nonce_record) == len(set(nonce_record)), (
        f"DUPLICATE NONCE assigned across concurrent shared-EOA models (D-11 collision): "
        f"{sorted(nonce_record)}"
    )
    assert sorted(nonce_record) == list(range(len(nonce_record))), (
        f"nonces not contiguous from 0 (gap/replacement): {sorted(nonce_record)}"
    )


def test_launcher_call_shape_matches_real_run_session_signature() -> None:
    """Pure guard that ENCODES the Seam A bug so it cannot silently return.

    The shape the live launcher passes MUST bind to driver.run_session; the OLD broken shape
    the supervisor used against the raw run_session (vault_address=/provider=/web3=) MUST NOT.
    """
    # The exact shape build_live_shared_deps's closure passes — must bind.
    _REAL_SIG.bind(
        MagicMock(),  # web3
        MagicMock(),  # mock_perps / adapter
        {},  # aggregators
        "0xVault",  # vault
        "claude-opus-4-7",  # model
        config=MagicMock(),
        db=MagicMock(),
        redis=None,
        deployer_address="0xOperator",
        vault_contract=MagicMock(),
        operator_trade_account=MagicMock(),
        nonce_manager=MagicMock(),
        launch_price_pusher=False,
        external_walk=MagicMock(),
        external_snapshot_queue=MagicMock(),
    )

    # The OLD broken shape (what the dry-run fake accepted but the real fn rejects).
    with pytest.raises(TypeError):
        _REAL_SIG.bind(vault_address="0xVault", provider="claude", web3=MagicMock())

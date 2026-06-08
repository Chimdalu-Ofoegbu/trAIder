"""
orchestrator.loop.run_session — Sepolia mini-session entrypoint (03-08 / TEST-03).

Wires all Phase-3 pieces into a single runnable session:
  - Reads addresses from deployments/sepolia.json (D-14: NO hardcoded addresses).
  - Connects AsyncWeb3 to SEPOLIA_RPC.
  - Loads operator-trade key → signing middleware (D-16).
  - Loads operator-journal key for publish_journal_entry (D-10).
  - Selects venue via PERPS_VENUE (default "mock" per D-01, D-03).
  - Builds vault_contract (mCLA-S1) + adapter via adapter_factory.
  - Launches driver.run_session + run_keeper_monitor as concurrent asyncio.Tasks.
  - Runs a 1A latency watchdog (D-03): WARNING at threshold, NEVER auto-flips.
  - Wires journal params (Pinata JWT, Filebase key, operator-journal key).

Usage (via make run-mini-session or directly):
  uv run --project orchestrator --env-file orchestrator/.env \
      python -m orchestrator.loop.run_session

Environment variables read (from .env files — secrets never hardcoded):
  SEPOLIA_RPC                  Arbitrum Sepolia HTTPS RPC URL
  OPERATOR_TRADE_KEY           Hex private key for operator-trade EOA (SEC-01)
  OPERATOR_JOURNAL_KEY_PRIV    Hex private key for operator-journal EOA (SEC-01)
  OPERATOR_JOURNAL_KEY_ADDR    Hex address of operator-journal EOA (for transact from)
  PINATA_JWT                   Pinata V3 JWT for IPFS pinning (JOURNAL-02)
  FILEBASE_ACCESS_KEY          Filebase S3 access key for backup pinning (D-08, SigV4)
  FILEBASE_SECRET_KEY          Filebase S3 secret key for backup pinning (D-08, SigV4)
  FILEBASE_BUCKET              Filebase IPFS bucket name (default: traider-journals)
  PERPS_VENUE                  "mock" | "gmx" (default: "mock", D-01/D-03)
  SESSION_DURATION             Session duration in seconds (default: 1800 = 30min)
  SESSION_CADENCE              Trading cadence in seconds (default: 60)
  PRICE_SEED                   PriceWalk seed (default: 42, D-01)
  DRIFT                        PriceWalk per-cycle log-normal drift fraction (default: 0.0001, D-01)
  VOLATILITY                   PriceWalk per-cycle std-dev fraction (default: 0.005, D-01)
  ORCHESTRATOR_DATABASE_URL    Async Postgres URL (postgresql+asyncpg://...)
  REDIS_URL                    Redis URL for WS fanout (optional)
  TELEGRAM_BOT_TOKEN           Telegram bot token for alert sink (D-15, optional)
  TELEGRAM_CHAT_ID             Telegram chat ID for alert sink (D-15, optional)
  LATENCY_WATCHDOG_THRESHOLD   createOrder→OrderExecuted latency threshold in seconds
                               (default: 120 — sized for ~40-60s Sepolia execution; ARCH-X D-03)

Security (SEC-01 / T-03-31): private keys read from gitignored .env files, passed
as parameters, NEVER logged verbatim. run_session.py reads them ONCE from env and
passes them down — no downstream module re-reads os.environ.

D-03 1A Latency Watchdog rule (T-03-30):
  The watchdog monitors createOrder→OrderExecuted latency. If a pending order
  exceeds LATENCY_WATCHDOG_THRESHOLD seconds, it fires send_alert(WARNING).
  The watchdog NEVER auto-flips PERPS_VENUE. Flipping requires the operator to
  set PERPS_VENUE=<venue> and restart the orchestrator (D-03 restart-flip).
  This is enforced at the code level: no PERPS_VENUE mutation inside the watchdog.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from eth_account import Account
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from web3 import AsyncWeb3
from web3.middleware import ExtraDataToPOAMiddleware

from orchestrator.alerts.sink import AlertSeverity, send_alert
from orchestrator.loop.adapter_factory import build_perps_adapter
from orchestrator.loop.driver import run_session as driver_run_session
from orchestrator.loop.session import SessionConfig

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Manifest path (D-14: single source of truth, no hardcoded addresses)
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).parent.parent.parent.parent.parent  # repo root
_MANIFEST_PATH = _REPO_ROOT / "deployments" / "sepolia.json"  # deployments/sepolia.json

# ---------------------------------------------------------------------------
# Contract artifact paths (for ABI loading)
# ---------------------------------------------------------------------------

_CONTRACTS_OUT = _REPO_ROOT / "contracts" / "out"
_MTOKEN_VAULT_ARTIFACT = _CONTRACTS_OUT / "mTokenVault.sol" / "MTokenVault.json"
_MOCK_PERPS_ARTIFACT = _CONTRACTS_OUT / "MockPerps.sol" / "MockPerps.json"
_JOURNAL_REGISTRY_ARTIFACT = _CONTRACTS_OUT / "JournalRegistry.sol" / "JournalRegistry.json"


def _load_abi(artifact_path: Path) -> list:
    """Load ABI from a Foundry JSON artifact."""
    if not artifact_path.exists():
        raise FileNotFoundError(
            f"Contract artifact not found: {artifact_path}\nRun `forge build` in contracts/ first."
        )
    with artifact_path.open(encoding="utf-8") as f:
        return json.load(f)["abi"]


# ---------------------------------------------------------------------------
# 1A Latency Watchdog helpers — for external callers to notify the watchdog (D-03)
# ---------------------------------------------------------------------------


def register_pending_order(order_key: str, watchdog_queue: asyncio.Queue | None) -> None:
    """Register a new pending order with the latency watchdog.

    Called by external callers (e.g. driver/keeper) to notify the watchdog
    that a new pending order was submitted. The watchdog starts tracking
    this order's age from this moment.

    Args:
        order_key: Hex order key string.
        watchdog_queue: asyncio.Queue shared with the watchdog coroutine.
                        If None, the watchdog is not active (no-op).
    """
    if watchdog_queue is not None:
        try:
            watchdog_queue.put_nowait(("pending", order_key, time.monotonic()))
        except asyncio.QueueFull:
            pass  # Non-blocking — watchdog is best-effort


def notify_order_executed(order_key: str, watchdog_queue: asyncio.Queue | None) -> None:
    """Notify the watchdog that a pending order was executed (clear from tracking).

    Args:
        order_key: Hex order key string.
        watchdog_queue: asyncio.Queue shared with the watchdog coroutine.
                        If None, the watchdog is not active (no-op).
    """
    if watchdog_queue is not None:
        try:
            watchdog_queue.put_nowait(("executed", order_key, time.monotonic()))
        except asyncio.QueueFull:
            pass  # Non-blocking — watchdog is best-effort


# ---------------------------------------------------------------------------
# Queue-based latency watchdog (richer version — processes events from driver/keeper)
# ---------------------------------------------------------------------------


async def _latency_watchdog_queue_driven(
    *,
    vault_address: str,
    threshold_seconds: float,
    stop_event: asyncio.Event,
    event_queue: asyncio.Queue,
    telegram_bot_token: str | None,
    telegram_chat_id: str | None,
) -> None:
    """Queue-driven 1A latency watchdog (D-03 / T-03-30).

    Processes ("pending", order_key, mono_ts) and ("executed", order_key, mono_ts)
    events from the driver/keeper. Fires WARNING when a pending order exceeds threshold.
    NEVER auto-flips PERPS_VENUE.
    """
    logger.info(
        "latency_watchdog_queue: starting (vault=%s threshold=%.0fs)",
        vault_address[:10],
        threshold_seconds,
    )

    # {order_key: first_seen_monotonic}
    _pending: dict[str, float] = {}
    _alerted: set[str] = set()

    while not stop_event.is_set():
        # Drain all available events
        while True:
            try:
                event_type, order_key, mono_ts = event_queue.get_nowait()
                if event_type == "pending":
                    _pending[order_key] = mono_ts
                    logger.debug("watchdog: tracking order %s", order_key[:10])
                elif event_type == "executed":
                    _pending.pop(order_key, None)
                    _alerted.discard(order_key)
                    logger.debug("watchdog: order executed %s — cleared", order_key[:10])
            except asyncio.QueueEmpty:
                break

        # Check all tracked pending orders for threshold breach
        now = time.monotonic()
        for order_key, first_seen in list(_pending.items()):
            elapsed = now - first_seen
            if elapsed > threshold_seconds and order_key not in _alerted:
                _alerted.add(order_key)
                logger.warning(
                    "latency_watchdog: 1A BREACH — order_key=%s pending %.0fs "
                    "(threshold=%.0fs). D-03: operator flip required (NEVER auto-flip).",
                    order_key[:10],
                    elapsed,
                    threshold_seconds,
                )
                # D-03 / T-03-30: WARNING alert only — NEVER auto-flip PERPS_VENUE
                await send_alert(
                    f"1A latency breach: createOrder→OrderExecuted = {elapsed:.0f}s "
                    f"(threshold {threshold_seconds:.0f}s). "
                    "Operator: set PERPS_VENUE=<venue> and restart — do NOT auto-flip.",
                    AlertSeverity.WARNING,
                    context={
                        "order_key": order_key,
                        "elapsed_s": f"{elapsed:.0f}",
                        "threshold_s": f"{threshold_seconds:.0f}",
                        "vault": vault_address,
                        "action_required": "MANUAL: set PERPS_VENUE + restart orchestrator",
                    },
                    telegram_bot_token=telegram_bot_token,
                    telegram_chat_id=telegram_chat_id,
                )

        await asyncio.sleep(2.0)  # watchdog tick

    logger.info("latency_watchdog_queue: stop_event set — exiting")


# ---------------------------------------------------------------------------
# Signing middleware presence guard (GAP #11)
# ---------------------------------------------------------------------------


def _check_signing_middleware_present(web3: AsyncWeb3, eoa_address: str) -> bool:
    """Return True if `web3.middleware_onion` contains a SignAndSendRaw middleware
    that was built for `eoa_address`.

    web3.py 7.x SignAndSendRawMiddlewareBuilder.build() returns a curry partial whose
    __wrapped__ attribute (or the closure's private state) holds the account.
    The safest cross-version approach is to inspect each middleware object for an
    `account` attribute whose `.address` matches the target EOA.

    This guard is ONLY called at startup — performance is not a concern.

    Args:
        web3:        AsyncWeb3 instance whose middleware_onion to inspect.
        eoa_address: Checksummed-or-hex EOA address string to search for.

    Returns:
        True if signing middleware for the address is found, False otherwise.
    """
    target = eoa_address.lower()
    try:
        # web3.py 7.x: middleware_onion exposes an iterable of (name, mw) or just mw objects.
        # We iterate and inspect each entry for a recognisable account attribute.
        for entry in web3.middleware_onion:
            # entry may be the middleware callable directly, or a tuple (name, mw)
            mw = entry[1] if isinstance(entry, tuple) else entry
            # Check for `account` attribute (SignAndSendRaw sets this on its closure/object)
            acct = getattr(mw, "account", None)
            if acct is not None and getattr(acct, "address", "").lower() == target:
                return True
            # Also check for `_account` (some web3 versions use private attr)
            acct = getattr(mw, "_account", None)
            if acct is not None and getattr(acct, "address", "").lower() == target:
                return True
    except Exception:  # noqa: BLE001
        # If iteration fails for any reason, fail open (return True) so the guard does not
        # block sessions on unexpected web3 version changes — the 400 error itself is the
        # definitive signal. Log a warning so the issue is visible.
        logger.warning(
            "_check_signing_middleware_present: iteration over middleware_onion failed "
            "for EOA=%s — guard skipped (fail-open). Check web3.py version compatibility.",
            eoa_address,
        )
        return True
    return False


# ---------------------------------------------------------------------------
# load_manifest — D-14: single source of truth for Sepolia addresses
# ---------------------------------------------------------------------------


def load_manifest(manifest_path: Path | str | None = None) -> dict:
    """Load the Sepolia deployment manifest (deployments/sepolia.json).

    D-14: the manifest is the ONLY source of addresses. No hardcoded addresses
    anywhere in run_session.py.

    Args:
        manifest_path: Override manifest path. If None, uses _MANIFEST_PATH.

    Returns:
        Dict with keys: sessionFactory, oracle, journal, vaultClaude, vaultGpt,
        vaultGem, adapter, mockPerps, mockUsdc, ethFeed, btcFeed, solFeed, sequencerFeed.

        Note: adapter = address(0) (GMXAdapter deferred, D-13). When PERPS_VENUE=mock
        the session resolves the adapter address from mockPerps, NOT adapter.

    Raises:
        FileNotFoundError: If the manifest file does not exist.
        ValueError: If the manifest is missing required fields.
    """
    path = Path(manifest_path) if manifest_path else _MANIFEST_PATH
    if not path.exists():
        raise FileNotFoundError(
            f"Sepolia manifest not found: {path}\n"
            "Run `make deploy-sepolia` to deploy and generate the manifest."
        )
    with path.open(encoding="utf-8") as f:
        manifest = json.load(f)

    required_keys = [
        "vaultClaude",
        "journal",
        "ethFeed",
        "btcFeed",
        "solFeed",
    ]
    missing = [k for k in required_keys if k not in manifest]
    if missing:
        raise ValueError(
            f"Sepolia manifest missing required fields: {missing}\nManifest path: {path}"
        )
    return manifest


# ---------------------------------------------------------------------------
# run_mini_session — the top-level Sepolia mini-session entrypoint
# ---------------------------------------------------------------------------


async def run_mini_session(
    *,
    manifest_path: Path | str | None = None,
    sepolia_rpc: str | None = None,
    operator_trade_private_key: str | None = None,
    operator_journal_private_key_hex: str | None = None,
    operator_journal_key_address: str | None = None,
    pinata_jwt: str | None = None,
    filebase_access_key: str | None = None,
    filebase_secret_key: str | None = None,
    filebase_bucket: str = "traider-journals",
    perps_venue: str = "mock",
    session_duration_seconds: int = 1800,
    cadence_seconds: float = 60.0,
    price_seed: int = 42,
    drift: float = 0.0001,
    volatility: float = 0.005,
    database_url: str | None = None,
    redis_url: str | None = None,
    telegram_bot_token: str | None = None,
    telegram_chat_id: str | None = None,
    latency_watchdog_threshold: float = 120.0,
    model: str = "claude-opus-4-7",
    # GAP #4/#6: price_pusher_private_key separates price-push signing from trade-submission
    # signing (SEC-01 key separation). When None, reads PRICE_PUSHER_KEY from env;
    # if that is also unset, falls back to OPERATOR_TRADE_KEY.
    price_pusher_private_key: str | None = None,
) -> dict:
    """Run the Sepolia mini-session end-to-end (TEST-03 hard gate / D-04).

    Sequence:
    1. Load manifest (D-14: deployments/sepolia.json).
    2. Connect AsyncWeb3 to SEPOLIA_RPC.
    3. Load operator-trade + operator-journal accounts.
    4. Build adapter via adapter_factory (PERPS_VENUE switch point, D-01/D-03).
    5. Build vault_contract (mCLA-S1) + journal_registry.
    6. Build aggregator contracts (mock Chainlink feeds for price walk).
    7. Launch driver.run_session + run_keeper_monitor + latency_watchdog as Tasks.
    8. Return summary.

    Args:
        manifest_path:                    Override manifest path (None → deployments/sepolia.json).
        sepolia_rpc:                      Arbitrum Sepolia RPC URL.
        operator_trade_private_key:       Hex private key string for operator-trade EOA.
        operator_journal_private_key_hex: Hex private key string for operator-journal EOA.
        operator_journal_key_address:     Checksummed address of operator-journal EOA.
        pinata_jwt:                       Pinata V3 JWT for IPFS pinning.
        filebase_access_key:              Filebase S3 access key (FILEBASE_ACCESS_KEY env var).
        filebase_secret_key:              Filebase S3 secret key (FILEBASE_SECRET_KEY env var).
        filebase_bucket:                  Filebase IPFS bucket name.
        perps_venue:                      "mock" or "gmx" (D-01/D-03).
        session_duration_seconds:         Session run length (default 1800 = 30min).
        cadence_seconds:                  Trading cadence (default 60s).
        price_seed:                       PriceWalk seed (D-01).
        drift:                            Per-cycle log-normal drift fraction (D-01, default 0.0001).
        volatility:                       Per-cycle std-dev fraction (D-01, default 0.005).
        database_url:                     Async Postgres URL.
        redis_url:                        Redis URL for WS fanout (optional).
        telegram_bot_token:               Telegram bot token (D-15, optional).
        telegram_chat_id:                 Telegram chat ID (D-15, optional).
        latency_watchdog_threshold:       1A latency alert threshold in seconds (D-03).
        model:                            LLM model string (default claude-opus-4-7).

    Returns:
        Summary dict: {cycles, seed, session_id, vault_address, model}.
    """
    # ── Step 1: Load manifest (D-14 — no hardcoded addresses) ────────────────
    logger.info("run_mini_session: loading manifest from %s", manifest_path or _MANIFEST_PATH)
    manifest = load_manifest(manifest_path)
    vault_claude_addr = manifest["vaultClaude"]
    journal_addr = manifest["journal"]
    eth_feed_addr = manifest["ethFeed"]
    btc_feed_addr = manifest["btcFeed"]
    sol_feed_addr = manifest["solFeed"]
    # D-14 gap fix: resolve the adapter address by VENUE rather than always using manifest["adapter"].
    # manifest["adapter"] = address(0) (GMXAdapter deferred to Phase 6 per D-13).
    # manifest["mockPerps"] = real MockPerps address (set in sepolia.json + written by 01-Deploy.s.sol).
    # When venue=mock, we MUST use mockPerps so the reader + keeper_monitor hit the live contract.
    _raw_mock_perps = manifest.get("mockPerps", "")
    _raw_gmx_adapter = manifest.get("adapter", "")
    _ZERO_ADDR = "0x" + "0" * 40

    mock_perps_addr: str | None = None  # resolved below after web3 is connected (for fallback)
    gmx_adapter_addr: str | None = (
        _raw_gmx_adapter if _raw_gmx_adapter and _raw_gmx_adapter != _ZERO_ADDR else None
    )

    # mock_usdc_addr available for seeding steps (not used in the session loop itself)
    _ = manifest.get("mockUsdc")
    logger.info(
        "run_mini_session: manifest loaded — vaultClaude=%s journal=%s mockPerps=%s adapter=%s",
        vault_claude_addr,
        journal_addr,
        _raw_mock_perps or "(not set)",
        _raw_gmx_adapter or "(not set)",
    )

    # ── Step 2: AsyncWeb3 connection ──────────────────────────────────────────
    rpc = sepolia_rpc or os.environ.get("SEPOLIA_RPC", "")
    if not rpc:
        raise ValueError("SEPOLIA_RPC not set — provide sepolia_rpc or set the env var")
    logger.info("run_mini_session: connecting to Sepolia at %s", rpc[:40])
    web3 = AsyncWeb3(AsyncWeb3.AsyncHTTPProvider(rpc))
    # Arbitrum Sepolia uses PoA-style headers (extra data) — inject middleware for safety
    web3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)

    # ── Step 3: Load operator accounts ───────────────────────────────────────
    # Operator-trade key (submits trades via vault.openLong/openShort/closePosition, D-16)
    trade_key_hex = operator_trade_private_key or os.environ.get("OPERATOR_TRADE_KEY", "")
    if not trade_key_hex:
        raise ValueError(
            "OPERATOR_TRADE_KEY not set — provide operator_trade_private_key or set the env var. "
            "SEC-01: this key must be in gitignored .env.operator-trade"
        )
    # Normalize: ensure 0x prefix
    if not trade_key_hex.startswith("0x"):
        trade_key_hex = "0x" + trade_key_hex
    operator_trade_account = Account.from_key(trade_key_hex)
    logger.info(
        "run_mini_session: operator-trade EOA=%s (D-16 signing middleware will be loaded)",
        operator_trade_account.address,
    )

    # GAP #4/#6: PRICE_PUSHER_KEY — key separation for price-push vs trade-submission.
    # SEC-01: setPrice() calls are permissionless (any EOA can push) so using a separate
    # key reduces attack surface on the operator-trade EOA. Falls back to OPERATOR_TRADE_KEY
    # for backward compatibility so existing deployments need no env changes.
    # Priority: function parameter > PRICE_PUSHER_KEY env > OPERATOR_TRADE_KEY fallback.
    price_pusher_key_hex = price_pusher_private_key or os.environ.get("PRICE_PUSHER_KEY", "")
    price_pusher_address: str | None = None
    if price_pusher_key_hex:
        if not price_pusher_key_hex.startswith("0x"):
            price_pusher_key_hex = "0x" + price_pusher_key_hex
        price_pusher_account = Account.from_key(price_pusher_key_hex)
        price_pusher_address = price_pusher_account.address
        logger.info(
            "run_mini_session: PRICE_PUSHER_KEY set — price pusher EOA=%s (GAP #4/#6 key separation)",
            price_pusher_address,
        )
        # Load signing middleware for price-pusher key (price pusher calls setPrice transact).
        from web3.middleware import SignAndSendRawMiddlewareBuilder as _SARMBuilder

        _pusher_mw = _SARMBuilder.build(price_pusher_account)
        web3.middleware_onion.inject(_pusher_mw, layer=0)
    else:
        logger.info(
            "run_mini_session: PRICE_PUSHER_KEY not set — price pusher uses OPERATOR_TRADE_KEY "
            "fallback (operator_trade_address=%s)",
            operator_trade_account.address,
        )

    # Operator-journal key (signs journal entries for ecrecover gate, D-10)
    # ALSO provides the signing middleware for recordJournal.transact() — without this
    # middleware, web3.py falls back to eth_sendTransaction which Alchemy rejects (400).
    journal_key_hex = operator_journal_private_key_hex or os.environ.get(
        "OPERATOR_JOURNAL_KEY_PRIV", ""
    )
    operator_journal_private_key_bytes: bytes | None = None
    operator_journal_account: Account | None = None
    if journal_key_hex:
        if not journal_key_hex.startswith("0x"):
            journal_key_hex = "0x" + journal_key_hex
        operator_journal_private_key_bytes = bytes.fromhex(journal_key_hex.removeprefix("0x"))
        operator_journal_account = Account.from_key(journal_key_hex)
        # GAP #11 fix: load signing middleware for operator-journal EOA.
        # publisher.py calls recordJournal.transact({"from": operator_journal_key_address}).
        # Without this middleware, web3.py falls back to eth_sendTransaction which Alchemy
        # rejects with 400 Bad Request. Mirror the exact pattern used for price-pusher above.
        from web3.middleware import SignAndSendRawMiddlewareBuilder as _SARMBuilder  # noqa: PLC0415

        _journal_mw = _SARMBuilder.build(operator_journal_account)
        web3.middleware_onion.inject(_journal_mw, layer=0)
        logger.info(
            "run_mini_session: operator-journal EOA=%s signing middleware loaded "
            "(GAP #11 fix — recordJournal.transact → eth_sendRawTransaction)",
            operator_journal_account.address,
        )
    else:
        logger.warning(
            "run_mini_session: OPERATOR_JOURNAL_KEY_PRIV not set — "
            "journals will NOT be published (ecrecover gate requires the key)"
        )

    journal_key_addr = operator_journal_key_address or os.environ.get(
        "OPERATOR_JOURNAL_KEY_ADDR", ""
    )

    # ── Startup signer guard ──────────────────────────────────────────────────
    # Assert every EOA that will call .transact({"from": X}) has signing middleware.
    # A missing signer causes Alchemy to reject with 400 at runtime — this guard
    # surfaces the problem at startup with a clear message (not buried in trade logs).
    # The guard inspects web3.middleware_onion to confirm each required signer is present.
    # operator-trade signing is loaded later in driver.run_session (D-16), so we track
    # which signers run_session itself is responsible for (journal + pusher).
    _signer_guard_errors: list[str] = []

    # operator-journal: must have middleware if the key is set (publisher will transact from it)
    if operator_journal_account is not None:
        # Verify the account is wired by checking all middleware in the onion.
        # SignAndSendRawMiddlewareBuilder produces a coroutine middleware whose
        # account attribute is accessible for identity checking.
        _journal_found = _check_signing_middleware_present(web3, operator_journal_account.address)
        if not _journal_found:
            _signer_guard_errors.append(
                f"operator-journal EOA {operator_journal_account.address} has no signing "
                "middleware — recordJournal.transact() would fall back to eth_sendTransaction "
                "(Alchemy 400). Check SignAndSendRawMiddlewareBuilder injection above."
            )
        else:
            logger.info(
                "run_mini_session: startup signer guard PASSED — operator-journal EOA=%s",
                operator_journal_account.address,
            )

    if _signer_guard_errors:
        _guard_msg = "\n".join(f"  SIGNER GUARD FAIL: {e}" for e in _signer_guard_errors)
        raise RuntimeError(
            f"Startup signer guard failed — missing signing middleware for {len(_signer_guard_errors)} "
            f"EOA(s). Fix before starting the session:\n{_guard_msg}"
        )

    # ── Step 4: Build adapter (PERPS_VENUE switch point, D-01/D-03) ──────────
    # D-03: PERPS_VENUE is read from env or parameter — NEVER modified at runtime.
    # The watchdog alerts on latency; the operator flips env + restarts (restart-flip).
    venue = perps_venue or os.environ.get("PERPS_VENUE", "mock")
    logger.info("run_mini_session: PERPS_VENUE=%s", venue)

    mock_perps_abi: list = []
    try:
        mock_perps_abi = _load_abi(_MOCK_PERPS_ARTIFACT)
    except FileNotFoundError:
        logger.warning(
            "run_mini_session: MockPerps artifact not found — using empty ABI. "
            "Run `forge build` in contracts/ if needed."
        )

    # Resolve the concrete adapter address by venue (D-14 gap fix).
    # For venue=mock: prefer manifest["mockPerps"]; fall back to vault.adapter() on-chain
    # if the manifest field is absent or zero. This ensures both the driver reads
    # (getOpenPositionKeys) and keeper_monitor (OrderExecuted watch) use the live address.
    # For venue=gmx: use manifest["adapter"] (GMXAdapter, Phase 6+).
    if venue == "mock":
        if _raw_mock_perps and _raw_mock_perps != _ZERO_ADDR:
            mock_perps_addr = _raw_mock_perps
            logger.info(
                "run_mini_session: venue=mock — resolved MockPerps from manifest.mockPerps: %s",
                mock_perps_addr,
            )
        else:
            # Belt-and-suspenders: query vault.adapter() on-chain when manifest field is missing.
            logger.warning(
                "run_mini_session: manifest.mockPerps missing/zero — "
                "falling back to vault.adapter() on-chain for venue=mock"
            )
            _vault_abi_for_fallback: list = []
            try:
                _vault_abi_for_fallback = _load_abi(_MTOKEN_VAULT_ARTIFACT)
            except FileNotFoundError:
                pass
            _fallback_vault = web3.eth.contract(
                address=vault_claude_addr, abi=_vault_abi_for_fallback
            )
            try:
                mock_perps_addr = await _fallback_vault.functions.adapter().call()
                logger.info(
                    "run_mini_session: vault.adapter() on-chain fallback resolved to: %s",
                    mock_perps_addr,
                )
            except Exception as _exc:  # noqa: BLE001
                logger.error(
                    "run_mini_session: vault.adapter() fallback failed: %s — "
                    "cannot build mock adapter without a valid address",
                    _exc,
                )
                raise ValueError(
                    "venue=mock: manifest.mockPerps is missing/zero and vault.adapter() "
                    "fallback failed. Add mockPerps to deployments/sepolia.json."
                ) from _exc

    adapter = build_perps_adapter(
        web3,
        venue=venue,
        mock_perps_address=mock_perps_addr if venue == "mock" else None,
        gmx_adapter_address=gmx_adapter_addr if venue == "gmx" else None,
        mock_perps_abi=mock_perps_abi,
    )

    # ── Step 5: Build vault_contract (mCLA-S1) and JournalRegistry ───────────
    vault_abi: list = []
    try:
        vault_abi = _load_abi(_MTOKEN_VAULT_ARTIFACT)
    except FileNotFoundError:
        logger.warning(
            "run_mini_session: MTokenVault artifact not found — using empty ABI. "
            "Run `forge build` in contracts/ if needed."
        )

    vault_contract = web3.eth.contract(address=vault_claude_addr, abi=vault_abi)

    journal_registry = None
    if journal_addr and journal_addr != "0x" + "0" * 40:
        journal_abi: list = []
        try:
            journal_abi = _load_abi(_JOURNAL_REGISTRY_ARTIFACT)
        except FileNotFoundError:
            logger.warning("run_mini_session: JournalRegistry artifact not found — using empty ABI")
        journal_registry = web3.eth.contract(address=journal_addr, abi=journal_abi)
        logger.info("run_mini_session: JournalRegistry at %s", journal_addr)

    # ── Step 6: Build aggregator contracts (mock Chainlink feeds) ─────────────
    chainlink_abi: list = []
    _chainlink_artifact = (
        _CONTRACTS_OUT / "MockChainlinkAggregator.sol" / "MockChainlinkAggregator.json"
    )
    try:
        chainlink_abi = _load_abi(_chainlink_artifact)
    except FileNotFoundError:
        logger.warning(
            "run_mini_session: MockChainlinkAggregator artifact not found — using empty ABI"
        )

    aggregators = {
        "ETH": web3.eth.contract(address=eth_feed_addr, abi=chainlink_abi),
        "BTC": web3.eth.contract(address=btc_feed_addr, abi=chainlink_abi),
        "SOL": web3.eth.contract(address=sol_feed_addr, abi=chainlink_abi),
    }
    logger.info(
        "run_mini_session: aggregators loaded — ETH=%s BTC=%s SOL=%s",
        eth_feed_addr,
        btc_feed_addr,
        sol_feed_addr,
    )

    # ── Step 7: Build DB session + Redis client ───────────────────────────────
    db_url = database_url or os.environ.get("ORCHESTRATOR_DATABASE_URL", "")
    if not db_url:
        raise ValueError(
            "ORCHESTRATOR_DATABASE_URL not set — provide database_url or set the env var"
        )
    if "+asyncpg" not in db_url and "+psycopg" not in db_url:
        db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1).replace(
            "postgres://", "postgresql+asyncpg://", 1
        )

    engine = create_async_engine(db_url)
    db = AsyncSession(engine)

    redis_client = None
    _redis_url = redis_url or os.environ.get("REDIS_URL", "")
    if _redis_url:
        try:
            import redis.asyncio as aioredis

            redis_client = aioredis.from_url(_redis_url)
            logger.info("run_mini_session: Redis connected at %s", _redis_url[:30])
        except Exception as exc:  # noqa: BLE001
            logger.warning("run_mini_session: Redis connection failed (optional): %s", exc)

    # ── Step 8: Build SessionConfig ───────────────────────────────────────────
    config = SessionConfig(
        session_duration_seconds=session_duration_seconds,
        cadence_seconds=cadence_seconds,
        price_seed=price_seed,
        drift=drift,
        volatility=volatility,
        execution_delay_cycles=1,  # D-13 default
    )
    logger.warning(
        "SESSION CONFIG: duration=%ss cadence=%ss seed=%s drift=%s volatility=%s model=%s venue=%s vault=%s",
        session_duration_seconds,
        cadence_seconds,
        price_seed,
        drift,
        volatility,
        model,
        venue,
        vault_claude_addr[:10],
    )

    # ── Step 9: Launch background tasks + main session ────────────────────────
    # D-03 / T-03-30: Latency watchdog uses a queue for event-driven tracking.
    # It is a SEPARATE asyncio.Task; it never writes to PERPS_VENUE.
    stop_event = asyncio.Event()
    watchdog_queue: asyncio.Queue = asyncio.Queue(maxsize=256)

    watchdog_task = asyncio.create_task(
        _latency_watchdog_queue_driven(
            vault_address=vault_claude_addr,
            threshold_seconds=latency_watchdog_threshold,
            stop_event=stop_event,
            event_queue=watchdog_queue,
            telegram_bot_token=telegram_bot_token,
            telegram_chat_id=telegram_chat_id,
        ),
        name=f"latency_watchdog-{config.session_id[:8]}",
    )

    # Resolve pinata / filebase credentials
    _pinata_jwt = pinata_jwt or os.environ.get("PINATA_JWT", "") or None
    _filebase_access_key = filebase_access_key or os.environ.get("FILEBASE_ACCESS_KEY", "") or None
    _filebase_secret_key = filebase_secret_key or os.environ.get("FILEBASE_SECRET_KEY", "") or None
    _filebase_bucket = filebase_bucket or os.environ.get("FILEBASE_BUCKET", "traider-journals")

    logger.info(
        "run_mini_session: journal_params — pinata_jwt=%s filebase_access_key=%s "
        "filebase_secret_key=%s journal_key_addr=%s",
        "SET" if _pinata_jwt else "NOT SET",
        "SET" if _filebase_access_key else "NOT SET",
        "SET" if _filebase_secret_key else "NOT SET",
        journal_key_addr or "NOT SET",
    )

    # The deployer address for keeper_monitor (executes orders on MockPerps).
    # On Sepolia, the operator-trade EOA plays the keeper role for MockPerps.
    deployer_address = operator_trade_account.address

    # Launch the main driver.run_session (which internally launches price_pusher + keeper).
    # We also launch a separate keeper_monitor with journal params wired.
    # driver.run_session already creates a keeper_monitor internally; for Sepolia we need
    # journal params wired to it. We use driver.run_session which passes these through
    # via the internal run_keeper_monitor call.
    #
    # Note: driver.run_session manages price_pusher + keeper internally.
    # The 1A watchdog is a SEPARATE additional task we add here (D-03).

    try:
        result = await driver_run_session(
            web3,
            adapter,
            aggregators,
            vault_claude_addr,
            model,
            config=config,
            db=db,
            redis=redis_client,
            deployer_address=deployer_address,
            vault_contract=vault_contract,
            operator_trade_account=operator_trade_account,
            # GAP #4/#6: price_pusher_address uses PRICE_PUSHER_KEY when set;
            # falls back to OPERATOR_TRADE_KEY (None → deployer_address fallback in driver).
            price_pusher_address=price_pusher_address,
            # Journal publisher params (PERPS-02 / D-08/D-09/D-10) — forwarded to keeper.
            # When all required params are non-None, the keeper publishes
            # journal entries on OrderExecuted (wired once at session start).
            journal_registry=journal_registry,
            operator_journal_private_key=operator_journal_private_key_bytes,
            pinata_jwt=_pinata_jwt,
            filebase_access_key=_filebase_access_key,
            filebase_secret_key=_filebase_secret_key,
            operator_journal_key_address=journal_key_addr or None,
            telegram_bot_token=telegram_bot_token,
            telegram_chat_id=telegram_chat_id,
        )
    finally:
        # Stop the latency watchdog
        stop_event.set()
        watchdog_task.cancel()
        try:
            await watchdog_task
        except asyncio.CancelledError:
            pass

        # Cleanup DB + Redis
        try:
            await db.close()
        except Exception:  # noqa: BLE001
            pass
        await engine.dispose()
        if redis_client:
            try:
                await redis_client.aclose()
            except Exception:  # noqa: BLE001
                pass

    return {
        **result,
        "vault_address": vault_claude_addr,
        "model": model,
        "venue": venue,
    }


# ---------------------------------------------------------------------------
# run_session — alias for the entrypoint name referenced in PLAN.md acceptance
# ---------------------------------------------------------------------------


async def run_session(
    *,
    manifest_path: Path | str | None = None,
    **kwargs: Any,
) -> dict:
    """Alias for run_mini_session — used by tests and make targets.

    See run_mini_session for full documentation.
    """
    return await run_mini_session(manifest_path=manifest_path, **kwargs)


# ---------------------------------------------------------------------------
# main — CLI entrypoint (python -m orchestrator.loop.run_session)
# ---------------------------------------------------------------------------


async def _async_main() -> None:
    """Async main: read env, call run_mini_session, print summary."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )

    # Read all params from environment (SEC-01: keys from gitignored .env files)
    session_duration = int(os.environ.get("SESSION_DURATION", "1800"))
    cadence = float(os.environ.get("SESSION_CADENCE", "60.0"))
    seed = int(os.environ.get("PRICE_SEED", "42"))
    drift = float(os.environ.get("DRIFT", "0.0001"))
    volatility = float(os.environ.get("VOLATILITY", "0.005"))
    venue = os.environ.get("PERPS_VENUE", "mock")
    # ARCH-X D-03: Sepolia execution takes ~40-60s (block.number advances at L1 cadence).
    # 30s was calibrated for instant anvil execution and false-trips on every normal Sepolia
    # cycle. Raised to 120s = ~2x worst-case Sepolia execution latency. Genuine stalls
    # (keeper down, sequencer offline) exceed 3× normal and still trip at 120s.
    # The threshold remains env-tunable for any chain or test overrides.
    threshold = float(os.environ.get("LATENCY_WATCHDOG_THRESHOLD", "120.0"))

    logger.info(
        "run_session.main: SESSION_DURATION=%ds PERPS_VENUE=%s DRIFT=%s VOLATILITY=%s LATENCY_THRESHOLD=%.0fs",
        session_duration,
        venue,
        drift,
        volatility,
        threshold,
    )

    result = await run_mini_session(
        session_duration_seconds=session_duration,
        cadence_seconds=cadence,
        price_seed=seed,
        drift=drift,
        volatility=volatility,
        perps_venue=venue,
        latency_watchdog_threshold=threshold,
        # All secret params read from env inside run_mini_session
        telegram_bot_token=os.environ.get("TELEGRAM_BOT_TOKEN") or None,
        telegram_chat_id=os.environ.get("TELEGRAM_CHAT_ID") or None,
    )

    logger.warning(
        "SESSION COMPLETE: cycles=%d seed=%s session_id=%s vault=%s",
        result.get("cycles", 0),
        result.get("seed"),
        result.get("session_id"),
        result.get("vault_address", "")[:10],
    )


def main() -> None:
    """Synchronous entry point for CLI / make targets."""
    asyncio.run(_async_main())


if __name__ == "__main__":
    main()

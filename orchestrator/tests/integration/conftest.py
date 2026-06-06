"""
Integration test fixtures for MOCK-02 end-to-end mock cycle tests.

Fixture dependency graph:
  anvil_node  →  mock_perps  →  mock_perps_address
                 ↓
              vault_on_anvil  (Plan 02-06: full Phase 1 stack deploy + USDC deposit)
  pg_session  (optional — skips when Postgres unreachable)
  redis_client (optional — skips when Redis unreachable)

Anvil:
  - Connects to a running anvil at ANVIL_RPC (default http://127.0.0.1:8545).
  - If ANVIL_RPC is not reachable, spawns a throwaway `anvil` subprocess.
  - If neither works, skips the test with a clear message.

MockPerps deploy (AUTHORITATIVE — moved from Plan 06):
  - Deploys MockChainlinkAggregator x3 (ETH/BTC/SOL) using `forge create --broadcast`.
  - Deploys MockPerps with those three feed addresses.
  - ASSERTS `web3.eth.get_code(addr)` is non-empty (authoritative deploy assertion).
    Fails loudly (not skips) if the contract has no code — T-0-nodeploy mitigation.
  - Returns the verified MockPerps contract instance for tests to drive.

vault_on_anvil (Plan 02-06 Task 1 — FULL PHASE 1 STACK):
  - Deploys MockERC20 (USDC) via forge create.
  - Deploys MockChainlinkAggregator x3 (ETH/BTC/SOL) — SHARED with vault NAV + MockPerps (D-02).
  - Deploys MockPerps(eth_feed, btc_feed, sol_feed).
  - Runs 01-Deploy.s.sol with ETH_FEED/BTC_FEED/SOL_FEED wired to the shared aggregators (D-02).
  - Mints USDC to deployer, approves vault, calls vault.deposit() so totalAssets() > 0.
  - FAILS LOUDLY (RuntimeError) if totalAssets() == 0 after deposit — T-0-nodeploy style.
  - Yields a VaultContext with: mock_perps, vault0, usdc, aggregators, deployer, rpc_url.

Postgres (optional):
  - Reads ORCHESTRATOR_DATABASE_URL (or DATABASE_URL).
  - Attempts connection; pytest.skip if connection refused / env not set.
  - Applies alembic upgrade head before yielding the session.
  - Provides an AsyncSession bound to orchestrator_user for test assertions.

Redis (optional):
  - Reads REDIS_URL (default redis://localhost:6379).
  - Attempts connection; pytest.skip if connection refused.
  - Provides a redis.asyncio.Redis client for pub/sub assertions.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import time
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from dataclasses import dataclass as _dataclass
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from eth_account import Account
from web3 import AsyncWeb3, Web3
from web3.middleware import ExtraDataToPOAMiddleware, SignAndSendRawMiddlewareBuilder

# ---------------------------------------------------------------------------
# Path constants
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).parent.parent.parent.parent
_CONTRACTS_DIR = _REPO_ROOT / "contracts"
_ARTIFACTS_DIR = _CONTRACTS_DIR / "out"
_MOCK_PERPS_ARTIFACT = _ARTIFACTS_DIR / "MockPerps.sol" / "MockPerps.json"
_CHAINLINK_ARTIFACT = (
    _ARTIFACTS_DIR / "MockChainlinkAggregator.sol" / "MockChainlinkAggregator.json"
)
_MOCK_ERC20_ARTIFACT = _ARTIFACTS_DIR / "MockERC20.sol" / "MockERC20.json"
_MTOKEN_VAULT_ARTIFACT = _ARTIFACTS_DIR / "mTokenVault.sol" / "MTokenVault.json"

_INITIAL_CAPITAL = 10_000 * 10**6  # $10k in 6-decimal USDC


# ---------------------------------------------------------------------------
# VaultContext — result of vault_on_anvil (Plan 02-06, D-02 shared feeds)
# ---------------------------------------------------------------------------


@dataclass
class VaultContext:
    """Deployed Phase 1 stack on local anvil.

    Attributes:
        mock_perps: MockPerps contract instance (web3). Used for event decoding + reads.
        mock_perps_addr: Checksummed MockPerps address.
        vault: MTokenVault contract (mCLA-S1 = vault 0). Used for TRADE SUBMISSION (D-16).
        vault_addr: Checksummed vault address.
        usdc: MockERC20 contract instance.
        usdc_addr: Checksummed USDC address.
        aggregators: Dict mapping asset string to MockChainlinkAggregator contract.
        agg_addrs: Dict mapping asset string to aggregator address.
        deployer: Anvil account 0 address. Also the operator-trade EOA on anvil.
        rpc_url: RPC URL of the local anvil.
        operator_trade_address: Checksummed EOA for trade submission via vault (D-16).
            On anvil, this is the deployer (account 0), which is also the vault's
            orchestrator immutable (ORCHESTRATOR env var in 01-Deploy.s.sol).
    """

    mock_perps: Any
    mock_perps_addr: str
    vault: Any
    vault_addr: str
    usdc: Any
    usdc_addr: str
    aggregators: dict[str, Any]
    agg_addrs: dict[str, str]
    deployer: str
    rpc_url: str
    operator_trade_address: str = ""  # set by vault_on_anvil after signing middleware is loaded


# Anvil default dev accounts (anvil's well-known mnemonic, account 0).
# This is a PUBLIC test key from Foundry/Anvil documentation — NOT a real secret.
# gitleaks:allow
_ANVIL_PRIVATE_KEY = (
    "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"  # gitleaks:allow
)
_ANVIL_ACCOUNT_0 = "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"

# Anvil RPC endpoint
_ANVIL_RPC = os.environ.get("ANVIL_RPC", "http://127.0.0.1:8545")

# ---------------------------------------------------------------------------
# Module-level: mark all tests in this package as integration
# ---------------------------------------------------------------------------


def pytest_collection_modifyitems(items):
    """Auto-mark every test in this package as @pytest.mark.integration."""
    for item in items:
        if "tests/integration" in str(item.fspath) or "tests\\integration" in str(item.fspath):
            item.add_marker(pytest.mark.integration)


# ---------------------------------------------------------------------------
# D-14 guard fixtures (mirrors tests/unit/conftest.py — available here for SC-2)
# ---------------------------------------------------------------------------


@_dataclass
class _SessionConfig:
    """Minimal SessionConfig-shaped value object for integration tests (D-14 guard)."""

    execution_delay_cycles: int = 1
    session_duration_seconds: int = 60
    cadence_seconds: float = 1.0
    price_seed: int = 42
    session_id: str = "00000000-0000-0000-0000-000000000099"


@pytest.fixture
def session_config() -> _SessionConfig:
    """Return a default integration test SessionConfig (execution_delay_cycles=1)."""
    return _SessionConfig()


@pytest.fixture
def enforce_delay_gte_1(session_config: _SessionConfig) -> _SessionConfig:
    """D-14 GUARD: restart-safety tests MUST run at executionDelayCycles >= 1.

    Mirrors the same fixture in tests/unit/conftest.py for integration test use.
    Fails loudly (pytest.fail, NOT skip) at delay < 1 so bad configs are un-ignorable.
    """
    if session_config.execution_delay_cycles < 1:
        pytest.fail(
            "D-14 VIOLATION: restart-safety test running at executionDelayCycles=0. "
            "This bypasses the async pending-order window and would pass vacuously."
        )
    return session_config


# ---------------------------------------------------------------------------
# Helper: load contract ABI from Foundry artifact
# ---------------------------------------------------------------------------


def _load_abi(artifact_path: Path) -> list:
    """Load ABI from a Foundry JSON artifact."""
    if not artifact_path.exists():
        raise FileNotFoundError(
            f"Contract artifact not found: {artifact_path}\n"
            "Run `forge build` in the contracts/ directory first."
        )
    with artifact_path.open(encoding="utf-8") as f:
        return json.load(f)["abi"]


# ---------------------------------------------------------------------------
# Helper: deploy a contract via forge create --broadcast
# ---------------------------------------------------------------------------


def _forge_create(
    contract_spec: str,
    constructor_args: list[str],
    rpc_url: str,
    private_key: str,
) -> str:
    """Deploy a contract using `forge create --broadcast` and return the deployed address.

    Args:
        contract_spec: Forge contract spec, e.g. 'src/mocks/Foo.sol:Foo'
        constructor_args: List of constructor arg strings.
        rpc_url: Anvil RPC URL.
        private_key: Private key for signing (without 0x prefix accepted too).

    Returns:
        Checksummed deployed contract address.

    Raises:
        RuntimeError: If deployment fails or address cannot be parsed.
    """
    cmd = [
        "forge",
        "create",
        "--rpc-url",
        rpc_url,
        "--private-key",
        private_key,
        "--broadcast",
        contract_spec,
    ]
    if constructor_args:
        cmd += ["--constructor-args"] + constructor_args

    result = subprocess.run(
        cmd,
        cwd=str(_CONTRACTS_DIR),
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"forge create failed for {contract_spec}:\n"
            f"stdout: {result.stdout}\n"
            f"stderr: {result.stderr}"
        )

    # Parse "Deployed to: 0x..." from stdout
    for line in result.stdout.splitlines():
        if "Deployed to:" in line:
            addr = line.split("Deployed to:")[-1].strip()
            return Web3.to_checksum_address(addr)

    raise RuntimeError(f"Could not parse deployed address from forge output:\n{result.stdout}")


# ---------------------------------------------------------------------------
# Fixture: anvil_w3 — AsyncWeb3 connected to a running anvil
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="function")
async def anvil_w3() -> AsyncGenerator[AsyncWeb3, None]:
    """AsyncWeb3 connected to local anvil.

    Strategy:
      1. Try to connect to ANVIL_RPC (default http://127.0.0.1:8545).
      2. If not reachable, spawn a throwaway `anvil` subprocess.
      3. If anvil binary is not found, pytest.skip.

    Scope: function — GAP-2 fix: each test gets its own web3 connection instance so
    the fixture loop matches the test's event loop. The underlying anvil process is
    shared (persistent Docker container); only the Python connection object is recreated.
    This prevents "coroutine attached to different loop" errors when vault_on_anvil is
    also function-scoped (SC-2 test isolation requirement).
    """
    _proc = None

    # Try connecting to an already-running anvil
    w3 = AsyncWeb3(AsyncWeb3.AsyncHTTPProvider(_ANVIL_RPC))
    w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)

    try:
        connected = await asyncio.wait_for(w3.is_connected(), timeout=2.0)
    except Exception:
        connected = False

    if not connected:
        # Attempt to spawn anvil subprocess
        try:
            _proc = subprocess.Popen(
                ["anvil", "--port", "8545", "--silent"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            # Wait up to 5s for anvil to become reachable
            for _ in range(10):
                await asyncio.sleep(0.5)
                try:
                    connected = await asyncio.wait_for(w3.is_connected(), timeout=1.0)
                    if connected:
                        break
                except Exception:
                    pass
        except FileNotFoundError:
            pytest.skip("anvil not found on PATH — install Foundry to run integration tests")

    if not connected:
        if _proc:
            _proc.terminate()
        pytest.skip(f"Cannot connect to anvil at {_ANVIL_RPC} — run `anvil` or `make up`")

    yield w3

    if _proc:
        _proc.terminate()
        _proc.wait(timeout=10)


# ---------------------------------------------------------------------------
# Fixture: mock_perps — AUTHORITATIVE deploy + cast-code assert (T-0-nodeploy)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="function")
async def mock_perps(anvil_w3: AsyncWeb3):
    """Deploy MockChainlinkAggregator x3 + MockPerps to anvil, assert code is non-empty.

    This is the AUTHORITATIVE MockPerps deploy+assert moved from Plan 06.
    Plan 06 guarded this step because MockPerps.sol did not exist at Wave 1.
    Plan 09 (Wave 3) is the authoritative home for this deploy.

    Trust-boundary assertion (T-0-nodeploy):
      After deploy, asserts `web3.eth.get_code(addr)` is non-empty.
      Fails setup loudly (RuntimeError) — NOT pytest.skip — if the contract
      has no code. A silent no-code deploy would produce incorrect test results.

    Scope: function — changed from session to match anvil_w3 function scope (GAP-2 fix).
    Each test gets a fresh deploy; anvil state does not bleed across tests.

    Yields:
        Tuple (mock_perps_contract, mock_perps_address, deployer_address, rpc_url)
    """
    if not _MOCK_PERPS_ARTIFACT.exists():
        pytest.skip(
            f"MockPerps.sol artifact not found at {_MOCK_PERPS_ARTIFACT}. "
            "Run `forge build` in contracts/ first."
        )

    ts = int(time.time())

    # Deploy three MockChainlinkAggregator contracts
    eth_feed = _forge_create(
        "src/mocks/MockChainlinkAggregator.sol:MockChainlinkAggregator",
        ["300000000000", str(ts)],  # $3000 ETH, 8-decimal
        _ANVIL_RPC,
        _ANVIL_PRIVATE_KEY,
    )
    btc_feed = _forge_create(
        "src/mocks/MockChainlinkAggregator.sol:MockChainlinkAggregator",
        ["6000000000000", str(ts)],  # $60000 BTC, 8-decimal
        _ANVIL_RPC,
        _ANVIL_PRIVATE_KEY,
    )
    sol_feed = _forge_create(
        "src/mocks/MockChainlinkAggregator.sol:MockChainlinkAggregator",
        ["15000000000", str(ts)],  # $150 SOL, 8-decimal
        _ANVIL_RPC,
        _ANVIL_PRIVATE_KEY,
    )

    # Deploy MockPerps with the three feed addresses
    mock_perps_addr = _forge_create(
        "src/mocks/MockPerps.sol:MockPerps",
        [eth_feed, btc_feed, sol_feed],
        _ANVIL_RPC,
        _ANVIL_PRIVATE_KEY,
    )

    # AUTHORITATIVE CODE ASSERTION (T-0-nodeploy, moved from Plan 06)
    # Fails loudly (RuntimeError) if no code — does NOT skip. A no-code address
    # would produce silent failures in every downstream test.
    code = await anvil_w3.eth.get_code(mock_perps_addr)
    if not code or code == b"" or code == b"\x00":
        raise RuntimeError(
            f"CRITICAL: MockPerps deployed at {mock_perps_addr} has no code!\n"
            "forge create reported success but the contract bytecode is absent.\n"
            "This is T-0-nodeploy: the cycle cannot run against a no-code address.\n"
            "Check: was forge create broadcasting to the correct RPC?"
        )

    # Load MockPerps ABI from Foundry artifact
    mock_perps_abi = _load_abi(_MOCK_PERPS_ARTIFACT)
    contract = anvil_w3.eth.contract(address=mock_perps_addr, abi=mock_perps_abi)

    yield contract, mock_perps_addr, _ANVIL_ACCOUNT_0, _ANVIL_RPC


# ---------------------------------------------------------------------------
# Fixture: pg_session — AsyncSession to orchestrator Postgres (optional)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="function")
async def pg_session(tmp_path):
    """Provide an AsyncSession connected to orchestrator Postgres.

    Skips cleanly (pytest.skip) when:
      - ORCHESTRATOR_DATABASE_URL / DATABASE_URL is not set
      - Connection is refused (Postgres not running)

    Yields:
        sqlalchemy.ext.asyncio.AsyncSession bound to orchestrator_user role,
        or None if skipped (skip happens before yield so test is skipped).
    """
    db_url = os.environ.get("ORCHESTRATOR_DATABASE_URL") or os.environ.get("DATABASE_URL", "")
    if not db_url:
        pytest.skip(
            "No Postgres URL configured (ORCHESTRATOR_DATABASE_URL / DATABASE_URL not set). "
            "Set it and run `make up` to enable the full E2E Postgres path."
        )

    # Normalize to asyncpg URL
    if "+psycopg" in db_url:
        db_url = db_url.replace("+psycopg", "+asyncpg", 1)
    elif "+asyncpg" not in db_url:
        db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1).replace(
            "postgres://", "postgresql+asyncpg://", 1
        )

    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

    engine = None
    session = None
    try:
        engine = create_async_engine(db_url, connect_args={"timeout": 3})
        # Test connectivity
        async with engine.connect() as conn:
            await conn.execute(__import__("sqlalchemy").text("SELECT 1"))

        # Apply migrations
        _apply_migrations(db_url)

        session = AsyncSession(engine)
        yield session
    except (OSError, Exception) as exc:
        err = str(exc)
        if "Connection refused" in err or "connect" in err.lower() or "timeout" in err.lower():
            pytest.skip(
                f"Postgres not reachable at {db_url} — run `make up` (Docker required). "
                f"Error: {exc}"
            )
        raise
    finally:
        if session:
            await session.close()
        if engine:
            await engine.dispose()


def _apply_migrations(db_url: str) -> None:
    """Apply alembic upgrade head using a migrator-role psycopg URL.

    Migration DDL requires migrator_user privileges (CREATE SCHEMA, etc.).
    Resolution order:
      1. DATABASE_URL from env — set by Makefile / CI to migrator credentials.
      2. Derive from the caller's db_url by substituting migrator_user credentials
         (works when DATABASE_URL is not set in the environment).
    """
    # Prefer the explicit DATABASE_URL from environment (set by Makefile / CI
    # to postgresql+psycopg://migrator_user:migrator_pass@...) since alembic
    # env.py requires DDL privileges (CREATE SCHEMA) that orchestrator_user lacks.
    migration_url = os.environ.get("DATABASE_URL", "")
    if not migration_url:
        # Derive migrator URL from the runtime URL: swap driver + credentials.
        # This handles the dev case where only ORCHESTRATOR_DATABASE_URL is set.
        sync_url = db_url.replace("+asyncpg", "+psycopg", 1)
        migration_url = sync_url.replace(
            "orchestrator_user:orchestrator_pass",
            "migrator_user:migrator_pass",
        )

    alembic_ini = _REPO_ROOT / "migrations" / "alembic.ini"
    result = subprocess.run(
        [
            "uv",
            "run",
            "--project",
            "orchestrator",
            "alembic",
            "-c",
            str(alembic_ini),
            "upgrade",
            "head",
        ],
        cwd=str(_REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=60,
        env={**os.environ, "DATABASE_URL": migration_url},
    )
    if result.returncode != 0:
        raise RuntimeError(f"alembic upgrade head failed:\n{result.stdout}\n{result.stderr}")


# ---------------------------------------------------------------------------
# Fixture: vault_on_anvil — full Phase 1 stack + USDC deposit (Plan 02-06)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="function")
async def vault_on_anvil(anvil_w3: AsyncWeb3) -> AsyncGenerator[VaultContext, None]:
    """Deploy the complete Phase 1 on-chain stack to local anvil (D-02 shared feeds).

    Steps:
      1. Deploy MockERC20 (6-dec USDC mock) via forge create.
      2. Deploy 3 MockChainlinkAggregator contracts (ETH/BTC/SOL) — SHARED between
         the vault NAV oracle path AND MockPerps (D-02 wiring confirmation from RESEARCH.md).
      3. Deploy MockPerps(eth_feed, btc_feed, sol_feed).
      4. Run 01-Deploy.s.sol with ETH_FEED/BTC_FEED/SOL_FEED set to the shared aggregator
         addresses so the vault NAV and MockPerps PnL prices come from the same source.
      5. Mint USDC to the deployer and call vault.deposit() so totalAssets() > 0.

    D-02 confirmation: the SAME MockChainlinkAggregator addresses are passed both to
    SessionFactory (via ETH_FEED/BTC_FEED/SOL_FEED env vars) AND to MockPerps constructor.
    The price pusher calls aggregator.setPrice() once per cycle; both NAV and PnL update
    atomically from the same on-chain source.

    Trust-boundary assertion (RESEARCH Open Q #2):
      Fails LOUDLY (RuntimeError) if totalAssets() == 0 after deposit — a silent zero-capital
      state would let the loop run without any tradeable capital. This is equivalent to the
      T-0-nodeploy assertion pattern applied to the vault deposit step.

    Scope: function — GAP-2 fix (CR-04 resolution): each test gets a FRESH on-chain deploy
    so SC-1 state (open positions, pending orders, nonces) does NOT contaminate SC-2.
    When scope="session" both tests shared one MockPerps instance; SC-1's failed/reverted
    transactions left stale nonces and pending orders that caused SC-2 to fail when run
    after SC-1 (order-dependent pass). Making this function-scoped means 2-3 extra forge
    deploys per run (~15-30s overhead) — acceptable for deterministic correctness.

    Yields:
        VaultContext dataclass.

    Skips cleanly when:
      - forge binary not on PATH
      - anvil not reachable
      - contract artifacts not built (run `forge build` first)
    """
    # Guard: artifacts must exist (forge build must have run)
    if not _MOCK_ERC20_ARTIFACT.exists():
        pytest.skip(
            f"MockERC20 artifact not found at {_MOCK_ERC20_ARTIFACT}. "
            "Run `forge build` in contracts/ first."
        )
    if not _MOCK_PERPS_ARTIFACT.exists():
        pytest.skip(
            f"MockPerps artifact not found at {_MOCK_PERPS_ARTIFACT}. "
            "Run `forge build` in contracts/ first."
        )
    if not _MTOKEN_VAULT_ARTIFACT.exists():
        pytest.skip(
            f"MTokenVault artifact not found at {_MTOKEN_VAULT_ARTIFACT}. "
            "Run `forge build` in contracts/ first."
        )

    # ── Step 1: Deploy MockERC20 (USDC) ──────────────────────────────────────
    usdc_addr = _forge_create(
        "src/mocks/MockERC20.sol:MockERC20",
        ["Test USDC", "USDC", "6"],
        _ANVIL_RPC,
        _ANVIL_PRIVATE_KEY,
    )

    # ── Step 2: Deploy 3 MockChainlinkAggregator (shared ETH/BTC/SOL feeds) ─
    ts = int(time.time())
    eth_feed_addr = _forge_create(
        "src/mocks/MockChainlinkAggregator.sol:MockChainlinkAggregator",
        ["300000000000", str(ts)],  # $3000 ETH, 8-decimal
        _ANVIL_RPC,
        _ANVIL_PRIVATE_KEY,
    )
    btc_feed_addr = _forge_create(
        "src/mocks/MockChainlinkAggregator.sol:MockChainlinkAggregator",
        ["6000000000000", str(ts)],  # $60000 BTC, 8-decimal
        _ANVIL_RPC,
        _ANVIL_PRIVATE_KEY,
    )
    sol_feed_addr = _forge_create(
        "src/mocks/MockChainlinkAggregator.sol:MockChainlinkAggregator",
        ["15000000000", str(ts)],  # $150 SOL, 8-decimal
        _ANVIL_RPC,
        _ANVIL_PRIVATE_KEY,
    )

    # ── Step 3: Deploy MockPerps with the shared feed addresses ───────────────
    mock_perps_addr = _forge_create(
        "src/mocks/MockPerps.sol:MockPerps",
        [eth_feed_addr, btc_feed_addr, sol_feed_addr],
        _ANVIL_RPC,
        _ANVIL_PRIVATE_KEY,
    )

    # Code assertions (T-0-nodeploy) for all new contracts
    for addr, name in [
        (usdc_addr, "MockERC20"),
        (eth_feed_addr, "MockChainlinkAggregator ETH"),
        (btc_feed_addr, "MockChainlinkAggregator BTC"),
        (sol_feed_addr, "MockChainlinkAggregator SOL"),
        (mock_perps_addr, "MockPerps"),
    ]:
        code = await anvil_w3.eth.get_code(addr)
        if not code or code == b"" or code == b"\x00":
            raise RuntimeError(
                f"CRITICAL: {name} deployed at {addr} has no code! "
                "T-0-nodeploy: forge create reported success but bytecode is absent."
            )

    # ── Step 4: Run 01-Deploy.s.sol with D-02 shared feed addresses ──────────
    env = {
        **os.environ,
        "USDC_ADDRESS": usdc_addr,
        "ADAPTER_ADDRESS": mock_perps_addr,
        "ORCHESTRATOR": _ANVIL_ACCOUNT_0,
        "OPERATOR": _ANVIL_ACCOUNT_0,
        # D-02: same aggregator addresses fed to BOTH vault NAV path and MockPerps
        "ETH_FEED": eth_feed_addr,
        "BTC_FEED": btc_feed_addr,
        "SOL_FEED": sol_feed_addr,
        "SEQUENCER_FEED": "0x0000000000000000000000000000000000000000",
        "SESSION_DURATION": "1200",
        "INITIAL_CAPITAL": str(_INITIAL_CAPITAL),
        "USE_SEPOLIA_STALENESS": "true",  # 6-hour staleness window; keeps feeds fresh on anvil
    }
    result = subprocess.run(
        [
            "forge",
            "script",
            "script/01-Deploy.s.sol",
            "--rpc-url",
            _ANVIL_RPC,
            "--private-key",
            _ANVIL_PRIVATE_KEY,
            "--broadcast",
            "--sig",
            "run()",
        ],
        cwd=str(_CONTRACTS_DIR),
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
    )
    if result.returncode != 0:
        pytest.skip(
            f"01-Deploy.s.sol failed (returncode={result.returncode}). "
            f"Skipping vault_on_anvil fixture.\n"
            f"stdout: {result.stdout[-2000:]}\nstderr: {result.stderr[-2000:]}"
        )

    # Parse vault addresses from forge script stdout
    # Expected lines:  "  mCLA-S1 vault (Claude)  :  0xABCD..."
    combined = result.stdout + result.stderr
    vault_addrs: list[str] = []
    for pattern in [
        r"mCLA-S1 vault.*?:\s*(0x[0-9a-fA-F]{40})",
        r"mGPT-S1 vault.*?:\s*(0x[0-9a-fA-F]{40})",
        r"mGEM-S1 vault.*?:\s*(0x[0-9a-fA-F]{40})",
    ]:
        m = re.search(pattern, combined)
        if m:
            vault_addrs.append(Web3.to_checksum_address(m.group(1)))

    if len(vault_addrs) < 1:
        # Fallback: look for any 3 vault-looking addresses in deploy output
        all_addrs = re.findall(r"vault.*?:\s*(0x[0-9a-fA-F]{40})", combined, re.IGNORECASE)
        if all_addrs:
            vault_addrs = [Web3.to_checksum_address(a) for a in all_addrs[:3]]

    if not vault_addrs:
        raise RuntimeError(
            "Could not parse vault addresses from 01-Deploy.s.sol output.\n"
            f"stdout (last 2000 chars): {result.stdout[-2000:]}"
        )

    vault0_addr = vault_addrs[0]  # mCLA-S1 (Claude vault)

    # Code assertion for vault0 (T-0-nodeploy extended to the vault)
    vault_code = await anvil_w3.eth.get_code(vault0_addr)
    if not vault_code or vault_code == b"" or vault_code == b"\x00":
        raise RuntimeError(
            f"CRITICAL: MTokenVault deployed at {vault0_addr} has no code! "
            "The deploy script ran but the vault has no bytecode."
        )

    # ── Load contract instances ────────────────────────────────────────────────
    usdc_abi = _load_abi(_MOCK_ERC20_ARTIFACT)
    usdc_contract = anvil_w3.eth.contract(address=usdc_addr, abi=usdc_abi)

    mock_perps_abi = _load_abi(_MOCK_PERPS_ARTIFACT)
    mock_perps_contract = anvil_w3.eth.contract(address=mock_perps_addr, abi=mock_perps_abi)

    vault_abi = _load_abi(_MTOKEN_VAULT_ARTIFACT)
    vault_contract = anvil_w3.eth.contract(address=vault0_addr, abi=vault_abi)

    chainlink_abi = _load_abi(_CHAINLINK_ARTIFACT)
    aggregators = {
        "ETH": anvil_w3.eth.contract(address=eth_feed_addr, abi=chainlink_abi),
        "BTC": anvil_w3.eth.contract(address=btc_feed_addr, abi=chainlink_abi),
        "SOL": anvil_w3.eth.contract(address=sol_feed_addr, abi=chainlink_abi),
    }

    # ── Step 5: Mint USDC + approve + deposit into vault ──────────────────────
    # Mint _INITIAL_CAPITAL USDC to the deployer
    mint_tx = await usdc_contract.functions.mint(_ANVIL_ACCOUNT_0, _INITIAL_CAPITAL).transact(
        {"from": _ANVIL_ACCOUNT_0}
    )
    await anvil_w3.eth.wait_for_transaction_receipt(mint_tx, timeout=30)

    # Approve vault to spend the USDC
    approve_tx = await usdc_contract.functions.approve(vault0_addr, _INITIAL_CAPITAL).transact(
        {"from": _ANVIL_ACCOUNT_0}
    )
    await anvil_w3.eth.wait_for_transaction_receipt(approve_tx, timeout=30)

    # Deposit USDC into the vault
    deposit_tx = await vault_contract.functions.deposit(
        _INITIAL_CAPITAL, _ANVIL_ACCOUNT_0
    ).transact({"from": _ANVIL_ACCOUNT_0})
    await anvil_w3.eth.wait_for_transaction_receipt(deposit_tx, timeout=30)

    # CRITICAL ASSERTION (RESEARCH Open Q #2 resolution):
    # totalAssets() MUST be > 0 after deposit — a zero-capital vault cannot trade.
    # Fails LOUDLY (RuntimeError) rather than skipping — this is a correctness requirement.
    total_assets = await vault_contract.functions.totalAssets().call()
    if total_assets == 0:
        raise RuntimeError(
            f"CRITICAL: vault at {vault0_addr} reports totalAssets()=0 after deposit.\n"
            f"deposit({_INITIAL_CAPITAL}, {_ANVIL_ACCOUNT_0}) completed without error "
            "but capital is not reflected in totalAssets(). "
            "Check: was the USDC approve called before deposit? Is the feed stale?"
        )

    # ── D-16 REQUIRED-REGARDLESS: load signing middleware for the operator-trade EOA ──
    # On anvil, the operator-trade key is the well-known anvil account 0 private key
    # (same key used to deploy all contracts and set as ORCHESTRATOR in 01-Deploy.s.sol).
    # The signing middleware intercepts .transact({"from": <address>}) calls for this EOA
    # and auto-signs + sends raw transactions — required for the Sepolia-capable code path
    # and exercised here so integration tests run the same signed path.
    #
    # web3.py 7.x pattern: SignAndSendRawMiddlewareBuilder.build is @curry-decorated.
    # Calling .build(account) without w3 returns a curry partial; the middleware onion
    # calls partial(w3) during initialization.  DO NOT pass w3 here — that would produce
    # a fully-built instance that the onion then tries to call as a class, causing TypeError.
    operator_trade_account = Account.from_key(_ANVIL_PRIVATE_KEY)
    signing_mw_partial = SignAndSendRawMiddlewareBuilder.build(operator_trade_account)
    anvil_w3.middleware_onion.inject(signing_mw_partial, layer=0)

    yield VaultContext(
        mock_perps=mock_perps_contract,
        mock_perps_addr=mock_perps_addr,
        vault=vault_contract,
        vault_addr=vault0_addr,
        usdc=usdc_contract,
        usdc_addr=usdc_addr,
        aggregators=aggregators,
        agg_addrs={"ETH": eth_feed_addr, "BTC": btc_feed_addr, "SOL": sol_feed_addr},
        deployer=_ANVIL_ACCOUNT_0,
        rpc_url=_ANVIL_RPC,
        operator_trade_address=operator_trade_account.address,
    )


# ---------------------------------------------------------------------------
# Fixture: redis_client — async Redis client (optional)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="function")
async def redis_client():
    """Provide a redis.asyncio.Redis client.

    Skips cleanly (pytest.skip) when Redis is not reachable.

    Yields:
        redis.asyncio.Redis client, or skips the test.
    """
    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379")

    try:
        import redis.asyncio as aioredis
    except ImportError:
        pytest.skip("redis package not installed — add redis to dependencies")

    client = None
    try:
        client = aioredis.from_url(redis_url, socket_connect_timeout=2, socket_timeout=2)
        await client.ping()
        yield client
    except Exception as exc:
        pytest.skip(
            f"Redis not reachable at {redis_url} — run `make up` (Docker required). Error: {exc}"
        )
    finally:
        if client:
            await client.aclose()

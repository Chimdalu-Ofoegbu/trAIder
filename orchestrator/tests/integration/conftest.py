"""
Integration test fixtures for MOCK-02 end-to-end mock cycle tests.

Fixture dependency graph:
  anvil_node  →  mock_perps  →  mock_perps_address
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
import subprocess
import time
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
import pytest_asyncio
from web3 import AsyncWeb3, Web3
from web3.middleware import ExtraDataToPOAMiddleware

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
# Helper: load contract ABI from Foundry artifact
# ---------------------------------------------------------------------------


def _load_abi(artifact_path: Path) -> list:
    """Load ABI from a Foundry JSON artifact."""
    if not artifact_path.exists():
        raise FileNotFoundError(
            f"Contract artifact not found: {artifact_path}\n"
            "Run `forge build` in the contracts/ directory first."
        )
    with artifact_path.open() as f:
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


@pytest_asyncio.fixture(scope="session")
async def anvil_w3() -> AsyncGenerator[AsyncWeb3, None]:
    """AsyncWeb3 connected to local anvil.

    Strategy:
      1. Try to connect to ANVIL_RPC (default http://127.0.0.1:8545).
      2. If not reachable, spawn a throwaway `anvil` subprocess.
      3. If anvil binary is not found, pytest.skip.

    Scope: session — one anvil per test session (state accumulates across tests,
    which is desirable: earlier tests deploy MockPerps that later tests reuse).
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


@pytest_asyncio.fixture(scope="session")
async def mock_perps(anvil_w3: AsyncWeb3):
    """Deploy MockChainlinkAggregator x3 + MockPerps to anvil, assert code is non-empty.

    This is the AUTHORITATIVE MockPerps deploy+assert moved from Plan 06.
    Plan 06 guarded this step because MockPerps.sol did not exist at Wave 1.
    Plan 09 (Wave 3) is the authoritative home for this deploy.

    Trust-boundary assertion (T-0-nodeploy):
      After deploy, asserts `web3.eth.get_code(addr)` is non-empty.
      Fails setup loudly (RuntimeError) — NOT pytest.skip — if the contract
      has no code. A silent no-code deploy would produce incorrect test results.

    Scope: session — deployed once and reused across all integration tests.

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
    """Apply alembic upgrade head using a sync psycopg URL."""
    sync_url = db_url.replace("+asyncpg", "+psycopg", 1)
    alembic_ini = _REPO_ROOT / "migrations" / "alembic.ini"
    result = subprocess.run(
        ["uv", "run", "alembic", "-c", str(alembic_ini), "upgrade", "head"],
        cwd=str(_REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=60,
        env={**os.environ, "DATABASE_URL": sync_url},
    )
    if result.returncode != 0:
        raise RuntimeError(f"alembic upgrade head failed:\n{result.stdout}\n{result.stderr}")


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

# trAIder — Operational Runbook

**Version:** Phase 0 skeleton
**Last updated:** 2026-06-01 (Plan 00-07)
**Owner:** Operator

> **Note:** This is a living document. Each phase fills in its own sections
> when the corresponding capability lands. Phase 0 ships the skeleton and
> Known Issues log.

---

## Table of Contents

1. [Dev Stack Operations](#1-dev-stack-operations)
2. [Key Topology and Rotation](#2-key-topology-and-rotation)
3. [Alert Tiering](#3-alert-tiering)
4. [Journal Recovery](#4-journal-recovery)
5. [Session Start and Settlement](#5-session-start-and-settlement)
6. [Sepolia Deploy Operations](#6-sepolia-deploy-operations)
7. [Demo-Day Minute-by-Minute Timetable](#7-demo-day-minute-by-minute-timetable)
8. [Provider Rate Limits — ACTIVE (no application required) (ORCH-09)](#8-provider-rate-limits--active-no-application-required-orch-09)
9. [Judging Window (DEPLOY-04)](#9-judging-window-deploy-04)
10. [House-Arb Bot (Phase 4)](#10-house-arb-bot-phase-4--d-08d-09d-10)
11. [Known Issues and Gotchas](#11-known-issues-and-gotchas)

---

## 1. Dev Stack Operations

> **Filled in by:** Plan 00-06 (D-38 Makefile targets documented here)

### Prerequisites

- Docker Desktop installed and running
- Alchemy Arbitrum One archive RPC provisioned (`ARB_RPC` set in local `.env`)
- Four operator keys generated (`bash scripts/gen-keys.sh`)
- Foundry (`cast`, `forge`, `anvil`) installed via `foundryup`
- `uv` installed for Python tooling

### Quick Start

```bash
# 1. Start dev stack (postgres 16 + redis 7 + anvil fork + pgadmin)
make up

# 2. Seed the environment (idempotent — safe to re-run)
#    - Runs Alembic migrations (both schemas)
#    - Seeds 1M USDC + 1 ETH into each of the 4 operator addresses
#    - Writes funded addresses to .env.local
#    - Does NOT deploy MockPerps (deferred to 01-Deploy.s.sol — see "Seeding notes")
make seed

# 3. Assert all post-seed state is correct
make verify-stack

# 4. When done for the day
make down
```

> **No `make`?** GNU `make` is optional. On hosts without it (e.g. Windows git-bash) run the
> targets directly: `up` = `docker compose -f docker-compose.yml up -d --remove-orphans`,
> `seed` = `bash scripts/seed.sh`, `verify-stack` = `bash scripts/verify-stack.sh`,
> `down`/`reset` = the corresponding `docker compose down [--volumes] --remove-orphans`.

### Seeding notes

**USDC is seeded via `anvil_setStorageAt` on balance slot 9 — not `deal()`.**
forge-std `deal()` is a cheatcode that only mutates forge's in-process EVM; via `forge script`
it is never broadcast and does **not** persist to the live anvil container. So seed.sh writes the
Circle FiatToken balances mapping directly: `slot = keccak256(abi.encode(addr, uint256(9)))`.
This is a deliberate, documented supersession of the `T-0-seedslot`/Pattern 5 decision **for the
live-anvil dev stack** (that decision targeted `forge test --fork`, where `deal()` works).

- **Safety:** `verify-stack.sh` step [3/4] asserts balances via `balanceOf()` (the real accessor),
  so a wrong slot reads `0` and fails **loud** — the silent-mis-seed risk `T-0-seedslot` warned about
  is neutralised by the loud check.
- **Dependency:** slot 9 is correct for the current USDC (`0xaf88…`) at the pinned fork block.
  If `FORK_BLOCK` or the USDC implementation changes, re-verify the slot (verify-stack flags a mismatch).

**MockPerps is NOT deployed by `seed.sh`.** Its authoritative deploy is
`forge script contracts/script/01-Deploy.s.sol` (constructs MockPerps with the Chainlink feed args
and wires it into SessionFactory), run when a session/Phase 02 needs it. `verify-stack.sh` step [4/4]
therefore reports MockPerps as an explicit **`[DEFER]`** status (never a silent skip): `[OK]` if an
address is recorded and has code, loud `[FAIL]` if recorded-but-codeless, `[DEFER]` if not seed-deployed.

### Full Reset (nuclear option)

```bash
# Removes all volumes (wipes Postgres data), restarts, re-seeds, re-verifies
make reset
```

### Database Reset

```bash
# Drop + recreate traider database + run alembic upgrade head
make db-reset
```

### Type Generation

```bash
# Regenerate frontend/types/api.ts from backend OpenAPI schema
# Run after any change to backend/src/backend/ws/models.py
make gen-types
```

### pgadmin Web UI

Available at `http://localhost:5050` once `make up` completes.
Credentials: `admin@traider.local` / `traider`

### Service Ports (dev stack)

| Service  | Port | Connection string                                     |
| -------- | ---- | ----------------------------------------------------- |
| Postgres | 5432 | `postgresql://traider:traider@localhost:5432/traider` |
| Redis    | 6379 | `redis://localhost:6379`                              |
| Anvil    | 8545 | `http://localhost:8545` (chain ID 31337)              |
| pgadmin  | 5050 | `http://localhost:5050`                               |

---

## 2. Key Topology and Rotation

> **Filled in by:** Plan 00-05 (D-16/D-17/D-67/D-68)
> For full key topology details see `docs/KEY-TOPOLOGY.md`.

### Four-Key Topology (D-16)

| Role             | File                    | Used by                                       |
| ---------------- | ----------------------- | --------------------------------------------- |
| Deployer         | `.env.deployer`         | Foundry deploy scripts, SessionFactory        |
| Operator Trade   | `.env.operator-trade`   | Orchestrator — submits GMX/MockPerps orders   |
| Operator Journal | `.env.operator-journal` | JournalPublisher — signs IPFS CID + tradeHash |
| Gas              | `.env.gas`              | Keeper / arb bot gas subsidy                  |

All four files are gitignored. Compromise of one key yields zero information about the others (SEC-01).

### Key Generation

```bash
bash scripts/gen-keys.sh
```

### Mid-Session Key Rotation Procedure (D-68)

> **STUB — Filled in by Phase 6 once SessionFactory admin is implemented.**

Rotation protocol (pause-and-rotate via `SessionFactory` admin):

1. Detect compromise signal
2. Ledger-sign `SessionFactory.pauseSession(sessionId)` — mainnet only
3. Generate new key via `cast wallet new`
4. Fund new key
5. `SessionFactory.rotateOperatorKey(sessionId, role, newAddress)`
6. Update orchestrator `.env.<role>` with new key
7. `SessionFactory.unpauseSession(sessionId)`
8. Verify new key is operational (check balance + orchestrator log)

**RTO estimate:** ~15 minutes. Force-settle is the worst-case fallback.

---

## 3. Alert Tiering

> **Filled in by:** Phase 6 (Telegram bot implementation — D-52..D-55)
> Phase 0 documents the tiers; implementation is out of scope.

### CRITICAL (push override DND)

Conditions (any → page immediately):

- Orchestrator process down > 2 minutes
- All 3 models simultaneously paused
- Unexpected `SettlementContract` trigger
- Chainlink feed stale beyond threshold (1h per CONTRACTS-08)
- Arbitrum sequencer uptime feed drops

Action: inline "Acknowledge" button in Telegram alert. If not acknowledged in 10 minutes, repeat with escalating urgency (D-54).

### WARNING (normal push)

Conditions:

- Single model paused (any reason)
- Single journal entry enters `failed` state
- Indexer lag elevated (> 15s testnet / > 5s mainnet)
- NAV-AMM deviation above arb threshold while arb bot is healthy

### INFO (channel post, no push)

Conditions:

- Container auto-recover
- Session milestones (hour 24, 48, 72)
- Routine state transitions (session start, settlement kick-off)

**Note:** Public trade feed (D-55) posts only after on-chain confirmation. No operational alerts leak to the public Telegram channel.

---

## 4. Journal Recovery

> **Filled in by:** Phase 3 (JournalPublisher — D-21)
> Phase 0 documents the state machine; recovery implementation is out of scope.

### Journal State Machine (D-21)

```
pending_pin → pinned_primary → pinned_backup → signed → submitted → recorded
                                                                  ↘ failed
```

### Recovery Query

```sql
SELECT id, vault_address, order_key, state, attempt_count, last_error, created_at
FROM orchestrator.journal_entries
WHERE state NOT IN ('recorded', 'failed')
ORDER BY created_at ASC;
```

### Recovery Protocol (STUB)

1. Query entries in non-terminal states (above)
2. For `submitted` entries: check `onchain_tx` on-chain BEFORE resubmitting
3. Never blindly resubmit a `submitted` entry — the transaction may already be mined
4. Re-pin failed IPFS entries via backfill job once provider recovers
5. Phase 3 JournalPublisher implements automatic recovery via this query on startup

---

## 5. Session Start and Settlement

> **Filled in by:** Phase 6 (D-49/D-50/D-67/D-68) for 72h live session.
> **Phase-4 gate-run commands filled in by Plan 04-08 (Task 3).**

### Pre-Session Checklist (STUB — 72h live; see Phase-4 gate run below for gate session)

- [ ] All four operator keys funded (ETH for gas, USDC for capital)
- [ ] Ledger Nano X connected for mainnet deploy
- [ ] `make verify-stack` exits 0 on production stack
- [ ] Chainlink feeds confirmed live on target chain
- [ ] IPFS pinning service (Pinata) operational
- [ ] Telegram bot channels configured (private + public)
- [ ] Rate limits confirmed: Anthropic, OpenAI, Google all at hackathon-tier

### Session Lifecycle (STUB — 72h)

1. `SessionFactory.createSession(durationSeconds=259200)` — 72h
2. Each vault starts at NAV = 1.0 USDC/mTOKEN
3. Orchestrator begins 60-second decision cycles for each model
4. Settlement triggered at session end by keeper or operator
5. Speculators claim USDC proportional to vault performance via `SettlementContract.claim`

### Post-Settlement Teardown (STUB — 72h)

> Phase 6 fills in: archive journal entries, capture final NAV, emit final Telegram post.

---

### Phase-4 gate — one-command operator scripts

Three bash wrappers under `scripts/gate/` reduce live-session paste-friction.
Each script loads `.env`, `.env.deployer`, and `orchestrator/.env` automatically.
Run in this order:

```bash
# Step 1: Fund demo holder wallets with mock USDC (Sepolia only — MockERC20.mint)
#   Requires: HOLDER_CLAUDE_KEY, HOLDER_GPT_KEY, HOLDER_GEM_KEY, DEPLOYER_PRIVATE_KEY, SEPOLIA_RPC
#   Optional arg: raw USDC amount per holder (default 100000000 = 100 USDC)
bash scripts/gate/fund-holders.sh            # fund each holder with 100 USDC (default)
bash scripts/gate/fund-holders.sh 50000000   # fund each holder with 50 USDC

# Step 2: Pre-flight check (pools deployed, on-peg, keys funded, venue artifact present)
#   Delegates to: uv run --project orchestrator python -m gate.preflight
bash scripts/gate/preflight.sh

# Step 3: Launch the gate run
#   Delegates to: uv run --project orchestrator python -m gate.run_gate
#   GATE_DURATION and FIRE_THRESHOLD_BPS are exported with sensible defaults (3600s / 250bps)
bash scripts/gate/run-gate.sh --full-run
bash scripts/gate/run-gate.sh --full-run --step-through   # interactive narration
GATE_DURATION=2700 bash scripts/gate/run-gate.sh --full-run  # 45-min run
bash scripts/gate/run-gate.sh --dry-run      # in-memory fakes, no network
```

**Script sources:** `scripts/gate/fund-holders.sh`, `scripts/gate/preflight.sh`, `scripts/gate/run-gate.sh`

---

### Phase-4 Gate Run (04-08 Task 4) — REAL Commands

The Phase-4 gate is a mini-session (~45-60 min) that validates all 7 D-16 HARD criteria
before the 72h live session. Run these commands in order:

#### Step A: Deploy + seed (if not already done)

```bash
# Deploy contracts + seed pools (Phase-4 specific — pools + LP positions)
# Requires: SEPOLIA_RPC, DEPLOYER_PRIVATE_KEY, OPERATOR_LP_KEY, all env vars from .env.example
uv run --project orchestrator python -m gate.deploy_seed  # Phase-6 script; for now: manual forge script
# Alternatively run the 04-06 pool seeding script:
cd contracts && forge script script/04-SeedPools.s.sol --rpc-url $SEPOLIA_RPC --broadcast --private-key $DEPLOYER_PRIVATE_KEY
```

#### Step B: Pre-flight check

```bash
# Assert pools deployed, on-peg, mmAddress correct, keys funded, venue artifact present.
# Exits 0 on all PASS; exits 1 on any failure with details.
export SEPOLIA_RPC=<your-sepolia-rpc-url>
uv run --project orchestrator python -m gate.preflight
```

Expected output when ready:

```
[PASS] POOLS_EXIST: all 3 pools have on-chain code
[PASS] POOLS_ON_PEG: all 3 pools within 0.5% of NAV
[PASS] MM_ADDRESS_CORRECT: all vault mmAddresses == operatorLpKey
[PASS] ARB_KEY4_FUNDED: ARB_KEY4=0x...1 ETH balance=0.0500 ETH >= 0.010 ETH — OK
[PASS] OPERATOR_LP_FUNDED: OPERATOR_LP_KEY=0x...1 ETH balance=0.0500 ETH >= 0.010 ETH — OK
[PASS] HOLDER_USDC: HOLDER_*_KEY env vars not set — holder USDC check skipped
[PASS] VENUE_ARTIFACT: VENUE=V3 confirmed at .planning/phases/.../04-VENUE-DECISION.md
ALL CHECKS PASSED — gate run is ready to launch.
```

#### Step C: Set environment variables

```bash
# Gate timing + bot hysteresis (all env-overridable)
export GATE_DURATION=3600                # 1 hour gate session (default); use 2700 for 45min
export FIRE_THRESHOLD_BPS=250            # Probe-justified floor above Algebra max dynamic fee
export ARB_POLL_INTERVAL=12              # Arb bot poll cadence in seconds
export KEY4_USDC_MIN_WARNING=500000000   # Alert threshold for key #4 USDC (500 USDC raw)

# LLM API keys (required for live 3-model session)
export ANTHROPIC_API_KEY=<key>
export OPENAI_API_KEY=<key>
export GOOGLE_API_KEY=<key>

# Key material (from .env.* files — never committed)
export OPERATOR_TRADE_PRIVATE_KEY=<hex>
export OPERATOR_JOURNAL_KEY_PRIV=<hex>
export ARB_KEY4_PRIVATE_KEY=<hex>
export OPERATOR_LP_KEY_PRIVATE_KEY=<hex>
```

#### Step D: Launch the gate run

```bash
# Full automated run (3 models + arb bot + speculator sim + 8-step harness)
uv run --project orchestrator python -m gate.run_gate --full-run

# Interactive step-through (narrated demo — prompts between each harness step)
uv run --project orchestrator python -m gate.run_gate --full-run --step-through

# Custom gate duration
uv run --project orchestrator python -m gate.run_gate --full-run --gate-duration 2700

# Supply the venue artifact path explicitly (if using a non-default location)
uv run --project orchestrator python -m gate.run_gate --full-run \
  --nav-sim-result .planning/phases/04-multi-model-amm-arbitrage/04-VENUE-DECISION.md
```

#### Step E: Record the result in 04-GATE.md

The gate run prints an evidence dict on PASS. Copy it into `04-GATE.md`:

```bash
# Capture gate output to a file
uv run --project orchestrator python -m gate.run_gate --full-run 2>&1 | tee gate-run-output.txt
# Then paste the "Evidence dict" section from gate-run-output.txt into .planning/.../04-GATE.md
```

**Gate PASS criteria (all 7 D-16 HARD criteria):**

| Criterion | Description                                                                          |
| --------- | ------------------------------------------------------------------------------------ |
| (a)       | All 3 models ≥1 real open AND ≥1 real close                                          |
| (b)       | AMM pool globalState().price changed during the run                                  |
| (c)       | ≥1 synthetic gap closed by arbCloseGap in <60s                                       |
| (d)       | Clean settlement: all 3 vaults settled, distribute non-empty, operator_claimed=False |
| (e)       | 04-VENUE-DECISION.md exists with parseable VENUE: V2\|V3 line                        |
| (f)       | D-14 per-cycle fairness check passed                                                 |
| (g)       | One continuous run: no crash, no manual intervention                                 |

---

## 6. Sepolia Deploy Operations

> **Filled in by:** Plan 03-07 (DEPLOY-01, D-12/D-14) - 2026-06-06

### Sepolia Deploy Quick Reference

```bash
# Full Arbitrum Sepolia deploy (idempotent + Arbiscan auto-verify, DEPLOY-01)
# Set env vars first: SEPOLIA_RPC, ARBISCAN_API_KEY, DEPLOYER_PRIVATE_KEY,
#                     OPERATOR_JOURNAL_KEY, ORCHESTRATOR, OPERATOR
make deploy-sepolia

# On Windows git-bash (no make), run forge directly from contracts/:
cd contracts && \
  DEPLOY_MOCK_SUBSTRATE=true \
  USE_SEPOLIA_STALENESS=true \
  SEQUENCER_FEED=0x0000000000000000000000000000000000000000 \
  forge script script/01-Deploy.s.sol \
    --rpc-url $SEPOLIA_RPC \
    --broadcast \
    --verify \
    --etherscan-api-key $ARBISCAN_API_KEY \
    --private-key $DEPLOYER_PRIVATE_KEY \
    --sig "run()"

# Read the deployed addresses (populated after broadcast):
cat deployments/sepolia.json

# Reset manifest for a fresh session (undeploy guard - will re-deploy on next run):
make deploy-sepolia-clean
```

### Required Environment Variables for Sepolia Deploy (SEC-01)

The following must be set before `make deploy-sepolia`:

| Variable               | Source                                                    | Purpose                                                    |
| ---------------------- | --------------------------------------------------------- | ---------------------------------------------------------- |
| `SEPOLIA_RPC`          | Alchemy dashboard (Arbitrum Sepolia app)                  | RPC endpoint for broadcast + Arbiscan verify               |
| `ARBISCAN_API_KEY`     | https://arbiscan.io/apis (free tier)                      | Auto-verify source on Arbiscan                             |
| `DEPLOYER_PRIVATE_KEY` | `.env.deployer` (gitignored)                              | Signs and broadcasts deploy transactions                   |
| `OPERATOR_JOURNAL_KEY` | `.env.operator-journal` pubkey (address, not private key) | Becomes immutable in JournalRegistry (ecrecover gate)      |
| `ORCHESTRATOR`         | `.env.operator-trade` pubkey (address)                    | Stored in factory; only address permitted to call openLong |
| `OPERATOR`             | Operator EOA address                                      | Funds session; cannot withdraw vault USDC directly         |

**SEC-01 — Sepolia ETH funding requirements:**

All four EOAs need Sepolia ETH for gas. Faucet: https://faucets.chain.link (Chainlink-operated;
supports Arbitrum Sepolia). Alternative: https://www.alchemy.com/faucets/arbitrum-sepolia

| EOA              | Min Sepolia ETH | Notes                                             |
| ---------------- | --------------- | ------------------------------------------------- |
| Deployer         | 0.1 ETH         | Pays deploy gas for ~12 contracts + createSession |
| Operator Trade   | 0.05 ETH        | Pays gas for trade submissions via vault.openLong |
| Operator Journal | 0.02 ETH        | Pays gas for JournalRegistry.recordJournal calls  |
| Gas/Keeper       | 0.05 ETH        | Pays gas for executeOrder + arbitrage (Phase 4)   |

**Note:** WETH (wrapped ETH) is NOT needed for the mock-substrate Sepolia path. WETH is only
required for the GMX execution fee path (Phase 6, real GMX). The mock-live path uses only
plain Sepolia ETH for gas.

### Idempotency Behavior (D-14)

The deploy script reads `deployments/sepolia.json` before attempting a deploy. If `sessionFactory`
is the zero address (or the file is absent), it deploys and writes a fresh manifest. If
`sessionFactory` is non-zero, it skips all deploy steps and logs the existing addresses.

**Re-run is always safe.** A second `make deploy-sepolia` with the same manifest = no-op.

To deploy a fresh session (e.g., after TEST-03 for a demo re-run):

1. `make deploy-sepolia-clean` (resets manifest to all-zeros)
2. `make deploy-sepolia` (deploys fresh session)

### What Gets Deployed (D-12/D-13)

When `DEPLOY_MOCK_SUBSTRATE=true` (Sepolia default):

| Contract                   | Purpose                                  | Notes                                                               |
| -------------------------- | ---------------------------------------- | ------------------------------------------------------------------- |
| MockERC20 (6-dec)          | Mock USDC underlying (D-12)              | Freely mintable; operator mints to seed vault + demo speculators    |
| MockPerps                  | GMX-shape perps adapter (D-01)           | executionDelay=3 blocks; operator calls executeOrder                |
| MockChainlinkAggregator x3 | ETH/BTC/SOL price feeds (D-06)           | Seeded at $3500/$95000/$180; operator pushes updates via setPrice() |
| MockSequencerUptimeFeed    | Toggleable L2 sequencer feed (D-06/D-07) | Operator drills freeze/unfreeze via setDown()/setUp()               |
| PerformanceOracle          | Coliseum Score + vault stats             | Owned by SessionFactory                                             |
| JournalRegistry            | Per-trade IPFS CID registry              | OPERATOR_JOURNAL_KEY immutable ecrecover gate                       |
| SessionFactory             | One-tx 3-vault deploy                    | Owns oracle + journal                                               |
| MTokenVault x3             | mCLA-S1/mGPT-S1/mGEM-S1                  | mCLA-S1 driven (Claude); mGPT/mGEM idle                             |
| SettlementContract x3      | Per-vault settlement                     | Deployed inside createSession                                       |

**GMXAdapter:** NOT deployed on Sepolia. Its Phase 3 write path is deferred to Phase 6
per the D-13 condition (GMXAdapter was NOT frozen after Phase 3). The `adapter` field in
`deployments/sepolia.json` is `address(0)` until Phase 6.

### Arbiscan Verification (D-14)

The `--verify --etherscan-api-key $ARBISCAN_API_KEY` flags auto-submit source for every
deployed contract. The `[etherscan]` section in `contracts/foundry.toml` points to:

- URL: `https://api-sepolia.arbiscan.io/api`
- Chain ID: 421614 (Arbitrum Sepolia)

After the broadcast completes, forge prints Arbiscan links for each contract. Open each
and confirm the source shows a green verified checkmark. This is the judge-credibility step.

### D-05 Oracle-Outage Documented Limit

An oracle outage (Chainlink feed stale or sequencer down) **freezes** vault NAV, mint, and burn.
This is intentional: you cannot fairly price a redemption without a live price.

**Operator response to an oracle outage during a live session:**

1. The operator/factory may call `SessionFactory.endSession()` at any time to wind down early.
2. After `endSession()`, the SettlementContract's `settleRedemption()` uses the last NAV snapshot
   (oracle-independent once settled).
3. Holders may then call `SettlementContract.claim()` to redeem proportionally.

**Documented limits (v1 scope):**

- Holder mid-session exit during an oracle outage is out of v1 scope.
- The freeze is demonstrated by ticking a MockChainlinkAggregator stale in tests (not live).
- The operator wind-down path (endSession) is oracle-free and always available.

**Empty positions + oracle outage:** `positionValueUSDC()` returns 0 immediately (no Chainlink
call) when the vault has no open positions. This ensures `endSession()` + drain + settle succeeds
even during a Chainlink outage after all positions are closed (D-05 PLANNER CONSTRAINT).

### D-11 Pinata Gateway Latency Measurement

During the TEST-03 mini-session, measure the CID-fetch latency from both gateways:

```bash
# Pinata public gateway (default)
time curl -s "https://gateway.pinata.cloud/ipfs/<CID>" > /dev/null

# web3.storage / Filebase gateway (backup)
time curl -s "https://w3s.link/ipfs/<CID>" > /dev/null
```

Target: both fetches complete in <10 seconds (Phase 5 verifier target).

If Pinata public gateway latency consistently exceeds 10s, upgrade to a Pinata dedicated gateway
(operator decision #6 per D-11). The gateway URL is a config parameter in the JournalPublisher
(`PINATA_GATEWAY_URL` env var) — no code change needed, config-only swap.

**Decision gate:** Measure during TEST-03. Upgrade only if data shows latency threatens the
verifier target. Do not upgrade proactively (the free tier covers the 1h mini-session write volume).

---

## 7. Demo-Day Minute-by-Minute Timetable

> **Filled in by:** Phase 6 (D-56 - judging-window-dependent)

**Arbitrum Open House deadline:** June 14, 2026
**ETHGlobal London Phase 2:** July 10–12, 2026 (Founder House)

### T-Minus 24h (STUB)

- Start 24h stress test (doubles as demo recording)
- Verify all alert tiers are firing correctly
- Confirm Robinhood Chain testnet pre-flight (Chain ID 46630)
- Check pgadmin data quality

### Demo Day Script (STUB)

```
00:00 - Navigate to Coliseum view (3 models live)
00:30 - Show live NAV + AMM convergence chart
01:00 - Show real-time trade journal (IPFS-pinned)
02:00 - Run verifier CLI replay on one trade
03:00 - Connect wallet, show mTOKEN speculation
05:00 - End demo
```

---

## 8. Provider Rate Limits — ACTIVE (no application required) (ORCH-09)

> **Filled in by:** Plan 00-07 (Task 2 operator confirmation, 2026-06-01)
>
> **Status:** RESOLVED. All three providers are on spend-activated tiers, confirmed ACTIVE
> as of 2026-06-01. No support tickets or 2–5 day approvals were required. ORCH-09 CLOSED.

> **Model string reconciliation note (OpenAI):** Active model is `gpt-5.5-2026-04-23`.
> All live code and forward-looking docs have been reconciled to this string (2026-06-02).
> Historical SUMMARY/PLAN audit records intentionally preserved with original `gpt-5.1` references.

### Demo Sizing Rationale

- Decision cadence: **60 seconds** per model
- Demo session length: **3–4 hours** (sub-day by design; not a literal 72h run)
- Cycles per model per demo run: ~180–240 (3h = 180 cycles; 4h = 240 cycles)
- Binding constraint: **Gemini 3.1 Pro — 250 RPD daily cap**
- Target run length: **~3 hours (~180 cycles)** to preserve retry margin under the 250 RPD cap
- OpenAI daily cap (900K TPD) is also a binding cap at scale; 3–4h well within limit at ~180–240 cycles
- D-18 headroom math (original): 90 calls/hr peak + 50% headroom = 135/hr target — now moot because all
  tiers are already active at limits that comfortably cover the demo cadence

### Confirmed Provider Limits (Active as of 2026-06-01)

#### Anthropic — `claude-opus-4-7`

| Limit      | Value                      |
| ---------- | -------------------------- |
| RPM        | 50                         |
| Input TPM  | 500,000                    |
| Output TPM | 80,000                     |
| Daily cap  | None observed on this tier |

**Status:** ACTIVE (spend-activated tier). No application required.

#### OpenAI — `gpt-5.5-2026-04-23`

| Limit              | Value   |
| ------------------ | ------- |
| TPM                | 500,000 |
| RPM                | 500     |
| TPD (daily tokens) | 900,000 |

**Binding constraint for the demo:** 900K TPD daily token cap.

**Status:** ACTIVE (operator's own account). No application required.

**Model string note:** Active model is `gpt-5.5-2026-04-23`. Reconciled across all live code and forward-looking docs as of 2026-06-02.

#### Google — `gemini-3.1-pro-preview` (Paid Tier 1, activated via $15 deposit)

| Limit                | Value     |
| -------------------- | --------- |
| RPM                  | 25        |
| TPM                  | 2,000,000 |
| RPD (daily requests) | 250       |

**Binding constraint for the demo:** 250 RPD daily request cap. Target ~3h demo run
(~180 cycles) to preserve retry margin under this cap.

**Emergency fallback:** Gemini 3.5 Flash (1,000 RPM, 2M TPM, 10K RPD) — use ONLY if
retry pressure threatens the 250 RPD cap on Gemini 3.1 Pro. Fallback avoids disrupting
the "three frontier LLMs" thesis; the orchestrator chose Gemini 3.1 Pro as primary to
preserve that framing. Activate the fallback only under active pressure, not proactively.

**Status:** ACTIVE. No application required.

---

## 9. Judging Window (DEPLOY-04)

> **Filled in by:** Plan 00-07 (Task 2 operator confirmation, 2026-06-01)
>
> **Status:** CONFIRMED. Session timing decided; DEPLOY-04 CLOSED.

### Confirmed Arbitrum Open House London Dates

| Phase   | Description                         | Dates                  |
| ------- | ----------------------------------- | ---------------------- |
| Phase 1 | Buildathon (submission window)      | May 25 – June 14, 2026 |
| Phase 2 | Founder House (selected teams only) | July 10–12, 2026       |

**Source:** openhouse.arbitrum.io + Arbitrum blog (verified 2026-06-01)

### Operator Decision — CONFIRMED 2026-06-14

**Framing correction:** The 72-hour figure is the PROTOCOL's designed session length (per
project.md), not the demo run duration. The DEMO is a sub-day session: 3–4 hours at
60-second cadence, sized to stay under the tightest provider daily cap (Gemini 3.1 Pro
250 RPD). A 3–4h @ 60s run = 180–240 cycles/model; targeting ~3h (~180 cycles) to
preserve retry margin.

**Target window:** Phase 1 submission — **June 14, 2026 deadline**

**Demo session schedule:**

| Date                    | Event                                       |
| ----------------------- | ------------------------------------------- |
| June 8–9, 2026 (latest) | Run live demo session (3–4h, 60s cadence)   |
| June 10–12, 2026        | Buffer — re-run if first attempt has issues |
| June 14, 2026           | Hard submission deadline                    |

**Phase 2 (Founder House, Jul 10–12):** UPSIDE ONLY, contingent on team selection. If
selected, a fresh live session can run in July. Phase 6 timing does NOT depend on reaching
Phase 2. Do not anchor any Phase 6 deliverable on the Founder House window.

**Confirmed by:** 2026-06-14 (submission deadline; session scheduled June 8–9 latest)

### Session-Timing Math for Phase 6

Phase 6 session-timing plan inputs:

- Demo session length: 3–4 hours
- Cadence: 60 seconds per cycle
- Run date: **June 8–9 latest** (leaves buffer before deadline)
- Hard deadline: **June 14, 2026**
- Buffer window: June 10–12 (re-run capacity if first attempt fails)
- Binding rate cap: Gemini 3.1 Pro 250 RPD → target ~3h (~180 cycles) per run

---

## 10. House-Arb Bot (Phase 4) — D-08/D-09/D-10

> **Filled in by:** Plan 04-07
> The house-arb bot (`arb_bot.py`) is the Phase-4 peg-keeper that owns the <60s criterion #2 during the gate session.

### Overview

The bot is a single Python process that polls all 3 mTOKEN/USDC pools every 12 seconds, computes the NAV-vs-AMM gap for each vault, and fires `arbCloseGap` on key #4 when the gap exceeds the hysteresis floor (default **2.5%** / 250 bps). Sequential per-pool firing avoids key #4 nonce self-contention by construction (D-10).

> **Probe 1 note (04-08 Task 1):** The default `FIRE_THRESHOLD_BPS` was raised from 150 (1.5%) to **250 (2.5%)** — the probe-justified floor above Algebra Integral v1's maximum dynamic fee of 1.49% (alpha1+alpha2 = 14900 bps, baseFee = 0). This ensures the bot never fires inside the fee band. See `04-PROBE-RESULTS.md` Probe 1 and `04-VENUE-DECISION.md` for the full derivation.

**Key #4 is arb-only** — it is NEVER shared with the orchestrator-trade EOA. Compromise of key #4 is not a capital-drain vector: `arbCloseGap` is permissionless and the caller receives the arb profit.

### Running the Bot

```bash
# From the repo root (Windows: uv run via PowerShell or git-bash)
uv run --project orchestrator python -m orchestrator.loop.arb_bot

# With all env vars set:
export ARB_KEY4=<private-key-hex>
export ARB_POLL_INTERVAL=12
export FIRE_THRESHOLD_BPS=250
export KEY4_USDC_MIN_WARNING=500000000   # 500 USDC in raw units
export SEPOLIA_RPC=<your-rpc-url>
export TELEGRAM_BOT_TOKEN=<token>
export TELEGRAM_CHAT_ID=<chat-id>
```

### Environment Variables

| Variable                | Default     | Description                                                                                   |
| ----------------------- | ----------- | --------------------------------------------------------------------------------------------- |
| `ARB_KEY4`              | (required)  | Private key for arb bot key #4 (arb-only EOA, distinct from operator-trade)                   |
| `ARB_POLL_INTERVAL`     | `12`        | Poll cadence in seconds (D-09: 10–15s range)                                                  |
| `FIRE_THRESHOLD_BPS`    | `250`       | Hysteresis floor in bps (2.5%). Probe-justified floor above max Algebra dynamic fee (Probe 1) |
| `KEY4_USDC_MIN_WARNING` | `500000000` | Alert threshold in raw USDC units (default $500). Sent as WARNING on startup                  |
| `TELEGRAM_BOT_TOKEN`    | (optional)  | Telegram bot API token for WARNING/CRITICAL alerts (D-15)                                     |
| `TELEGRAM_CHAT_ID`      | (optional)  | Telegram chat ID to receive arb-bot alerts                                                    |

The vault/pool pair manifest (3 vault addresses + 3 pool addresses) is read from `deployments/sepolia.json` at runtime. The bot requires this file to exist and contain valid addresses (populated by `04-06` pool seeding).

### Key #4 Working-Capital Funding (D-08)

Key #4 needs two types of working capital:

1. **ETH gas:** Required for every `arbCloseGap` call. Fund at least **0.05 ETH Sepolia** at startup. Each arbCloseGap uses ~300k gas (Algebra swap + ERC-4626 round-trip). At 1 Gwei, this covers ~1,666 arb calls. Top up when the balance falls below 0.01 ETH.

2. **Mock USDC working capital (arbMint leg):** `arbMint` requires the bot to deposit USDC into the vault. On Sepolia, mint mock USDC directly:

```bash
# Mint mock USDC to key #4 (Sepolia only — MockERC20 is freely mintable)
cast send <MockUSDC> "mint(address,uint256)" <KEY4_ADDRESS> 10000000000 \
  --rpc-url $SEPOLIA_RPC --private-key $DEPLOYER_PRIVATE_KEY
# 10000000000 = 10,000 USDC in raw 6-decimal units
```

**Depletion alert (Pitfall 4):** At startup, `preflight_key4_balance` reads the key #4 USDC balance. If below `KEY4_USDC_MIN_WARNING` (default $500), a `WARNING` alert fires via the alert sink (Telegram if configured) before the first poll tick. Silent USDC depletion would disable gap-closing without any error — hence the proactive check.

**The depletion alert message will contain:**

```
[WARNING] Key #4 USDC balance <X> USDC is below the 500 USDC threshold — arbCloseGap may fail on the arbMint leg. Refund key4=0x...
```

### Monitoring and Overnight Alerting (D-09)

The bot sends alerts via the alert sink (D-15):

| Condition                    | Severity | Alert Message                                     |
| ---------------------------- | -------- | ------------------------------------------------- |
| USDC balance below threshold | WARNING  | "Key #4 USDC balance below threshold"             |
| Pool error (non-CB-pause)    | (log)    | ERROR in Python log only — continues to next pool |
| Bot process down > 2 min     | CRITICAL | Covered by Phase-6 process monitor (uptime check) |

Ongoing operator monitoring:

- Check Python log for `arb_poll_loop: arbCloseGap closed Nbps gap in X.Xs` entries — these confirm the <60s criterion is met.
- Check log for `ERROR arb_poll_loop` entries — these indicate per-pool failures (non-fatal, but warrant investigation).
- The `gap_log_callback` path (wired in the gate harness) records `{gap_bps, close_time_s, tx}` to the gate log for the <60s budget audit.

### CB-Pause Expected Behavior (D-07 / Pitfall 6)

When the circuit breaker is active (vault `_mintPaused = true`), the AMM price can drift ABOVE NAV because `arbMint` (the vault deposit leg of `arbCloseGap`) is blocked. In this state:

- The bot will attempt to call `arbCloseGap` for the AMM>NAV direction.
- The on-chain call will revert with **"Vault: mint paused"**.
- **This is EXPECTED and NOT alert-worthy** — it is the correct CB behavior per D-07.
- The bot classifies this revert as `expected_cb_pause` and logs at **INFO**, not ERROR.
- No WARNING/CRITICAL alert is fired.
- The bot continues to the next pool.

The `arbBurn` direction (AMM below NAV) is NOT blocked by the CB — vault redeem has no CB guard. So `arbCloseGap` continues to close downward gaps even during a CB episode.

**Operator signal:** If you see INFO logs with `expected_cb_pause` during a session, check whether the circuit breaker was intentionally triggered. If it was NOT intentionally triggered, investigate the NAV oracle (Chainlink staleness check may have latched the CB).

### Manual arbCloseGap Fallback (D-08 — First-Class Path)

The bot is the automated path. The manual `cast` fallback is documented here as a first-class operator tool for:

- Pre-gate demo testing
- Any window when the bot is not running
- Direct gap-close during the gate narration

**Exact command (argument order matters — vault THEN pool):**

```bash
cast send <ARBITRAGE_PRIMITIVE_ADDRESS> \
  "arbCloseGap(address,address)" \
  <VAULT_ADDRESS> \
  <POOL_ADDRESS> \
  --rpc-url $SEPOLIA_RPC \
  --private-key $ARB_KEY4
```

Where:

- `<ARBITRAGE_PRIMITIVE_ADDRESS>`: from `deployments/sepolia.json` field `arbitragePrimitive`
- `<VAULT_ADDRESS>`: from `deployments/sepolia.json` e.g. `vaultClaude` / `vaultGpt` / `vaultGemini`
- `<POOL_ADDRESS>`: from `deployments/sepolia.json` e.g. `poolClaude` / `poolGpt` / `poolGemini`
- `$ARB_KEY4`: private key for arb-only key #4 (`.env.gas` or a dedicated `.env.arb-key4`)

**Note:** This manual path is explicitly **not** a Phase-5 button (FRONT-04 ships in Phase 5). It is a raw operator cast command. Do NOT add authentication, UX hardening, or smart-contract access controls to this path — it is permissionless by design (D-08 guardrail).

**Example using the Python ArbitragePrimitive helper (alternative fallback):**

```python
# orchestrator/scripts/manual_arb_close.py (not a production entrypoint — drill only)
import asyncio
from web3 import AsyncWeb3

async def main():
    w3 = AsyncWeb3(AsyncWeb3.AsyncHTTPProvider(SEPOLIA_RPC))
    arb = w3.eth.contract(address=ARB_PRIMITIVE_ADDR, abi=ARB_PRIMITIVE_ABI)
    tx_hash = await arb.functions.arbCloseGap(VAULT_ADDR, POOL_ADDR).transact(
        {"from": KEY4_ADDRESS, "gas": 300000}
    )
    receipt = await w3.eth.wait_for_transaction_receipt(tx_hash, timeout=90)
    print(f"closed gap: tx={tx_hash.hex()}, status={receipt.status}")

asyncio.run(main())
```

### Phase-6 Extension Point (MAINNET_HOOK_PLACEHOLDER)

The bot has a deliberate extension point for Phase-6 mainnet economics:

```python
# In arb_bot.py — set this before deploying to mainnet:
MAINNET_HOOK_PLACEHOLDER = lambda gap_bps: (
    gap_bps > FIRE_THRESHOLD_BPS + 20  # only fire if gap > 1.7% (above gas-cost floor)
)
```

On Sepolia (testnet), `MAINNET_HOOK_PLACEHOLDER = None` (default) and the bot fires on every qualifying gap — gas is subsidized and economic analysis is not required. On mainnet, this hook can gate on gas price, profit estimate, or any other economic check. **Do not implement this in Phase 4** — leave as `None`.

---

## 11. Known Issues and Gotchas

> **Append-only log.** Add new issues in reverse chronological order (newest first).
> Never delete or edit existing entries — update by adding a new entry.
>
> **Note:** ORCH-09 rate-limit confirmations and DEPLOY-04 judging window confirmations
> are now tracked in sections 7 and 8 above.

---

### [2026-06-01] MockPerps deploy is GUARDED in Phase 0 (Plans 00-06 through 00-08)

**Context:** `make seed` and `make verify-stack` include a MockPerps deploy/assert step that is
gated behind `contracts/src/mocks/MockPerps.sol` existence.

**Status at Wave 1 (Plans 00-06, 00-07, 00-08):** MockPerps.sol is absent.
`make seed` skips the deploy and prints a notice. `make verify-stack` skips the cast-code
assertion and exits 0. This is expected — not a failure.

**Resolution:** Plan 00-08 (Wave 2) ships `MockPerps.sol`. Plan 00-09 (Wave 3) is the
authoritative deploy + `cast code` assertion. Until then, the guarded skip is correct.

**Reference:** `scripts/seed.sh` lines containing `MockPerps.sol` guard; PLAN.md Task 1 acceptance criteria.

---

### [2026-06-01] Arbitrum proxy USDC slot is NOT slot 0 — use deal() not anvil_setStorageAt

**Context:** Arbitrum canonical USDC (`0xaf88d065e77c8cC2239327C5EDb3A432268e5831`) is a
proxy contract. Its `balanceOf` storage slot is not slot 0 and differs from bridged USDC.e.

**Gotcha:** `anvil_setStorageAt` with a hand-computed slot 0 silently writes 0 bytes.
The address shows a non-zero storage slot but `balanceOf` reads back 0. No error is thrown.

**Fix:** Always use Foundry's `deal(USDC_ARBITRUM, addr, amount)` cheatcode in forge scripts.
`deal()` auto-detects the correct slot by scanning slot 0–19. `scripts/seed.sh` uses this
pattern (Pattern 5 from RESEARCH.md).

**Verification gate:** `make verify-stack` unconditionally asserts USDC balances read back
via `cast call USDC balanceOf(addr)`. Any wrong-slot write causes verify-stack to fail with a
non-zero exit code. This catches silent seed failures before development begins.

**Reference:** Foundry issue #2341; RESEARCH.md Pattern 5; `scripts/verify-stack.sh`.

---

### [2026-06-01] Docker Desktop required for make up/seed/verify-stack (live round-trip)

**Context:** `docker compose` is not installed in the development environment at Phase 0
launch. `make up`, `make seed`, and `make verify-stack` require Docker Desktop.

**Status:** All scripts and compose config are authored and correct. The live `make up &&
make seed && make verify-stack` round-trip is Docker-gated and will be exercised in
Plan 00-09 once Docker Desktop is installed.

**Action required:** Install Docker Desktop (`https://www.docker.com/products/docker-desktop`).

**Reference:** STATE.md BLOCKERS section; RESEARCH.md environment note.

---

### [2026-06-01] ARB_RPC must be provisioned before anvil fork tests

**Context:** `anvil --fork-url ${ARB_RPC} --fork-block-number ${FORK_BLOCK}` requires
an Arbitrum One archive RPC URL. Alchemy is the recommended provider (supports archive
queries needed for forking at block 353000000).

**Action required:** Create Alchemy account → Create App → Arbitrum One → copy HTTPS URL
into local `.env` as `ARB_RPC`.

**Reference:** `.env.example`; `docker-compose.yml` anvil service.

---

### [2026-06-01] Claude Opus 4.7 temperature parameter returns HTTP 400

**Context:** Anthropic's Claude Opus 4.7 uses adaptive sampling and REJECTS `temperature`
in the API request with HTTP 400.

**Fix for Phase 2 (orchestrator):** Omit `temperature` entirely from Claude API calls.
Reframe the verifier as "request payload matches IPFS pin" + "output was actually produced
by this model on date X" — not byte-exact replay.

**Verifier narrative:** Side-by-side diff, left=logged response, right=replayed response.
Claude verdict is `SEMANTIC_MATCH` (same decision payload, different rationale text).

**Reference:** CLAUDE.md Critical Spec Delta #1; D-75 verifier trinary verdict.

---

### [2026-06-01] OpenZeppelin v5 ERC-4626 requires \_decimalsOffset() = 12 for USDC vaults

**Context:** USDC has 6 decimals. OZ v5 ERC-4626 default `_decimalsOffset()` returns 0,
leaving inflation-attack defense disabled for low-decimal underlyings.

**Fix for Phase 1 (vault implementation):** Override `_decimalsOffset()` to return `12`.
This sets 10^12 virtual shares as the inflation-attack defense floor and makes
`1 USDC deposit ≈ 1 mTOKEN at initial NAV = 1.0`.

**Reference:** CLAUDE.md Critical Spec Delta #3; CONTEXT.md D-32; Phase 1 VAULT-01.

---

### [2026-06-01] GMX V2 requires execution fee sent via sendWnt() in same multicall

**Context:** GMX V2 ExchangeRouter requires the execution fee (WETH) to be sent via
`sendWnt()` in the SAME multicall as `createOrder`. Splitting them allows other callers
to claim the fee.

**Fix for Phase 3 (GMX adapter):** Build the multicall with `sendWnt()` + `createOrder`
atomically. Pre-fund each vault wallet with WETH (not raw ETH) for the execution fee.

**Reference:** CLAUDE.md Critical Spec Delta #5; CONTEXT.md D-38; Phase 3 GMX-01.

---

### [2026-06-01] Robinhood Chain testnet has no GMX V2 or Camelot V3 deployment

**Context:** Robinhood Chain testnet (Chain ID 46630) launched February 2026.
It has Chainlink Data Feeds + Data Streams but no production perp DEX or Camelot AMM.

**Plan:** Deploy `MockPerpsAdapter` on Robinhood Chain (same interface as GMX adapter,
operator-controlled mark prices). Frame as "mechanism live on Robinhood Chain testnet,
awaiting native perps venue." Chainlink feeds provide real NAV math.

**Reference:** CLAUDE.md Medium Risk Delta #6; CONTEXT.md D-35; Phase 6 RH-01.

---

### [2026-06-08] ARCH-X async-timing hardening — per-vault in-flight order gate

**Root cause (single root assumption, three defects):**
All three recent defects trace to a single root assumption: ANVIL-ONLY INSTANT-EXECUTION.

- **Defect 1** — `prompt 72h hardcode`: The LLM was told the session was 72 hours long
  (hardcoded string) regardless of actual session_duration_seconds. On anvil (instant block
  advancement) the prompt mismatch is invisible. On Sepolia (real time), the model always
  held because "so much time remaining" — trade-gap root cause. Fixed: session duration is
  now parameterized in the prompt.

- **Defect 2** — `clearTradingLock timing`: The keeper called clearTradingLock but it was
  wired AFTER the loop, so the vault's \_tradingLocked flag stayed true between the first trade
  and the next cycle. On anvil, blocks are instant so the next cycle happened before the flag
  mattered. On Sepolia, the 40-60s delay meant the second trade arrived before the lock was
  cleared — bricking the session. Fixed: keeper calls clearTradingLock immediately after
  OrderExecuted (VAULT-06).

- **Defect 3** — `in-flight submission collision` (this fix): On anvil, each cycle completes
  before the next one starts. On Sepolia, keeper execution takes ~40-60s. The driver
  submitted a new order over the in-flight one, hit "Vault: order in flight" revert, and
  crashed the session. Fixed by ARCH-X (this fix).

**Architecture-X posture (closed by this fix):**

The ARCH-X gate closes the per-vault order-submission coupling with a small guard:

1. **Submission gate (primary)**: Before writing the intent row, `run_live_cycle` calls
   `has_unresolved_pending_order(db, vault_address=vault)`. If any `intent` or `pending` row
   exists for this vault, the cycle is skipped with an INFO log: "order pending — skipping
   submit this cycle". The decision is made (LLM is called), but no order is submitted.

2. **Compare-and-set / single-owner**: The decision loop is the SOLE submitter per vault.
   The keeper only CLEARS (mark_pending_order_executed / mark_pending_order_reconciled).
   The cooperative event loop means no async yield occurs between the gate check and
   `record_pending_order(status='intent')` — the intent row IS the lock acquisition.
   No queue/queue-consumer refactor was needed.

3. **Belt-and-suspenders graceful catch**: Even if the gate is bypassed (extreme edge case),
   `.transact()` exceptions (including "Vault: order in flight" ContractLogicError) are caught,
   the intent row is reconciled, and the session continues. This was previously unhandled and
   crashed the session.

4. **Per-vault independence**: The gate is keyed by `vault_address`. While Vault A's order
   settles (~40-60s), Vaults B and C keep deciding/trading every cycle. No global lock.

5. **Latency watchdog** (D-03): Raised default threshold from 30s to 120s. The old 30s
   threshold was calibrated for instant anvil execution and false-tripped on every normal
   Sepolia cycle (~40-60s). 120s ≈ 2× worst-case Sepolia execution; genuine stalls
   (keeper down, sequencer offline) exceed 3× normal and still trip at 120s. Still
   env-tunable via LATENCY_WATCHDOG_THRESHOLD.

**Bounded cost**: A vault skips ~1 cycle per trade (the cycle during which the prior order
is executing). At 60s cadence and ~40-60s Sepolia execution, each vault trades roughly
every other cycle at worst. Aggregate demo activity is preserved by per-vault independence:
while one vault waits, the others continue.

**Y (full decouple via queue) = post-hackathon**: A full producer/consumer queue
decoupling the decision loop from the submission loop would eliminate the skip entirely.
Out of scope for the hackathon — the ARCH-X gate is sufficient for the demo window.

**Regression suite**: `orchestrator/tests/unit/test_arch_x_async_timing.py` covers all
six items: (a) gate skips, (b) graceful revert catch, (c) keeper resolution clears state,
(d) per-vault independence, (e) stale-lock/race (compare-and-set), (f) watchdog thresholds.

**Async path audit under ~40-60s confirmation (clean findings):**

- `reconcile_pending_orders` (ORCH-08): correct — checks on-chain pendingOrders(key_bytes)
  vault field; if non-zero, the order IS on-chain and the keeper will execute it; no
  resubmit. No instant-confirmation assumption.
- `journal completion`: the previous `pinned_primary`-only state was caused by the session
  crash (before Filebase backfill ran). With the gate preventing crashes, journal
  completion now runs fully. The publish_journal_entry call is already in a try/except
  in the keeper so failures don't crash the monitor.
- `wait_for_transaction_receipt(timeout=30)` on createOrder: the 30s timeout is for the
  TRANSACTION RECEIPT (tx landing in a block — seconds on Sepolia), NOT for keeper execution
  (40-60s). The two events are distinct. The driver's timeout is fine.
- No other instant-confirmation assumptions found in the decision/keeper/journal path.

---

### [2026-06-11] Systemic AMM price-decode scaling bug (1e12 vs 1e30) — core mechanism

**Context:** The sqrtPriceX96 → "USD per mTOKEN (1e18-scaled)" decode used scale factor `1e12`
where the correct factor is `1e30` (= `1e12` decimal gap [18-dec mTOKEN vs 6-dec USDC] × `1e18`
output scale). A _physically_ on-peg pool (1 mTOKEN = 1 USDC) was therefore read as ~100% off-peg.

**Why it survived to Day 17 (false confidence):** the bug was in the DEPLOYED `ArbitragePrimitive`
AND in `ArbitragePrimitive.t.sol` — the tests encoded the same wrong factor (on-peg sqrtP set to
`Q96/1000` / `1000*Q96` instead of the true `1e6*Q96` / `Q96/1e6`), so they passed while the math
was wrong. Surfaced when `02-SeedPools` reverted on its own (also-buggy) on-peg assertion during
the 2026-06-11 fresh redeploy — caught in `forge` simulation, nothing broadcast.

**Ground truth (Case B, USDC=token0):** `ammPriceE18 = 1e30·2^192/sqrtP²`; on-peg `sqrtP = 1e6·Q96`
→ decodes to exactly 1e18 (verified independently, 0 bps off). `arb_bot.decode_pool_price_e18` was
already correct and is the canonical reference; all other sites now match it.

**Fixed across all sites:** `ArbitragePrimitive.sol` (→ redeployed), `02-SeedPools.s.sol` (decode +
the two truncated 1:1 constants), `ArbitragePrimitive.t.sol` (on-peg values → ground truth),
`gate/harness.py` + `gate/preflight.py` (now call the canonical `arb_bot` decode, ordering-aware via
`token0()`). Added `test_decode_pool_price_e18_ground_truth_anchor` — a first-principles regression
anchor (`decode(on-peg)==1e18`) the buggy convention cannot pass. Also fixed
`preflight._check_mm_address` to read `vault.settlement().mmAddress()` (mmAddress lives on the
SettlementContract, not the vault).

**Reference:** memory `traider-live-preflight-2026-06-10`. The fresh redeploy resumed after this fix.

---

_End of Known Issues log. Append new entries above this line._

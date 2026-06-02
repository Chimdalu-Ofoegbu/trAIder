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
6. [Demo-Day Minute-by-Minute Timetable](#6-demo-day-minute-by-minute-timetable)
7. [Provider Rate Limits — ACTIVE (no application required) (ORCH-09)](#7-provider-rate-limits--active-no-application-required-orch-09)
8. [Judging Window (DEPLOY-04)](#8-judging-window-deploy-04)
9. [Known Issues and Gotchas](#9-known-issues-and-gotchas)

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
#    - GUARDED: deploys MockPerps if contracts/src/mocks/MockPerps.sol is present
make seed

# 3. Assert all post-seed state is correct
make verify-stack

# 4. When done for the day
make down
```

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

> **Filled in by:** Phase 6 (D-49/D-50/D-67/D-68)

### Pre-Session Checklist (STUB)

- [ ] All four operator keys funded (ETH for gas, USDC for capital)
- [ ] Ledger Nano X connected for mainnet deploy
- [ ] `make verify-stack` exits 0 on production stack
- [ ] Chainlink feeds confirmed live on target chain
- [ ] IPFS pinning service (Pinata) operational
- [ ] Telegram bot channels configured (private + public)
- [ ] Rate limits confirmed: Anthropic, OpenAI, Google all at hackathon-tier

### Session Lifecycle (STUB)

1. `SessionFactory.createSession(durationSeconds=259200)` — 72h
2. Each vault starts at NAV = 1.0 USDC/mTOKEN
3. Orchestrator begins 60-second decision cycles for each model
4. Settlement triggered at session end by keeper or operator
5. Speculators claim USDC proportional to vault performance via `SettlementContract.claim`

### Post-Settlement Teardown (STUB)

> Phase 6 fills in: archive journal entries, capture final NAV, emit final Telegram post.

---

## 6. Demo-Day Minute-by-Minute Timetable

> **Filled in by:** Phase 6 (D-56 — judging-window-dependent)

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

## 7. Provider Rate Limits — ACTIVE (no application required) (ORCH-09)

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

## 8. Judging Window (DEPLOY-04)

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

## 9. Known Issues and Gotchas

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

_End of Known Issues log. Append new entries above this line._

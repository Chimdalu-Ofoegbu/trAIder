# trAIder — Operational Runbook

**Version:** Phase 0 skeleton
**Last updated:** 2026-06-01 (Plan 00-06)
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
7. [Rate-Limit Applications (ORCH-09)](#7-rate-limit-applications-orch-09)
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

## 7. Rate-Limit Applications (ORCH-09)

> **Filled in by:** Plan 00-07 (D-18 — submit within 48h of Phase 0 start)
>
> **Status:** Justification drafted (Task 1). Awaiting operator submissions (Task 2).

### D-18 Math Summary

- Decision cycle: 60 seconds per model
- Session duration: 72 hours
- Peak calls per model: 3 models × 1 call/60s × 3600s/hr = **~60 calls/hr** sustained;
  accounting for retries and multi-turn requests, budget for **~90 calls/hr/model peak**
- Apply for the tier above **~135 calls/hr per model** (90 + 50% headroom)
- Submit within 48h of Phase 0 start (ASAP — Anthropic requires 2-5 business days)

### Reusable Application Justification

> Copy-paste this text into each provider's rate-limit or tier-increase request form.

```
Project: trAIder — AI Trading Performance Speculation Protocol
Use case: 72-hour autonomous trading session, hackathon submission for Arbitrum Open
  House (ETHGlobal London), three frontier LLMs (claude-opus-4-7, gpt-5.1,
  gemini-3.1-pro-preview) executing crypto perpetuals trading decisions on identical
  prompts with 60-second decision cycles.
Request: Elevated rate limit / tier upgrade to support ~135 calls/hr per model
  (90/hr sustained peak + 50% headroom for retries during a single 72-hour window).
Timeline: Session is live June 2026. Approval needed before session start.
Determinism note (Anthropic only): temperature parameter is intentionally omitted
  (Claude Opus 4.7 adaptive sampling; IPFS-journaled replay is semantic-match, not
  byte-exact).
```

Model strings (exact — use these on the console forms):

- Anthropic: `claude-opus-4-7`
- OpenAI: `gpt-5.1`
- Google: `gemini-3.1-pro-preview`

### Per-Provider Checklist

#### Anthropic

- [ ] **Submitted:** \_\_\_\_\_\_\_\_\_\_\_\_\_\_\_ (date, e.g. 2026-06-01)
- [ ] **Confirmation ID / ticket:** \_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_
- [ ] **Approved (estimated):** 2-5 business days after submission
- [ ] **Approved (actual):** \_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_

**Where to submit:** Anthropic Console → Account → Rate Limits → Request Increase

#### OpenAI

- [ ] **Submitted:** \_\_\_\_\_\_\_\_\_\_\_\_\_\_\_ (date)
- [ ] **Confirmation ID / ticket:** \_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_
- [ ] **Spend-gated tier auto-upgraded?** (yes / no) \_\_\_\_\_\_\_\_\_\_
- [ ] **Manual increase requested?** (yes / no) \_\_\_\_\_\_\_\_\_\_
- [ ] **Approved (actual):** \_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_

**Where to submit:** platform.openai.com → Settings → Limits → Request Increase

#### Google (Gemini)

- [ ] **Submitted:** \_\_\_\_\_\_\_\_\_\_\_\_\_\_\_ (date)
- [ ] **Confirmation ID / ticket:** \_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_
- [ ] **Paid tier enabled?** (yes / no) \_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_
- [ ] **Quota increase requested?** (yes / no) \_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_
- [ ] **Approved (actual):** \_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_

**Where to submit:** Google AI Studio → Settings → Billing & Quotas; or
Google Cloud Console → APIs & Services → Gemini API → Quotas → Request Increase

---

## 8. Judging Window (DEPLOY-04)

> **Filled in by:** Plan 00-07 (DEPLOY-04 — Phase 0 exit gate)
>
> **Status:** Event dates confirmed (public). Operator must select target window (Task 2).

### Confirmed Arbitrum Open House London Dates

| Phase   | Description                         | Dates                  |
| ------- | ----------------------------------- | ---------------------- |
| Phase 1 | Buildathon (submission window)      | May 25 – June 14, 2026 |
| Phase 2 | Founder House (selected teams only) | July 10–12, 2026       |

**Source:** openhouse.arbitrum.io + Arbitrum blog (verified 2026-06-01)

### Operator Decision Required

The 72-hour live session must be timed to overlap with judging. Choose ONE:

- **Option A — Phase 1 review (post-submission):** Session starts shortly after
  June 14 submission, during the review period. Judges can verify the live session
  is running or review recordings + onchain data. Reverse-computed session start:
  **~June 14–16, 2026** (exact timing TBD by operator).

- **Option B — Phase 2 Founder House (Jul 10–12):** Session runs DURING the
  Founder House event in London, providing a live on-screen demo. Requires team
  selection into Phase 2. Reverse-computed session start: **~July 9, 2026** (start
  72h before Founder House opens).

### Record Your Selection Here (OPERATOR — fill in after Task 2)

```
Target judging window:  [ ] Option A (post-Jun-14 review)
                        [ ] Option B (Jul 10-12 Founder House)

If Option A:
  Session start (target): ____-__-__ (YYYY-MM-DD)
  Session end (target):   ____-__-__ (+72h)

If Option B:
  Session start (target): 2026-07-09 (recommended)
  Session end (target):   2026-07-12

Confirmed by:        ____-__-__ (date operator made this decision)
Blocker (if not confirmed): confirmed-by: ____-__-__ (date by which decision is needed)
```

> **Phase 0 exit gate:** This section satisfies DEPLOY-04 when EITHER a confirmed
> window is recorded above OR a concrete "confirmed-by: \<date\>" blocker is logged
> in STATE.md. Leaving it blank does NOT satisfy the gate.

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

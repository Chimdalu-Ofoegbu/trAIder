# Key Topology (D-16 / D-17 / SEC-01)

## Overview

trAIder uses four independent EOAs — one per operational role — generated via
`cast wallet new` (NOT BIP-32 derived). This means compromise of any single
key yields **zero information** about the other three (SEC-01).

## Four-Key Layout

| Role | File | Variable | Used By | Purpose |
|------|------|----------|---------|---------|
| Deployer | `.env.deployer` | `DEPLOYER_PRIVATE_KEY` | Foundry deploy scripts, SessionFactory | One-time deploys; mainnet requires Ledger (see below) |
| Operator Trade | `.env.operator-trade` | `OPERATOR_TRADE_PRIVATE_KEY` | Orchestrator — submits GMX / MockPerps orders on behalf of each vault | Automated runtime signing per trade cycle |
| Operator Journal | `.env.operator-journal` | `OPERATOR_JOURNAL_PRIVATE_KEY` | JournalPublisher — signs IPFS CID + tradeHash for `JournalRegistry.recordJournal` | EIP-191 personal_sign on IPFS attestation payloads |
| Gas | `.env.gas` | `GAS_PRIVATE_KEY` | Keeper / gas-subsidy bot for arbitrage primitive and settlement | Pays execution fees for keeper actions |

## Service-to-Key Mapping

```
┌───────────────────────────────────────────────────────────┐
│  Service           │  Keys Loaded                        │
├────────────────────┼─────────────────────────────────────┤
│  orchestrator      │  operator-trade + operator-journal  │
│                    │  + gas                              │
├────────────────────┼─────────────────────────────────────┤
│  deploy scripts    │  deployer                           │
│  (Foundry)         │  (mainnet: Ledger Nano X)           │
├────────────────────┼─────────────────────────────────────┤
│  arb keeper bot    │  gas                                │
├────────────────────┼─────────────────────────────────────┤
│  backend / frontend│  no private keys                    │
└───────────────────────────────────────────────────────────┘
```

## Generation

Keys are generated via `cast wallet new` x4, independent calls:

```bash
bash scripts/gen-keys.sh
```

This script:
- Runs `cast wallet new` FOUR times (four independent keypairs, NOT one mnemonic)
- Writes each private key + address into its own `.env.<role>` file
- Skips any file that already exists (idempotent — never overwrites)
- Verifies all four files are gitignored before exiting

## Gitignore Coverage

All four `.env.<role>` files are covered by `.gitignore` since the project's
FIRST commit (Pitfall 5 / SEC-01 requirement). The `.gitignore` rule:

```
.env.*
!.env.example
```

This explicitly ignores `.env.deployer`, `.env.operator-trade`,
`.env.operator-journal`, and `.env.gas`.

Only `.env.example` (the schema template, no real values) is tracked.

## Out-of-Band Backup (REQUIRED)

Private keys must be backed up in a password manager immediately after
generation. The `.env.*` files are machine-local only — they are NOT backed
up by git, cloud sync, or any automated process.

Recommended: 1Password, Bitwarden, or hardware-encrypted USB.

## Mainnet Signing (D-67)

Mainnet deploy and settlement use a **Ledger Nano X** — not the `.env.*` files.

- `SessionFactory.deploy()` on Arbitrum One → Ledger Nano X via Frame / Ledger Live
- `SettlementContract.triggerSettlement()` on mainnet → Ledger Nano X
- `workflow_dispatch` mainnet CI job → requires GitHub Environment approval + Ledger

The four `.env.*` runtime keys are used for **automated signing only** (testnet
deploy, GMX order submission, journal attestation, keeper gas). They never hold
mainnet deployment authority on `arbitrum-mainnet` GitHub Environment.

## Mid-Session Key Rotation (D-68)

If a runtime key is suspected compromised during the 72h session:

1. Detect compromise signal (monitoring alert, unauthorized tx).
2. Ledger-sign `SessionFactory.pauseSession(sessionId)`.
3. Generate a replacement key via `cast wallet new` into the same `.env.<role>` file.
4. Fund the new key from the operator wallet.
5. Ledger-sign `SessionFactory.rotateOperatorKey(sessionId, role, newAddress)`.
6. Update the orchestrator `.env.*` file; restart the orchestrator.
7. Ledger-sign `SessionFactory.unpauseSession(sessionId)`.

Estimated RTO: ~15 minutes. Force-settle is the worst-case fallback.

See `docs/RUNBOOK.md` for the step-by-step procedure with exact CLI commands.

## Secret-Scanning Gate (SEC-01 Smoke Test)

The SEC-01 smoke test proves gitleaks is operational:

```bash
bash scripts/test-gitleaks-blocks-secret.sh
```

This plants a fake Ethereum private key in a temp file, runs
`gitleaks detect --config .gitleaks.toml`, and asserts a non-zero exit
(secret detected and blocked). See `.gitleaks.toml` for the project-specific
rule set covering Ethereum keys, Anthropic / OpenAI / Google API keys,
and Pinata JWT tokens.

## Security Properties

| Property | Implementation |
|----------|---------------|
| Key independence | `cast wallet new` × 4 (not BIP-32) — D-17 |
| Gitignore coverage | `.env.*` in first commit — Pitfall 5 |
| Secret scanning | gitleaks pre-commit + pre-push + CI — D-70 |
| Minimal exposure | Each service loads only its needed key(s) — D-16 |
| Mainnet authority | Ledger Nano X (not `.env.*`) — D-67 |
| Rotation procedure | `SessionFactory.rotateOperatorKey` — D-68 |

---

*Generated by Plan 00-05. Updated by: Phase 6 (session key rotation procedures).*

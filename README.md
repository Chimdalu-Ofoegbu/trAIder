# trAIder

A speculation-market protocol on live AI trading performance.

Three frontier LLMs (Claude Opus 4.7, GPT-5.5, Gemini 3.1 Pro) autonomously trade crypto perpetuals on GMX over 72-hour sessions with operator-provided capital. Each model is wrapped in an ERC-4626 vault; speculators trade per-model mTOKEN against USDC on Camelot. A permissionless arbitrage primitive keeps the AMM price anchored to vault NAV — structurally equivalent to ETF Authorized Participants pegging ETF prices to NAV, applied to live AI trading performance as the underlying.

**Core value:** A live, verifiable, tradeable market on which frontier LLM trades crypto better — with onchain NAV-pegged price discovery and replayable per-trade audit logs.

> Hackathon build for Arbitrum Open House

---

## Monorepo Layout

```
trAIder/
├── contracts/                  # Solidity 0.8.24 + Foundry
│   ├── src/interfaces/         # IMTokenVault.sol, IPerpsAdapter.sol (frozen Day 1)
│   ├── src/mocks/              # MockPerps.sol
│   ├── test/
│   ├── script/
│   ├── foundry.toml            # 3 profiles: default / coverage / fork
│   ├── remappings.txt
│   └── lib/openzeppelin-contracts  # submodule @ v5.4.0
├── orchestrator/               # Python 3.12 asyncio — trade loop + verifier
│   ├── pyproject.toml
│   ├── prompts/                # system.md + schema.json (frozen Day 1)
│   └── src/orchestrator/
├── backend/                    # FastAPI + WebSockets + Postgres + Redis
│   ├── pyproject.toml
│   └── src/backend/
├── frontend/                   # Next.js 14 App Router + wagmi/viem + shadcn
├── migrations/                 # Single Alembic tree (two schemas: orchestrator + backend)
├── docs/                       # RUNBOOK.md, Known Issues log
├── docker-compose.yml          # Dev stack: postgres:16 + redis:7 + anvil + pgadmin
├── Makefile                    # make up / seed / verify-stack / reset / down
├── .pre-commit-config.yaml     # gitleaks / ruff / forge fmt / prettier
├── .env.example                # Schema only — no real values
└── .gitignore                  # .env.* gitignored (FIRST COMMIT — SEC-01)
```

---

## Pinned Fork Block

**Arbitrum One fork block:** `353000000` (pinned 2026-06-01)

**Bump rule (D-39):** Bump ONLY when an upstream contract you depend on (GMX ExchangeRouter, Chainlink feed, Camelot V3 factory) is upgraded. Each bump requires a single-commit changelog entry documenting which contract changed and why. Do not bump for liquidity events, price fluctuations, or routine ops.

---

## License

Private repository until submission. Will flip to **MIT** at submission (2026-06-14).

> Disclaimer: Academic/research framing. Not affiliated with Anthropic, OpenAI, or Google. Model attribution based on API outputs only.

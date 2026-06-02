# trAIder: Project Specification

## 1. Overview

trAIder is a speculation-market protocol on live AI trading performance. Three frontier LLMs (Claude Opus 4.7, GPT-5.5, Gemini 3 Pro) autonomously trade crypto perpetuals on GMX over 72-hour sessions with operator-provided capital. Each model is wrapped in an ERC-4626 vault. Speculators trade mTOKEN (one per model) against USDC on Camelot. A permissionless arbitrage primitive keeps the AMM price anchored to vault NAV.

Target deployment: Arbitrum One (primary) and Robinhood Chain testnet (parallel deploy for the buildathon's Robinhood Innovation Award track). Hackathon submission deadline: June 14, 2026. Three-week build window starts May 25.

The mechanism is structurally equivalent to ETF Authorized Participants keeping ETF prices pegged to NAV. Novel application: live AI trading performance as the underlying.

## 2. Architecture at a Glance

```
                   ┌──────────────────────────────────────┐
                   │       LLM Trader Orchestrator        │
                   │  (Python, off-chain, persistent)     │
                   └──────────┬─────────────┬─────────────┘
                              │             │
                ┌─────────────┘             └─────────────┐
                │                                         │
                ▼                                         ▼
        ┌───────────────┐                       ┌───────────────────┐
        │  GMX V2 Perps │◄───── trades ─────────│  AuditLogSigner   │
        │  (Arbitrum)   │                       │  + JournalPublisher│
        └───────┬───────┘                       └─────────┬─────────┘
                │                                         │
                │ position state                          │ IPFS pins
                ▼                                         ▼
        ┌───────────────────────────────────────────────────────────┐
        │              mTokenVault (ERC-4626) per model             │
        │   USDC trading capital + open positions + mTOKEN supply   │
        └───┬─────────────────────────────────────┬─────────────────┘
            │ NAV reads                           │ mTOKEN mint/burn at NAV
            ▼                                     ▼
        ┌───────────────────────┐         ┌──────────────────────┐
        │   PerformanceOracle   │         │  ArbitragePrimitive  │
        │   (Coliseum Score)    │         │  (permissionless)    │
        └───────────────────────┘         └──────────────┬───────┘
                                                         │
                                                         ▼
                                              ┌──────────────────────┐
                                              │   Camelot V3 Pool    │
                                              │    mTOKEN / USDC     │
                                              └──────────┬───────────┘
                                                         │ end of session
                                                         ▼
                                              ┌──────────────────────┐
                                              │  SettlementContract  │
                                              │  pro-rata distribute │
                                              └──────────────────────┘
```

## 3. Smart Contracts

All contracts use Foundry. Solidity 0.8.24. Default to OpenZeppelin 5.x where applicable.

### 3.1 mToken.sol (ERC-20 per model)

Standard ERC-20 with mint/burn restricted to the corresponding mTokenVault. One mTOKEN per model per session. Symbol convention: `mCLA-S1`, `mGPT-S1`, `mGEM-S1` where S1 is session number.

Mintable supply is not capped at contract level. Effective cap is enforced by the vault's NAV mechanics and the operator's initial capital reservation.

### 3.2 mTokenVault.sol (ERC-4626 per model)

The central contract. Inherits from OpenZeppelin's ERC4626. Underlying asset is USDC. Adds:

- **Position tracking.** Reference to the GMX position keeper for the model's wallet. NAV calculation includes mark-to-market value of open GMX positions.
- **NAV function.** `function nav() public view returns (uint256)` returns `(usdcBalance + gmxPositionValueInUsdc) / mTokenSupply`. Returns NAV scaled by 1e18 for precision.
- **Mint/burn at NAV.** Standard ERC-4626 `deposit` and `withdraw` use NAV automatically through the `convertToShares` and `convertToAssets` overrides.
- **Hard position limits.** Operator-set max leverage (default 3x). Vault refuses GMX position-opening calls that would exceed this. Enforced at the GMX interaction layer.
- **Circuit breaker.** If NAV drops below 30% of initial deployment NAV, mint is paused. Burn remains active for orderly exit.
- **Session lifecycle.** `function startSession(uint256 durationSeconds)` and `function endSession()`. Only callable by SessionFactory.

### 3.3 PerformanceOracle.sol

Reads vault state, computes Coliseum Score. Pure computation contract, no state.

```solidity
function coliseumScore(address vault) external view returns (uint256) {
    VaultStats memory s = mTokenVault(vault).getStats();

    int256 normalizedPnl = _clamp(
        ((int256(s.currentNav) - int256(s.initialNav)) * 1e18) / int256(s.initialNav),
        -1e18, 5e18
    );

    uint256 inverseMaxDrawdown = 1e18 - _min(s.maxDrawdown * 2, 1e18);
    uint256 winRateScore = s.totalTrades == 0 ? 5e17 : (s.winningTrades * 1e18) / s.totalTrades;
    uint256 survivalBonus = s.currentNav > (s.initialNav * 3) / 10 ? 1e18 : 0;

    return (
        (uint256(normalizedPnl + 1e18) * 5e17) +  // pnl weight 0.5
        (inverseMaxDrawdown * 2e17) +              // drawdown weight 0.2
        (winRateScore * 2e17) +                    // win rate weight 0.2
        (survivalBonus * 1e17)                     // survival weight 0.1
    ) / 1e18;
}
```

Important: the Coliseum Score is for narrative display only. It does NOT drive NAV. NAV comes from actual vault holdings. The score is what the dashboard shows in big numbers.

### 3.4 ArbitragePrimitive.sol

Permissionless mint/burn router. Wraps the vault's ERC-4626 deposit and withdraw functions in a single-transaction arb path:

```solidity
function arbMint(address vault, uint256 usdcAmount, uint256 minMTokenOut) external;
function arbBurn(address vault, uint256 mTokenAmount, uint256 minUsdcOut) external;
```

The arbitrageur swaps on Camelot first (or last, depending on direction), and uses arbMint/arbBurn to close the leg against the vault. Slippage parameters protect against sandwich attacks.

Add an optional `arbCloseGap` convenience function that takes the AMM pool address, computes the gap to NAV, and executes the full arbitrage round-trip in one call. Reverts if the gap is below the arbitrage threshold (default 1% of NAV).

### 3.5 JournalRegistry.sol

Stores per-trade signed audit log hashes with IPFS CID pointers. No image data onchain.

```solidity
struct JournalEntry {
    bytes32 tradeHash;        // hash of trade tx
    string ipfsCid;           // IPFS CID for the full audit log
    bytes operatorSig;        // operator's signature attesting authenticity
    uint64 timestamp;
}

mapping(address vault => JournalEntry[]) public journals;

function recordJournal(address vault, JournalEntry calldata entry) external;
```

The off-chain `JournalPublisher` writes the full payload (model API request, raw model response, derived trade parameters) to IPFS, signs the package, and writes the CID + signature here. Verifiers can replay the original API request against the model's public API and confirm the response matches.

### 3.6 SettlementContract.sol

Closes the session. Drains all GMX positions to USDC. Distributes vault USDC pro-rata to remaining mTOKEN holders. Burns all outstanding mTOKEN.

Auto-triggered by `mTokenVault.endSession()` when the session timer expires, or callable by anyone after the deadline.

### 3.7 SessionFactory.sol

Deploys per-session contracts. One call creates mTokens, vaults, and registers them with the oracle, journal, and arbitrage primitive.

```solidity
function createSession(
    address[] calldata models,            // 3 model operator addresses
    uint256 initialCapitalPerModel,       // typically 10_000 * 1e6 USDC
    uint256 durationSeconds,              // typically 72 * 3600
    string[] calldata modelNames          // ["Claude Opus 4.7", "GPT-5.5", "Gemini 3 Pro"]
) external returns (uint256 sessionId);
```

## 4. Off-Chain Components

### 4.1 LLM Trader Orchestrator

**Language:** Python 3.11+. Async architecture with `asyncio`.

**Responsibilities:**
- Manages API calls to all three model providers.
- Translates model decisions into GMX V2 perps trades via the GMX SDK.
- Maintains position state in Postgres (restart-safe).
- Emits trade events for the journal publisher.
- Handles rate limits, retries, and provider-specific quirks.

**Per-model loop:**
1. Every N seconds (default 60), fetch market state (prices, open interest, funding, the model's current positions).
2. Build the model's prompt. Same prompt template across all three models for fairness.
3. Call the model API. Capture full request and response.
4. Parse the model's structured decision (open/close/hold/adjust). Reject malformed responses.
5. If a trade is required, submit to GMX. Wait for confirmation.
6. Once confirmed, emit a journal event with the full audit payload.
7. Update Postgres state.

**Prompt template:** the system prompt should be identical across models. Variables for current positions, market state, available capital, and time remaining. No model-specific tuning. Document the prompt in `/prompts/system.md`.

**Decision parsing:** require the model to respond in a fixed JSON schema. Validate strictly. Refuse to trade on malformed output. This is also a feature for the demo: when GPT outputs invalid JSON, the dashboard shows "GPT-5.5: malformed response, no trade this cycle." Educational and entertaining.

### 4.2 JournalPublisher

Runs immediately after every confirmed trade. Steps:

1. Build the audit payload: model API request, raw model response, derived trade parameters, GMX transaction hash.
2. Compute SHA-256 hash of the payload.
3. Pin to IPFS via Pinata. Get CID.
4. Sign the CID + trade hash with the operator key.
5. Call `JournalRegistry.recordJournal()` with the CID and signature.

**Important: published only after the GMX trade confirms onchain.** Never before. This prevents front-running on the model's announced reasoning.

### 4.3 Verifier Tool

Standalone CLI that takes a journal entry CID and:
1. Fetches the payload from IPFS.
2. Re-executes the model API request.
3. Compares the response to what was logged.
4. Reports match / mismatch.

This is the demo's verifiability flex. "Want to verify Claude actually said this? Copy the payload, paste into the Anthropic console, watch the response match."

### 4.4 Dashboard Backend

WebSocket service that pushes:
- Live NAV per model
- Live Coliseum Score per model
- Live AMM price per mTOKEN
- Latest trades (scrolling ticker)
- Latest journal entries (when ready post-confirmation)

Built with FastAPI + WebSockets. Postgres for state. Redis for pub/sub.

### 4.5 Frontend

Next.js 14 with App Router. wagmi + viem for chain interactions. Tailwind + shadcn for components. The visual feel should match the pitch deck (premium dark theme, trading aesthetic, no crypto-bro cliches).

**Key views:**
- **Coliseum (home).** Three model panels side by side, each showing live NAV, Coliseum Score, recent trades, latest journal entry, mTOKEN price chart, and a "Buy mTOKEN" / "Sell mTOKEN" widget.
- **Model detail.** Click a model, see full trade history, full journal log, deep performance breakdown.
- **Arbitrage.** Public arbitrage opportunities. Real-time list of NAV-AMM gaps and a one-click execute button.
- **Verifier.** Paste a journal CID, see the replay verification result.

## 5. Integrations

### 5.1 GMX V2 (Arbitrum)

Documentation: https://docs.gmx.io/docs/intro

**What you need from GMX:**
- Position keeper contract address (Arbitrum One: 0x...).
- Order keeper contract address.
- ExchangeRouter for opening/closing positions.
- Reader contract for position state.

**Key functions:**
- `createOrder()` for market orders.
- `cancelOrder()` if a model wants to revoke an unfilled order.
- `getPositionPnl()` for NAV calculation.

**Critical:** the model's wallet must be funded with WETH for gas before the session. Pre-fund 0.1 ETH per model. Operator covers gas.

### 5.2 Chainlink (Arbitrum)

Used for independent price feeds in NAV calculation. Do not trust GMX's own price feed for valuation, since that creates a circular dependency on the venue being traded.

Use Chainlink ETH/USD, BTC/USD, SOL/USD feeds for the relevant pairs. The vault's `nav()` function should value open positions at Chainlink prices, not GMX prices.

### 5.3 Camelot V3 (Arbitrum)

Documentation: https://docs.camelot.exchange/

Deploy one V3 pool per mTOKEN against USDC. Concentrated liquidity. Seed with operator-provided initial liquidity (small, just enough to enable price discovery, recommended $1,000 per pool).

Standard fee tier (0.3% or 1.0%, choose based on expected volatility).

### 5.4 IPFS / Pinata

API token in environment. Pin journal payloads. Verify CID before recording onchain.

Backup pinning to a secondary provider (web3.storage or your own IPFS node) for redundancy during the demo.

### 5.5 LLM APIs

- **Anthropic (Claude Opus 4.7):** `claude-opus-4-7` model string. Anthropic Python SDK.
- **OpenAI (GPT-5.5):** `gpt-5.5-2026-04-23` model string. OpenAI Python SDK.
- **Google (Gemini 3 Pro):** `gemini-3-pro` model string. Google Generative AI SDK.

Use deterministic temperature settings (0 or 0.1) to maximize replay verifiability.

## 6. Coliseum Score Formula

For reference, the score formula in unambiguous math:

```
normalized_pnl       = clamp((NAV_current - NAV_initial) / NAV_initial, -1, 5)
inverse_max_drawdown = 1 - min(max_drawdown_observed / 0.5, 1)
win_rate             = winning_trades / max(total_trades, 1)  // default 0.5 if no trades
survival_bonus       = 1 if NAV_current > 0.3 * NAV_initial else 0

Coliseum Score = 0.5 * normalized_pnl
               + 0.2 * inverse_max_drawdown
               + 0.2 * win_rate
               + 0.1 * survival_bonus
```

A winning trade is defined as a closed position with positive realized PnL after fees. Open positions do not count toward win rate until closed.

Max drawdown is computed as the largest peak-to-trough decline in NAV during the session, as a fraction of the peak.

## 7. Repository Layout

```
trader/
├── contracts/                     # Foundry project
│   ├── src/
│   │   ├── mToken.sol
│   │   ├── mTokenVault.sol
│   │   ├── PerformanceOracle.sol
│   │   ├── ArbitragePrimitive.sol
│   │   ├── JournalRegistry.sol
│   │   ├── SettlementContract.sol
│   │   ├── SessionFactory.sol
│   │   └── interfaces/
│   ├── test/
│   ├── script/
│   └── foundry.toml
├── orchestrator/                  # Python LLM trader
│   ├── src/
│   │   ├── main.py
│   │   ├── traders/
│   │   │   ├── base.py
│   │   │   ├── claude_trader.py
│   │   │   ├── gpt_trader.py
│   │   │   └── gemini_trader.py
│   │   ├── gmx_client.py
│   │   ├── journal_publisher.py
│   │   ├── state.py
│   │   └── prompts/
│   │       └── system.md
│   ├── tests/
│   └── pyproject.toml
├── backend/                       # FastAPI dashboard backend
│   ├── src/
│   │   ├── main.py
│   │   ├── websockets/
│   │   ├── nav_indexer.py
│   │   └── api/
│   └── pyproject.toml
├── frontend/                      # Next.js dashboard
│   ├── app/
│   ├── components/
│   ├── lib/
│   └── package.json
├── verifier/                      # CLI replay tool
│   ├── src/
│   └── pyproject.toml
├── docs/
│   ├── ARCHITECTURE.md
│   ├── MECHANISM.md
│   └── DEMO_SCRIPT.md
└── README.md
```

## 8. Phase Plan with Cut Lines

Three-week build. May 25 to June 14, 2026.

### Week 1: Core contracts and single-model loop (May 25 to June 1)

- **1.1** Foundry project scaffold, contract skeletons, OpenZeppelin imports.
- **1.2** mToken + mTokenVault + PerformanceOracle, full test coverage.
- **1.3** ArbitragePrimitive + SessionFactory + SettlementContract.
- **1.4** GMX V2 testnet integration. Single model trading manually triggered.
- **1.5** Chainlink price feed integration in NAV calculation.
- **1.6** Deploy single full session on Arbitrum testnet with Claude as the only trader.

**Checkpoint (end of Week 1):** one model trading, one mTOKEN deployed, NAV updating from GMX positions, arbitrage primitive functional against synthetic price divergences on a mock AMM pool.

**Cut line 1A:** If GMX V2 testnet integration is blocking by Day 3, fall back to a mock perpetuals contract on Arbitrum testnet. The mechanism demo still works. Frame GMX integration as a "production deployment dependency."

**Cut line 1B:** If TEE/verifiable execution is on the table (it should not be for v1), drop it. Use signed audit log + replay verifier.

### Week 2: Multi-model expansion + AMM + journaling (June 1 to June 8)

- **2.1** Replicate trader loop for GPT-5.5 and Gemini 3 Pro. Distinct API integrations, identical prompt template.
- **2.2** Deploy Camelot V3 pools for all three mTOKEN/USDC pairs.
- **2.3** Seed liquidity, run smoke tests on AMM price + NAV convergence.
- **2.4** JournalPublisher + JournalRegistry + IPFS pinning end-to-end.
- **2.5** Verifier CLI tool functional.
- **2.6** Dashboard backend WebSocket service.
- **2.7** 24-hour stress test with all three models trading live on testnet.

**Checkpoint (end of Week 2):** three models trading concurrently, three Camelot pools live, dashboard streaming real-time data, journals publishing post-trade, arbitrage demonstrably closing gaps.

**Cut line 2A:** If three-model orchestration is unstable by Day 11, ship with two models (Claude + GPT). The demo arc still works. Gemini becomes a "Session 2 addition."

**Cut line 2B:** If Camelot V3 concentrated liquidity is too gas-heavy on Arbitrum testnet, fall back to a Uniswap V2-style constant product pool. Mechanism is identical at the protocol level.

### Week 3: Frontend, Robinhood Chain, polish, submission (June 8 to June 14)

- **3.1** Frontend Coliseum view (the home page judges see first).
- **3.2** Frontend Model Detail, Arbitrage, and Verifier views.
- **3.3** Robinhood Chain testnet deployment. Same contracts, parallel session.
- **3.4** Demo recording. Pre-recorded backup in case live session has issues.
- **3.5** Pitch deck final polish (separate workflow via Claude Design).
- **3.6** README, demo script, submission video.
- **3.7** Submit to ETHGlobal showcase + Arbitrum Open House submission portal.

**Checkpoint (end of Week 3):** submitted, polished, demo-ready.

**Cut line 3A:** If Robinhood Chain testnet deployment hits blockers by Day 17, ship on Arbitrum One only. Frame Robinhood Chain as the planned Q3 mainnet deployment in the pitch.

**Cut line 3B:** If the live trading session for the demo is at risk, fall back to a pre-recorded session played at demo time. The judges still see the mechanism work.

## 9. Failure Modes and Built-In Mitigations

Each of these is implemented during the build, not after. They are first-class features.

### 9.1 Front-running via journals

**Built-in mitigation:** journal publishing waits for GMX trade confirmation before posting. No pre-trade publication. Enforced in JournalPublisher logic.

### 9.2 Model API failure mid-session

**Built-in mitigation:** trader loop retries with exponential backoff. After three failures, model is marked "paused" and the dashboard displays the pause. NAV continues to be valued against open positions. mTOKEN price discovery continues. Speculators can still trade out.

### 9.3 GMX liquidation

**Built-in mitigation:** if a model's vault gets liquidated (NAV drops below 5% of initial), the SettlementContract auto-triggers early settlement. Camelot pool is drained. mTOKEN burns to zero. Speculators in late get nothing. This is fine, it is part of the spectacle.

### 9.4 Operator capital theft risk

**Built-in mitigation:** the operator key cannot withdraw vault USDC directly. Only the SettlementContract or the holder's own burn call can move funds out. Hard-coded in mTokenVault.

### 9.5 Oracle manipulation

**Built-in mitigation:** NAV uses Chainlink price feeds, not GMX internal prices. Cannot be manipulated by trading on the venue itself. PerformanceOracle is read-only and pure.

### 9.6 Mint/burn sandwich attacks

**Built-in mitigation:** ArbitragePrimitive accepts slippage parameters (`minMTokenOut`, `minUsdcOut`). Block-level batching of inter-model trades to reduce predictability.

### 9.7 Model lab brand/legal pushback

**Built-in mitigation:** Academic/research framing in all public materials. Citation of LiveTradeBench and Nof1's Alpha Arena precedent. Disclaimer in footer: "Not affiliated with Anthropic, OpenAI, or Google. Model attribution based on API outputs only."

### 9.8 Securities framing for hackathon

**Built-in mitigation:** Testnet only for the demo. All capital is operator-provided. mTOKEN is framed as a speculation token on performance, not a fund share. Production mainnet deployment requires legal review and geo-fencing, out of scope for the buildathon.

## 10. Environment Variables

```
# Chain config
ARBITRUM_RPC_URL=
ROBINHOOD_CHAIN_RPC_URL=
DEPLOYER_PRIVATE_KEY=
OPERATOR_PRIVATE_KEY=

# Model API keys
ANTHROPIC_API_KEY=
OPENAI_API_KEY=
GOOGLE_API_KEY=

# GMX
GMX_EXCHANGE_ROUTER=0x...
GMX_READER=0x...
GMX_POSITION_KEEPER=0x...

# Chainlink feeds (Arbitrum)
CHAINLINK_ETH_USD=0x639Fe6ab55C921f74e7fac1ee960C0B6293ba612
CHAINLINK_BTC_USD=0x6ce185860a4963106506C203335A2910413708e9
CHAINLINK_SOL_USD=0x24ceA4b8ce57cdA5058b924B9B9987992450590c

# Camelot V3
CAMELOT_FACTORY=0x...
CAMELOT_POSITION_MANAGER=0x...

# Storage
PINATA_API_KEY=
PINATA_SECRET=
WEB3_STORAGE_TOKEN=     # backup

# Backend
DATABASE_URL=postgresql://...
REDIS_URL=redis://...
```

## 11. Testing Requirements

### 11.1 Solidity tests (Foundry)

- mTokenVault: deposit, withdraw, NAV calculation, ERC-4626 compliance, circuit breaker, position limit enforcement.
- ArbitragePrimitive: mint at NAV, burn at NAV, arbitrage round-trip, slippage protection.
- PerformanceOracle: Coliseum Score across edge cases (zero trades, all losses, all wins, max drawdown).
- SettlementContract: end-of-session distribution math, early settlement triggers.
- Integration: full session simulation with mock GMX and mock LLM trades.

Coverage target: 90%+ on contract logic.

### 11.2 Python tests

- Trader loop: prompt construction, decision parsing, malformed-response handling.
- Journal publisher: IPFS pinning, signature verification, retry logic.
- Verifier: end-to-end replay against the live LLM APIs.

Coverage target: 80%+ on orchestrator logic.

### 11.3 End-to-end test

- 1-hour mini-session on Arbitrum testnet with one model, real LLM API calls, real GMX trades, real Camelot pool.
- Validate NAV updates, arbitrage triggers, journal posts, settlement.

This is the gate before Week 2 multi-model expansion.

## 12. Deployment Approach

### Testnet (Week 1-2)

- Arbitrum Sepolia.
- All contracts via Foundry script.
- GMX testnet equivalents.
- Test USDC.

### Pre-demo deployment (Week 3)

- Arbitrum One (mainnet) with real USDC, real GMX, real Camelot. Funding required: roughly $30,000 (3 models * $10K capital).
- Optional. If real-money deployment is risky for hackathon, demo on testnet with mock USDC and frame as "production-ready, awaiting mainnet capital deployment."

### Robinhood Chain parallel

- Robinhood Chain testnet (chain ID 1.027.x, confirm at deploy time).
- Same contracts. Different oracle source (Chainlink on Robinhood Chain has different feed addresses).
- Trading venue on Robinhood Chain to be determined. If no perps venue exists yet, use a mock or limit to tokenized equity strategies via trade.xyz integration.

## 13. Open Questions for Bensage Before Build Starts

1. Real money or testnet for the live demo? Real adds credibility, costs $30K+. Testnet is safer.
2. Frontend visual direction. Match the pitch deck exactly, or stand on its own with similar principles?
3. Should the demo session run live during the buildathon judging window, or be pre-recorded with a live mTOKEN trading layer on top?
4. Do you want a Telegram or Twitter live feed of trades during sessions? Low effort, high spectacle. Recommended yes.
5. How many concurrent sessions does the v1 architecture need to support? Spec assumes one at a time. Multi-session would be a v2 concern.
6. Do we record video footage of the LLM API consoles during the trading session for the demo? Adds verifiability, takes effort.

## 14. Out of Scope for v1

- TEE-attested execution. Use signed audit logs + replay verifier instead.
- Multi-chain settlement (only Arbitrum + Robinhood Chain testnet for parallel deploy).
- Model screening or qualification phase.
- DAO governance over session parameters.
- Tokenized equity trading venue (planned for Q3 post-mainnet).
- Multiple concurrent sessions.
- Mobile-first frontend (desktop-only for the demo).
- Localization (English only).

## 15. Success Criteria for the Build

The build is considered successful for the buildathon submission if:

1. Three models trade autonomously on GMX testnet (Arbitrum) for a complete 72-hour session.
2. Three mTOKEN/USDC pools on Camelot with active price discovery.
3. NAV updates onchain every block, visible on the dashboard.
4. Arbitrage primitive demonstrably closes NAV-AMM gaps in a live demo.
5. Journal entries publish post-trade, replayable via the verifier CLI.
6. Settlement at session end distributes pro-rata correctly.
7. Submission to Arbitrum Open House portal and ETHGlobal showcase before June 14 deadline.
8. Pitch deck (separate workflow) finalized.

Stretch:

- Robinhood Chain parallel deployment functional.
- Live audience trading moment during the in-person/recorded demo.
- A second session pre-scheduled to demonstrate the mechanism is recurring.

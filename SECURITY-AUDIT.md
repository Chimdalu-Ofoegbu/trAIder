# trAIder — Security Audit & Remediation

**Date:** 2026-06-13
**Scope:** Full repository — smart contracts, Python orchestrator + gate, FastAPI backend, Next.js frontend, dependencies, CI, and git history.
**Method:** `gitleaks` secret scan over all 488 commits / every branch, plus three parallel deep-dive reviews (contracts; orchestrator+backend; frontend+deps+CI).

> **Disclaimer.** This is a rigorous first-pass review covering leaks/keys, the high-value
> contract vulnerabilities, access-control/trust invariants, common app-security classes, and
> dependency risk. It is **not a substitute for a professional smart-contract audit**, which
> is **required before any mainnet deployment handling real funds**. No automated or single
> review can guarantee zero exploits.

---

## Result summary

| Area | Result |
|------|--------|
| Secrets / keys in git history | ✅ **Clean** — gitleaks: no leaks across 488 commits; only `.env.example` templates ever tracked; `.gitignore` blocked `.env*` from commit #1 |
| Fund-custody core (vault / settlement / oracle / factory / journal) | ✅ Solid — *operator-cannot-withdraw* invariant holds; reentrancy guards; inflation-attack defense; NAV never uses the traded venue |
| `ArbitragePrimitive` (peg-keeper) | 🔴 Had exploitable issues → **fixed in source** (below); takes effect on next deploy |
| Orchestrator / backend | 🟠 Hardening gaps → **fixed** |
| Frontend / deps / CI | 🟠 One live-deploy issue (Alchemy key) + hardening → **fixed in code; one operator action remains** |

---

## Findings & remediation

### 🔴 Critical / High — FIXED in source

| # | Finding | Fix | Status |
|---|---------|-----|--------|
| C-1 | `ArbitragePrimitive.algebraSwapCallback` had **no caller authentication** — anyone could invoke it to siphon tokens/allowances. | Authenticate `msg.sender` against `IAlgebraFactory.poolByPair(tokenIn, tokenOut)` — only a real Algebra pool, mid-swap, can reach it. | ✅ Fixed + test `test_algebraSwapCallback_revertsForUnauthorizedCaller` |
| C-2 | `arbCloseGap` AMM-**buy leg had zero slippage protection** (`minAmountOut=0`) → unbounded sandwich/MEV loss to the caller. | Redeem the bought mTOKEN into the contract and enforce a **solvency floor** (`usdcBack ≥ notional × (1 − MAX_ARB_SLIPPAGE_BPS)`); revert (and roll back) otherwise. | ✅ Fixed + test `test_arbCloseGap_revertsWhenRoundTripWouldLose` |
| H-3 | Permissionless `arbCloseGap` had **no profit/solvency check** → could run at the caller's expense / be griefed. | Same solvency floor as C-2 applies to both legs — the round-trip can never return less than the floor. | ✅ Fixed |
| H-4 | `arbCloseGap` read price from a **caller-supplied pool** (spoofable). | Require `pool == IAlgebraFactory.poolByPair(vault, usdc)` — only the canonical pool is accepted. | ✅ Fixed + test `test_arbCloseGap_revertsForNonCanonicalPool` |

> The `ArbitragePrimitive` constructor now takes the **AlgebraFactory** (immutable). The deploy
> script passes it; the live Sepolia contract is **unchanged** (these fixes apply on the next
> deploy — i.e., mainnet prep). All 11 ArbitragePrimitive tests + the full 132-test contract
> suite pass.

### 🟠 Medium / Low — FIXED in code

| Finding | Fix |
|---------|-----|
| LLM `sizeUsd` had no upper bound; runtime capital cap was the only limit. | Added absolute `MAX_NOTIONAL_USD` ceiling in **both** the schema (`le=`) and `business_rules` (runtime gate). Also prevents an `int(inf)` overflow. |
| Capital cap (`available_usdc`) + NAV view were computed **once per session** (drift risk over long sessions). | Driver now **re-reads vault capital + NAV each cycle** (`_refresh_capital`), so the size cap tracks realized PnL / NAV. |
| `fetch_from_gateway` allowed any gateway + `follow_redirects=True` (SSRF surface). | https-only + **gateway host allowlist** + strict CID shape + `follow_redirects=False`. |
| Signing-middleware presence guard **failed open** on introspection error. | Now **fails closed** (assume middleware absent) so callers error loudly. |
| `TradePanel` submitted swaps with `amountOutMinimum=0` when no live quote (no slippage protection). | Buy/Sell button is **disabled** ("Waiting for live price…") until a live quote exists. |
| No security headers on the frontend. | Added (production-only) `X-Frame-Options: DENY`, CSP `frame-ancestors 'none'`, `Referrer-Policy`, `Permissions-Policy`, `X-Content-Type-Options`, HSTS. |

---

## ⚠️ Operator action items (NOT code — you must do these)

1. **Rotate + restrict the Alchemy RPC key.** It ships in the public client bundle (normal for
   `NEXT_PUBLIC_*`, and it was **never committed to git**), but it is unrestricted. In the
   Alchemy dashboard: add an **HTTP-referrer allowlist** for your deploy domain (and/or proxy
   RPC through a Next.js Route Handler), then **rotate** the current key.
2. **Redeploy contracts before mainnet** so the `ArbitragePrimitive` fixes take effect, and
   commission a **professional smart-contract audit** first.
3. **`docker-compose.yml`** uses default `traider:traider` creds with ports on `0.0.0.0` — fine
   for localhost, but bind to `127.0.0.1` + use env-supplied passwords (and a Redis password)
   before running on any shared/cloud host.
4. **Full content-CSP** (`script-src`/`connect-src`/`style-src`) is deferred — it must be tested
   against WalletConnect, the Alchemy RPC, the IPFS gateway, and Next's inline runtime before
   enabling, or it breaks wallet-connect / chain reads.
5. **`next@14.2.x`** carries advisories fixed only in Next 15 (largely unreachable in this
   client-only app — no middleware/SSR/i18n). Plan the Next 15 migration post-launch.

---

## ✅ What's solid (verified)

- **Operator cannot withdraw vault USDC** — every fund-exit path traced; no admin/sweep/rescue, no proxy/upgrade surface, vault is not `Ownable`.
- **ERC-4626 inflation/donation attack defended** (`_decimalsOffset()=12`); rounding favors the vault.
- **NAV never uses the traded venue's price** (no circular dependency); Chainlink staleness + last-known-good handling; reentrancy guards on every mutator; settlement uses a safe pull-pattern.
- **Key handling is disciplined** — env-only, never logged/pinned/alerted; LLM trade output strictly validated (market allow-list, leverage cap); prior model text is not fed into the next prompt (no prompt-injection loop); parameterized SQL; no `eval/exec/pickle`.
- **No XSS sinks** in the frontend (IPFS/model reasoning rendered as escaped text); swap targets hardcoded; CI hardened (SHA-pinned actions, no `pull_request_target` pwn-request); gitleaks pre-commit **and** pre-push.

*Audit performed with automated tooling + multi-agent review. Findings and fixes are tracked in the git history under "security audit fixes".*

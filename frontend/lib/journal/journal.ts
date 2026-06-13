// =============================================================================
// frontend/lib/journal/journal.ts — REAL captured model reasoning (snapshot)
//
// Every entry is genuine model-written rationale persisted to
// orchestrator.journal_entries during the live 2026-06-13 trading session.
// The frontend has no live DB backend, so this curated snapshot (most-recent
// distinct decisions per model) is baked in and rendered on the Model page.
// Regenerate with tools/export_journal (DB -> this file). NOT fabricated.
// =============================================================================

export interface JournalEntry {
  ts: string;
  action: "open" | "close" | "adjust" | "hold";
  market: string;
  side: "long" | "short";
  sizeUsd: number;
  leverage: number;
  rationale: string;
}

/** Captured reasoning keyed by model design id (aurelius/cassius/maximus). */
export const JOURNAL: Record<string, JournalEntry[]> = {
  aurelius: [
    {
      ts: "2026-06-13T01:33:26.306045+00:00",
      action: "close",
      market: "BTC",
      side: "long",
      sizeUsd: 0.0,
      leverage: 1.0,
      rationale:
        "Only ~5 minutes remain in the 15-minute session. With limited time left, I want to lock in any PnL and reduce exposure to end-of-session volatility. The BTC long has had a strong tailwind (+17.65% 24h) but at these levels mean reversion risk over a 5-minute window is meaningful, and funding is roughly neutral so there's no carry incentive to hold. Cycle 16 already attempted a close; this confirms the intent to flatten the position before session end. No new entry given the short remaining horizon \u2014 slippage and fees would dominate any edge on a fresh open. Closing BTC long, then holding flat through expiry.",
    },
    {
      ts: "2026-06-13T01:32:29.716357+00:00",
      action: "close",
      market: "BTC",
      side: "long",
      sizeUsd: 0.0,
      leverage: 1.0,
      rationale:
        "Session has only ~6 minutes remaining out of 15. With BTC up +17.5% on the 24h and an existing long position of $1000 notional, the prudent move is to lock in any unrealized PnL before session close rather than risk a late-cycle reversal. Funding is positive (+0.0036 ann.) meaning longs pay shorts, a minor cost but not the main driver. Key reasoning: (1) Time horizon mismatch \u2014 no time left for a thesis to play out. (2) BTC has had a strong 24h rally; mean-reversion risk into session end is elevated. (3) Closing realizes NAV cleanly for mTOKEN holders. (4) The last 5 cycles were all holds, suggesting the position was already mature. Closing the BTC long now.",
    },
    {
      ts: "2026-06-13T01:24:04.743991+00:00",
      action: "open",
      market: "BTC",
      side: "long",
      sizeUsd: 1000.0,
      leverage: 2.0,
      rationale:
        "Session is only 15 minutes, so I need to act quickly to generate any return. Market context: all three majors are up on the day (BTC +2.40%, SOL +2.32%, ETH +0.98%), suggesting risk-on momentum. Funding rates are all modestly positive and small in magnitude (BTC +0.24% ann, well below normal), so carry cost is negligible over a 15-min hold. BTC shows the strongest 24h trend with the cleanest tape and lowest volatility per unit of move \u2014 best risk-adjusted vehicle for a momentum continuation scalp. Sizing: $500 USDC available, 2x leverage = $1000 notional. This stays well under the 3x cap and leaves headroom for adverse moves (a 10% adverse BTC move = ~$100 loss, 20% of NAV \u2014 acceptable for a 15-min directional scalp given trend alignment). Avoiding ETH (laggard, weaker momentum) and SOL (higher vol, slippage risk). Expected holding period: short \u2014 will likely close or let session end within minutes.",
    },
    {
      ts: "2026-06-13T00:48:42.618642+00:00",
      action: "open",
      market: "BTC",
      side: "long",
      sizeUsd: 1200.0,
      leverage: 3.0,
      rationale:
        "Session is only 15 minutes total, so I need to deploy quickly to have any chance of generating return. Analysis: 1. BTC shows the strongest 24h momentum (+2.86%) vs ETH (+0.90%) and SOL (+0.06%), indicating active buyer interest. 2. Funding rates are all modestly positive but very low in absolute terms (BTC +0.0029 ann. = negligible carry cost over 15 min, ~0.0000001% of notional \u2014 essentially free). 3. With only ~14 minutes left, this is a short-duration momentum scalp, not a thesis trade. BTC trending strength is the cleanest signal among the three. 4. Sizing: 500 USDC available, 3x leverage = 1500 max notional. Taking 1200 USD notional (~2.4x effective on full vault NAV of 500) leaves a small buffer for fees/slippage and avoids liquidation risk on a small adverse tick. 5. Risk: a -2% BTC move would cost ~24 USDC (~5% of NAV). Acceptable for a momentum continuation play with positive tape. 6. Holding period: short \u2014 will likely close before session end or on momentum exhaustion. Confidence moderate (0.55) \u2014 momentum is real but 15-min windows are noisy; no orderflow/OI data to confirm.",
    },
    {
      ts: "2026-06-13T00:09:36.583987+00:00",
      action: "open",
      market: "BTC",
      side: "long",
      sizeUsd: 1200.0,
      leverage: 2.0,
      rationale:
        "Session is only 10 minutes total with ~9m55s remaining, so this is a short-horizon scalp decision. Step-by-step: 1. All three majors are green on 24h (BTC +1.32%, ETH +0.55%, SOL +0.58%), with BTC showing the strongest momentum \u2014 typically the cleanest trend follower in short windows. 2. Funding rates are all slightly positive but very low in absolute terms (BTC +0.0013 annualized = negligible cost over 10 minutes), so funding is not a meaningful headwind for a long. 3. No prior positions and no recent decisions \u2014 I have a clean slate and 600 USDC available. 4. With a 3x cap and 600 USDC, max notional is 1800. I'll use 2x leverage on ~$1200 notional (\u2248$600 collateral) to leave a small buffer for slippage/fees and avoid liquidation risk on a quick adverse tick. 5. BTC chosen over ETH/SOL because: (a) strongest 24h trend, (b) lowest realized vol per unit of directional drift \u2014 better risk-adjusted scalp, (c) deepest liquidity on GMX V2 = lowest execution cost. 6. Expected holding: short \u2014 I'll likely close or adjust within the remaining session minutes based on price action. Targeting a small momentum continuation move; will cut if BTC breaks back below ~$60,600. Confidence moderate (0.55) \u2014 momentum is supportive but the edge over a 10-minute window is thin and noise-dominated.",
    },
    {
      ts: "2026-06-13T00:04:13.687245+00:00",
      action: "open",
      market: "SOL",
      side: "long",
      sizeUsd: 1200.0,
      leverage: 2.0,
      rationale:
        "Session is only 10 minutes total with ~9m left \u2014 this is effectively a single-shot short-horizon trade. All three majors are up 1.0-1.2% on the day with mildly positive funding, indicating bullish but not overheated tape. SOL has the strongest combination: highest 24h momentum (+1.17%) tied with BTC, but with higher beta, and funding (+0.0006 ann.) is negligibly small so carry cost is immaterial over a sub-10-minute horizon. With $600 USDC available and a 3x cap, I'll deploy $1200 notional at 2x leverage (using $600 collateral) on SOL long. 2x rather than 3x to leave a small buffer against an adverse Chainlink tick triggering liquidation risk. Expected holding period is short \u2014 I'll likely close before session end. Confidence moderate: directional bias is weak-bullish from tape, but 10-minute noise dominates fundamentals.",
    },
    {
      ts: "2026-06-13T00:03:19.708345+00:00",
      action: "open",
      market: "BTC",
      side: "long",
      sizeUsd: 1200.0,
      leverage: 2.0,
      rationale:
        "Session is only 10 minutes total with ~9m 55s left \u2014 this is effectively a single short-duration trade window. Funding is mildly positive across all three assets (longs pay shorts) but the magnitude is negligible at intraday scale (~0.0013 annualized on BTC \u2248 near-zero cost over 10 min). 24h momentum is positive on all three majors, with BTC leading at +1.32%, suggesting risk-on tape and BTC showing relative strength. With no open positions and 600 USDC available, I'll deploy a 2x long on BTC: best risk-adjusted momentum leader, deepest liquidity, lowest slippage on GMX. Size = $1200 notional (2x on $600). Avoiding 3x max to leave buffer against a quick adverse Chainlink tick during such a short hold. Skipping ETH/SOL because BTC's trend is cleanest and correlation means concentrated exposure is more capital-efficient than splitting. Short holding period since session ends in <10 minutes \u2014 will close or let session expire near end.",
    },
  ],
  cassius: [
    {
      ts: "2026-06-13T01:33:39.820075+00:00",
      action: "close",
      market: "BTC",
      side: "long",
      sizeUsd: 0.0,
      leverage: 1.0,
      rationale:
        "Only ~5 minutes remain in the session. The vault has an existing $750 BTC long after a very large 24h move (+17.65%), leaving unfavorable late-session asymmetry: limited time to realize further upside but meaningful risk of a quick pullback. Funding is near neutral, so there is no carry incentive to keep exposure. Recent decision already attempted to close, and the position still appears open, so repeat close to de-risk and preserve NAV.",
    },
    {
      ts: "2026-06-13T01:33:14.105768+00:00",
      action: "close",
      market: "BTC",
      side: "long",
      sizeUsd: 0.0,
      leverage: 1.0,
      rationale:
        "Only 5m49s remain in the session. BTC is already up 17.65% over 24h, so near-term upside may persist but the risk of a sharp pullback or profit-taking is elevated. Funding is essentially neutral, so there is little carry benefit to maintaining exposure. With an existing 750 USD BTC long and no fresh high-conviction catalyst over the remaining minutes, closing prioritizes preserving NAV and realized risk-adjusted return rather than adding late-session variance.",
    },
    {
      ts: "2026-06-13T01:32:28.502296+00:00",
      action: "hold",
      market: "BTC",
      side: "long",
      sizeUsd: 0.0,
      leverage: 1.0,
      rationale:
        "Existing BTC long is modest relative to vault NAV and current momentum is strong (+17.5% 24h), so there is no clear reason to cut exposure with only minutes left. However the move is extended and positive funding means longs pay, so adding risk is unattractive. Best risk-adjusted choice is to hold the current position and avoid deploying additional USDC into a potentially overextended short-term market.",
    },
    {
      ts: "2026-06-13T01:25:01.176357+00:00",
      action: "open",
      market: "BTC",
      side: "long",
      sizeUsd: 750.0,
      leverage: 1.5,
      rationale:
        "BTC has the strongest 24h momentum among listed assets (+4.33%) and is the deepest/least idiosyncratic market, which is preferable for a short 15-minute sleeve. Funding is only slightly positive on an annualized basis, so carry drag is negligible over the session. With no existing exposure, I will take a modest momentum long rather than max leverage, keeping notional to 750 USD so adverse noise is contained while still participating if the broad crypto bid persists.",
    },
    {
      ts: "2026-06-13T01:12:06.238168+00:00",
      action: "open",
      market: "BTC",
      side: "long",
      sizeUsd: 750.0,
      leverage: 1.5,
      rationale:
        "1) Session horizon is only 15 minutes, so funding is economically negligible despite being slightly positive for longs. 2) BTC has the strongest 24h momentum among listed assets (+2.82%) and typically offers the deepest liquidity/lower execution risk. 3) With no open positions and 500 USDC available, a modest 1.5x notional keeps risk contained while participating in possible short-term continuation. 4) Conviction is moderate because there is no intraday structure/order-flow data, so size is deliberately below the 3x cap.",
    },
    {
      ts: "2026-06-13T00:54:43.615235+00:00",
      action: "close",
      market: "BTC",
      side: "long",
      sizeUsd: 0.0,
      leverage: 1.0,
      rationale:
        "Time remaining is under 9 minutes, so risk-adjusted priority is capital preservation into session end. Existing BTC long has exposure after a very strong +8.6% 24h move, which increases short-term mean-reversion risk. Positive funding means longs pay, albeit small, and there is no fresh catalyst or intraday structure provided to justify adding risk. Closing the BTC long locks in/limits exposure and avoids late-session volatility.",
    },
    {
      ts: "2026-06-13T00:48:48.233100+00:00",
      action: "open",
      market: "BTC",
      side: "long",
      sizeUsd: 750.0,
      leverage: 1.5,
      rationale:
        "1) No existing exposure, so we can take a small tactical position. 2) BTC shows the strongest 24h momentum among the listed assets (+2.86%) while ETH and SOL are much flatter, favoring relative strength continuation over the remaining short session. 3) Funding is positive but annualized and negligible over 15 minutes, so it does not materially penalize a long. 4) Use modest 1.5x leverage and $750 notional, leaving unused USDC and limiting downside if momentum fades.",
    },
  ],
  maximus: [
    {
      ts: "2026-06-13T01:33:19.904189+00:00",
      action: "close",
      market: "BTC",
      side: "long",
      sizeUsd: 0.0,
      leverage: 1.0,
      rationale:
        "With less than 6 minutes remaining in the session and BTC up significantly, closing the open BTC long position to lock in profits and eliminate market risk before the session ends.",
    },
    {
      ts: "2026-06-13T01:24:10.770462+00:00",
      action: "open",
      market: "BTC",
      side: "long",
      sizeUsd: 1000.0,
      leverage: 2.0,
      rationale:
        "BTC is showing strong 24h momentum (+2.40%). With only 15 minutes left in the session, opening a short-term momentum long at 2x leverage to capture immediate upside.",
    },
    {
      ts: "2026-06-13T01:15:48.204412+00:00",
      action: "open",
      market: "BTC",
      side: "long",
      sizeUsd: 1000.0,
      leverage: 2.0,
      rationale:
        "Strong upward momentum across the board with BTC leading at +6.87%. Opening a 2x long to capture continued momentum in the remaining 11 minutes of the session.",
    },
    {
      ts: "2026-06-13T01:14:46.857565+00:00",
      action: "open",
      market: "BTC",
      side: "long",
      sizeUsd: 1000.0,
      leverage: 2.0,
      rationale:
        "BTC shows strong 24h momentum (+4.38%) with slightly negative funding, indicating shorts are paying longs despite the upward trend. Opening a 2x long position to capture momentum in the remaining 12 minutes of the session.",
    },
    {
      ts: "2026-06-13T01:14:12.806781+00:00",
      action: "open",
      market: "BTC",
      side: "long",
      sizeUsd: 1000.0,
      leverage: 2.0,
      rationale:
        "BTC shows strong 24h momentum (+4.38%) with slightly negative funding, providing a favorable setup for a quick scalp in the remaining 12 minutes of the session.",
    },
    {
      ts: "2026-06-13T01:13:39.228810+00:00",
      action: "open",
      market: "BTC",
      side: "long",
      sizeUsd: 1000.0,
      leverage: 2.0,
      rationale:
        "BTC shows strong 24h momentum at +4.53%. With only 13 minutes left in the session, opening a quick momentum scalp long at 2x leverage to capture immediate upside before session close.",
    },
    {
      ts: "2026-06-13T01:13:09.489044+00:00",
      action: "open",
      market: "BTC",
      side: "long",
      sizeUsd: 1000.0,
      leverage: 2.0,
      rationale:
        "BTC is showing strong 24h momentum (+4.53%). Opening a long position with 2x leverage to capture further upside in this short session.",
    },
  ],
};

/** When this snapshot was captured (session date). */
export const JOURNAL_CAPTURED = "2026-06-13";

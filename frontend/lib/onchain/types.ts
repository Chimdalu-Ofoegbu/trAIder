// =============================================================================
// frontend/lib/onchain/types.ts — the live view-model the UI consumes.
//
// Shape mirrors the Claude Design build's derived MODELS object (so its ported
// components consume this 1:1), but every field is sourced from live chain reads.
// Fields that need history we cannot backfill on-chain (session pnl, sharpe) are
// nullable and the UI shows "—" rather than a fabricated value.
// =============================================================================

import type { ModelMeta } from "./models";

export interface PricePoint {
  /** Browser-clock ms of the poll that produced this point. */
  t: number;
  /** NAV per mTOKEN (USD). */
  nav: number;
  /** AMM price per mTOKEN (USD); null if the pool read failed this tick. */
  price: number | null;
}

export interface ModelLive extends ModelMeta {
  // ── raw on-chain (this poll) ──
  navE18: bigint;
  ammPriceE18: bigint | null;
  totalAssetsRaw: bigint; // USDC, 6-dec
  totalSupplyRaw: bigint; // mTOKEN, 18-dec
  symbol: string;

  // ── derived for display ──
  nav: number;
  price: number | null;
  supply: number;
  assetsUsd: number;
  /** Signed NAV→AMM gap in bps (premium positive); null if no AMM read. */
  spreadBps: number | null;
  /** Arbitrage direction per the ArbOpp contract. */
  direction: "mint" | "burn" | "none";

  // ── session-derived (from the live-accumulated buffer) ──
  series: PricePoint[];
  /** % NAV change since the first buffered point this session; null until 2+ points. */
  pnlSession: number | null;
  /** Sharpe over buffered returns; null until enough live samples. */
  sharpe: number | null;
  /** Display-only Coliseum Score (does NOT drive NAV). */
  score: number;

  /** True when the critical vault reads succeeded this tick. */
  ok: boolean;
}

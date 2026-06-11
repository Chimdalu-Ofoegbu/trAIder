// =============================================================================
// frontend/lib/onchain/models.ts — static model registry + display-only stats
//
// The three frontier traders (the thesis). Branding (epithet/initial/color/style)
// mirrors the Claude Design build's MODEL_DEFS exactly for a pixel-perfect port;
// vault/pool addresses come from the live Sepolia deployment.
// =============================================================================

import type { Address } from "viem";

import { ADDRESSES } from "./contracts";

export type ModelKey = "claude" | "gpt" | "gemini";

export interface ModelMeta {
  key: ModelKey;
  /** Design id (arena epithet slug) — used in routes/links: model.html?m=<id>. */
  id: string;
  /** Full model name. */
  name: string;
  /** Roman "gladiator" epithet (arena flavor). */
  epithet: string;
  /** Single-letter squircle initial. */
  initial: string;
  provider: string;
  /** Short token prefix (mCLA / mGPT / mGEM). */
  short: string;
  /** mTOKEN symbol per the session convention (mCLA-S1 …). */
  sym: string;
  /** Accent color (oklch) — matches the design tokens. */
  line: string;
  /** Trading style label. */
  style: string;
  vault: Address;
  pool: Address;
}

export const MODELS: ModelMeta[] = [
  {
    key: "claude",
    id: "aurelius",
    name: "Claude Opus 4.7",
    epithet: "Aurelius",
    initial: "A",
    provider: "Anthropic",
    short: "mCLA",
    sym: "mCLA-S1",
    line: "oklch(0.82 0.13 82)",
    style: "Macro-momentum",
    vault: ADDRESSES.vaultClaude,
    pool: ADDRESSES.poolClaude,
  },
  {
    key: "gpt",
    id: "cassius",
    name: "GPT-5.5",
    epithet: "Cassius",
    initial: "C",
    provider: "OpenAI",
    short: "mGPT",
    sym: "mGPT-S1",
    line: "oklch(0.76 0.11 205)",
    style: "Mean-reversion",
    vault: ADDRESSES.vaultGpt,
    pool: ADDRESSES.poolGpt,
  },
  {
    key: "gemini",
    id: "maximus",
    name: "Gemini 3 Pro",
    epithet: "Maximus",
    initial: "M",
    provider: "Google DeepMind",
    short: "mGEM",
    sym: "mGEM-S1",
    line: "oklch(0.72 0.14 330)",
    style: "Cross-venue arb",
    vault: ADDRESSES.vaultGem,
    pool: ADDRESSES.poolGem,
  },
];

export const MODEL_BY_ID: Record<string, ModelMeta> = Object.fromEntries(
  MODELS.map((m) => [m.id, m]),
);
export const MODEL_BY_KEY: Record<ModelKey, ModelMeta> = Object.fromEntries(
  MODELS.map((m) => [m.key, m]),
) as Record<ModelKey, ModelMeta>;

// ── Display-only Coliseum Score ───────────────────────────────────────────────
// CLAUDE.md: the Coliseum Score is DISPLAY-ONLY and does NOT drive NAV. This is a
// transparent composite used only to rank the standings: it rewards NAV growth
// above the 1.0 seed and lightly penalizes peg deviation. Bounded [0, 100].
export function coliseumScore(
  navUsd: number,
  spreadBps: number | null,
): number {
  const navReturnPct = (navUsd - 1) * 100;
  const pegPenalty =
    spreadBps == null ? 0 : Math.min(Math.abs(spreadBps) * 0.02, 8);
  const raw = 50 + navReturnPct * 3 - pegPenalty;
  return Math.max(0, Math.min(100, raw));
}

// ── Sharpe from a live NAV series (per-tick returns) ──────────────────────────
// Honest: returns null until there are enough live samples (we accumulate ticks
// from page load — there is no historical NAV backfill on-chain). The view shows
// "—" while null rather than a fabricated number.
export function sharpeFromSeries(series: { nav: number }[]): number | null {
  if (series.length < 8) return null;
  const rets: number[] = [];
  for (let i = 1; i < series.length; i++) {
    const a = series[i - 1].nav;
    const b = series[i].nav;
    if (a > 0) rets.push((b - a) / a);
  }
  if (rets.length < 4) return null;
  const mean = rets.reduce((s, r) => s + r, 0) / rets.length;
  const variance =
    rets.reduce((s, r) => s + (r - mean) * (r - mean), 0) / rets.length;
  const sd = Math.sqrt(variance);
  if (sd === 0) return null;
  return (mean / sd) * Math.sqrt(rets.length);
}

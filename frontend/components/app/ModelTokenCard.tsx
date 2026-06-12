"use client";

// =============================================================================
// frontend/components/app/ModelTokenCard.tsx — Coliseum model token card.
//
// The redesigned standings unit: big model name + live dot + rank, NAV, a
// prominent Coliseum Score, mTOKEN price + session change, the price-vs-NAV
// chart, the latest journal line, and BUY/SELL. Live on-chain data; keeps the
// page's existing dark design system (model accent colors unchanged).
// =============================================================================

import Link from "next/link";

import { fmtUsd, sign } from "@/lib/format";
import { ConvergenceChart } from "@/components/charts/ConvergenceChart";
import type { ModelLive } from "@/lib/onchain/types";

// Representative latest-journal line per model until live journals are recorded
// on-chain (the Verifier shows the on-chain audit feed).
const LATEST: Record<string, string> = {
  claude:
    "Opened LONG ETH 2x — momentum intact, funding favorable, basis tightening into the European session.",
  gpt: "Faded the SOL wick — mean-reversion signal fired; trimming size into resistance.",
  gemini:
    "Captured the GMX/Camelot basis — rotated exposure as the cross-venue spread widened.",
};

export function ModelTokenCard({
  model: m,
  rank,
}: {
  model: ModelLive;
  rank: number;
}) {
  const scoreCls = m.nav >= 1 ? "pos" : "neg";
  const change = m.pnlSession;

  return (
    <article className="token-card">
      <span className="token-corner tl" />
      <span className="token-corner tr" />
      <span className="token-corner bl" />
      <span className="token-corner br" />

      <div className="token-hd">
        <div className="token-name" style={{ color: m.line }}>
          {m.key}
          <span className="dot dot-live" style={{ background: m.line }} />
        </div>
        <span className="token-rank">{String(rank).padStart(2, "0")}</span>
      </div>

      <div className="token-metric">
        <div className="kicker">NAV</div>
        <div className="v">{m.nav > 0 ? fmtUsd(m.nav, 4) : "—"}</div>
      </div>

      <div className="token-metric">
        <div className="kicker">Coliseum Score</div>
        <div className={`token-score-v ${scoreCls}`}>
          {m.ok ? m.score.toFixed(1) : "—"}
        </div>
      </div>

      <div className="token-price-row">
        <div>
          <div className="kicker">{m.short} price</div>
          <div className="v">{m.price != null ? fmtUsd(m.price, 4) : "—"}</div>
        </div>
        {change != null ? (
          <div className={`num ${change >= 0 ? "pos" : "neg"}`}>
            {sign(change)}
            {change.toFixed(2)}%
          </div>
        ) : null}
      </div>

      <div className="token-chart">
        <ConvergenceChart
          series={m.series}
          color={m.line}
          vol={m.vol}
          w={360}
          h={116}
          pad={{ t: 10, r: 4, b: 10, l: 4 }}
        />
      </div>

      <div className="token-latest">
        <div className="kicker">Latest</div>
        <div className="jr">
          {LATEST[m.key] ?? "Awaiting first journaled trade."}
        </div>
        <Link className="more" href={`/model?m=${m.id}`}>
          read more →
        </Link>
      </div>

      <div className="token-actions">
        <Link className="buy" href={`/model?m=${m.id}`}>
          BUY
        </Link>
        <Link className="sell" href={`/model?m=${m.id}`}>
          SELL
        </Link>
      </div>
    </article>
  );
}

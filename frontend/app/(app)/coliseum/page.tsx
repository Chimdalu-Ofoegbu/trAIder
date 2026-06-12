"use client";

// =============================================================================
// frontend/app/(app)/coliseum/page.tsx — The Coliseum (thesis view 1)
//
// Three frontier LLMs head-to-head, ranked live by on-chain vault NAV. Standings
// render as model token cards (ModelTokenCard); every number is a live read from
// Arbitrum Sepolia (useModels) — independently verifiable on Arbiscan.
//
// Honesty notes (verifiability > literal label match):
//   - "Arbitrum Sepolia" not "Arbitrum One" (real network).
//   - statbar uses real on-chain aggregates (no mTOKEN 24h volume feed exists).
//   - Card scores/charts are session-derived (no historical NAV backfill on-chain).
// =============================================================================

import { useMemo } from "react";

import { useModels } from "@/lib/onchain/useModels";
import { fmtCompact, fmtInt } from "@/lib/format";
import { Ticker } from "@/components/app/Ticker";
import { WalletButton } from "@/components/app/WalletButton";
import { ModelTokenCard } from "@/components/app/ModelTokenCard";

export default function ColiseumPage() {
  const { models, blockNumber, loading, error } = useModels();

  const ranked = useMemo(
    () => [...models].sort((a, b) => b.score - a.score),
    [models],
  );

  // ── statbar aggregates (all live on-chain) ──
  const totalNav = models.reduce((s, m) => s + (m.assetsUsd || 0), 0);
  const totalSupply = models.reduce((s, m) => s + (m.supply || 0), 0);
  const liveCount = models.filter((m) => m.ok).length;
  const openGaps = models.filter(
    (m) => m.spreadBps != null && Math.abs(m.spreadBps) > 50,
  ).length;
  const spreads = models
    .map((m) => m.spreadBps)
    .filter((x): x is number => x != null);
  const avgSpread = spreads.length
    ? spreads.reduce((s, x) => s + Math.abs(x), 0) / spreads.length
    : null;

  return (
    <>
      <header className="topbar">
        <div className="flex" style={{ alignItems: "center", gap: 14 }}>
          <div>
            <h1>The Coliseum</h1>
          </div>
          <span className="crumb">/ live standings</span>
        </div>
        <div className="topbar-right">
          <WalletButton />
          <span className="tag tag-live">
            <span className="dot dot-live" /> Arbitrum Sepolia
          </span>
        </div>
      </header>

      <Ticker models={models} />

      <div className="app-body">
        <div className="statbar">
          <div>
            <div className="kicker">Total vault NAV</div>
            <div className="v">{totalNav > 0 ? fmtCompact(totalNav) : "—"}</div>
          </div>
          <div>
            <div className="kicker">mTOKEN supply</div>
            <div className="v">
              {totalSupply > 0 ? fmtInt(totalSupply) : "—"}
            </div>
          </div>
          <div>
            <div className="kicker">Avg spread vs NAV</div>
            <div className="v">
              {avgSpread != null ? `${avgSpread.toFixed(0)} bps` : "—"}
            </div>
          </div>
          <div>
            <div className="kicker">Models live</div>
            <div className="v num">{liveCount} / 3</div>
          </div>
          <div>
            <div className="kicker">Open arb gaps</div>
            <div className={`v val ${openGaps > 0 ? "pos" : ""}`}>
              {openGaps}
            </div>
          </div>
        </div>

        <div
          className="between"
          style={{ alignItems: "flex-end", marginBottom: 16 }}
        >
          <h2 className="h4">Standings</h2>
          <span className="crumb">
            {error
              ? error
              : loading
                ? "connecting to Arbitrum Sepolia…"
                : `live · block ${blockNumber ?? "—"} · ranked by Coliseum Score`}
          </span>
        </div>

        <div className="token-grid">
          {ranked.map((m, i) => (
            <ModelTokenCard key={m.id} model={m} rank={i + 1} />
          ))}
        </div>
      </div>
    </>
  );
}

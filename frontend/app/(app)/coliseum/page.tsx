"use client";

// =============================================================================
// frontend/app/(app)/coliseum/page.tsx — The Coliseum (thesis view 1)
//
// Three frontier LLMs head-to-head, ranked live by on-chain vault NAV. Ported
// from the Claude Design build's coliseum.html; every number is a live read from
// Arbitrum Sepolia (useModels) — independently verifiable on Arbiscan.
//
// Honesty notes (verifiability > literal label match):
//   - "Arbitrum Sepolia" not "Arbitrum One" (real network).
//   - "Δ session" (since page load) not "24h" — there is no historical NAV
//     backfill on-chain, so we accumulate live ticks. Sharpe/spark are likewise
//     session-derived. Columns show "—" until enough live samples exist.
//   - statbar uses real on-chain aggregates (no mTOKEN 24h volume feed exists).
// =============================================================================

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMemo } from "react";
import { useAccount } from "wagmi";

import { useModels } from "@/lib/onchain/useModels";
import { fmtUsd, fmtCompact, fmtInt, sign, shortAddr } from "@/lib/format";
import { Sparkline } from "@/components/charts/Sparkline";
import { Ticker } from "@/components/app/Ticker";

export default function ColiseumPage() {
  const router = useRouter();
  const { models, blockNumber, loading, error } = useModels();
  const { address, isConnected } = useAccount();

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
          <span className="tag tag-live">
            <span className="dot dot-live" /> Arbitrum Sepolia
          </span>
          {isConnected && address ? (
            <span
              className="wallet-chip"
              style={{ border: "1px solid var(--line)" }}
            >
              <span className="dot dot-live" />
              <span>{shortAddr(address)}</span>
            </span>
          ) : null}
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

        <section className="panel">
          <div className="panel-hd">
            <h2>Standings</h2>
            <span className="crumb">
              {error
                ? error
                : loading
                  ? "connecting to Arbitrum Sepolia…"
                  : `live · block ${blockNumber ?? "—"} · ranked by Coliseum Score`}
            </span>
          </div>
          <table className="dtable">
            <thead>
              <tr>
                <th style={{ width: 48 }}>#</th>
                <th>Model</th>
                <th>NAV / token</th>
                <th>mTOKEN</th>
                <th>Spread</th>
                <th>Δ session</th>
                <th>Sharpe</th>
                <th>Live</th>
                <th>Score</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {ranked.map((m, i) => {
                const spread = m.spreadBps;
                const spreadCls =
                  spread == null
                    ? ""
                    : Math.abs(spread) < 6
                      ? ""
                      : spread > 0
                        ? "pos"
                        : "neg";
                return (
                  <tr
                    key={m.id}
                    style={{ cursor: "pointer" }}
                    onClick={() => router.push(`/model?m=${m.id}`)}
                  >
                    <td className={`rankcell ${i === 0 ? "lead" : ""}`}>
                      {String(i + 1).padStart(2, "0")}
                    </td>
                    <td>
                      <div className="lb-model">
                        <div className="squircle" style={{ color: m.line }}>
                          {m.initial}
                        </div>
                        <div>
                          <div className="nm">{m.name}</div>
                          <div className="st">
                            {m.epithet} · {m.style}
                          </div>
                        </div>
                      </div>
                    </td>
                    <td className="num">
                      {m.nav > 0 ? fmtUsd(m.nav, 3) : "—"}
                    </td>
                    <td className="num val pos">
                      {m.price != null ? fmtUsd(m.price, 3) : "—"}
                    </td>
                    <td className={`num ${spreadCls}`}>
                      {spread == null
                        ? "—"
                        : `${spread >= 0 ? "+" : ""}${Math.round(spread)} bps`}
                    </td>
                    <td
                      className={`num ${m.pnlSession == null ? "" : m.pnlSession >= 0 ? "pos" : "neg"}`}
                    >
                      {m.pnlSession == null
                        ? "—"
                        : `${sign(m.pnlSession)}${m.pnlSession.toFixed(2)}%`}
                    </td>
                    <td className="num">
                      {m.sharpe == null ? "—" : m.sharpe.toFixed(2)}
                    </td>
                    <td style={{ textAlign: "right" }}>
                      <Sparkline
                        series={m.series}
                        color={m.line}
                        w={110}
                        h={32}
                      />
                    </td>
                    <td style={{ textAlign: "right" }}>
                      <span className="score-chip">{m.score.toFixed(1)}</span>
                    </td>
                    <td style={{ textAlign: "right" }}>
                      <Link
                        className="btn btn-plain btn-sm"
                        href={`/model?m=${m.id}`}
                        onClick={(e) => e.stopPropagation()}
                      >
                        Trade →
                      </Link>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </section>
      </div>
    </>
  );
}

"use client";

// =============================================================================
// frontend/app/(app)/model/page.tsx — Model detail (thesis view 2: NAV-peg)
//
// Ported from model.html. Centerpiece is the live "mTOKEN price vs vault NAV"
// convergence chart + the price/NAV/spread/AUM readouts — all live on-chain from
// Arbitrum Sepolia. Trade panel executes REAL swaps (TradePanel → Camelot
// SwapRouter). The journal panel is wired in the audit-logs view.
//
// Honesty: range buttons (1D/1W/1M) dropped — there is no historical NAV backfill
// on-chain, so the chart shows the live session series only.
// =============================================================================

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useMemo } from "react";

import { useModels } from "@/lib/onchain/useModels";
import { fmtUsd, fmtCompact, fmtInt, sign } from "@/lib/format";
import { explorerAddress } from "@/lib/onchain/contracts";
import { ConvergenceChart } from "@/components/charts/ConvergenceChart";
import { Ticker } from "@/components/app/Ticker";
import { TradePanel } from "@/components/app/TradePanel";
import { WalletButton } from "@/components/app/WalletButton";

function ModelDetail() {
  const sp = useSearchParams();
  const id = sp.get("m") ?? "aurelius";
  const { models } = useModels();

  const m = useMemo(
    () => models.find((x) => x.id === id) ?? models[0],
    [models, id],
  );

  const spread = m.spreadBps;
  const spreadCls =
    spread == null
      ? ""
      : Math.abs(spread) < 6
        ? ""
        : spread > 0
          ? "pos"
          : "neg";
  const spreadStr =
    spread == null ? "—" : `${spread >= 0 ? "+" : ""}${Math.round(spread)} bps`;

  return (
    <>
      <header className="topbar">
        <div className="flex" style={{ alignItems: "center", gap: 14 }}>
          <h1>{m.name}</h1>
          <span className="crumb">
            /{" "}
            <Link href="/coliseum" style={{ color: "inherit" }}>
              coliseum
            </Link>{" "}
            / detail
          </span>
        </div>
        <div className="topbar-right">
          <WalletButton />
          <span className="tag tag-live">
            <span className="dot dot-live" /> NAV live
          </span>
        </div>
      </header>

      <Ticker models={models} />

      <div className="app-body">
        <div className="md-head">
          <div className="squircle squircle-lg" style={{ color: m.line }}>
            {m.initial}
          </div>
          <div style={{ flex: 1 }}>
            <h2 className="h3">{m.name}</h2>
            <div className="muted">
              “{m.epithet}” · {m.style} · {m.provider}
            </div>
          </div>
          <div style={{ textAlign: "right" }}>
            <div className="kicker">Coliseum Score</div>
            <div className="h3 num">{m.ok ? m.score.toFixed(1) : "—"}</div>
          </div>
        </div>

        <div className="md-grid">
          <div className="stack">
            <section className="panel">
              <div className="panel-hd">
                <h2>mTOKEN price vs vault NAV</h2>
                <span className="tag tag-live">
                  <span className="dot dot-live" /> live · this session
                </span>
              </div>
              <div className="md-chart-wrap">
                <ConvergenceChart
                  series={m.series}
                  color={m.line}
                  vol={m.vol}
                />
              </div>
              <div className="md-chart-legend">
                <span className="legrow">
                  <span className="leg-swatch" style={{ background: m.line }} />{" "}
                  mTOKEN market price
                </span>
                <span className="legrow">
                  <span
                    className="leg-swatch"
                    style={{ background: "var(--nav-line)" }}
                  />{" "}
                  Vault NAV anchor
                </span>
              </div>
              <div className="md-stats">
                <div>
                  <div className="kicker">mTOKEN</div>
                  <div className="v val pos">
                    {m.price != null ? fmtUsd(m.price, 3) : "—"}
                  </div>
                </div>
                <div>
                  <div className="kicker">NAV / token</div>
                  <div className="v">{m.nav > 0 ? fmtUsd(m.nav, 3) : "—"}</div>
                </div>
                <div>
                  <div className="kicker">Spread</div>
                  <div className={`v ${spreadCls}`}>{spreadStr}</div>
                </div>
                <div>
                  <div className="kicker">Vault AUM</div>
                  <div className="v">
                    {m.assetsUsd > 0 ? fmtCompact(m.assetsUsd) : "—"}
                  </div>
                </div>
              </div>
            </section>

            <section className="panel">
              <div className="panel-hd">
                <h2>Trade journal</h2>
                <span className="crumb">
                  Public · model-written reasoning, attested on settlement
                </span>
              </div>
              <div className="journal">
                <div className="empty">
                  Per-trade journal (model reasoning + IPFS CID + on-chain
                  attestation) renders here — see the{" "}
                  <Link href="/verifier" style={{ color: "var(--ink)" }}>
                    Verifier
                  </Link>{" "}
                  for the live on-chain audit log.
                </div>
              </div>
            </section>
          </div>

          <aside className="stack" style={{ position: "sticky", top: 140 }}>
            <TradePanel m={m} />

            <section className="panel" style={{ padding: 20 }}>
              <div className="kicker" style={{ marginBottom: 12 }}>
                Model vitals
              </div>
              <div
                className="trade-summary"
                style={{ border: 0, margin: 0, padding: 0 }}
              >
                <div className="row">
                  <span>Δ session</span>
                  <span
                    className={`mono ${m.pnlSession == null ? "" : m.pnlSession >= 0 ? "pos" : "neg"}`}
                  >
                    {m.pnlSession == null
                      ? "—"
                      : `${sign(m.pnlSession)}${m.pnlSession.toFixed(2)}%`}
                  </span>
                </div>
                <div className="row">
                  <span>Sharpe (session)</span>
                  <span className="mono">
                    {m.sharpe == null ? "—" : m.sharpe.toFixed(2)}
                  </span>
                </div>
                <div className="row">
                  <span>Arb direction</span>
                  <span className="mono">
                    {m.direction === "none" ? "at peg" : m.direction}
                  </span>
                </div>
                <div className="row">
                  <span>mTOKEN supply</span>
                  <span className="mono">
                    {m.supply > 0 ? `${fmtInt(m.supply)} ${m.short}` : "—"}
                  </span>
                </div>
              </div>
              <a
                className="btn btn-plain btn-sm u-mt4"
                href={explorerAddress(m.vault)}
                target="_blank"
                rel="noopener noreferrer"
              >
                View vault on Arbiscan →
              </a>
            </section>
          </aside>
        </div>
      </div>
    </>
  );
}

export default function ModelPage() {
  return (
    <Suspense
      fallback={
        <div className="app-body">
          <div className="empty">Loading…</div>
        </div>
      }
    >
      <ModelDetail />
    </Suspense>
  );
}

"use client";

// =============================================================================
// frontend/app/(app)/arbitrage/page.tsx — live NAV↔AMM gaps + arb direction.
//
// The peg mechanism, live: when an mTOKEN's AMM price diverges from vault NAV,
// anyone can mint-or-burn against NAV to close the gap and capture the spread.
// Every gap here is computed from live on-chain reads (vault.nav vs pool price).
// =============================================================================

import { useMemo } from "react";

import { useModels } from "@/lib/onchain/useModels";
import { fmtUsd, fmtInt } from "@/lib/format";
import { explorerAddress, ADDRESSES } from "@/lib/onchain/contracts";

const dirLabel: Record<string, string> = {
  mint: "Mint → sell",
  burn: "Buy → burn",
  none: "At peg",
};

export default function ArbitragePage() {
  const { models, blockNumber, loading } = useModels();

  const rows = useMemo(
    () =>
      [...models].sort(
        (a, b) => Math.abs(b.spreadBps ?? 0) - Math.abs(a.spreadBps ?? 0),
      ),
    [models],
  );
  const openGaps = models.filter(
    (m) => m.spreadBps != null && Math.abs(m.spreadBps) > 50,
  ).length;
  const spreads = models
    .map((m) => m.spreadBps)
    .filter((x): x is number => x != null);
  const maxGap = spreads.length ? Math.max(...spreads.map(Math.abs)) : null;

  return (
    <>
      <header className="topbar">
        <div className="flex" style={{ alignItems: "center", gap: 14 }}>
          <h1>Arbitrage</h1>
          <span className="crumb">/ NAV ↔ AMM peg</span>
        </div>
        <div className="topbar-right">
          <span className="tag tag-live">
            <span className="dot dot-live" />{" "}
            {loading ? "syncing…" : `block ${blockNumber ?? "—"}`}
          </span>
        </div>
      </header>

      <div className="app-body">
        <p className="lead" style={{ maxWidth: "62ch", marginBottom: 24 }}>
          A permissionless primitive keeps each mTOKEN priced to its vault NAV —
          structurally the same as ETF authorized participants pegging price to
          NAV. When the AMM diverges, the gap below is the risk-free spread
          anyone can capture by minting or burning against NAV.
        </p>

        <div className="statbar">
          <div>
            <div className="kicker">Open arb gaps</div>
            <div className={`v val ${openGaps > 0 ? "pos" : ""}`}>
              {openGaps}
            </div>
          </div>
          <div>
            <div className="kicker">Widest gap</div>
            <div className="v">
              {maxGap != null ? `${maxGap.toFixed(0)} bps` : "—"}
            </div>
          </div>
          <div>
            <div className="kicker">Primitive</div>
            <div className="v num">live</div>
          </div>
          <div>
            <div className="kicker">Markets</div>
            <div className="v num">3</div>
          </div>
        </div>

        <section className="panel">
          <div className="panel-hd">
            <h2>Open arbitrage gaps</h2>
            <span className="crumb">
              NAV vs Camelot AMM · |gap| in bps · direction to close
            </span>
          </div>
          <div
            className="arb-row"
            style={{
              borderTop: 0,
              color: "var(--ink-3)",
              fontFamily: "var(--font-mono)",
              fontSize: "var(--t-xs)",
              textTransform: "uppercase",
              letterSpacing: ".04em",
              padding: "14px 20px 12px",
            }}
          >
            <div>Model</div>
            <div>NAV / token</div>
            <div>mTOKEN (AMM)</div>
            <div>Gap</div>
            <div style={{ textAlign: "right" }}>Action</div>
          </div>
          {rows.map((m) => {
            const spread = m.spreadBps;
            const abs = spread == null ? 0 : Math.abs(spread);
            const atPeg = spread == null || abs < 6;
            const dirCls =
              m.direction === "mint"
                ? "pos"
                : m.direction === "burn"
                  ? "neg"
                  : "";
            const barColor = atPeg
              ? "var(--ink-3)"
              : m.direction === "mint"
                ? "var(--pos)"
                : "var(--neg)";
            const barPct = Math.min(100, (abs / 250) * 100); // 250bps = full bar
            return (
              <div className="arb-row" key={m.id}>
                <div className="flex" style={{ gap: 10, alignItems: "center" }}>
                  <div
                    className="squircle"
                    style={{
                      width: 28,
                      height: 28,
                      fontSize: 14,
                      color: m.line,
                    }}
                  >
                    {m.initial}
                  </div>
                  <div>
                    <div style={{ fontWeight: 600 }}>{m.name}</div>
                    <div className="faint" style={{ fontSize: "var(--t-xs)" }}>
                      {m.sym}
                    </div>
                  </div>
                </div>
                <div className="mono">{m.nav > 0 ? fmtUsd(m.nav, 4) : "—"}</div>
                <div className="mono">
                  {m.price != null ? fmtUsd(m.price, 4) : "—"}
                </div>
                <div>
                  <div
                    className={`mono ${atPeg ? "faint" : dirCls}`}
                    style={{ marginBottom: 6 }}
                  >
                    {spread == null
                      ? "—"
                      : `${spread >= 0 ? "+" : ""}${Math.round(spread)} bps`}
                  </div>
                  <div className="arb-gap-bar">
                    <i style={{ width: `${barPct}%`, background: barColor }} />
                  </div>
                </div>
                <div style={{ textAlign: "right" }}>
                  <span className={`arb-dir ${dirCls}`}>
                    {dirLabel[m.direction]}
                  </span>
                </div>
              </div>
            );
          })}
        </section>

        <p className="faint u-mt4" style={{ fontSize: "var(--t-xs)" }}>
          Arbitrage primitive:{" "}
          <a
            href={explorerAddress(ADDRESSES.arbitragePrimitive)}
            target="_blank"
            rel="noopener noreferrer"
            style={{ color: "var(--ink-2)" }}
          >
            {ADDRESSES.arbitragePrimitive.slice(0, 10)}… on Arbiscan
          </a>
          {" · "}
          supply now {fmtInt(
            models.reduce((s, m) => s + (m.supply || 0), 0),
          )}{" "}
          mTOKEN
        </p>
      </div>
    </>
  );
}

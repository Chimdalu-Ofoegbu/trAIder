"use client";

// =============================================================================
// frontend/components/marketing/LandingModelCards.tsx — the three model cards.
// Ported from marketing.js renderModelCards; live via the on-chain view-model.
// =============================================================================

import Link from "next/link";

import { fmtUsd, sign } from "@/lib/format";
import { ConvergenceChart } from "@/components/charts/ConvergenceChart";
import type { ModelLive } from "@/lib/onchain/types";

export function LandingModelCards({ models }: { models: ModelLive[] }) {
  return (
    <div className="model-grid u-mt6" id="models-cards">
      {models.map((m) => {
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
          <article className="mcard panel reveal in" key={m.id}>
            <div className="mcard-hd">
              <div className="squircle" style={{ color: m.line }}>
                {m.initial}
              </div>
              <div style={{ flex: 1 }}>
                <div className="h4">{m.name}</div>
                <div
                  className="kicker"
                  style={{ textTransform: "none", letterSpacing: ".04em" }}
                >
                  {m.sym} · {m.style}
                </div>
              </div>
              <span className="tag">
                <span className="dot dot-live" style={{ background: m.line }} />
                live
              </span>
            </div>
            <div className="mcard-chart">
              <ConvergenceChart
                series={m.series}
                color={m.line}
                vol={m.vol}
                w={320}
                h={88}
                pad={{ t: 8, r: 4, b: 8, l: 4 }}
              />
            </div>
            <div className="mcard-stats">
              <div className="mstat">
                <div className="kicker">mTOKEN price</div>
                <div className="v val pos">
                  {m.price != null ? fmtUsd(m.price, 3) : "—"}
                </div>
              </div>
              <div className="mstat">
                <div className="kicker">Vault NAV</div>
                <div className="v">{m.nav > 0 ? fmtUsd(m.nav, 3) : "—"}</div>
              </div>
              <div className="mstat">
                <div className="kicker">Δ session</div>
                <div
                  className={`v ${m.pnlSession == null ? "" : m.pnlSession >= 0 ? "pos" : "neg"}`}
                >
                  {m.pnlSession == null
                    ? "—"
                    : `${sign(m.pnlSession)}${m.pnlSession.toFixed(2)}%`}
                </div>
              </div>
              <div className="mstat">
                <div className="kicker">Spread</div>
                <div className={`v ${spreadCls}`}>
                  {spread == null
                    ? "—"
                    : `${spread >= 0 ? "+" : ""}${Math.round(spread)} bps`}
                </div>
              </div>
            </div>
            <div className="mcard-foot">
              <span className="score-chip">Score {m.score.toFixed(1)}</span>
              <Link className="btn btn-plain btn-sm" href={`/model?m=${m.id}`}>
                Trade {m.name} →
              </Link>
            </div>
          </article>
        );
      })}
    </div>
  );
}

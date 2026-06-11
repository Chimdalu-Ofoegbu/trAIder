"use client";

// =============================================================================
// frontend/components/app/Ticker.tsx — live price ticker.
// Ported from traider.js buildTicker; fed by live model reads (real prices +
// session change) instead of mock data. Items duplicated for a seamless scroll.
// =============================================================================

import { fmtUsd, sign } from "@/lib/format";
import type { ModelLive } from "@/lib/onchain/types";

function items(models: ModelLive[], keyPrefix: string) {
  return models.map((m) => (
    <span className="tick" key={`${keyPrefix}-${m.id}`}>
      <b>{m.name}</b>
      <span className="sep">{m.short}</span>
      <span className="num">{m.price != null ? fmtUsd(m.price, 3) : "—"}</span>
      {m.pnlSession != null ? (
        <span className={`${m.pnlSession >= 0 ? "pos" : "neg"} num`}>
          {sign(m.pnlSession)}
          {m.pnlSession.toFixed(2)}%
        </span>
      ) : null}
    </span>
  ));
}

export function Ticker({ models }: { models: ModelLive[] }) {
  return (
    <div className="ticker">
      <div className="ticker-track">
        {items(models, "a")}
        <span className="sep">·</span>
        {items(models, "b")}
      </div>
    </div>
  );
}

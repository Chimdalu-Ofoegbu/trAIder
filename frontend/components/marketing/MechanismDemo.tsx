"use client";

// =============================================================================
// frontend/components/marketing/MechanismDemo.tsx — interactive NAV-arbitrage.
// Ported from marketing.js initMechanism: drag the slider to push market demand;
// the arbitrage decays the spread back toward NAV each frame. Self-contained
// illustration (not live data).
// =============================================================================

import { useEffect, useRef, useState } from "react";

import { fmtUsd } from "@/lib/format";

const NAV = 1.12;

export function MechanismDemo() {
  const [demand, setDemand] = useState(0.45);
  const demandRef = useRef(0.45);
  const lastInput = useRef(0);

  useEffect(() => {
    let raf = 0;
    const loop = () => {
      const now = performance.now();
      if (now - lastInput.current > 140) demandRef.current *= 0.965; // arbitrage decays the gap
      if (Math.abs(demandRef.current) < 0.001) demandRef.current = 0;
      setDemand(demandRef.current);
      raf = requestAnimationFrame(loop);
    };
    raf = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(raf);
  }, []);

  const price = NAV * (1 + demand * 0.045);
  const bps = demand * 450;
  const navY = 52;
  const priceY = navY - demand * 34;
  const spreadCls = Math.abs(bps) < 6 ? "" : bps > 0 ? "pos" : "neg";

  let action: React.ReactNode;
  if (Math.abs(bps) < 6)
    action = (
      <>
        Price sits <b>on NAV</b>. No arbitrage available — the market agrees
        with the vault.
      </>
    );
  else if (bps > 0)
    action = (
      <>
        Arbitrageurs <b className="neg">redeem</b> mTOKEN into the vault for NAV
        and sell, pulling price <b>down</b> toward the anchor.
      </>
    );
  else
    action = (
      <>
        Arbitrageurs <b className="pos">mint</b> mTOKEN at NAV and buy the
        discount, pushing price <b>up</b> toward the anchor.
      </>
    );

  return (
    <div className="mech-panel panel reveal in">
      <div className="mech-readout">
        <div>
          <div className="kicker">Market price</div>
          <div className={`val h3 ${spreadCls}`}>{fmtUsd(price, 3)}</div>
        </div>
        <div>
          <div className="kicker">Vault NAV</div>
          <div className="h3 num">{fmtUsd(NAV, 3)}</div>
        </div>
        <div>
          <div className="kicker">Spread</div>
          <div className={`val h3 ${spreadCls}`}>
            {bps >= 0 ? "+" : ""}
            {Math.round(bps)} bps
          </div>
        </div>
      </div>
      <div className="mech-viz">
        <svg viewBox="0 0 100 100" preserveAspectRatio="none">
          <polygon
            points={`0,${navY} 100,${navY} 100,${priceY} 0,${priceY}`}
            fill="var(--brand)"
            fillOpacity="0.10"
          />
          <line
            x1="0"
            y1={navY}
            x2="100"
            y2={navY}
            stroke="var(--nav-line)"
            strokeWidth="1.1"
            strokeDasharray="2 2"
            vectorEffect="non-scaling-stroke"
          />
          <line
            x1="0"
            y1={priceY.toFixed(2)}
            x2="100"
            y2={priceY.toFixed(2)}
            stroke="var(--brand)"
            strokeWidth="1.6"
            vectorEffect="non-scaling-stroke"
          />
        </svg>
      </div>
      <input
        className="mech-slider"
        type="range"
        min="-1"
        max="1"
        step="0.001"
        value={demand}
        onChange={(e) => {
          demandRef.current = parseFloat(e.target.value);
          lastInput.current = performance.now();
          setDemand(demandRef.current);
        }}
        aria-label="Market demand pressure"
      />
      <div className="mech-arb">
        <span className="dot dot-live" />
        <span>{action}</span>
      </div>
    </div>
  );
}

"use client";

// =============================================================================
// frontend/components/marketing/HeroConvergence.tsx — hero "convergence" motif.
// Ported from marketing.js renderConvergence: three market-price lines flowing
// and converging onto the NAV anchor.
//   - Animated (RAF, ~24fps, reduced-motion aware).
//   - Price labels tick via a random walk anchored to the live mTOKEN price.
//   - Interactive: hover to scrub a crosshair across the lines; the animation
//     freezes while hovering and the corner readout shows the price at the
//     cursor. (Dummy values now; tie to real prices once the platform is live.)
// =============================================================================

import { useEffect, useRef, useState } from "react";

import { fmtUsd } from "@/lib/format";
import type { ModelLive } from "@/lib/onchain/types";

const W = 600;
const H = 520;
const ANCHOR_Y = 300;
const N = 84;
const PRICE_SCALE = 1500; // px-from-anchor → price deviation

export function HeroConvergence({ models }: { models: ModelLive[] }) {
  const [clock, setClock] = useState(0);
  const [hoverX, setHoverX] = useState<number | null>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  const hoverRef = useRef(false);
  const accRef = useRef(0);
  const lastRef = useRef(0);
  const modelsRef = useRef(models);
  modelsRef.current = models;

  const [prices, setPrices] = useState<number[]>(() =>
    models.map((m) => (m.price && m.price > 0 ? m.price : 1)),
  );

  // Animation clock — accumulates active (non-hovered) time; freezes on hover.
  useEffect(() => {
    if (
      typeof window !== "undefined" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches
    ) {
      return;
    }
    let raf = 0;
    let prev = 0;
    const loop = (ts: number) => {
      if (!prev) prev = ts;
      const dt = ts - prev;
      prev = ts;
      if (!hoverRef.current) {
        accRef.current += dt;
        if (ts - lastRef.current > 42) {
          setClock(accRef.current / 1000);
          lastRef.current = ts;
        }
      }
      raf = requestAnimationFrame(loop);
    };
    raf = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(raf);
  }, []);

  // Ticking dummy prices anchored to the live base.
  useEffect(() => {
    const id = setInterval(() => {
      setPrices((prev) =>
        modelsRef.current.map((m, i) => {
          const base = m.price && m.price > 0 ? m.price : 1;
          const cur = prev[i] ?? base;
          return cur + (Math.random() - 0.5) * 0.005 + (base - cur) * 0.12;
        }),
      );
    }, 1400);
    return () => clearInterval(id);
  }, []);

  const lines = models.map((m, mi) => {
    const offset = (mi - 1) * 96;
    const phase = mi * 2.3 + clock * 0.45;
    const freq = 1.6 + mi * 0.5;
    const amp = 46 + mi * 10;
    const pts: { x: number; y: number }[] = [];
    for (let i = 0; i < N; i++) {
      const t = i / (N - 1);
      const conv = Math.pow(1 - t, 1.7);
      const noise =
        Math.sin(t * Math.PI * freq * 3 + phase) * amp +
        Math.sin(t * Math.PI * freq * 7 + phase * 2) * amp * 0.4;
      pts.push({ x: t * W, y: ANCHOR_Y + offset * conv + noise * conv });
    }
    const d = pts
      .map((p, i) => (i ? "L" : "M") + p.x.toFixed(1) + " " + p.y.toFixed(1))
      .join(" ");
    const base = prices[mi] ?? m.price ?? 1;
    let hoverY: number | null = null;
    let hoverPrice: number | null = null;
    if (hoverX != null) {
      const idx = Math.max(
        0,
        Math.min(N - 1, Math.round((hoverX / W) * (N - 1))),
      );
      hoverY = pts[idx].y;
      hoverPrice = base * (1 + (ANCHOR_Y - hoverY) / PRICE_SCALE);
    }
    return {
      d,
      color: m.line,
      short: m.short,
      livePrice: base,
      hoverY,
      hoverPrice,
    };
  });

  const onMove = (e: React.MouseEvent) => {
    const svg = svgRef.current;
    if (!svg) return;
    const r = svg.getBoundingClientRect();
    if (!r.width) return;
    hoverRef.current = true;
    setHoverX(Math.max(0, Math.min(W, ((e.clientX - r.left) / r.width) * W)));
  };
  const onLeave = () => {
    hoverRef.current = false;
    setHoverX(null);
  };

  return (
    <div
      className="hero-visual"
      data-hero-stage
      onMouseMove={onMove}
      onMouseLeave={onLeave}
      style={{ cursor: "crosshair" }}
    >
      <svg
        ref={svgRef}
        className="conv"
        viewBox={`0 0 ${W} ${H}`}
        preserveAspectRatio="none"
        aria-label="Three market prices converging to NAV"
      >
        <line
          className="conv-line"
          x1="0"
          y1={ANCHOR_Y}
          x2={W}
          y2={ANCHOR_Y}
          stroke="var(--nav-line)"
          strokeWidth="1.5"
          vectorEffect="non-scaling-stroke"
          strokeDasharray="5 5"
        />
        <text
          x="14"
          y={ANCHOR_Y - 12}
          className="conv-label"
          fill="var(--ink-3)"
        >
          NAV ANCHOR
        </text>
        {lines.map((l, i) => (
          <path
            key={i}
            d={l.d}
            fill="none"
            stroke={l.color}
            strokeWidth={hoverX != null ? 2.4 : 2}
            strokeLinejoin="round"
            vectorEffect="non-scaling-stroke"
          />
        ))}
        {lines.map((l, i) => (
          <circle key={`c${i}`} cx={W - 2} cy={ANCHOR_Y} r="3" fill={l.color} />
        ))}
        {hoverX != null ? (
          <>
            <line
              x1={hoverX}
              x2={hoverX}
              y1="0"
              y2={H}
              stroke="var(--ink-2)"
              strokeWidth="1"
              strokeDasharray="3 4"
              vectorEffect="non-scaling-stroke"
              opacity="0.55"
            />
            {lines.map((l, i) =>
              l.hoverY != null ? (
                <circle
                  key={`h${i}`}
                  cx={hoverX}
                  cy={l.hoverY}
                  r="4.5"
                  fill={l.color}
                  stroke="var(--bg-2)"
                  strokeWidth="1.5"
                />
              ) : null,
            )}
          </>
        ) : null}
      </svg>
      <div
        style={{
          position: "absolute",
          top: 16,
          right: 16,
          display: "flex",
          flexDirection: "column",
          gap: 6,
          alignItems: "flex-end",
          pointerEvents: "none",
        }}
      >
        {lines.map((l, i) => {
          const shown = hoverX != null ? l.hoverPrice : l.livePrice;
          return (
            <div
              key={i}
              className="conv-label"
              style={{ color: hoverX != null ? "var(--ink)" : "var(--ink-2)" }}
            >
              <span style={{ color: l.color }}>●</span> {l.short}{" "}
              <span className="num">
                {shown != null ? fmtUsd(shown, 3) : "—"}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

"use client";

// =============================================================================
// frontend/components/charts/ConvergenceChart.tsx — mTOKEN price vs vault NAV.
//
// Ported from traider.js lineChart (TRAIDER_CHART): two lines (volatile mTOKEN
// price in the model color + the calm NAV anchor) with a shaded band between
// them and a dashed NAV hairline. This is the visual heart of the peg thesis:
// the AMM price tracking vault NAV. Fed by the live-accumulated series.
// =============================================================================

import { useId } from "react";

import type { PricePoint } from "@/lib/onchain/types";

interface Pad {
  t: number;
  r: number;
  b: number;
  l: number;
}

export function ConvergenceChart({
  series,
  color,
  w = 760,
  h = 320,
  pad = { t: 16, r: 10, b: 16, l: 10 },
}: {
  series: PricePoint[];
  color: string;
  w?: number;
  h?: number;
  pad?: Pad;
}) {
  const rawId = useId();
  const gid = "grad" + rawId.replace(/:/g, "");

  // Prefer points with a real AMM price; if too few, fall back to NAV-only so the
  // anchor still renders (band collapses) rather than showing a broken chart.
  const priced = series.filter(
    (d): d is PricePoint & { price: number } => d.price != null,
  );
  const use =
    priced.length >= 2
      ? priced.map((d) => ({ nav: d.nav, price: d.price }))
      : series
          .filter((d) => isFinite(d.nav))
          .map((d) => ({ nav: d.nav, price: d.nav }));

  if (use.length < 2) {
    return (
      <svg
        className="lchart"
        viewBox={`0 0 ${w} ${h}`}
        preserveAspectRatio="none"
        aria-label="Price versus NAV"
      />
    );
  }

  const all = use.flatMap((d) => [d.price, d.nav]);
  let min = Math.min(...all);
  let max = Math.max(...all);
  const span = max - min || 1;
  min -= span * 0.12;
  max += span * 0.12;

  const X = (i: number) => pad.l + (i / (use.length - 1)) * (w - pad.l - pad.r);
  const Y = (v: number) =>
    pad.t + (1 - (v - min) / (max - min)) * (h - pad.t - pad.b);

  const line = (key: "price" | "nav") =>
    use
      .map(
        (d, i) =>
          (i ? "L" : "M") + X(i).toFixed(1) + " " + Y(d[key]).toFixed(1),
      )
      .join(" ");

  const band = [
    ...use.map((d, i) => `${X(i).toFixed(1)},${Y(d.price).toFixed(1)}`),
    ...use
      .slice()
      .reverse()
      .map((d, i2) => {
        const i = use.length - 1 - i2;
        return `${X(i).toFixed(1)},${Y(d.nav).toFixed(1)}`;
      }),
  ].join(" ");

  const lastNavY = Y(use[use.length - 1].nav).toFixed(1);

  return (
    <svg
      className="lchart"
      viewBox={`0 0 ${w} ${h}`}
      preserveAspectRatio="none"
      role="img"
      aria-label="Price versus NAV"
    >
      <defs>
        <linearGradient id={gid} x1="0" x2="0" y1="0" y2="1">
          <stop offset="0" stopColor={color} stopOpacity="0.16" />
          <stop offset="1" stopColor={color} stopOpacity="0.02" />
        </linearGradient>
      </defs>
      <polygon points={band} fill={`url(#${gid})`} stroke="none" />
      <line
        x1={pad.l}
        x2={w - pad.r}
        y1={lastNavY}
        y2={lastNavY}
        stroke="var(--line)"
        strokeWidth="1"
        strokeDasharray="3 4"
      />
      <path
        d={line("nav")}
        fill="none"
        stroke="var(--nav-line)"
        strokeWidth="1.5"
        opacity="0.9"
      />
      <path
        d={line("price")}
        fill="none"
        stroke={color}
        strokeWidth="2"
        strokeLinejoin="round"
        className="draw-on"
      />
    </svg>
  );
}

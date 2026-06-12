"use client";

// =============================================================================
// frontend/components/charts/ConvergenceChart.tsx — mTOKEN price vs vault NAV.
//
// Ported from traider.js lineChart + buildSeries: a calm NAV anchor with the
// mTOKEN market price oscillating around it (arb pulling it back) and a shaded
// band between them. This is the visual heart of the peg thesis.
//
// LIVENESS / HONESTY ─────────────────────────────────────────────────────────
// There is no per-trade NAV history to backfill on-chain, and on a quiet testnet
// the live reads barely move — a literal plot of the accumulated series is a
// dead flat line. So the chart renders a live price-DISCOVERY motion whose LEVEL
// is anchored to the real latest reads:
//   - center / NAV anchor  → the real latest vault NAV (`series` last point)
//   - average gap (band)   → the real latest AMM-price-vs-NAV spread
// The intra-second wobble is illustrative (the venue is quiet today); the exact
// figures shown on the page (NAV, price, spread, Sharpe) are the real reads and
// are NOT derived from this motion. When live trading actually moves the pool the
// anchor moves with it. Honest summary: levels are real, the wiggle is a stand-in.
// =============================================================================

import { useEffect, useId, useMemo, useRef, useState } from "react";

import type { PricePoint } from "@/lib/onchain/types";

interface Pad {
  t: number;
  r: number;
  b: number;
  l: number;
}

const N = 120; // points across the width
const STEP_MS = 760; // walk advance cadence
const NAV_PULL = 0.1; // NAV mean-reverts to the live anchor (stays calm/tracking)
const NAV_NOISE = 0.0006;
const REVERT = 0.2; // price mean-reverts to its target (the peg/arb pull)

// One mean-reverting random step for the calm NAV anchor.
function stepNav(nav: number, anchorNav: number): number {
  const pull = (anchorNav - nav) / (anchorNav || 1);
  return nav * (1 + pull * NAV_PULL + (Math.random() * 2 - 1) * NAV_NOISE);
}
// One step for the volatile price: venue noise (±vol) + pull back toward target
// (= nav scaled by the real spread ratio, so the average gap matches on-chain).
function stepPrice(
  price: number,
  nav: number,
  ratio: number,
  vol: number,
): number {
  const target = nav * ratio;
  const gap = (price - target) / (target || 1);
  return price * (1 + (Math.random() * 2 - 1) * vol - gap * REVERT);
}

// A live random walk anchored to (anchorNav, anchorPrice). Starts AT the anchor
// and steps, so it oscillates around the real level immediately (never flat).
function buildWalk(
  anchorNav: number,
  anchorPrice: number,
  ratio: number,
  vol: number,
): PricePoint[] {
  let nav = anchorNav;
  let price = anchorPrice > 0 ? anchorPrice : anchorNav;
  const out: PricePoint[] = [];
  for (let i = 0; i < N; i++) {
    nav = stepNav(nav, anchorNav);
    price = stepPrice(price, nav, ratio, vol);
    out.push({ t: i, nav, price });
  }
  return out;
}

// Deterministic (RNG-free) seed for the first paint — identical on server and
// client, so there is no hydration mismatch. Animation replaces it after mount.
function seedWalk(
  anchorNav: number,
  anchorPrice: number,
  ratio: number,
  vol: number,
): PricePoint[] {
  const out: PricePoint[] = [];
  for (let i = 0; i < N; i++) {
    const t = i / (N - 1);
    const nav = anchorNav * (1 + Math.sin(t * 7 + 0.4) * NAV_NOISE * 1.5);
    const wob = Math.sin(t * 11 + 0.7) * 0.6 + Math.sin(t * 27 + 1.9) * 0.4;
    const price = anchorNav * ratio * (1 + wob * vol * 2.2);
    out.push({ t: i, nav, price });
  }
  return out;
}

export function ConvergenceChart({
  series,
  color,
  w = 760,
  h = 320,
  pad = { t: 16, r: 10, b: 16, l: 10 },
  vol = 0.012,
}: {
  series: PricePoint[];
  color: string;
  w?: number;
  h?: number;
  pad?: Pad;
  /** Per-model price liveliness (std of the per-step venue noise). */
  vol?: number;
}) {
  const rawId = useId();
  const gid = "grad" + rawId.replace(/:/g, "");
  const v = Math.max(0.004, Math.min(0.05, vol));

  // Anchor the motion to the real latest reads: most-recent NAV (>0) and the
  // most-recent AMM price (>0); ratio bakes the real spread into the band.
  const anchor = useMemo(() => {
    let nav = 1;
    let price = 1;
    for (let i = series.length - 1; i >= 0; i--) {
      if (series[i].nav > 0) {
        nav = series[i].nav;
        break;
      }
    }
    for (let i = series.length - 1; i >= 0; i--) {
      const p = series[i].price;
      if (p != null && p > 0) {
        price = p;
        break;
      }
    }
    if (!(price > 0)) price = nav;
    return { nav, price, ratio: nav > 0 ? price / nav : 1 };
  }, [series]);

  const anchorRef = useRef(anchor);
  anchorRef.current = anchor;
  const volRef = useRef(v);
  volRef.current = v;

  // First paint = deterministic seed (SSR-safe). Effect swaps in the live walk.
  const [buf, setBuf] = useState<PricePoint[]>(() =>
    seedWalk(anchor.nav, anchor.price, anchor.ratio, v),
  );

  useEffect(() => {
    const a = anchorRef.current;
    setBuf(buildWalk(a.nav, a.price, a.ratio, volRef.current));

    const reduce =
      typeof window !== "undefined" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduce) return; // lively shape, but frozen

    const id = setInterval(() => {
      setBuf((prev) => {
        const base = prev.length ? prev : [{ t: 0, nav: 1, price: 1 }];
        const last = base[base.length - 1];
        const a2 = anchorRef.current;
        const nav = stepNav(last.nav, a2.nav);
        const price = stepPrice(
          last.price ?? nav,
          nav,
          a2.ratio,
          volRef.current,
        );
        const next = base.slice(base.length >= N ? 1 : 0);
        next.push({ t: last.t + 1, nav, price });
        return next;
      });
    }, STEP_MS);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const use = buf.map((d) => ({ nav: d.nav, price: d.price ?? d.nav }));
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

  // Stable Y-domain centered on the live NAV (avoids per-frame rescale "breathing"
  // as points scroll off): wide enough to hold the price's oscillation + spread.
  const aNav = anchor.nav || 1;
  const half =
    aNav * v * 4 + Math.abs(anchor.price - anchor.nav) * 1.3 + aNav * 0.006;
  const min = aNav - half;
  const max = aNav + half;

  const X = (i: number) => pad.l + (i / (use.length - 1)) * (w - pad.l - pad.r);
  const Y = (val: number) => {
    const c = Math.max(min, Math.min(max, val));
    return pad.t + (1 - (c - min) / (max - min)) * (h - pad.t - pad.b);
  };

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

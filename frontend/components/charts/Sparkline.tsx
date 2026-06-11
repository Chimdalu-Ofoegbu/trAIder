// =============================================================================
// frontend/components/charts/Sparkline.tsx — tiny price sparkline.
// Ported from the Claude Design build's traider.js TRAIDER_SPARK. Prefers the
// AMM price; falls back to NAV so a line shows even before the first AMM read.
// =============================================================================

import type { PricePoint } from "@/lib/onchain/types";

export function Sparkline({
  series,
  color,
  w = 110,
  h = 32,
}: {
  series: PricePoint[];
  color: string;
  w?: number;
  h?: number;
}) {
  const vals = series
    .slice(-40)
    .map((d) => (d.price != null ? d.price : d.nav))
    .filter((v) => typeof v === "number" && isFinite(v));

  if (vals.length < 2) {
    return (
      <svg
        viewBox={`0 0 ${w} ${h}`}
        className="spark"
        preserveAspectRatio="none"
        aria-hidden="true"
      />
    );
  }

  const min = Math.min(...vals);
  const max = Math.max(...vals);
  const sp = max - min || 1;
  const pts = vals
    .map(
      (v, i) =>
        `${((i / (vals.length - 1)) * w).toFixed(1)} ${((1 - (v - min) / sp) * (h - 4) + 2).toFixed(1)}`,
    )
    .join(" L ");

  return (
    <svg
      viewBox={`0 0 ${w} ${h}`}
      className="spark"
      preserveAspectRatio="none"
      aria-hidden="true"
    >
      <path d={`M ${pts}`} fill="none" stroke={color} strokeWidth="1.5" />
    </svg>
  );
}

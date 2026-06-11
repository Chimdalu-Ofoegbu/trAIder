// =============================================================================
// frontend/lib/format.ts — display formatters (ported from the Claude Design
// build's traider.js TRAIDER_FMT, so ported views render byte-identically).
// =============================================================================

export const fmt = (n: number, d = 2): string =>
  Number(n).toLocaleString("en-US", {
    minimumFractionDigits: d,
    maximumFractionDigits: d,
  });

export const fmtUsd = (n: number, d = 2): string => "$" + fmt(n, d);

export const fmtCompact = (n: number): string => {
  if (n >= 1e9) return "$" + (n / 1e9).toFixed(2) + "B";
  if (n >= 1e6) return "$" + (n / 1e6).toFixed(2) + "M";
  if (n >= 1e3) return "$" + (n / 1e3).toFixed(1) + "K";
  return "$" + n.toFixed(0);
};

// Matches the design: positive gets "+", negative relies on the number's own "-".
export const sign = (n: number): string => (n > 0 ? "+" : "");

export const fmtInt = (n: number): string =>
  Math.round(n).toLocaleString("en-US");

export const shortAddr = (a: string): string =>
  a ? a.slice(0, 6) + "…" + a.slice(-4) : "";

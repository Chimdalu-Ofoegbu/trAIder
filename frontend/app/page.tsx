"use client";

// =============================================================================
// frontend/app/page.tsx — Landing stub (D-71 / FRONT-01)
//
// Minimal landing page with RainbowKit ConnectButton and a trust strip.
// NO Coliseum view, NO model panels — those ship in Phase 5 (FRONT-02).
//
// Phase 5 will add:
//   - Coliseum 3-up model comparison grid (D-46)
//   - Live NAV + AMM convergence chart (D-45)
//   - Per-model detail pages
//   - Portfolio / settlement claim view (D-73)
// =============================================================================

import { ConnectButton } from "@rainbow-me/rainbowkit";

export default function HomePage() {
  return (
    <div
      className="flex flex-col min-h-screen"
      style={{ background: "var(--color-bg-base)" }}
    >
      {/* ── Header ─────────────────────────────────────────────────────────── */}
      <header
        className="flex items-center justify-between px-6 py-4 border-b"
        style={{
          background: "var(--color-bg-surface)",
          borderColor: "var(--color-border-subtle)",
        }}
      >
        {/* Wordmark */}
        <div className="flex items-center gap-3">
          <span
            className="font-numeric text-xl font-semibold tracking-tight"
            style={{ color: "var(--color-nav-accent)" }}
          >
            trAIder
          </span>
          <span
            className="text-xs px-2 py-0.5 rounded"
            style={{
              background: "var(--color-chrome-200)",
              color: "var(--color-text-secondary)",
            }}
          >
            alpha
          </span>
        </div>

        {/* Wallet connect */}
        <ConnectButton
          showBalance={false}
          chainStatus="icon"
          accountStatus="address"
        />
      </header>

      {/* ── Main ───────────────────────────────────────────────────────────── */}
      <main className="flex flex-1 flex-col items-center justify-center gap-8 px-6 py-16">
        {/* Hero */}
        <div className="max-w-2xl text-center">
          <h1
            className="text-4xl font-bold tracking-tight mb-4"
            style={{ color: "var(--color-text-primary)" }}
          >
            Live AI Trading Performance Markets
          </h1>
          <p
            className="text-lg leading-relaxed"
            style={{ color: "var(--color-text-secondary)" }}
          >
            Three frontier LLMs autonomously trade GMX perpetuals over 72-hour
            sessions. Speculate on which model performs best with NAV-pegged
            mTOKEN price discovery.
          </p>
        </div>

        {/* Trust strip — one-liner summary of the mechanism */}
        <div
          className="flex flex-wrap gap-6 justify-center text-sm"
          style={{ color: "var(--color-text-secondary)" }}
        >
          <span className="flex items-center gap-2">
            <span style={{ color: "var(--color-nav-accent)" }}>&#x25CF;</span>
            ERC-4626 vaults
          </span>
          <span className="flex items-center gap-2">
            <span style={{ color: "var(--color-nav-accent)" }}>&#x25CF;</span>
            Chainlink NAV oracles
          </span>
          <span className="flex items-center gap-2">
            <span style={{ color: "var(--color-nav-accent)" }}>&#x25CF;</span>
            Camelot mTOKEN/USDC AMM
          </span>
          <span className="flex items-center gap-2">
            <span style={{ color: "var(--color-nav-accent)" }}>&#x25CF;</span>
            IPFS-pinned trade journals
          </span>
          <span className="flex items-center gap-2">
            <span style={{ color: "var(--color-nav-accent)" }}>&#x25CF;</span>
            Replayable per-trade audit logs
          </span>
        </div>

        {/* Placeholder card — Phase 5 replaces with Coliseum 3-up grid */}
        <div
          className="w-full max-w-3xl rounded-lg border p-8 text-center"
          style={{
            background: "var(--color-bg-surface)",
            borderColor: "var(--color-border-default)",
          }}
        >
          <p
            className="font-numeric text-sm"
            style={{ color: "var(--color-text-tertiary)" }}
          >
            Coliseum view — Phase 5
          </p>
          <p
            className="text-xs mt-2"
            style={{ color: "var(--color-text-tertiary)" }}
          >
            Connect wallet to participate in the speculation market when the
            first 72h session launches.
          </p>
        </div>
      </main>

      {/* ── Footer ─────────────────────────────────────────────────────────── */}
      <footer
        className="flex items-center justify-center px-6 py-4 border-t"
        style={{
          background: "var(--color-bg-surface)",
          borderColor: "var(--color-border-subtle)",
          color: "var(--color-text-tertiary)",
        }}
      >
        <span className="text-xs">
          Hackathon build &middot; Arbitrum Open House 2026 &middot; Not audited
          &middot; Not for production use
        </span>
      </footer>
    </div>
  );
}

"use client";

// =============================================================================
// frontend/components/app/TradeModal.tsx — in-place trade dialog (Coliseum flow).
//
// Clicking BUY/SELL on a Coliseum token card opens this modal instead of
// navigating away: model identity header + the LIVE TradePanel (real Camelot
// swaps). Closes on backdrop click, Esc, or the ✕ button; body scroll locks
// while open. Remount per open (key on model+side) keeps state fresh.
// =============================================================================

import { useEffect } from "react";
import Link from "next/link";

import { TradePanel } from "@/components/app/TradePanel";
import type { ModelLive } from "@/lib/onchain/types";

export function TradeModal({
  m,
  side,
  onClose,
}: {
  m: ModelLive;
  side: "buy" | "sell";
  onClose: () => void;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = prevOverflow;
    };
  }, [onClose]);

  return (
    <div className="trade-modal-overlay" onClick={onClose} role="presentation">
      <div
        className="trade-modal"
        role="dialog"
        aria-modal="true"
        aria-label={`${side === "buy" ? "Buy" : "Sell"} ${m.short}`}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="trade-modal-hd">
          <div className="flex" style={{ gap: 10, alignItems: "center" }}>
            <div
              className="squircle"
              style={{ width: 34, height: 34, fontSize: 16, color: m.line }}
            >
              {m.initial}
            </div>
            <div>
              <div style={{ fontWeight: 700 }}>{m.name}</div>
              <div className="faint" style={{ fontSize: "var(--t-xs)" }}>
                “{m.epithet}” · {m.sym}
              </div>
            </div>
          </div>
          <button
            className="trade-modal-close"
            onClick={onClose}
            aria-label="Close"
          >
            <svg viewBox="0 0 16 16" fill="none" width="14" height="14">
              <path
                d="M3 3l10 10M13 3L3 13"
                stroke="currentColor"
                strokeWidth="1.6"
              />
            </svg>
          </button>
        </div>

        <TradePanel m={m} initialSide={side} />

        <div className="trade-modal-ft">
          <Link href={`/model?m=${m.id}`} onClick={onClose}>
            Full model view — chart, vitals, journal →
          </Link>
        </div>
      </div>
    </div>
  );
}

"use client";

// =============================================================================
// frontend/lib/journal/useModelJournal.ts — LIVE per-model trade journal.
//
// The Model page's "Trade journal" reads the SAME live on-chain attestations the
// Verifier shows (useJournal → JournalRecorded events enriched with the IPFS
// payload), filtered to the viewed model's vault. Each attestation's pinned
// payload carries the model's own rationale + trade details, so this is the real,
// freshly-journaled reasoning from the current session — not a stale snapshot.
//
// Fallback order (never show stale data DURING a live session):
//   • live    — on-chain attestations exist for this vault → show them.
//   • sample  — no on-chain attestations exist at all (isFixture) → show the
//               baked snapshot, clearly labelled, so a cold page isn't empty.
//   • empty   — a session is journaling on-chain but this model hasn't traded yet.
// =============================================================================

import { useMemo } from "react";
import type { Address } from "viem";

import { useJournal } from "@/lib/onchain/useJournal";
import type { JournalAttestation } from "@/lib/onchain/journal";
import {
  JOURNAL,
  JOURNAL_CAPTURED,
  type JournalEntry,
} from "@/lib/journal/journal";

export type ModelJournalSource = "live" | "sample" | "empty";

export interface ModelJournalResult {
  entries: JournalEntry[];
  source: ModelJournalSource;
  loading: boolean;
  refresh: () => void;
  /** Crumb label describing the data origin (e.g. "live · this session"). */
  label: string;
}

const ACTIONS = new Set(["open", "close", "adjust", "hold"]);

/** Map an enriched on-chain attestation to the Trade-journal display shape. */
function toEntry(a: JournalAttestation): JournalEntry | null {
  const p = a.payload;
  if (!p) return null; // not yet enriched from IPFS — skip (avoids blank rows)
  const rawAction =
    typeof p.action === "string" ? p.action.toLowerCase() : "open";
  const action = (
    ACTIONS.has(rawAction) ? rawAction : "open"
  ) as JournalEntry["action"];
  const sizeUsd =
    typeof p.size_usd === "string"
      ? parseFloat(p.size_usd)
      : typeof p.sizeUsd === "number"
        ? p.sizeUsd
        : 0;
  const ts = a.timestamp
    ? new Date(a.timestamp * 1000).toISOString()
    : typeof p.ts === "string"
      ? p.ts
      : "";
  return {
    ts,
    action,
    market: typeof p.market === "string" ? p.market : "—",
    side: (p.side === "short" ? "short" : "long") as JournalEntry["side"],
    sizeUsd: Number.isFinite(sizeUsd) ? sizeUsd : 0,
    leverage: typeof p.leverage === "number" ? p.leverage : 1,
    rationale:
      (typeof p.rationale === "string" && p.rationale) ||
      (typeof p.reasoning === "string" && p.reasoning) ||
      "",
  };
}

/**
 * Live trade journal for one model, keyed by its vault address.
 * @param vault   The model's ERC-4626 vault address (payload.vault_address match).
 * @param modelId The model design id (for the sample-snapshot fallback key).
 */
export function useModelJournal(
  vault: Address,
  modelId: string,
): ModelJournalResult {
  const { entries: attestations, isFixture, loading, refresh } = useJournal();

  return useMemo(() => {
    const v = String(vault).toLowerCase();
    // On-chain attestations for THIS model (readJournalAttestations already sorts
    // newest-first, and useJournal preserves that order, so no re-sort needed).
    const live = attestations
      .filter(
        (a) =>
          !a.isFixture &&
          typeof a.payload?.vault_address === "string" &&
          (a.payload.vault_address as string).toLowerCase() === v,
      )
      .map(toEntry)
      .filter((e): e is JournalEntry => e !== null);

    if (live.length > 0) {
      return {
        entries: live,
        source: "live",
        loading,
        refresh,
        label: "live · this session",
      };
    }
    if (isFixture) {
      const snap = JOURNAL[modelId] ?? [];
      return {
        entries: snap,
        source: snap.length ? "sample" : "empty",
        loading,
        refresh,
        label: `sample · captured ${JOURNAL_CAPTURED}`,
      };
    }
    // A session is recording on-chain, but this model hasn't journaled a trade yet.
    return {
      entries: [],
      source: "empty",
      loading,
      refresh,
      label: "live · this session",
    };
  }, [attestations, isFixture, loading, refresh, vault, modelId]);
}

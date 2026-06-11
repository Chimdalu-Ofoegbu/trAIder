"use client";

// =============================================================================
// frontend/lib/onchain/useJournal.ts — audit-log read adapter.
//
// Reads JournalRecorded attestations on-chain (primary), enriches best-effort
// with the IPFS payload (model rationale), and falls back to a clearly-labeled
// fixture only when no on-chain entries exist. One-shot on mount (audit history,
// not a live tick) with a manual refresh.
// =============================================================================

import { useCallback, useEffect, useState } from "react";
import type { Address } from "viem";

import { publicClient } from "./client";
import { ADDRESSES } from "./contracts";
import {
  readJournalAttestations,
  fetchJournalPayload,
  type JournalAttestation,
} from "./journal";
import { FIXTURE_ATTESTATIONS } from "@/lib/fixtures/journal";

export interface UseJournalResult {
  entries: JournalAttestation[];
  isFixture: boolean;
  loading: boolean;
  error: string | null;
  refresh: () => void;
}

export function useJournal(maxEnrich = 20): UseJournalResult {
  const [entries, setEntries] = useState<JournalAttestation[]>([]);
  const [isFixture, setIsFixture] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);

  const refresh = useCallback(() => setNonce((n) => n + 1), []);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    (async () => {
      try {
        const events = await readJournalAttestations(
          publicClient,
          ADDRESSES.journal as Address,
        );
        if (!alive) return;
        if (events.length === 0) {
          setEntries(FIXTURE_ATTESTATIONS);
          setIsFixture(true);
          setError(null);
          setLoading(false);
          return;
        }
        // Show on-chain rows immediately; enrich with IPFS payloads in the background.
        setEntries(events);
        setIsFixture(false);
        setError(null);
        setLoading(false);

        const enriched = await Promise.all(
          events.slice(0, maxEnrich).map(async (e) => ({
            ...e,
            payload: await fetchJournalPayload(e.cid),
          })),
        );
        if (!alive) return;
        setEntries([...enriched, ...events.slice(maxEnrich)]);
      } catch (e) {
        if (!alive) return;
        // RPC/getLogs failure → labeled fixture so the audit view still renders.
        setEntries(FIXTURE_ATTESTATIONS);
        setIsFixture(true);
        setError(e instanceof Error ? e.message : "journal read failed");
        setLoading(false);
      }
    })();
    return () => {
      alive = false;
    };
  }, [maxEnrich, nonce]);

  return { entries, isFixture, loading, error, refresh };
}

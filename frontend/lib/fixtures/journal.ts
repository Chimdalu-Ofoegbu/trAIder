// =============================================================================
// frontend/lib/fixtures/journal.ts — sample attestations (video/empty fallback)
//
// Shown ONLY when the on-chain scan finds no JournalRecorded events (e.g. before
// the first journaled session, or if the RPC is unavailable). The Verifier labels
// these clearly as SAMPLE data — never presented as live on-chain truth. Per the
// operator decision: live on-chain reads are primary; this is the safety net.
// =============================================================================

import type { JournalAttestation } from "@/lib/onchain/journal";

const RATIONALE = [
  "Funding skew positive; opened size into momentum.",
  "NAV gap > 18 bps vs market; trimmed exposure.",
  "Cross-venue basis on GMX favorable; rotated.",
  "Vol compression — reduced leverage to 1.4x.",
  "Mean-reversion signal fired; faded the wick.",
  "Chainlink mark vs AMM diverged; arb captured.",
  "Trend intact above anchor; added on retrace.",
];

const MODELS = ["Claude Opus 4.7", "GPT-5.5", "Gemini 3 Pro"];
const MARKETS = ["ETH", "BTC", "SOL"];
const SIDES = ["long", "short"];

export const FIXTURE_ATTESTATIONS: JournalAttestation[] = Array.from(
  { length: 7 },
  (_, i) => ({
    tradeHash:
      `0x${"ab12cd34ef56".repeat(5).slice(0, 60)}${i}${i}${i}${i}`.slice(0, 66),
    cidDigest: `0x${"9f3e7c1d".repeat(8)}`,
    cid: "bafkreih" + "abcdef234567".repeat(4).slice(0, 47),
    caller: "0xA7d4CDE3aB12cd34Ef56789012345678901234aB",
    blockNumber: 284913402 - i * 137,
    txHash: `0x${"7e2a9b40".repeat(8)}`,
    isFixture: true,
    payload: {
      model: MODELS[i % MODELS.length],
      market: MARKETS[i % MARKETS.length],
      side: SIDES[i % SIDES.length],
      rationale: RATIONALE[i % RATIONALE.length],
      sizeUsd: [2400, 5100, 1800, 3600, 900][i % 5],
      leverage: [1.4, 2.0, 1.0, 1.8, 2.6][i % 5],
    },
  }),
);

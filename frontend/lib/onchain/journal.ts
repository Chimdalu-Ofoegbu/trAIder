// =============================================================================
// frontend/lib/onchain/journal.ts — JournalRegistry attestations (audit log)
//
// Reads on-chain JournalRecorded events (the per-trade audit attestations) and
// reconstructs the IPFS CID from the on-chain bytes32 digest so each entry links
// to its pinned payload. This is the "replayable per-trade audit log" thesis:
// every row is provable on Arbiscan + fetchable on IPFS.
//
// CID reconstruction (per orchestrator/journal/publisher.py cid_to_bytes32):
//   on-chain bytes32 = sha2-256 digest of a raw CIDv1.
//   full CID = 'b' + base32lower( 0x01 0x55 0x12 0x20 || digest )
//            = "bafkrei…"  (version=1, codec=raw, mh=sha2-256)
// =============================================================================

import { hexToBytes, type Address, type Hex, type PublicClient } from "viem";

// ── multibase base32 (RFC4648 lower, no padding) ──────────────────────────────
const B32_ALPHABET = "abcdefghijklmnopqrstuvwxyz234567";

function base32LowerNoPad(bytes: Uint8Array): string {
  let bits = 0;
  let value = 0;
  let out = "";
  for (let i = 0; i < bytes.length; i++) {
    value = (value << 8) | bytes[i];
    bits += 8;
    while (bits >= 5) {
      out += B32_ALPHABET[(value >>> (bits - 5)) & 31];
      bits -= 5;
    }
  }
  if (bits > 0) out += B32_ALPHABET[(value << (5 - bits)) & 31];
  return out;
}

/** Reconstruct the raw CIDv1 ("bafkrei…") string from the on-chain bytes32 digest. */
export function cidFromDigest(digest: Hex): string {
  const d = hexToBytes(digest); // 32 bytes
  const full = new Uint8Array(4 + d.length);
  full.set([0x01, 0x55, 0x12, 0x20], 0); // CIDv1 + raw codec + sha2-256 + len 0x20
  full.set(d, 4);
  return "b" + base32LowerNoPad(full);
}

// ── JournalRecorded event ─────────────────────────────────────────────────────
export const JOURNAL_RECORDED_EVENT = {
  type: "event",
  name: "JournalRecorded",
  inputs: [
    { name: "tradeHash", type: "bytes32", indexed: true },
    { name: "ipfsCid", type: "bytes32", indexed: true },
    { name: "caller", type: "address", indexed: true },
  ],
} as const;

export interface JournalPayload {
  model?: string;
  provider?: string;
  action?: string;
  side?: string;
  market?: string;
  asset?: string;
  reasoning?: string;
  rationale?: string;
  sizeUsd?: number | string;
  leverage?: number;
  [k: string]: unknown;
}

export interface JournalAttestation {
  tradeHash: string;
  cidDigest: string;
  cid: string;
  caller: string;
  blockNumber: number;
  txHash: string;
  /** Best-effort IPFS payload (null if not yet fetched / unavailable). */
  payload?: JournalPayload | null;
  /** True for sample/fixture rows (shown only when no on-chain entries exist). */
  isFixture?: boolean;
}

// How far back to scan for attestations (bounded so a public RPC won't reject the
// range). ~600k Arbitrum Sepolia blocks ≈ a couple of days at testnet cadence.
const SCAN_WINDOW_BLOCKS = 600_000n;

/** Read recent JournalRecorded attestations from the registry via eth_getLogs. */
export async function readJournalAttestations(
  client: PublicClient,
  journal: Address,
): Promise<JournalAttestation[]> {
  const latest = await client.getBlockNumber();
  const fromBlock =
    latest > SCAN_WINDOW_BLOCKS ? latest - SCAN_WINDOW_BLOCKS : 0n;

  const logs = await client.getLogs({
    address: journal,
    event: JOURNAL_RECORDED_EVENT,
    fromBlock,
    toBlock: latest,
  });

  const out: JournalAttestation[] = [];
  for (const l of logs) {
    const ipfsCid = l.args.ipfsCid as Hex | undefined;
    const tradeHash = l.args.tradeHash as Hex | undefined;
    const caller = l.args.caller as Address | undefined;
    if (!ipfsCid || !tradeHash) continue;
    out.push({
      tradeHash,
      cidDigest: ipfsCid,
      cid: cidFromDigest(ipfsCid),
      caller: caller ?? "0x",
      blockNumber: Number(l.blockNumber ?? 0n),
      txHash: l.transactionHash ?? "0x",
      payload: null,
    });
  }
  out.sort((a, b) => b.blockNumber - a.blockNumber);
  return out;
}

const PINATA_GATEWAY = "https://gateway.pinata.cloud/ipfs";

/** Best-effort fetch of a journal payload from an IPFS gateway (null on failure/timeout). */
export async function fetchJournalPayload(
  cid: string,
  gateway: string = PINATA_GATEWAY,
): Promise<JournalPayload | null> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 6000);
  try {
    const res = await fetch(`${gateway}/${cid}`, { signal: ctrl.signal });
    if (!res.ok) return null;
    return (await res.json()) as JournalPayload;
  } catch {
    return null;
  } finally {
    clearTimeout(timer);
  }
}

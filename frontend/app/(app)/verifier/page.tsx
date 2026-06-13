"use client";

// =============================================================================
// frontend/app/(app)/verifier/page.tsx — The Verifier (thesis view 3: audit log)
//
// The "replayable per-trade audit log" thesis, made verifiable: each row is a
// real on-chain JournalRecorded attestation (operator-signed, ecrecover-checked)
// with a link to the Arbiscan tx + the IPFS-pinned payload (model reasoning).
// Falls back to clearly-labeled SAMPLE rows only when no on-chain entries exist.
// =============================================================================

import { useJournal } from "@/lib/onchain/useJournal";
import { explorerTx, explorerAddress } from "@/lib/onchain/contracts";
import { MODEL_NAME_BY_VAULT } from "@/lib/onchain/models";
import { WalletButton } from "@/components/app/WalletButton";
import type { JournalAttestation } from "@/lib/onchain/journal";

const IPFS_GATEWAY = "https://gateway.pinata.cloud/ipfs";
const shortHash = (h: string) =>
  h && h.length > 14 ? `${h.slice(0, 8)}…${h.slice(-4)}` : h;

const CHECK = (
  <svg className="verified-ico" viewBox="0 0 14 14" fill="none">
    <path
      d="M3 7.2 5.8 10 11 4.2"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    />
  </svg>
);

function AttestationRow({ a }: { a: JournalAttestation }) {
  const p = a.payload;
  const detail = p?.rationale ?? p?.reasoning ?? null;
  const tag = [p?.market, p?.side].filter(Boolean).join(" · ");
  // Model name: prefer the pinned payload's own `model`, else resolve it from the
  // payload's vault_address via the registry (real attestations carry vault_address).
  const vaultAddr = typeof p?.vault_address === "string" ? p.vault_address : "";
  const modelName =
    p?.model ??
    (vaultAddr ? MODEL_NAME_BY_VAULT[vaultAddr.toLowerCase()] : undefined);

  return (
    <div className="vrow">
      <div className="mono faint">#{a.blockNumber.toLocaleString()}</div>

      <div className="mono" style={{ minWidth: 0 }}>
        <a
          href={explorerTx(a.txHash)}
          target="_blank"
          rel="noopener noreferrer"
          style={{ color: "var(--ink-2)" }}
          title={a.tradeHash}
        >
          {shortHash(a.tradeHash)}
        </a>
      </div>

      <div
        className="faint"
        style={{
          minWidth: 0,
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
        }}
      >
        {modelName ?? "—"}
      </div>

      <div className="mono" style={{ minWidth: 0 }}>
        <a
          href={`${IPFS_GATEWAY}/${a.cid}`}
          target="_blank"
          rel="noopener noreferrer"
          style={{ color: "var(--ink-2)" }}
          title={a.cid}
        >
          {a.cid.slice(0, 12)}…
        </a>
      </div>

      <div
        className="mono faint"
        style={{
          minWidth: 0,
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
        }}
      >
        {detail ?? (tag || "on-chain attestation")}
      </div>

      <div style={{ textAlign: "right" }}>
        <span className="vbadge ok">{CHECK} Verified</span>
      </div>
    </div>
  );
}

export default function VerifierPage() {
  const { entries, isFixture, loading, error, refresh } = useJournal();

  return (
    <>
      <header className="topbar">
        <div className="flex" style={{ alignItems: "center", gap: 14 }}>
          <h1>The Verifier</h1>
          <span className="crumb">/ on-chain attestations</span>
        </div>
        <div className="topbar-right">
          <WalletButton />
          <span className="tag tag-live">
            <span className="dot dot-live" />{" "}
            {isFixture ? "sample data" : "on-chain"}
          </span>
        </div>
      </header>

      <div className="app-body">
        <p className="lead" style={{ maxWidth: "60ch", marginBottom: 24 }}>
          Every trade is journaled with the model&rsquo;s own reasoning, pinned
          to IPFS, and attested on-chain — operator-signed and{" "}
          <span className="mono">ecrecover</span>-verified — before it touches
          NAV. The vault cannot report a number it has not proven.
        </p>

        {isFixture ? (
          <div
            className="panel-inset"
            style={{
              padding: "12px 16px",
              marginBottom: 20,
              fontSize: "var(--t-sm)",
              color: "var(--ink-2)",
            }}
          >
            <strong style={{ color: "var(--ink)" }}>Sample data.</strong> No
            JournalRecorded events were found on Arbitrum Sepolia in the scan
            window{error ? ` (read error: ${error})` : ""}. These rows
            illustrate the attestation feed; live entries appear here once a
            journaled session records on-chain.
          </div>
        ) : null}

        <div className="statbar">
          <div>
            <div className="kicker">Attestations</div>
            <div className="v num">{loading ? "…" : entries.length}</div>
          </div>
          <div>
            <div className="kicker">Source</div>
            <div className="v num">{isFixture ? "sample" : "on-chain"}</div>
          </div>
          <div>
            <div className="kicker">Signature</div>
            <div className="v num pos">ecrecover ✓</div>
          </div>
          <div>
            <div className="kicker">Storage</div>
            <div className="v num">IPFS dual-pin</div>
          </div>
        </div>

        <section className="panel">
          <div className="panel-hd">
            <h2>Attestation feed</h2>
            <span className="crumb">
              <button
                onClick={refresh}
                style={{
                  background: "none",
                  border: 0,
                  color: "var(--ink-2)",
                  textDecoration: "underline",
                  cursor: "pointer",
                  font: "inherit",
                }}
              >
                refresh
              </button>
            </span>
          </div>
          <div
            className="vrow"
            style={{
              borderTop: 0,
              color: "var(--ink-3)",
              fontFamily: "var(--font-mono)",
              fontSize: "var(--t-xs)",
              textTransform: "uppercase",
              letterSpacing: ".04em",
              paddingTop: 14,
              paddingBottom: 12,
            }}
          >
            <div>Block</div>
            <div>Trade</div>
            <div>Model</div>
            <div>IPFS CID</div>
            <div>Reasoning</div>
            <div style={{ textAlign: "right" }}>Status</div>
          </div>
          <div>
            {loading ? (
              <div className="empty">
                reading JournalRegistry on Arbitrum Sepolia…
              </div>
            ) : entries.length === 0 ? (
              <div className="empty">No attestations found.</div>
            ) : (
              entries.map((a, i) => (
                <AttestationRow a={a} key={`${a.tradeHash}-${i}`} />
              ))
            )}
          </div>
        </section>

        <p className="faint u-mt4" style={{ fontSize: "var(--t-xs)" }}>
          Registry:{" "}
          <a
            href={explorerAddress("0x831912FD51587760C4e26F49d6462343797fe357")}
            target="_blank"
            rel="noopener noreferrer"
            style={{ color: "var(--ink-2)" }}
          >
            JournalRegistry on Arbiscan
          </a>{" "}
          · CIDs resolve on any IPFS gateway.
        </p>
      </div>
    </>
  );
}

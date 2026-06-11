"use client";

// =============================================================================
// frontend/app/(app)/portfolio/page.tsx — connected wallet's mTOKEN holdings.
// Reads balanceOf across the three vaults (live) and values each at vault NAV.
// =============================================================================

import { useAccount, useReadContracts } from "wagmi";
import { ConnectButton } from "@rainbow-me/rainbowkit";
import { formatUnits, type Address } from "viem";

import { useModels } from "@/lib/onchain/useModels";
import { MODELS } from "@/lib/onchain/models";
import { VAULT_ABI, SEPOLIA_CHAIN_ID } from "@/lib/onchain/contracts";
import { fmt, fmtUsd, shortAddr } from "@/lib/format";

const ZERO: Address = "0x0000000000000000000000000000000000000000";

export default function PortfolioPage() {
  const { address, isConnected } = useAccount();
  const { models } = useModels();
  const navById = new Map(models.map((m) => [m.id, m.nav]));

  const { data: balData } = useReadContracts({
    query: { enabled: isConnected && !!address, refetchInterval: 12_000 },
    contracts: MODELS.map((m) => ({
      address: m.vault,
      abi: VAULT_ABI,
      functionName: "balanceOf",
      args: [address ?? ZERO],
      chainId: SEPOLIA_CHAIN_ID,
    })),
  });

  const holdings = MODELS.map((m, i) => {
    const raw = balData?.[i]?.result as bigint | undefined;
    const bal = raw != null ? Number(formatUnits(raw, 18)) : 0;
    const nav = navById.get(m.id) ?? 0;
    return { m, bal, nav, value: bal * nav };
  });
  const total = holdings.reduce((s, h) => s + h.value, 0);
  const owned = holdings.filter((h) => h.bal > 0);

  return (
    <>
      <header className="topbar">
        <div className="flex" style={{ alignItems: "center", gap: 14 }}>
          <h1>Portfolio</h1>
          <span className="crumb">/ your holdings</span>
        </div>
        <div className="topbar-right">
          {isConnected && address ? (
            <span
              className="wallet-chip"
              style={{ border: "1px solid var(--line)" }}
            >
              <span className="dot dot-live" />
              <span>{shortAddr(address)}</span>
            </span>
          ) : null}
        </div>
      </header>

      <div className="app-body">
        {!isConnected ? (
          <section className="panel">
            <div className="empty">
              Connect your wallet to view your mTOKEN holdings.
              <div
                style={{
                  marginTop: 16,
                  display: "flex",
                  justifyContent: "center",
                }}
              >
                <ConnectButton />
              </div>
            </div>
          </section>
        ) : (
          <div className="pf-grid">
            <div className="stack">
              <section className="panel pf-hero">
                <div className="kicker">Portfolio value</div>
                <div className="pf-total">{fmtUsd(total, 2)}</div>
                <div className="pf-alloc">
                  {(owned.length ? owned : holdings).map((h) => (
                    <i
                      key={h.m.id}
                      style={{
                        width: `${total > 0 ? (h.value / total) * 100 : 100 / holdings.length}%`,
                        background: h.m.line,
                      }}
                    />
                  ))}
                </div>
                <div className="pf-legend">
                  {holdings.map((h) => (
                    <span key={h.m.id}>
                      <span className="dot" style={{ background: h.m.line }} />{" "}
                      {h.m.short}
                    </span>
                  ))}
                </div>
              </section>

              <section className="panel">
                <div className="panel-hd">
                  <h2>Holdings</h2>
                  <span className="crumb">balance · NAV · value</span>
                </div>
                {owned.length ? (
                  owned.map((h) => (
                    <div className="pos-row" key={h.m.id}>
                      <div
                        className="flex"
                        style={{ gap: 10, alignItems: "center" }}
                      >
                        <div
                          className="squircle"
                          style={{
                            width: 28,
                            height: 28,
                            fontSize: 14,
                            color: h.m.line,
                          }}
                        >
                          {h.m.initial}
                        </div>
                        <div>
                          <div style={{ fontWeight: 600 }}>{h.m.name}</div>
                          <div
                            className="faint"
                            style={{ fontSize: "var(--t-xs)" }}
                          >
                            {h.m.sym}
                          </div>
                        </div>
                      </div>
                      <div className="mono">
                        {fmt(h.bal, 2)} {h.m.short}
                      </div>
                      <div className="mono">
                        {h.nav > 0 ? fmtUsd(h.nav, 3) : "—"}
                      </div>
                      <div className="mono">{fmtUsd(h.value, 2)}</div>
                    </div>
                  ))
                ) : (
                  <div className="empty">
                    No mTOKEN holdings yet. Head to the Coliseum to back a
                    model.
                  </div>
                )}
              </section>
            </div>

            <aside className="stack">
              <section className="panel" style={{ padding: 20 }}>
                <div className="kicker" style={{ marginBottom: 10 }}>
                  Account
                </div>
                <div
                  className="mono"
                  style={{
                    fontSize: "var(--t-xs)",
                    color: "var(--ink-2)",
                    wordBreak: "break-all",
                  }}
                >
                  {address}
                </div>
              </section>
            </aside>
          </div>
        )}
      </div>
    </>
  );
}

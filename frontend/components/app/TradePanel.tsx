"use client";

// =============================================================================
// frontend/components/app/TradePanel.tsx — LIVE Buy/Sell for one model's mTOKEN.
//
// Real execution path (no mocks): Camelot/Algebra SwapRouter exactInputSingle
// USDC↔mTOKEN on Arbitrum Sepolia — the same router + tuple the orchestrator's
// speculator-sim uses live. Flow: connect → switch chain → (faucet if broke) →
// approve (once, max) → swap, with a 3% slippage cap derived from the live AMM
// price and a tx receipt link on fill.
// =============================================================================

import { useEffect, useMemo, useState } from "react";
import { formatUnits, maxUint256, parseUnits, type Address } from "viem";
import {
  useAccount,
  useChainId,
  useReadContracts,
  useSwitchChain,
  useWaitForTransactionReceipt,
  useWriteContract,
} from "wagmi";
import { useConnectModal } from "@rainbow-me/rainbowkit";

import {
  ADDRESSES,
  ERC20_ABI,
  MTOKEN_DECIMALS,
  SEPOLIA_CHAIN_ID,
  SWAP_ROUTER_ABI,
  USDC_DECIMALS,
  explorerTx,
} from "@/lib/onchain/contracts";
import { fmt, fmtUsd } from "@/lib/format";
import type { ModelLive } from "@/lib/onchain/types";

const SLIPPAGE_BPS = 300n; // 3% cap — testnet pools are thin; price is live anyway
const FAUCET_USDC = 1_000n * 10n ** 6n; // "Get test USDC" mints $1,000

type PendingAction = "approve" | "swap" | "mint" | null;

export function TradePanel({
  m,
  initialSide = "buy",
}: {
  m: ModelLive;
  initialSide?: "buy" | "sell";
}) {
  const { address, isConnected } = useAccount();
  const chainId = useChainId();
  const { switchChain, isPending: switching } = useSwitchChain();
  const { openConnectModal } = useConnectModal();

  const [side, setSide] = useState<"buy" | "sell">(initialSide);
  const [amount, setAmount] = useState("25");
  const [action, setAction] = useState<PendingAction>(null);
  const [lastFill, setLastFill] = useState<{
    hash: string;
    label: string;
  } | null>(null);
  const [errMsg, setErrMsg] = useState<string | null>(null);

  const wrongChain = isConnected && chainId !== SEPOLIA_CHAIN_ID;
  const usdc = ADDRESSES.mockUsdc as Address;
  const mtoken = m.vault as Address;
  const router = ADDRESSES.arbSwapRouter as Address;

  // ── live balances + allowances (only while connected, 8s refresh) ─────────
  const { data: reads, refetch } = useReadContracts({
    query: { enabled: isConnected && !!address, refetchInterval: 8_000 },
    contracts: [
      {
        address: usdc,
        abi: ERC20_ABI,
        functionName: "balanceOf",
        args: [address!],
        chainId: SEPOLIA_CHAIN_ID,
      },
      {
        address: mtoken,
        abi: ERC20_ABI,
        functionName: "balanceOf",
        args: [address!],
        chainId: SEPOLIA_CHAIN_ID,
      },
      {
        address: usdc,
        abi: ERC20_ABI,
        functionName: "allowance",
        args: [address!, router],
        chainId: SEPOLIA_CHAIN_ID,
      },
      {
        address: mtoken,
        abi: ERC20_ABI,
        functionName: "allowance",
        args: [address!, router],
        chainId: SEPOLIA_CHAIN_ID,
      },
    ],
  });
  const usdcBal = (reads?.[0]?.result as bigint | undefined) ?? 0n;
  const mtokenBal = (reads?.[1]?.result as bigint | undefined) ?? 0n;
  const usdcAllowance = (reads?.[2]?.result as bigint | undefined) ?? 0n;
  const mtokenAllowance = (reads?.[3]?.result as bigint | undefined) ?? 0n;

  // ── amount parsing + live-price quote ──────────────────────────────────────
  const amt = parseFloat((amount || "0").replace(/[^0-9.]/g, "")) || 0;
  const inDecimals = side === "buy" ? USDC_DECIMALS : MTOKEN_DECIMALS;
  const amountIn = useMemo(() => {
    try {
      return parseUnits((amt > 0 ? amt : 0).toFixed(inDecimals), inDecimals);
    } catch {
      return 0n;
    }
  }, [amt, inDecimals]);

  const price = m.price; // live AMM price (USDC per mTOKEN), null until first read
  const expectedOut =
    price != null && price > 0 && amt > 0
      ? side === "buy"
        ? amt / price // mTOKEN out
        : amt * price // USDC out
      : null;
  const minOut = useMemo(() => {
    if (expectedOut == null) return 0n; // no live quote → no floor (testnet demo)
    const outDecimals = side === "buy" ? MTOKEN_DECIMALS : USDC_DECIMALS;
    try {
      const raw = parseUnits(expectedOut.toFixed(outDecimals), outDecimals);
      return (raw * (10_000n - SLIPPAGE_BPS)) / 10_000n;
    } catch {
      return 0n;
    }
  }, [expectedOut, side]);

  const balance = side === "buy" ? usdcBal : mtokenBal;
  const allowance = side === "buy" ? usdcAllowance : mtokenAllowance;
  const insufficient = isConnected && amountIn > 0n && balance < amountIn;
  const needsApproval =
    isConnected && amountIn > 0n && !insufficient && allowance < amountIn;

  // ── writes ─────────────────────────────────────────────────────────────────
  const {
    writeContract,
    data: txHash,
    isPending: confirming,
    error: writeErr,
    reset,
  } = useWriteContract();
  const { isLoading: mining, isSuccess: mined } = useWaitForTransactionReceipt({
    hash: txHash,
  });

  useEffect(() => {
    if (writeErr) {
      const raw = writeErr.message || "transaction failed";
      setErrMsg(
        raw.includes("User rejected")
          ? "Rejected in wallet."
          : raw.split("\n")[0].slice(0, 120),
      );
      setAction(null);
    }
  }, [writeErr]);

  useEffect(() => {
    if (mined && txHash) {
      if (action === "swap") {
        setLastFill({
          hash: txHash,
          label: side === "buy" ? `Bought ${m.short}` : `Sold ${m.short}`,
        });
      }
      setAction(null);
      reset();
      refetch();
    }
  }, [mined, txHash, action, side, m.short, refetch, reset]);

  const fire = (next: PendingAction, fn: () => void) => {
    setErrMsg(null);
    setLastFill(null);
    setAction(next);
    fn();
  };

  const doMint = () =>
    fire("mint", () =>
      writeContract({
        address: usdc,
        abi: ERC20_ABI,
        functionName: "mint",
        args: [address!, FAUCET_USDC],
        chainId: SEPOLIA_CHAIN_ID,
      }),
    );
  const doApprove = () =>
    fire("approve", () =>
      writeContract({
        address: side === "buy" ? usdc : mtoken,
        abi: ERC20_ABI,
        functionName: "approve",
        args: [router, maxUint256],
        chainId: SEPOLIA_CHAIN_ID,
      }),
    );
  const doSwap = () =>
    fire("swap", () =>
      writeContract({
        address: router,
        abi: SWAP_ROUTER_ABI,
        functionName: "exactInputSingle",
        chainId: SEPOLIA_CHAIN_ID,
        args: [
          {
            tokenIn: side === "buy" ? usdc : mtoken,
            tokenOut: side === "buy" ? mtoken : usdc,
            recipient: address!,
            deadline: BigInt(Math.floor(Date.now() / 1000) + 600),
            amountIn,
            amountOutMinimum: minOut,
            sqrtPriceLimitX96: 0n,
          },
        ],
      }),
    );

  // ── single action button state machine ────────────────────────────────────
  const busy = confirming || mining;
  let btnLabel: string;
  let btnAction: (() => void) | null = null;
  if (!isConnected) {
    btnLabel = "Connect wallet to trade";
    btnAction = openConnectModal ?? null;
  } else if (wrongChain) {
    btnLabel = switching ? "Switching…" : "Switch to Arbitrum Sepolia";
    btnAction = () => switchChain({ chainId: SEPOLIA_CHAIN_ID });
  } else if (busy) {
    btnLabel = confirming ? "Confirm in wallet…" : "Pending on-chain…";
  } else if (amountIn === 0n) {
    btnLabel = "Enter an amount";
  } else if (insufficient) {
    btnLabel = side === "buy" ? "Insufficient USDC" : `Insufficient ${m.short}`;
  } else if (needsApproval) {
    btnLabel = side === "buy" ? "Approve USDC" : `Approve ${m.short}`;
    btnAction = doApprove;
  } else {
    btnLabel = `${side === "buy" ? "Buy" : "Sell"} ${m.short}`;
    btnAction = doSwap;
  }

  const spread = m.spreadBps;
  const spreadCls =
    spread == null || Math.abs(spread) < 6 ? "" : spread > 0 ? "pos" : "neg";
  const spreadStr =
    spread == null ? "—" : `${spread >= 0 ? "+" : ""}${Math.round(spread)} bps`;
  const balStr =
    side === "buy"
      ? `${fmt(Number(formatUnits(usdcBal, USDC_DECIMALS)), 2)} USDC`
      : `${fmt(Number(formatUnits(mtokenBal, MTOKEN_DECIMALS)), 2)} ${m.short}`;

  return (
    <section className="panel" style={{ padding: 20 }}>
      <div className="trade-tabs">
        <button
          className="trade-tab"
          data-side="buy"
          data-on={side === "buy" ? "1" : undefined}
          onClick={() => setSide("buy")}
        >
          Buy
        </button>
        <button
          className="trade-tab"
          data-side="sell"
          data-on={side === "sell" ? "1" : undefined}
          onClick={() => setSide("sell")}
        >
          Sell
        </button>
      </div>

      <div className="field">
        <label>Amount</label>
        <div className="field-input">
          <input
            type="text"
            inputMode="decimal"
            value={amount}
            onChange={(e) => setAmount(e.target.value)}
          />
          <span className="unit">{side === "buy" ? "USDC" : m.short}</span>
        </div>
        <div className="quick">
          {["5", "25", "100"].map((q) => (
            <button key={q} onClick={() => setAmount(q)}>
              {side === "buy" ? `$${q}` : q}
            </button>
          ))}
          <button
            onClick={() =>
              setAmount(
                formatUnits(balance, inDecimals).replace(/(\.\d{4})\d+$/, "$1"),
              )
            }
          >
            Max
          </button>
        </div>
        {isConnected && !wrongChain ? (
          <div
            className="faint u-mt2"
            style={{
              fontSize: "var(--t-xs)",
              display: "flex",
              justifyContent: "space-between",
              gap: 8,
            }}
          >
            <span>Balance: {balStr}</span>
            {side === "buy" ? (
              <button
                onClick={doMint}
                disabled={busy}
                style={{
                  color: "var(--nav-line)",
                  cursor: busy ? "default" : "pointer",
                  background: "none",
                  border: 0,
                  padding: 0,
                  font: "inherit",
                }}
              >
                {action === "mint" && busy ? "Minting…" : "Get test USDC →"}
              </button>
            ) : null}
          </div>
        ) : null}
      </div>

      <div className="trade-summary">
        <div className="row">
          <span>You receive</span>
          <span className="mono">
            {expectedOut != null
              ? `${expectedOut.toFixed(2)} ${side === "buy" ? m.short : "USDC"}`
              : `— ${side === "buy" ? m.short : "USDC"}`}
          </span>
        </div>
        <div className="row">
          <span>Price</span>
          <span className="mono">{price != null ? fmtUsd(price, 3) : "—"}</span>
        </div>
        <div className="row">
          <span>vs NAV</span>
          <span className={`mono ${spreadCls}`}>{spreadStr}</span>
        </div>
        <div className="row">
          <span>Slippage cap</span>
          <span className="mono">{Number(SLIPPAGE_BPS) / 100}%</span>
        </div>
      </div>

      <button
        className={`btn btn-lg ${side === "buy" ? "btn-buy" : "btn-sell"}`}
        style={{ width: "100%", justifyContent: "center" }}
        disabled={btnAction == null}
        onClick={btnAction ?? undefined}
      >
        {btnLabel}
      </button>

      {errMsg ? (
        <p
          className="u-mt3"
          style={{
            fontSize: "var(--t-xs)",
            color: "var(--neg)",
            textAlign: "center",
          }}
        >
          {errMsg}
        </p>
      ) : null}
      {lastFill ? (
        <p
          className="u-mt3"
          style={{ fontSize: "var(--t-xs)", textAlign: "center" }}
        >
          <span className="pos">{lastFill.label} ✓</span>{" "}
          <a
            href={explorerTx(lastFill.hash)}
            target="_blank"
            rel="noopener noreferrer"
            style={{ color: "var(--nav-line)" }}
          >
            View tx →
          </a>
        </p>
      ) : null}

      <p
        className="faint u-mt3"
        style={{ fontSize: "var(--t-xs)", textAlign: "center" }}
      >
        Live execution · Camelot pool on Arbitrum Sepolia · price from the pool,
        arbitrage holds it to NAV
      </p>
    </section>
  );
}

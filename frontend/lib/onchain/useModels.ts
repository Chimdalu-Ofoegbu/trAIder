"use client";

// =============================================================================
// frontend/lib/onchain/useModels.ts — poll-based live read adapter (FRONT live-reads)
//
// The data source the Claude Design UI consumes. Polls each model's vault
// (nav/totalAssets/totalSupply/symbol via multicall) + its Camelot pool (AMM
// price via raw globalState call) every few seconds, derives the view-model,
// and accumulates a live NAV/price series for the charts + sparklines.
//
// This replaces the assumed WebSocket push (NavTick/ArbOpp) with actual polling.
// The output shape is normalized so a future WS source is a drop-in swap.
// =============================================================================

import { useEffect, useRef, useState } from "react";
import { formatUnits } from "viem";

import { publicClient } from "./client";
import { VAULT_ABI, MTOKEN_DECIMALS, USDC_DECIMALS } from "./contracts";
import { readAmmPriceE18, gapBps, arbDirection } from "./amm";
import {
  MODELS,
  coliseumScore,
  sharpeFromSeries,
  type ModelMeta,
} from "./models";
import type { ModelLive, PricePoint } from "./types";

const MAX_POINTS = 120;
const DEFAULT_POLL_MS = 6000;

function seed(meta: ModelMeta): ModelLive {
  return {
    ...meta,
    navE18: 0n,
    ammPriceE18: null,
    totalAssetsRaw: 0n,
    totalSupplyRaw: 0n,
    symbol: meta.sym,
    nav: 0,
    price: null,
    supply: 0,
    assetsUsd: 0,
    spreadBps: null,
    direction: "none",
    series: [],
    pnlSession: null,
    sharpe: null,
    score: 0,
    ok: false,
  };
}

interface RawRead {
  meta: ModelMeta;
  ok: boolean;
  navE18: bigint;
  ammPriceE18: bigint | null;
  totalAssetsRaw: bigint;
  totalSupplyRaw: bigint;
  symbol: string;
}

async function readOne(meta: ModelMeta): Promise<RawRead> {
  try {
    const [navRes, assetsRes, supplyRes, symRes] = await publicClient.multicall(
      {
        allowFailure: true,
        contracts: [
          { address: meta.vault, abi: VAULT_ABI, functionName: "nav" },
          { address: meta.vault, abi: VAULT_ABI, functionName: "totalAssets" },
          { address: meta.vault, abi: VAULT_ABI, functionName: "totalSupply" },
          { address: meta.vault, abi: VAULT_ABI, functionName: "symbol" },
        ],
      },
    );

    const navE18 = navRes.status === "success" ? (navRes.result as bigint) : 0n;
    const totalAssetsRaw =
      assetsRes.status === "success" ? (assetsRes.result as bigint) : 0n;
    const totalSupplyRaw =
      supplyRes.status === "success" ? (supplyRes.result as bigint) : 0n;
    const symbol =
      symRes.status === "success" ? (symRes.result as string) : meta.sym;

    // AMM price uses a raw eth_call (non-standard globalState layout) — not multicall.
    let ammPriceE18: bigint | null = null;
    try {
      ammPriceE18 = (await readAmmPriceE18(publicClient, meta.pool, meta.vault))
        .priceE18;
    } catch {
      ammPriceE18 = null;
    }

    return {
      meta,
      ok: navRes.status === "success",
      navE18,
      ammPriceE18,
      totalAssetsRaw,
      totalSupplyRaw,
      symbol,
    };
  } catch {
    return {
      meta,
      ok: false,
      navE18: 0n,
      ammPriceE18: null,
      totalAssetsRaw: 0n,
      totalSupplyRaw: 0n,
      symbol: meta.sym,
    };
  }
}

export interface UseModelsResult {
  models: ModelLive[];
  updatedAt: number | null;
  blockNumber: number | null;
  loading: boolean;
  error: string | null;
}

/**
 * Live, poll-based read of all three model vaults + pools.
 * @param pollMs polling interval (default 6s).
 */
export function useModels(pollMs: number = DEFAULT_POLL_MS): UseModelsResult {
  const [models, setModels] = useState<ModelLive[]>(() => MODELS.map(seed));
  const [updatedAt, setUpdatedAt] = useState<number | null>(null);
  const [blockNumber, setBlockNumber] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const bufRef = useRef<Map<string, PricePoint[]>>(new Map());

  useEffect(() => {
    let alive = true;

    async function tick() {
      try {
        const [parts, block] = await Promise.all([
          Promise.all(MODELS.map(readOne)),
          publicClient.getBlockNumber().catch(() => null),
        ]);
        if (!alive) return;
        const now = Date.now();

        const merged: ModelLive[] = parts.map((p) => {
          const nav = Number(formatUnits(p.navE18, 18));
          const price =
            p.ammPriceE18 != null
              ? Number(formatUnits(p.ammPriceE18, 18))
              : null;
          const supply = Number(formatUnits(p.totalSupplyRaw, MTOKEN_DECIMALS));
          const assetsUsd = Number(
            formatUnits(p.totalAssetsRaw, USDC_DECIMALS),
          );
          const spreadBps =
            p.ammPriceE18 != null ? gapBps(p.navE18, p.ammPriceE18) : null;
          const direction =
            p.ammPriceE18 != null
              ? arbDirection(p.navE18, p.ammPriceE18)
              : "none";

          // Accumulate the live series (no historical NAV backfill exists on-chain).
          const series = bufRef.current.get(p.meta.id) ?? [];
          if (p.ok && nav > 0) {
            series.push({ t: now, nav, price });
            while (series.length > MAX_POINTS) series.shift();
            bufRef.current.set(p.meta.id, series);
          }
          const first = series[0];
          const pnlSession =
            first && first.nav > 0 && nav > 0
              ? ((nav - first.nav) / first.nav) * 100
              : null;
          const sharpe = sharpeFromSeries(series);
          const score = coliseumScore(nav > 0 ? nav : 1, spreadBps);

          return {
            ...p.meta,
            navE18: p.navE18,
            ammPriceE18: p.ammPriceE18,
            totalAssetsRaw: p.totalAssetsRaw,
            totalSupplyRaw: p.totalSupplyRaw,
            symbol: p.symbol,
            nav,
            price,
            supply,
            assetsUsd,
            spreadBps,
            direction,
            series: series.slice(),
            pnlSession,
            sharpe,
            score,
            ok: p.ok,
          };
        });

        setModels(merged);
        setUpdatedAt(now);
        setBlockNumber(block != null ? Number(block) : null);
        setError(
          merged.some((m) => m.ok)
            ? null
            : "No vault reads succeeded — check RPC / network.",
        );
      } catch (e) {
        if (!alive) return;
        setError(e instanceof Error ? e.message : "read failed");
      }
    }

    tick();
    const iv = setInterval(tick, pollMs);
    return () => {
      alive = false;
      clearInterval(iv);
    };
  }, [pollMs]);

  return { models, updatedAt, blockNumber, loading: updatedAt === null, error };
}

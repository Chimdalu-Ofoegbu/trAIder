// =============================================================================
// frontend/lib/onchain/amm.ts — Camelot/Algebra sqrtPriceX96 → e18 price decode
//
// Direct TS port of orchestrator/src/orchestrator/loop/arb_bot.py
// (decode_pool_price_e18 + read_sqrt_price_x96). This is the judge-facing peg
// number — the decimals + token-ordering handling must be IDENTICAL to the
// orchestrator's arb bot, or the NAV-vs-AMM gap is wrong (the systemic
// 1e12→1e30/decimal-scaling bug class). Do not "simplify" the math.
// =============================================================================

import type { Address, Hex, PublicClient } from "viem";

import { MTOKEN_DECIMALS, USDC_DECIMALS, POOL_ABI } from "./contracts";

// Algebra Integral v1 globalState() selector = keccak256("globalState()")[:4].
const GLOBAL_STATE_SELECTOR: Hex = "0xe76c01e4";

const Q192 = 1n << 192n; // 2^192
const E18 = 10n ** 18n;

/**
 * Convert an Algebra V3 globalState().price (sqrtPriceX96, Q64.96) to the USD
 * price of 1 mTOKEN scaled to 1e18 (matching vault.nav()).
 *
 * Token ordering depends on address sort order:
 *   - mtokenIsToken0 = true  (mTOKEN < USDC addr): price = USDC per mTOKEN
 *   - mtokenIsToken0 = false (USDC < mTOKEN addr): price = mTOKEN per USDC → invert
 */
export function decodePoolPriceE18(
  sqrtPriceX96: bigint,
  token0Decimals: number,
  token1Decimals: number,
  mtokenIsToken0: boolean,
): bigint {
  if (sqrtPriceX96 === 0n) return 0n;

  if (mtokenIsToken0) {
    // token0 = mTOKEN(18), token1 = USDC(6)
    // price_e18 = sqrtP^2 * 10^(t0dec - t1dec) * 1e18 / 2^192
    const num = sqrtPriceX96 * sqrtPriceX96;
    const decimalAdj = 10n ** BigInt(token0Decimals - token1Decimals);
    return (num * decimalAdj * E18) / Q192;
  }
  // token0 = USDC(6), token1 = mTOKEN(18) → invert
  // price_e18 = 2^192 * 10^(t1dec - t0dec) * 1e18 / sqrtP^2
  const den = sqrtPriceX96 * sqrtPriceX96;
  if (den === 0n) return 0n;
  const decimalAdj = 10n ** BigInt(token1Decimals - token0Decimals);
  return (Q192 * decimalAdj * E18) / den;
}

/**
 * Read an Algebra pool's sqrtPriceX96 via a raw eth_call, taking only the first
 * 32 bytes. globalState() returns a non-standard 256-byte/8-slot layout that may
 * not ABI-decode cleanly; slot 0 is ALWAYS the sqrtPriceX96 (uint160). A raw call
 * is unconditionally safe (VENUE-DECISION finding #1).
 */
export async function readSqrtPriceX96(
  client: PublicClient,
  pool: Address,
): Promise<bigint> {
  const res = await client.call({ to: pool, data: GLOBAL_STATE_SELECTOR });
  const data = res.data;
  if (!data || data.length < 2 + 64) {
    throw new Error(
      `readSqrtPriceX96: pool=${pool.slice(0, 10)} returned ${data?.length ?? 0} chars ` +
        `(expected ≥66); pool may not be initialized or the address is wrong`,
    );
  }
  // First 32 bytes = "0x" + 64 hex chars.
  return BigInt(data.slice(0, 66) as Hex);
}

/**
 * Full live AMM price read for one mTOKEN/USDC pool: detect orientation from
 * token0(), read sqrtPriceX96, decode to e18 USD-per-mTOKEN.
 *
 * @param mtokenAddress the vault address (the mTOKEN ERC-20 is the vault itself).
 * @returns { priceE18, mtokenIsToken0 }
 */
export async function readAmmPriceE18(
  client: PublicClient,
  pool: Address,
  mtokenAddress: Address,
): Promise<{ priceE18: bigint; mtokenIsToken0: boolean }> {
  const [sqrtPriceX96, token0] = await Promise.all([
    readSqrtPriceX96(client, pool),
    client.readContract({
      address: pool,
      abi: POOL_ABI,
      functionName: "token0",
    }),
  ]);
  const mtokenIsToken0 = token0.toLowerCase() === mtokenAddress.toLowerCase();
  const [t0dec, t1dec] = mtokenIsToken0
    ? [MTOKEN_DECIMALS, USDC_DECIMALS]
    : [USDC_DECIMALS, MTOKEN_DECIMALS];
  const priceE18 = decodePoolPriceE18(
    sqrtPriceX96,
    t0dec,
    t1dec,
    mtokenIsToken0,
  );
  return { priceE18, mtokenIsToken0 };
}

/**
 * NAV-vs-AMM gap in basis points (|amm - nav| / nav * 1e4), signed: positive when
 * the AMM trades at a premium to NAV (price > nav), negative at a discount.
 * Mirrors the design's spreadBps and the ArbOpp.gap_bps contract.
 */
export function gapBps(navE18: bigint, ammE18: bigint): number {
  if (navE18 === 0n) return 0;
  const diff = ammE18 - navE18; // signed
  // Integer-first to stay Number-safe: (diff/nav) in ppm, then → bps (0.01 precision).
  const ppm = (diff * 1_000_000n) / navE18;
  return Number(ppm) / 100;
}

/** Arbitrage direction per ArbOpp contract: nav < amm → "mint"; nav > amm → "burn". */
export function arbDirection(
  navE18: bigint,
  ammE18: bigint,
): "mint" | "burn" | "none" {
  if (navE18 === 0n || ammE18 === 0n) return "none";
  if (ammE18 > navE18) return "mint";
  if (ammE18 < navE18) return "burn";
  return "none";
}

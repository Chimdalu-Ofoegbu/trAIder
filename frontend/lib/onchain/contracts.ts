// =============================================================================
// frontend/lib/onchain/contracts.ts — deployed Sepolia addresses + ABIs (FRONT live-reads)
//
// Single source of truth for the frontend's LIVE on-chain reads. The Claude
// Design UI reads these contracts directly on Arbitrum Sepolia (chainId 421614)
// — no backend — so every number on screen is independently verifiable on
// Arbiscan. Addresses mirror deployments/sepolia.json (redeploy + Seam-D re-seed).
//
// ABIs are hand-written minimal fragments (only what the UI reads) for a small
// bundle + full viem type inference.
// =============================================================================

import type { Address } from "viem";

export const SEPOLIA_CHAIN_ID = 421614 as const;

// mTOKEN (ERC-4626 vault share) = 18 dec; USDC underlying = 6 dec.
export const MTOKEN_DECIMALS = 18 as const;
export const USDC_DECIMALS = 6 as const;

// Block-explorer base (Arbitrum Sepolia) — used for "verify on-chain" links.
export const EXPLORER_BASE = "https://sepolia.arbiscan.io" as const;
export const explorerAddress = (a: string) => `${EXPLORER_BASE}/address/${a}`;
export const explorerTx = (h: string) => `${EXPLORER_BASE}/tx/${h}`;

// ── Deployed addresses (deployments/sepolia.json — authoritative) ─────────────
export const ADDRESSES = {
  sessionFactory: "0x3A8e78Eb08ba2F7117B891A930b288E10739A322",
  oracle: "0x1983e02b02B72b62ecc49d54Fed73dCE714Ca194",
  journal: "0x831912FD51587760C4e26F49d6462343797fe357",
  vaultClaude: "0xd755A69E5DeAC38890412e68Ea9a9b5A00d4153E",
  vaultGpt: "0x3B11463a85f5Ea513e62f5aF37dd66D09dc0c26e",
  vaultGem: "0xA4eDE74F0992bFb3c034DE8ebF9CBD01E699e84f",
  mockPerps: "0x8Dd2FBA5fC20BF5e8dd656e53c79b2E7BD6344E2",
  mockUsdc: "0xA840055101acf7BdE519AF1e386c764e0e297fAE",
  arbitragePrimitive: "0x14c94d4ECb4A367D70EfD32c92F2db28926C5A1F",
  poolClaude: "0xE55458A526137BB1cBc413eBb4237A2C4Ba47C5c",
  poolGpt: "0x24e39c038AE3C3ff320c446B730e1c48e673ffdb",
  poolGem: "0xaD23422A7B64BA7fAa874b845c305F4b6B1DC272",
  arbSwapRouter: "0x171B925C51565F5D2a7d8C494ba3188D304EFD93",
} as const satisfies Record<string, Address>;

// ── mTokenVault — minimal ERC-4626 + NAV read fragment (mTokenVault.sol) ──────
// nav() returns the 1e18-scaled USD-per-mTOKEN NAV (the core "performance" signal).
export const VAULT_ABI = [
  {
    type: "function",
    name: "nav",
    stateMutability: "view",
    inputs: [],
    outputs: [{ type: "uint256" }],
  },
  {
    type: "function",
    name: "totalAssets",
    stateMutability: "view",
    inputs: [],
    outputs: [{ type: "uint256" }],
  },
  {
    type: "function",
    name: "totalSupply",
    stateMutability: "view",
    inputs: [],
    outputs: [{ type: "uint256" }],
  },
  {
    type: "function",
    name: "balanceOf",
    stateMutability: "view",
    inputs: [{ name: "account", type: "address" }],
    outputs: [{ type: "uint256" }],
  },
  {
    type: "function",
    name: "symbol",
    stateMutability: "view",
    inputs: [],
    outputs: [{ type: "string" }],
  },
  {
    type: "function",
    name: "name",
    stateMutability: "view",
    inputs: [],
    outputs: [{ type: "string" }],
  },
  {
    type: "function",
    name: "decimals",
    stateMutability: "view",
    inputs: [],
    outputs: [{ type: "uint8" }],
  },
] as const;

// ── Minimal ERC-20 fragment — balances/allowances/approve + mock-USDC faucet ───
// Works for BOTH mock USDC and the mTOKEN (the vault share IS an ERC-20).
// mint() is the MockERC20 permissionless faucet (testnet substrate only).
export const ERC20_ABI = [
  {
    type: "function",
    name: "balanceOf",
    stateMutability: "view",
    inputs: [{ name: "account", type: "address" }],
    outputs: [{ type: "uint256" }],
  },
  {
    type: "function",
    name: "allowance",
    stateMutability: "view",
    inputs: [
      { name: "owner", type: "address" },
      { name: "spender", type: "address" },
    ],
    outputs: [{ type: "uint256" }],
  },
  {
    type: "function",
    name: "approve",
    stateMutability: "nonpayable",
    inputs: [
      { name: "spender", type: "address" },
      { name: "amount", type: "uint256" },
    ],
    outputs: [{ type: "bool" }],
  },
  {
    type: "function",
    name: "mint",
    stateMutability: "nonpayable",
    inputs: [
      { name: "to", type: "address" },
      { name: "amount", type: "uint256" },
    ],
    outputs: [],
  },
] as const;

// ── Camelot/Algebra SwapRouter — exactInputSingle (live mTOKEN↔USDC swaps) ─────
// Tuple shape mirrors the orchestrator's proven-live inline ABI (gate/run_gate.py
// _SWAP_ROUTER_ABI): Algebra has no fee field; deadline lives in the struct.
export const SWAP_ROUTER_ABI = [
  {
    type: "function",
    name: "exactInputSingle",
    stateMutability: "payable",
    inputs: [
      {
        name: "params",
        type: "tuple",
        components: [
          { name: "tokenIn", type: "address" },
          { name: "tokenOut", type: "address" },
          { name: "recipient", type: "address" },
          { name: "deadline", type: "uint256" },
          { name: "amountIn", type: "uint256" },
          { name: "amountOutMinimum", type: "uint256" },
          { name: "sqrtPriceLimitX96", type: "uint160" },
        ],
      },
    ],
    outputs: [{ name: "amountOut", type: "uint256" }],
  },
] as const;

// ── Algebra Integral v1 pool — token ordering (price decode lives in amm.ts) ───
// NOTE: globalState() returns a non-standard 8-slot layout that may not ABI-decode
// cleanly — read the price via the raw-call path in amm.ts, not this fragment.
// token0()/token1() ARE standard and used to detect pool orientation.
export const POOL_ABI = [
  {
    type: "function",
    name: "token0",
    stateMutability: "view",
    inputs: [],
    outputs: [{ type: "address" }],
  },
  {
    type: "function",
    name: "token1",
    stateMutability: "view",
    inputs: [],
    outputs: [{ type: "address" }],
  },
] as const;

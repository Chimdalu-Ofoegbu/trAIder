// =============================================================================
// frontend/lib/wagmi.ts — wagmi v2 config + Robinhood Chain defineChain (D-71)
//
// Chains:
//   arbitrum        — Arbitrum One mainnet (from wagmi/chains)
//   arbitrumSepolia — Arbitrum Sepolia testnet (from wagmi/chains)
//   robinhoodTestnet — Robinhood Chain testnet (custom via viem defineChain, id 46630)
//
// Threat T-0-chainmisconfig: robinhoodTestnet uses VERIFIED id 46630 + official RPC
// per RESEARCH.md Pitfall 7 (live-probe confirmed chainId 46630).
//
// RPC: getDefaultConfig defaults to PUBLIC RPCs. For judge-facing live reads we
// override the Arbitrum Sepolia transport with an env-driven Alchemy/Infura URL
// (NEXT_PUBLIC_ARBITRUM_SEPOLIA_RPC_URL). http(undefined) falls back to the
// chain's public RPC, so the app still runs if the env is unset.
// =============================================================================

import { getDefaultConfig } from "@rainbow-me/rainbowkit";
import { arbitrum, arbitrumSepolia } from "wagmi/chains";
import { defineChain, http } from "viem";

// ── Robinhood Chain Testnet (D-71 / RESEARCH.md Pitfall 7) ───────────────────
// Chain ID: 46630 (VERIFIED from Thirdweb + Robinhood launch announcement 2026-02)
// RPC: https://rpc.testnet.chain.robinhood.com (official Robinhood endpoint)
// Explorer: https://explorer.testnet.chain.robinhood.com (Blockscout)
// Native gas token: ETH
// DA: Ethereum blobs (Arbitrum Orbit L2)
export const robinhoodTestnet = defineChain({
  id: 46630,
  name: "Robinhood Chain Testnet",
  nativeCurrency: {
    name: "Ether",
    symbol: "ETH",
    decimals: 18,
  },
  rpcUrls: {
    default: {
      http: ["https://rpc.testnet.chain.robinhood.com"],
    },
    public: {
      http: ["https://rpc.testnet.chain.robinhood.com"],
    },
  },
  blockExplorers: {
    default: {
      name: "Blockscout",
      url: "https://explorer.testnet.chain.robinhood.com",
    },
  },
  testnet: true,
});

// ── WalletConnect Project ID ──────────────────────────────────────────────────
// Replace with your WalletConnect Cloud project ID before production deploy.
// For local development, use the placeholder value (connect UI still works for
// MetaMask and Coinbase Wallet which don't require a project ID).
const WALLETCONNECT_PROJECT_ID =
  process.env.NEXT_PUBLIC_WALLETCONNECT_PROJECT_ID ?? "traider-dev-placeholder";

// ── Reliable RPC for judge-facing live reads (Alchemy/Infura via env) ─────────
const ARBITRUM_SEPOLIA_RPC_URL =
  process.env.NEXT_PUBLIC_ARBITRUM_SEPOLIA_RPC_URL;

// ── wagmi config (shared across app via WagmiProvider in providers.tsx) ──────
export const wagmiConfig = getDefaultConfig({
  appName: "trAIder",
  projectId: WALLETCONNECT_PROJECT_ID,
  chains: [arbitrum, arbitrumSepolia, robinhoodTestnet],
  transports: {
    [arbitrum.id]: http(),
    // Demo target — override with a reliable provider URL via .env.local.
    [arbitrumSepolia.id]: http(ARBITRUM_SEPOLIA_RPC_URL),
    [robinhoodTestnet.id]: http("https://rpc.testnet.chain.robinhood.com"),
  },
  ssr: true, // Next.js App Router SSR compatibility
});

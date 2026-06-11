// =============================================================================
// frontend/lib/onchain/client.ts — read-only viem public client for live reads
//
// Used for the reads that sit OUTSIDE the wagmi/wallet context: the raw eth_call
// to Algebra globalState() (amm.ts) and eth_getLogs over the JournalRegistry
// (audit-log feed). wagmi's hooks cover the React component reads; this client
// covers imperative reads in the data adapter.
// =============================================================================

import { createPublicClient, http } from "viem";
import { arbitrumSepolia } from "viem/chains";

export const ARBITRUM_SEPOLIA_RPC_URL =
  process.env.NEXT_PUBLIC_ARBITRUM_SEPOLIA_RPC_URL ??
  arbitrumSepolia.rpcUrls.default.http[0];

if (
  typeof window !== "undefined" &&
  !process.env.NEXT_PUBLIC_ARBITRUM_SEPOLIA_RPC_URL
) {
  // Judge-facing reads should not lean on the flaky public RPC.
  console.warn(
    "[trAIder] NEXT_PUBLIC_ARBITRUM_SEPOLIA_RPC_URL is not set — falling back to the public " +
      "Arbitrum Sepolia RPC. Set an Alchemy/Infura URL in .env.local for reliable demo reads.",
  );
}

export const publicClient = createPublicClient({
  chain: arbitrumSepolia,
  transport: http(ARBITRUM_SEPOLIA_RPC_URL),
});

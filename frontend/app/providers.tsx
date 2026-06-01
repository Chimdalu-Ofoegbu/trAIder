"use client";

// =============================================================================
// frontend/app/providers.tsx — Client-side providers (D-71)
//
// Wraps the app in:
//   WagmiProvider         — wagmi v2 config (arbitrum + arbitrumSepolia + robinhoodTestnet)
//   QueryClientProvider   — TanStack Query v5 (wagmi v2 hard peer dep)
//   RainbowKitProvider    — ConnectButton + wallet UX (D-71)
//
// This is a "use client" boundary. All children can use wagmi hooks + rainbowkit.
// =============================================================================

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RainbowKitProvider, darkTheme } from "@rainbow-me/rainbowkit";
import { WagmiProvider } from "wagmi";
import { wagmiConfig } from "@/lib/wagmi";

// RainbowKit CSS — import here, once per client boundary
import "@rainbow-me/rainbowkit/styles.css";

// Create QueryClient once (singleton per provider mount)
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 10_000, // 10s — live data is pushed via WS; REST queries are supplemental
      gcTime: 5 * 60_000, // 5min cache
      retry: 2,
    },
  },
});

// RainbowKit theme aligned to design tokens (D-62..D-66)
const rainbowTheme = darkTheme({
  accentColor: "#4a8fff", // --color-nav-accent
  accentColorForeground: "#f0f2f7",
  borderRadius: "medium",
  fontStack: "system",
  overlayBlur: "small",
});

export function Providers({ children }: { children: React.ReactNode }) {
  return (
    <WagmiProvider config={wagmiConfig}>
      <QueryClientProvider client={queryClient}>
        <RainbowKitProvider theme={rainbowTheme} coolMode={false}>
          {children}
        </RainbowKitProvider>
      </QueryClientProvider>
    </WagmiProvider>
  );
}

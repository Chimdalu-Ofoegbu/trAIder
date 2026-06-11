// =============================================================================
// frontend/app/layout.tsx — Root layout
//
// Adopts the Claude Design build's visual system as the app-wide baseline:
//   - styles/traider.css (design tokens, typography, components) + styles/app.css
//     (app shell) imported AFTER globals.css so the design wins the cascade.
//   - Design fonts (Newsreader / Hanken Grotesk / IBM Plex Mono) via <link>,
//     matching the prototype exactly.
//   - data-theme="dark" (the design keys its themes off [data-theme]).
//   - Wraps children in Providers (WagmiProvider + QueryClient + RainbowKit).
// =============================================================================

import type { Metadata } from "next";

import { Providers } from "./providers";
import "./globals.css";
import "../styles/traider.css";
import "../styles/app.css";

export const metadata: Metadata = {
  title: "trAIder — Live AI Trading Performance Markets",
  description:
    "Speculation market on three frontier LLMs autonomously trading GMX perps. " +
    "ERC-4626 vaults with NAV-pegged mTOKEN price discovery on Camelot.",
  keywords: ["AI trading", "DeFi", "GMX", "Arbitrum", "ERC-4626"],
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" data-theme="dark" suppressHydrationWarning>
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link
          rel="preconnect"
          href="https://fonts.gstatic.com"
          crossOrigin="anonymous"
        />
        <link
          href="https://fonts.googleapis.com/css2?family=Newsreader:opsz,wght@6..72,300;6..72,360;6..72,400;6..72,500&family=Hanken+Grotesk:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500;600&display=swap"
          rel="stylesheet"
        />
      </head>
      <body>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}

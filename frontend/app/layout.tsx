// =============================================================================
// frontend/app/layout.tsx — Root layout (D-62..D-66, D-71)
//
// - Inter for prose, JetBrains Mono for numerics (D-64) via next/font
// - Imports styles/tokens.css design tokens (D-62)
// - Wraps children in Providers (WagmiProvider + QueryClient + RainbowKit)
// - Dark-only: no light mode (D-63)
// =============================================================================

import type { Metadata } from "next";
import { Inter, JetBrains_Mono } from "next/font/google";

import { Providers } from "./providers";
import "../styles/tokens.css";
import "./globals.css";

// ── Inter — prose font (D-64) ─────────────────────────────────────────────────
const inter = Inter({
  subsets: ["latin"],
  variable: "--font-inter",
  display: "swap",
  weight: ["400", "500", "600", "700"],
});

// ── JetBrains Mono — ALL numerics: NAV, prices, sizes, timestamps (D-64) ─────
const jetbrainsMono = JetBrains_Mono({
  subsets: ["latin"],
  variable: "--font-jetbrains-mono",
  display: "swap",
  weight: ["400", "500", "600"],
});

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
    <html lang="en" className="dark">
      <body
        className={`${inter.variable} ${jetbrainsMono.variable} antialiased`}
        style={{
          background: "var(--color-bg-base)",
          color: "var(--color-text-primary)",
          fontFamily: "var(--font-prose)",
        }}
      >
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}

import type { Config } from "tailwindcss";

// =============================================================================
// frontend/tailwind.config.ts — Tailwind v3.4.x config (D-62..D-66, D-46)
//
// Extends Tailwind with trAIder design tokens:
//   - Custom 'coliseum' breakpoint at 1280px (D-46: 3-up grid)
//   - Font families wired to CSS custom property variables
//   - Color tokens pointing at CSS custom properties (dark-only palette, D-63)
//
// Design token source of truth: styles/tokens.css
// Tailwind config READS the tokens via CSS custom properties — no duplication.
// =============================================================================

const config: Config = {
  content: [
    "./pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
    "./lib/**/*.{js,ts,jsx,tsx}",
    "./store/**/*.{js,ts,jsx,tsx}",
  ],
  darkMode: "class", // dark class on <html> (D-63 — dark-only, class applied in layout.tsx)
  theme: {
    extend: {
      // ── Custom breakpoints ─────────────────────────────────────────────────
      screens: {
        // D-46: Coliseum 3-up grid activates at 1280px; below = compact strip + vertical stack
        coliseum: "1280px",
      },

      // ── Font families ──────────────────────────────────────────────────────
      fontFamily: {
        // inter: prose — headings, labels, rationale text (D-64)
        inter: ["var(--font-inter)", "Inter", "system-ui", "sans-serif"],
        // mono: JetBrains Mono — ALL numerics (NAV, prices, timestamps) (D-64)
        numeric: [
          "var(--font-jetbrains-mono)",
          "JetBrains Mono",
          "Fira Code",
          "monospace",
        ],
      },

      // ── Colors wired to CSS custom properties ─────────────────────────────
      // Values point at tokens.css -- Tailwind generates utility classes that
      // inherit the live CSS custom property values (dark-only, never hardcoded).
      colors: {
        // Background layers
        "bg-base": "var(--color-bg-base)",
        "bg-surface": "var(--color-bg-surface)",
        "bg-elevated": "var(--color-bg-elevated)",
        "bg-input": "var(--color-bg-input)",

        // Text
        "text-primary": "var(--color-text-primary)",
        "text-secondary": "var(--color-text-secondary)",
        "text-tertiary": "var(--color-text-tertiary)",

        // NAV accent (calm blue)
        "nav-accent": "var(--color-nav-accent)",
        "nav-accent-muted": "var(--color-nav-accent-muted)",

        // PnL semantic colors
        "pnl-positive": "var(--color-pnl-positive)",
        "pnl-negative": "var(--color-pnl-negative)",

        // Model differentiation
        "claude-accent": "var(--color-claude-accent)",
        "gpt-accent": "var(--color-gpt-accent)",
        "gemini-accent": "var(--color-gemini-accent)",

        // Chrome
        "chrome-100": "var(--color-chrome-100)",
        "chrome-200": "var(--color-chrome-200)",
        "chrome-300": "var(--color-chrome-300)",

        // Keep default Tailwind mappings for background/foreground
        background: "var(--color-bg-base)",
        foreground: "var(--color-text-primary)",
      },

      // ── Border radius ──────────────────────────────────────────────────────
      borderRadius: {
        sm: "var(--radius-sm)",
        md: "var(--radius-md)",
        lg: "var(--radius-lg)",
        xl: "var(--radius-xl)",
        full: "var(--radius-full)",
      },

      // ── Box shadows ────────────────────────────────────────────────────────
      boxShadow: {
        sm: "var(--shadow-sm)",
        md: "var(--shadow-md)",
        lg: "var(--shadow-lg)",
      },

      // ── Transition timing ──────────────────────────────────────────────────
      transitionDuration: {
        fast: "100",
        normal: "180",
        slow: "300",
      },
    },
  },
  plugins: [],
};

export default config;

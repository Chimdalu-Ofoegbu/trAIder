"use client";

// =============================================================================
// frontend/components/marketing/MarketingNav.tsx — landing top nav.
// Ported from index.html: wordmark + hover megamenus (CSS-driven via landing.css)
// + theme toggle + real wallet connect (RainbowKit ConnectButton.Custom).
// =============================================================================

import Link from "next/link";
import { ConnectButton } from "@rainbow-me/rainbowkit";

function Chev() {
  return (
    <svg className="chev" viewBox="0 0 12 12" fill="none">
      <path d="M2.5 4.5 6 8l3.5-3.5" stroke="currentColor" strokeWidth="1.4" />
    </svg>
  );
}

function Arr() {
  return (
    <svg className="arr" viewBox="0 0 16 16" fill="none">
      <path d="M3 8h9M9 4l4 4-4 4" stroke="currentColor" strokeWidth="1.5" />
    </svg>
  );
}

function MegaLink({
  href,
  title,
  desc,
  d,
}: {
  href: string;
  title: string;
  desc: string;
  d: string;
}) {
  const internal = href.startsWith("/");
  const inner = (
    <>
      <svg className="mega-ico" viewBox="0 0 24 24" fill="none">
        <path d={d} stroke="currentColor" strokeWidth="1.6" />
      </svg>
      <div>
        <div className="mtitle">{title}</div>
        <div className="mdesc">{desc}</div>
      </div>
    </>
  );
  return internal ? (
    <Link className="mega-link" href={href} role="menuitem">
      {inner}
    </Link>
  ) : (
    <a className="mega-link" href={href} role="menuitem">
      {inner}
    </a>
  );
}

function toggleTheme() {
  const el = document.documentElement;
  el.setAttribute(
    "data-theme",
    el.getAttribute("data-theme") === "light" ? "dark" : "light",
  );
}

export function MarketingNav() {
  return (
    <header className="nav">
      <div className="container container-wide nav-inner">
        <Link className="wordmark" href="/" aria-label="trAIder home">
          tr<span className="ai">AI</span>der
        </Link>

        <nav className="nav-links" aria-label="Primary">
          <div className="nav-item" data-mega>
            <a className="nav-link" href="#mechanism" aria-haspopup="true">
              Protocol <Chev />
            </a>
            <div className="mega" role="menu">
              <MegaLink
                href="#mechanism"
                title="NAV-anchored arbitrage"
                desc="How mTOKEN price tracks live vault NAV"
                d="M3 17 9 9l4 5 7-9"
              />
              <MegaLink
                href="#features"
                title="ERC-4626 vaults"
                desc="One autonomous vault per model"
                d="M4 4h16v16H4zM4 10h16M10 4v16"
              />
              <MegaLink
                href="/verifier"
                title="The Verifier"
                desc="Every trade attested on-chain"
                d="M12 3 5 6v5c0 4 3 7 7 9 4-2 7-5 7-9V6l-7-3Z"
              />
              <MegaLink
                href="#features"
                title="Per-trade journaling"
                desc="Public reasoning for every position"
                d="M12 3v18M5 8l7-5 7 5"
              />
              <div className="mega-feature">
                <div>
                  <div className="kicker">Live now</div>
                  <div className="h4" style={{ marginTop: 8 }}>
                    The Coliseum
                  </div>
                  <p
                    className="faint"
                    style={{ fontSize: "var(--t-xs)", marginTop: 6 }}
                  >
                    Three frontier models. One arena. Real capital.
                  </p>
                </div>
                <Link
                  className="btn btn-primary btn-sm"
                  href="/coliseum"
                  style={{ alignSelf: "flex-start", marginTop: 14 }}
                >
                  Enter <Arr />
                </Link>
              </div>
            </div>
          </div>

          <div className="nav-item" data-mega>
            <Link className="nav-link" href="/coliseum" aria-haspopup="true">
              Coliseum <Chev />
            </Link>
            <div className="mega" role="menu">
              <MegaLink
                href="/coliseum"
                title="Dashboard"
                desc="Live leaderboard and NAV"
                d="M4 20V8m5 12V4m5 16v-7m5 7V9"
              />
              <MegaLink
                href="/model"
                title="Model detail"
                desc="Price vs NAV, trade journal"
                d="M3 16l5-6 4 4 6-8"
              />
              <MegaLink
                href="/arbitrage"
                title="Arbitrage"
                desc="Open NAV gaps to capture"
                d="M4 8h12l-3-3m3 11H4l3 3"
              />
              <MegaLink
                href="/portfolio"
                title="Portfolio"
                desc="Your positions and P&L"
                d="M12 4v8l5 3"
              />
              <div className="mega-feature">
                <div>
                  <div className="kicker">Leader</div>
                  <div className="h4" style={{ marginTop: 8 }}>
                    Gemini 3 Pro
                  </div>
                  <p
                    className="faint"
                    style={{ fontSize: "var(--t-xs)", marginTop: 6 }}
                  >
                    “Maximus” · Cross-venue arb
                  </p>
                </div>
                <Link
                  className="btn btn-ghost btn-sm"
                  href="/model?m=maximus"
                  style={{ alignSelf: "flex-start", marginTop: 14 }}
                >
                  View model
                </Link>
              </div>
            </div>
          </div>

          <div className="nav-item" data-mega>
            <a className="nav-link" href="#tech" aria-haspopup="true">
              Developers <Chev />
            </a>
            <div className="mega" role="menu">
              <MegaLink
                href="#tech"
                title="Architecture"
                desc="Vaults, oracle, settlement"
                d="M8 7 3 12l5 5m8-10 5 5-5 5"
              />
              <MegaLink
                href="/verifier"
                title="The Verifier"
                desc="On-chain attestation feed"
                d="M3 5h18v14H3zM3 9h18"
              />
              <MegaLink
                href="#tech"
                title="Coliseum Score"
                desc="Risk-and-consistency composite"
                d="M12 3 4 7v6c0 4 8 8 8 8s8-4 8-8V7l-8-4Z"
              />
              <MegaLink
                href="#ecosystem"
                title="Integrations"
                desc="Arbitrum, GMX, Camelot, Chainlink"
                d="M8 11l8-4M8 13l8 4"
              />
              <div className="mega-feature">
                <div>
                  <div className="kicker">Open</div>
                  <div className="h4" style={{ marginTop: 8 }}>
                    Permissionless
                  </div>
                  <p
                    className="faint"
                    style={{ fontSize: "var(--t-xs)", marginTop: 6 }}
                  >
                    Run the arbitrage bot. Index the journal. Build on the feed.
                  </p>
                </div>
                <a
                  className="btn btn-ghost btn-sm"
                  href="#tech"
                  style={{ alignSelf: "flex-start", marginTop: 14 }}
                >
                  Read the docs
                </a>
              </div>
            </div>
          </div>

          <a className="nav-link" href="#ecosystem">
            Ecosystem
          </a>
        </nav>

        <div className="nav-right">
          <button
            className="icon-btn"
            onClick={toggleTheme}
            aria-label="Toggle theme"
          >
            <svg viewBox="0 0 24 24" fill="none">
              <circle
                cx="12"
                cy="12"
                r="4.2"
                stroke="currentColor"
                strokeWidth="1.6"
              />
              <path
                d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.5 1.5M17.5 17.5 19 19M19 5l-1.5 1.5M6.5 17.5 5 19"
                stroke="currentColor"
                strokeWidth="1.6"
              />
            </svg>
          </button>
          <ConnectButton.Custom>
            {({ account, mounted, openConnectModal, openAccountModal }) => {
              const connected = mounted && !!account;
              return connected ? (
                <button className="btn btn-primary" onClick={openAccountModal}>
                  {account.displayName}
                </button>
              ) : (
                <button className="btn btn-primary" onClick={openConnectModal}>
                  Connect Wallet <Arr />
                </button>
              );
            }}
          </ConnectButton.Custom>
        </div>
      </div>
    </header>
  );
}

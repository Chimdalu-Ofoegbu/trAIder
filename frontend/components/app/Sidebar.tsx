"use client";

// =============================================================================
// frontend/components/app/Sidebar.tsx — app shell sidebar
//
// Ported from the Claude Design build's app.js renderSidebar (identical classes
// + icons). Active item from the route; wallet chip wired to real wagmi state;
// theme toggle flips [data-theme] on <html> (the design supports light too).
//
// Mobile (<=820px): the sidebar becomes an off-canvas drawer opened by the
// hamburger; tapping a nav link, the backdrop, or changing route closes it.
// =============================================================================

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useAccount } from "wagmi";

import { shortAddr } from "@/lib/format";

interface NavLink {
  id: string;
  label: string;
  href: string;
  icon: React.ReactNode;
}
type NavEntry = { sec: string } | NavLink;

const NAV: NavEntry[] = [
  { sec: "Trade" },
  {
    id: "coliseum",
    label: "Coliseum",
    href: "/coliseum",
    icon: (
      <path
        d="M4 20V8m5 12V4m5 16v-7m5 7V9"
        stroke="currentColor"
        strokeWidth="1.6"
      />
    ),
  },
  {
    id: "model",
    label: "Models",
    href: "/model",
    icon: (
      <path d="M3 16l5-6 4 4 6-8" stroke="currentColor" strokeWidth="1.6" />
    ),
  },
  {
    id: "arbitrage",
    label: "Arbitrage",
    href: "/arbitrage",
    icon: (
      <path
        d="M4 8h12l-3-3m3 11H4l3 3"
        stroke="currentColor"
        strokeWidth="1.6"
      />
    ),
  },
  { sec: "Account" },
  {
    id: "portfolio",
    label: "Portfolio",
    href: "/portfolio",
    icon: (
      <>
        <circle cx="12" cy="12" r="8" stroke="currentColor" strokeWidth="1.6" />
        <path d="M12 4v8l5 3" stroke="currentColor" strokeWidth="1.6" />
      </>
    ),
  },
  {
    id: "verifier",
    label: "Verifier",
    href: "/verifier",
    icon: (
      <>
        <path
          d="M12 3 5 6v5c0 4 3 7 7 9 4-2 7-5 7-9V6l-7-3Z"
          stroke="currentColor"
          strokeWidth="1.6"
        />
        <path d="M9 12l2 2 4-4" stroke="currentColor" strokeWidth="1.6" />
      </>
    ),
  },
];

function toggleTheme() {
  const el = document.documentElement;
  const next = el.getAttribute("data-theme") === "light" ? "dark" : "light";
  el.setAttribute("data-theme", next);
}

export function Sidebar() {
  const pathname = usePathname();
  const { address, isConnected } = useAccount();
  const [open, setOpen] = useState(false);
  const close = () => setOpen(false);

  // Close the mobile drawer whenever the route changes.
  useEffect(() => {
    setOpen(false);
  }, [pathname]);

  return (
    <>
      <button
        type="button"
        className="side-burger"
        aria-label={open ? "Close navigation menu" : "Open navigation menu"}
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
          <path
            d={open ? "M6 6l12 12M18 6 6 18" : "M4 7h16M4 12h16M4 17h16"}
            stroke="currentColor"
            strokeWidth="1.8"
            strokeLinecap="round"
          />
        </svg>
      </button>

      <div
        className="side-backdrop"
        data-open={open ? "" : undefined}
        onClick={close}
        aria-hidden="true"
      />

      <aside className="side" data-side data-open={open ? "" : undefined}>
        <div className="side-top">
          <Link className="wordmark" href="/" style={{ fontSize: "1.15rem" }}>
            tr<span className="ai">AI</span>der
          </Link>
          <button
            className="icon-btn"
            onClick={toggleTheme}
            aria-label="Toggle theme"
            style={{ width: 30, height: 30 }}
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
        </div>

        <nav className="side-nav" aria-label="App">
          {NAV.map((n, i) =>
            "sec" in n ? (
              <div className="side-sec" key={`sec-${i}`}>
                {n.sec}
              </div>
            ) : (
              <Link
                className="side-link"
                href={n.href}
                key={n.id}
                onClick={close}
                aria-current={pathname.startsWith(n.href) ? "page" : undefined}
              >
                <svg viewBox="0 0 24 24" fill="none">
                  {n.icon}
                </svg>
                {n.label}
              </Link>
            ),
          )}
        </nav>

        <div className="side-foot">
          <nav className="side-res" aria-label="Resources">
            <div className="side-sec">Docs &amp; Socials</div>
            <Link className="side-link" href="/verifier" onClick={close}>
              <svg viewBox="0 0 24 24" fill="none">
                <path
                  d="M8 7 3 12l5 5m8-10 5 5-5 5"
                  stroke="currentColor"
                  strokeWidth="1.6"
                />
              </svg>
              Docs
            </Link>
            <div className="side-social">
              <a
                href="https://twitter.com/traider"
                target="_blank"
                rel="noopener noreferrer"
                aria-label="trAIder on X / Twitter"
              >
                <svg viewBox="0 0 24 24" aria-hidden="true">
                  <path
                    fill="currentColor"
                    d="M18.244 2.25h3.308l-7.227 8.26 8.502 11.24h-6.66l-5.214-6.817L4.99 21.75H1.68l7.73-8.835L1.254 2.25H8.08l4.713 6.231 5.451-6.231Zm-1.161 17.52h1.833L7.084 4.126H5.117l11.966 15.644Z"
                  />
                </svg>
              </a>
            </div>
          </nav>
          <div className="wallet-chip">
            <span
              className="dot dot-live"
              style={isConnected ? undefined : { background: "var(--ink-3)" }}
            />
            <span>{isConnected ? "Connected" : "Not connected"}</span>
            <span className="wallet-addr" style={{ marginLeft: "auto" }}>
              {isConnected && address ? shortAddr(address) : "—"}
            </span>
          </div>
        </div>
      </aside>
    </>
  );
}

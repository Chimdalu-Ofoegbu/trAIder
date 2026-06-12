"use client";

// =============================================================================
// frontend/app/page.tsx — marketing landing (ported from index.html).
//
// Root layout (no app sidebar). Renders the full marketing page with its own
// sticky nav. Live bits (ticker, hero convergence + stats, model cards) share a
// single useModels() poller; the mechanism demo + dot-matrix are self-contained.
// Reveal-on-scroll mirrors the design's initReveal, with a timeout fallback so
// nothing can stay hidden if the observer misbehaves.
// =============================================================================

import { useEffect } from "react";
import Link from "next/link";

import { useModels } from "@/lib/onchain/useModels";
import { fmtCompact, fmtInt } from "@/lib/format";
import { MarketingNav } from "@/components/marketing/MarketingNav";
import { Ticker } from "@/components/app/Ticker";
import { HeroConvergence } from "@/components/marketing/HeroConvergence";
import { LandingModelCards } from "@/components/marketing/LandingModelCards";
import { MechanismDemo } from "@/components/marketing/MechanismDemo";
import { DotMatrix } from "@/components/marketing/DotMatrix";

const Arr = () => (
  <svg className="arr" viewBox="0 0 16 16" fill="none">
    <path d="M3 8h9M9 4l4 4-4 4" stroke="currentColor" strokeWidth="1.5" />
  </svg>
);

export default function Home() {
  const { models } = useModels();
  const totalNav = models.reduce((s, m) => s + (m.assetsUsd || 0), 0);
  const totalSupply = models.reduce((s, m) => s + (m.supply || 0), 0);
  const liveCount = models.filter((m) => m.ok).length;

  useEffect(() => {
    const els = Array.from(document.querySelectorAll<HTMLElement>(".reveal"));
    let io: IntersectionObserver | null = null;
    if ("IntersectionObserver" in window) {
      io = new IntersectionObserver(
        (entries) =>
          entries.forEach((e) => {
            if (e.isIntersecting) {
              e.target.classList.add("in");
              io?.unobserve(e.target);
            }
          }),
        { threshold: 0.12, rootMargin: "0px 0px -8% 0px" },
      );
      els.forEach((e) => io!.observe(e));
    } else {
      els.forEach((e) => e.classList.add("in"));
    }
    const fallback = setTimeout(
      () => els.forEach((e) => e.classList.add("in")),
      1500,
    );
    return () => {
      io?.disconnect();
      clearTimeout(fallback);
    };
  }, []);

  return (
    <>
      <MarketingNav />
      <Ticker models={models} />

      {/* ── HERO ───────────────────────────────────────────────────────────── */}
      <section className="hero">
        <div className="container hero-grid">
          <div className="hero-copy reveal in">
            <div className="tag tag-live" style={{ marginBottom: 22 }}>
              <span className="dot dot-live" /> 3 models trading live on
              Arbitrum
            </div>
            <h1 className="display">Speculate on the machines that trade.</h1>
            <p className="lead u-mt5" style={{ maxWidth: "48ch" }}>
              Three frontier models trade autonomously inside on-chain vaults.
              You trade a token for each one, priced to its live net asset value
              by permissionless arbitrage. Conviction, not custody.
            </p>
            <div className="flex u-mt6" style={{ gap: 12, flexWrap: "wrap" }}>
              <Link className="btn btn-primary btn-lg" href="/coliseum">
                Enter the Coliseum <Arr />
              </Link>
              <a className="btn btn-ghost btn-lg" href="#mechanism">
                Read the mechanism
              </a>
            </div>
            <div className="hero-stats u-mt7">
              <div className="hstat">
                <div className="kicker">Total vault NAV</div>
                <div className="val h3">
                  {totalNav > 0 ? fmtCompact(totalNav) : "—"}
                </div>
              </div>
              <div className="hstat">
                <div className="kicker">mTOKEN supply</div>
                <div className="val h3">
                  {totalSupply > 0 ? fmtInt(totalSupply) : "—"}
                </div>
              </div>
              <div className="hstat">
                <div className="kicker">Models live</div>
                <div className="h3 num">
                  {liveCount}
                  <span className="faint" style={{ fontSize: ".5em" }}>
                    {" "}
                    / 3
                  </span>
                </div>
              </div>
            </div>
          </div>

          <div className="hero-stage">
            <HeroConvergence models={models} />
          </div>
        </div>
      </section>

      {/* ── VALUE PROP ─────────────────────────────────────────────────────── */}
      <section className="section" id="what">
        <div className="container">
          <div className="vp-grid">
            <div className="vp-step reveal">
              <div className="vp-n num">01</div>
              <h3 className="h2 vp-h">The models trade.</h3>
              <p className="muted u-maxw">
                Each frontier model runs a live strategy inside its own ERC-4626
                vault on Arbitrum, deploying real capital across perps, spot,
                and yield. No backtests. No paper. The vault&rsquo;s net asset
                value is the model&rsquo;s report card.
              </p>
            </div>
            <div className="vp-step reveal">
              <div className="vp-n num">02</div>
              <h3 className="h2 vp-h">You trade the models.</h3>
              <p className="muted u-maxw">
                Every vault mints an mTOKEN. Buy the model you believe in, short
                the one you don&rsquo;t. You hold a claim on performance, never
                the keys to the capital. Conviction expressed as a position.
              </p>
            </div>
            <div className="vp-step reveal">
              <div className="vp-n num">03</div>
              <h3 className="h2 vp-h">Arbitrage keeps it honest.</h3>
              <p className="muted u-maxw">
                When an mTOKEN drifts from its vault NAV, anyone can mint or
                redeem to close the gap and pocket the spread. The market price
                is tethered to real performance by economics, not by trust.
              </p>
            </div>
          </div>
        </div>
      </section>

      {/* ── MECHANISM ──────────────────────────────────────────────────────── */}
      <section
        className="section"
        id="mechanism"
        style={{ background: "var(--bg-inset)" }}
      >
        <div className="container">
          <div className="mech-grid">
            <div className="reveal">
              <h2 className="h1">
                The NAV-anchored
                <br />
                arbitrage loop.
              </h2>
              <p className="lead u-mt5 u-maxw">
                An mTOKEN is only worth trading if its price means something.
                trAIder ties price to truth: the moment the market price
                diverges from the vault&rsquo;s per-token NAV, an arbitrage
                opens. Close it, keep the spread.
              </p>
              <div className="mech-legend u-mt6">
                <div className="legrow">
                  <span
                    className="leg-swatch"
                    style={{ background: "var(--brand)" }}
                  />
                  <span>
                    mTOKEN market price{" "}
                    <span className="faint">— set by traders on Camelot</span>
                  </span>
                </div>
                <div className="legrow">
                  <span
                    className="leg-swatch"
                    style={{ background: "var(--nav-line)" }}
                  />
                  <span>
                    Vault NAV per token{" "}
                    <span className="faint">— calm, oracle-marked anchor</span>
                  </span>
                </div>
              </div>
              <p className="faint u-mt5" style={{ fontSize: "var(--t-xs)" }}>
                Drag to push market demand. Watch the arbitrage pull price back
                to NAV.
              </p>
            </div>
            <MechanismDemo />
          </div>
        </div>
      </section>

      {/* ── FEATURES ───────────────────────────────────────────────────────── */}
      <section className="section" id="features">
        <div className="container">
          <h2 className="h1 reveal" style={{ maxWidth: "18ch" }}>
            Six parts, one machine.
          </h2>
          <div className="feat-grid u-mt7">
            {[
              {
                t: "The Coliseum",
                d: "A live leaderboard ranking every model by NAV growth, risk, and consistency. The arena where capital decides who is winning, in real time.",
                href: "/coliseum",
                cta: "Open dashboard →",
                icon: "M4 20V9l8-5 8 5v11M9 20v-6h6v6",
              },
              {
                t: "ERC-4626 vaults",
                d: "One standard, audited vault per model. Deposits are pooled, the model trades, and shares price exactly to net asset value. Non-custodial by construction.",
                href: "#tech",
                cta: "Read architecture →",
                icon: "M4 4h16v16H4zM4 10h16M10 4v16",
              },
              {
                t: "mTOKEN trading",
                d: "Each vault mints a freely tradable token. Go long the model you trust, exit instantly on Camelot. Liquidity without unwinding the underlying book.",
                href: "/model",
                cta: "Trade a model →",
                icon: "M12 8v8m-3-6h4.5a1.8 1.8 0 0 1 0 3.6H9",
              },
              {
                t: "NAV-anchored arbitrage",
                d: "The primitive that makes the price real. Permissionless mint and redeem against live NAV means any gap is a paid invitation to close it.",
                href: "/arbitrage",
                cta: "See open gaps →",
                icon: "M4 8h12l-3-3m3 11H4l3 3",
              },
              {
                t: "Public journaling",
                d: "Every model writes its reasoning before each trade. The full journal is public and timestamped, so the thesis is auditable, not just the outcome.",
                href: "/verifier",
                cta: "Read the journal →",
                icon: "M9 9h6M9 13h6M9 17h3",
              },
              {
                t: "The Verifier",
                d: "Each fill is reconciled against Chainlink marks and attested on-chain. NAV is not asserted, it is proven block by block.",
                href: "/verifier",
                cta: "Inspect proofs →",
                icon: "M12 3 5 6v5c0 4 3 7 7 9 4-2 7-5 7-9V6l-7-3Z",
              },
            ].map((f) => {
              const internal = f.href.startsWith("/");
              return (
                <article className="feat panel reveal" key={f.t}>
                  <div className="feat-ico">
                    <svg viewBox="0 0 24 24" fill="none">
                      <path
                        d={f.icon}
                        stroke="currentColor"
                        strokeWidth="1.5"
                      />
                    </svg>
                  </div>
                  <h3 className="h4">{f.t}</h3>
                  <p className="muted feat-p">{f.d}</p>
                  {internal ? (
                    <Link className="btn btn-plain btn-sm" href={f.href}>
                      {f.cta}
                    </Link>
                  ) : (
                    <a className="btn btn-plain btn-sm" href={f.href}>
                      {f.cta}
                    </a>
                  )}
                </article>
              );
            })}
          </div>
        </div>
      </section>

      {/* ── MODELS ─────────────────────────────────────────────────────────── */}
      <section
        className="section"
        id="models"
        style={{ background: "var(--bg-inset)" }}
      >
        <div className="container">
          <div
            className="between reveal"
            style={{ alignItems: "flex-end", marginBottom: 8 }}
          >
            <h2 className="h1" style={{ maxWidth: "16ch" }}>
              Three models. Real capital. Live NAV.
            </h2>
            <Link
              className="btn btn-ghost"
              href="/coliseum"
              style={{ flexShrink: 0 }}
            >
              Full leaderboard <Arr />
            </Link>
          </div>
          <LandingModelCards models={models} />
        </div>
      </section>

      {/* ── TECH ───────────────────────────────────────────────────────────── */}
      <section className="section" id="tech">
        <div className="container">
          <div className="tech-grid">
            <div className="reveal">
              <h2 className="h1">The Coliseum Score.</h2>
              <p className="lead u-mt5 u-maxw">
                Leaderboard rank is not raw return. It is a risk-and-consistency
                composite, recomputed each block, so a lucky streak cannot
                outrank a durable edge.
              </p>
              <div className="formula u-mt6">
                <code className="mono">
                  Score = 0.50·PnLₙ + 0.20·(1 − MaxDD) + 0.20·Win +
                  0.10·Survival
                </code>
              </div>
              <div className="metric-rows u-mt6">
                <div className="metric-row">
                  <span className="faint">PnLₙ</span>
                  <span className="mono">
                    Clamped NAV return, −100% to +500%
                  </span>
                </div>
                <div className="metric-row">
                  <span className="faint">MaxDD</span>
                  <span className="mono">
                    Inverse max drawdown, capped at 50%
                  </span>
                </div>
                <div className="metric-row">
                  <span className="faint">Win</span>
                  <span className="mono">Share of closed trades in profit</span>
                </div>
                <div className="metric-row">
                  <span className="faint">Survival</span>
                  <span className="mono">
                    Vault held above 30% of starting NAV
                  </span>
                </div>
              </div>
            </div>
            <div className="arch panel reveal">
              <div className="kicker" style={{ padding: "18px 20px 0" }}>
                System architecture
              </div>
              <div className="arch-flow">
                {[
                  ["01", "Model agent", "Reasons, journals, signs the order"],
                  ["02", "ERC-4626 vault", "Holds capital, executes on GMX"],
                  ["03", "Chainlink oracle", "Marks positions, computes NAV"],
                  ["04", "mTOKEN / Camelot", "Market price, open to arbitrage"],
                  ["05", "Verifier", "Attests every fill on-chain"],
                ].map(([n, b, d], i, arr) => (
                  <div key={n}>
                    <div className="arch-node">
                      <span className="num faint">{n}</span>
                      <div>
                        <b>{b}</b>
                        <span className="faint">{d}</span>
                      </div>
                    </div>
                    {i < arr.length - 1 ? <div className="arch-conn" /> : null}
                  </div>
                ))}
              </div>
              <div className="arch-stats">
                <div>
                  <div className="h4 num">~0.25s</div>
                  <div className="kicker">Block time</div>
                </div>
                <div>
                  <div className="h4 num">100%</div>
                  <div className="kicker">On-chain settled</div>
                </div>
                <div>
                  <div className="h4 num">0</div>
                  <div className="kicker">Custodial steps</div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* ── ECOSYSTEM ──────────────────────────────────────────────────────── */}
      <section
        className="section"
        id="ecosystem"
        style={{ background: "var(--bg-inset)" }}
      >
        <div className="container">
          <h2
            className="h1 reveal"
            style={{
              maxWidth: "20ch",
              marginInline: "auto",
              textAlign: "center",
            }}
          >
            Composed from the best of Arbitrum DeFi.
          </h2>
          <div className="eco-grid u-mt7">
            {[
              [
                "Arbitrum",
                "Settlement layer",
                "Every vault, token, and attestation lives on Arbitrum. Low fees and fast finality make per-block NAV marking economical.",
              ],
              [
                "GMX",
                "Perp liquidity",
                "Models route perpetual exposure through GMX, deep books and transparent funding the vault accounts for in NAV.",
              ],
              [
                "Camelot",
                "mTOKEN venue",
                "Each mTOKEN trades in a Camelot pool. The AMM price is the market's opinion; arbitrage is the correction.",
              ],
              [
                "Chainlink",
                "Oracle & proof",
                "Price feeds mark positions and the Verifier reconciles fills against them, so NAV is independently provable.",
              ],
            ].map(([mark, role, p]) => (
              <div className="eco panel reveal" key={mark}>
                <div className="eco-mark mono">{mark}</div>
                <div className="eco-role">{role}</div>
                <p className="muted eco-p">{p}</p>
              </div>
            ))}
          </div>
          <div className="eco-models u-mt6 reveal">
            <span className="kicker">Model providers</span>
            <div className="eco-model-row">
              {models.map((m) => (
                <div className="eco-model" key={m.id}>
                  <div
                    className="squircle"
                    style={{
                      width: 34,
                      height: 34,
                      fontSize: 16,
                      color: m.line,
                    }}
                  >
                    {m.initial}
                  </div>
                  <div>
                    <div className="nm">{m.name}</div>
                    <div className="rl">{m.provider}</div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </section>

      {/* ── CTA ────────────────────────────────────────────────────────────── */}
      <section className="section-tight cta-band">
        <div className="container u-center">
          <h2
            className="display reveal"
            style={{ fontSize: "clamp(2.5rem,1.5rem+4vw,4.5rem)" }}
          >
            Pick your fighter.
          </h2>
          <p
            className="lead u-mt4 reveal"
            style={{ marginInline: "auto", maxWidth: "42ch" }}
          >
            The Coliseum is open. NAV is live. The arbitrage never sleeps.
          </p>
          <div
            className="flex u-mt6 reveal"
            style={{ gap: 12, justifyContent: "center", flexWrap: "wrap" }}
          >
            <Link className="btn btn-primary btn-lg" href="/coliseum">
              Enter the Coliseum <Arr />
            </Link>
            <Link className="btn btn-ghost btn-lg" href="/arbitrage">
              Run the arbitrage
            </Link>
          </div>
        </div>
      </section>

      {/* ── DOT MATRIX + FOOTER ────────────────────────────────────────────── */}
      <div className="matrix-wrap">
        <DotMatrix word="trAIder" />
      </div>

      <footer className="footer">
        <div className="container container-wide">
          <div className="footer-cols">
            <div>
              <Link
                className="wordmark"
                href="/"
                style={{ fontSize: "1.4rem" }}
              >
                tr<span className="ai">AI</span>der
              </Link>
              <p
                className="faint u-mt3"
                style={{ fontSize: "var(--t-sm)", maxWidth: "30ch" }}
              >
                A speculation market on live AI trading performance. Built on
                Arbitrum.
              </p>
              <div className="tag tag-live u-mt4">
                <span className="dot dot-live" /> All systems live
              </div>
            </div>
            <div>
              <h5>Protocol</h5>
              <a className="footer-link" href="#mechanism">
                Mechanism
              </a>
              <a className="footer-link" href="#features">
                Vaults
              </a>
              <Link className="footer-link" href="/arbitrage">
                Arbitrage
              </Link>
              <Link className="footer-link" href="/verifier">
                Verifier
              </Link>
            </div>
            <div>
              <h5>App</h5>
              <Link className="footer-link" href="/coliseum">
                Coliseum
              </Link>
              <Link className="footer-link" href="/model">
                Models
              </Link>
              <Link className="footer-link" href="/portfolio">
                Portfolio
              </Link>
              <Link className="footer-link" href="/portfolio">
                Connect
              </Link>
            </div>
            <div>
              <h5>Developers</h5>
              <a className="footer-link" href="#tech">
                Architecture
              </a>
              <Link className="footer-link" href="/verifier">
                Verifier feed
              </Link>
              <a className="footer-link" href="#tech">
                Coliseum Score
              </a>
              <a className="footer-link" href="#ecosystem">
                Integrations
              </a>
            </div>
            <div>
              <h5>Company</h5>
              <a className="footer-link" href="#what">
                About
              </a>
              <a className="footer-link" href="#">
                Disclosures
              </a>
              <a className="footer-link" href="#">
                Careers
              </a>
              <a className="footer-link" href="#">
                Contact
              </a>
            </div>
          </div>
          <div className="footer-bottom">
            <span>© 2026 trAIder Labs · Not investment advice</span>
            <span>Arbitrum Sepolia · testnet</span>
          </div>
        </div>
      </footer>
    </>
  );
}

# Design Prompt for trAIder (Claude)

## Role

You are a senior product and brand designer with deep expertise in Web3 UX, infrastructure marketing, and editorial-grade interface design. You think like Perena's design team, ship like Linear\'s, and obsess over typographic and motion details like Hyperliquid's.

## Objective

Design the website and application interface for trAIder, an Arbitrum-based speculation-market protocol on live AI trading performance (three frontier LLMs trade autonomously inside on-chain vaults, and speculators trade per-model tokens whose price is anchored to live vault NAV by permissionless arbitrage), into a creative, design-centric, UX-focused interface that meaningfully advances the current build. The output should feel like a deliberate, taste-led design, not a templated landing page. The full technical specification is in the attached project.md; reference it so the interface never contradicts the build.


## Step 1: Synthesize the inspiration set

Study each of the following reference sites and extract what makes them distinctive:

   - https://app.perena.org - The font type and design style here speaks to me, I want it exactly as it

   - https://mistral.ai/ - The header style (including the drop-down interaction)

   - https://www.llamaindex.ai/ - The header style (including the drop-down interaction)

   - https://cipherdigital.com/ - I love the overall look of this website, but I'm more focused on the hero page (tentative)

   - https://www.synthesis.partners/ - The footer interests me, take it as inspiration one for footer

   - https://www.gte.xyz/ - I love the footer layout.

   - https://integratedbio.com/ - I love the background 3d object animation swirling around. Make trAIders' interactive by mouse movement.

   - https://fourmula.ai/ - I love the hover animation interaction on the "Get Started" button at the top right head

   - https://fourmula.ai/ - The footer in this page is extremely unique, I want to have it replicated but the dot matrix animation will form a word "trAIder"

For each site, identify:

   - The single strongest design idea (typography system, motion language, layout grid, color use, interaction pattern, narrative structure, etc.)

   - Borrow the best parts of the reference sites (especially using what I stated was my likes about each reference)

   - Then propose a merged design direction, not a Frankenstein of all of them, but a coherent point of view that takes the best ideas and resolves them into one unified system that fits trAIder's positioning (a speculation-market protocol on live AI trading performance on       Arbitrum: data-dense, trust-critical, theatrical but restrained).


## Step 2: Define the design system foundation

Lock in before designing screens:

Typography: type pairing (serif/sans/mono), scale, weights, justification rules

Color: primary, secondary, accent, neutral ramps, background treatments (light, dark, or both; recommend which fits trAIder)

Spacing and grid: base unit, container widths, vertical rhythm

Motion principles: what should animate, how, why

Imagery / graphic language: illustration style, 3D, photography, generative, abstract data viz, etc.

Explain each choice in one sentence, why this serves trAIder specifically.


## Step 3: Design the interface

Produce a working HTML/CSS interface (Tailwind utility classes preferred) as an artifact, covering at minimum:

Navigation: header, footer, any persistent UI

Hero section: strongest single moment of the page; this should sell the protocol in 5 seconds

Value proposition / "what we do" section: clarifying trAIder's role and how the mechanism works

Product / feature breakdown: the Coliseum, the per-model ERC-4626 vaults, mTOKEN trading, the NAV-anchored arbitrage primitive, public per-trade journaling, the verifier

Technical credibility section: for a finance-literate and developer audience (the NAV-vs-AMM-price arbitrage mechanism, the Coliseum Score, metrics, architecture, integrations)

Ecosystem / integrations section: Arbitrum, GMX, Camelot, Chainlink, the three model providers

Call-to-action and footer

If a fuller flow is warranted, add additional sections (the application surfaces: Coliseum dashboard preview, model detail, arbitrage page, verifier, portfolio).

## Design principles to apply throughout

Earn every pixel. Whitespace is a feature, not absence.

Typography does heavy narrative lifting; treat headlines and big numbers as design objects, not filler.

Avoid generic crypto-site tropes (gradient blobs, glass cards, vague "fast-secure-decentralized" tags, AI-generated hero abstracts).

Motion should clarify, not decorate.

The site should feel like a market people want to trade in and infrastructure people want to build on: confident, technical, taste-led.

Mobile-first responsiveness, accessible contrast, keyboard navigability.

- No italics anywhere, no em dashes in copy, no accent color on chrome (accent appears only on live data and interactive states).


## Motion

  - Loading state = a price line converging to an anchor line (the
    convergence motif animating once), NOT a spinner, NEVER
    shimmer/skeleton screens.
  - Price, NAV & P&L updates: single-frame value swap with a 100ms
    color flash (--accent-pos on uptick, --accent-neg on downtick).
    No counting animation.
  - Modal/overlay transitions: 150ms.
  - No decorative animation. No Lottie. No confetti on wins.
  - Desktop hover on primary buttons: invert (dark-bg/accent-text to
    accent-bg/dark-text), instant, no transition.
  - The trade ticker is the only continuous motion element.


## Iconography

  - Custom inline SVG only, geometric, 24x24 grid, consistent weight.
  - You can import Heroicons, Lucide, Material Icons, Tabler, or any
    icon set
  - Model "logos" are the model's actual logo initial in a squircle square


## Tech Stack

  - Next.js 14 App Router, TypeScript, Tailwind CSS with the custom
    tokens above (do not use Tailwind's default color palette).
  - State: Zustand (live WebSocket-driven state), React Query (REST
    server state). Keep the two strictly separate: live data in
    Zustand, request-response data in React Query.
  - Charts: the per-model price/NAV chart MAY use a lightweight
    financial chart approach, but for this one-shot mock, render
    CUSTOM SVG line charts. Two lines per chart: a volatile mTOKEN
    market-price line (color-coded) and a calm NAV anchor line
    (--accent-neutral). A faint shaded band fills the gap between
    them. One horizontal hairline, no grid, no candlesticks ever.
  - Components: Import shadcn/ui, MUI, Chakra, Mantine, Radix, or any component kit. Hand-build everything.
  - Auth/Wallet: MOCK the wallet connect flow for now — a "Connect
    Wallet" button that routes straight to /app/coliseum and sets a
    fake session/address. (Real wagmi/viem connection is a later pass.)
  - All data is MOCKED using the shapes defined at the bottom of this
    prompt. Every screen renders with believable data.

## Output structure

Return your response in this order:

Audit observations (5 to 8 bullets)

Inspiration synthesis (per-site notes plus merged direction in one paragraph)

Design system foundation (typography, color, spacing, motion, imagery, with rationales)

The interface as a single working HTML artifact

A brief designer's note (3 to 5 sentences) explaining the strongest creative decisions and what you would test or iterate on next

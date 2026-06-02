# Design Brief: trAIder Interface

## 0. Context

You are designing the production interface for **trAIder**, a speculation-market protocol on live AI trading performance. Three frontier LLMs (Claude Opus 4.7, GPT-5.5, Gemini 3 Pro) autonomously trade crypto perpetuals on GMX over 72-hour sessions. Each model is wrapped in an ERC-4626 vault. Speculators trade mTOKEN (one per model) against USDC on Camelot. A permissionless arbitrage primitive keeps the AMM price anchored to vault NAV. The mechanism is the ETF Authorized Participant creation/redemption pattern applied for the first time to live AI trading performance.

The accompanying `project.md` contains the full technical specification. Read it before designing. The frontend will be wired to the backend described there in week 3 of the build sprint.

This is the production interface, not the pitch deck. The pitch deck is a separate workflow with its own brief. Both must feel like they come from the same brand, but the interface is the operational surface that traders, speculators, judges, and partners will actually interact with.

## Audience

Primary: speculators (crypto-native traders looking for novel markets), buildathon judges (evaluating during the June 14 submission window), investors and sponsors (reading the same interface as proof of build quality).

Secondary: model operators (running the LLM traders), arbitrageurs (closing NAV-AMM gaps for profit), researchers (studying the journal logs and replay verifier).

Tertiary: the general public discovering trAIder via Twitter, hackathon coverage, or word of mouth.

## Positioning

trAIder is the continuous speculation layer for the AI agent economy. Not a memecoin. Not a binary prediction market. A new market structure where prices update in real time as AI models trade, anchored to vault NAV by permissionless arbitrage, with public per-trade journaling for verifiability.

## Key value props

1. Live performance, live prices. Watch three frontier models trade autonomously. Speculate continuously on their performance.
2. NAV-anchored pricing. Arbitrage keeps mTOKEN prices honest. External capital cannot pump or dump indefinitely.
3. Public reasoning. Every trade journaled with the model's full reasoning, posted after execution.
4. Verifiable attribution. Replay any trade against the model's public API and confirm the response matches.
5. 72-hour sessions. Compressed time horizon keeps attention sustained. Multiple sessions per quarter.

## Step 1: Positioning brief

Before designing any screen, answer these in writing:

### 1.1 What the interface must communicate

1. The audience this is for (default: institutional speculators and academic-curious traders, not retail degen).
2. The positioning (continuous speculation, not a memecoin, not a binary bet).
3. The five value props above, in priority order based on landing context.

### 1.2 Common pitfalls to avoid

This is greenfield. Nothing exists yet. The risks are pitfalls to dodge, not flaws to fix.

1. Generic crypto aesthetics: laser eyes, rocket emojis, gradient meme tokens, "WAGMI" copy, degen visual cliches. trAIder must feel premium and institutional, not like a 2021 launchpad.
2. Weak typography hierarchy: in a data-dense product, type discipline is the spine of the interface. Tight hierarchy or it falls apart.
3. Unclear narrative: a first-time visitor must understand the mechanism in 30 seconds. If they leave confused, they do not come back.
4. Generic AI-product visuals: glowing orbs, abstract gradients, vague "intelligence" metaphors. trAIder is about specific named models doing specific named trades. Be concrete.
5. Missed personality opportunities: trAIder has theatrical bones (a coliseum where AI models fight). The design can have a point of view. Restrained, but present. Bloomberg Terminal redesigned for 2026, not Bloomberg Terminal copied.

### 1.3 Information architecture

The full IA, finalized based on the technical spec:

**Public marketing surface:**
1. Landing page. Hero (live ticker from the current session). Mechanism explainer. Live session card. Past session results. Roadmap. Team.
2. About / how it works. Deep mechanism explanation. The math. The arbitrage flow. The verifiability story.
3. Sessions. List of past, current, upcoming sessions. Click into session detail.
4. Manifesto / research. Long-form pieces. LiveTradeBench citation. Why AI performance markets are infrastructure.

**Product application:**
5. Coliseum (home of the app). Three model panels side by side. Live NAV. Live Coliseum Score. Recent trades scrolling. Latest journal entry. mTOKEN price chart. Buy/sell widget.
6. Model detail. Full trade history. Full journal log. Deep performance breakdown. mTOKEN order book or AMM depth.
7. Arbitrage. Live NAV-AMM gap list across all three models. One-click execute. Estimated profit shown. Recent arbitrage activity feed.
8. Verifier. Paste a journal CID. See the replay verification result. Side-by-side: original payload, replayed response, match/mismatch verdict.
9. Portfolio. Connected wallet view. mTOKEN holdings. PnL. Open positions. Settlement claims.
10. Session settings (operator only). Start session, end session, configure parameters. Hidden behind operator auth.

Five summary observations after analyzing the above:

1. trAIder is a dual-surface product: marketing site and live application. Both surfaces must feel unified but the application is data-dense while the marketing must convert.
2. The mechanism is non-obvious to first-time visitors. The landing page lives or dies on its mechanism explainer.
3. Live data is the protagonist of the application. Every Coliseum screen, every Model Detail screen, must feel alive.
4. Verifiability is a competitive differentiator. The Verifier page is small but strategically important. It should feel like a flex, not an afterthought.
5. The arbitrage page is the only screen designed primarily for sophisticated users. It can be more dense, more technical, more terminal-like than the rest.
6. Speculation requires fast iteration: buy mTOKEN, watch price, sell mTOKEN. The Coliseum page must support fast repeated action without friction.

## Step 2: Synthesize the inspiration set

Study these six reference sites. For each, identify (a) the single strongest design idea, (b) what is borrowable for trAIder, (c) what is brand-specific and should not be copied.

### 2.1 Ethena (https://app.ethena.fi/)

Strongest design idea: typography and information density as a craft discipline. Linear treats type hierarchy as the load-bearing element. Small font sizes used confidently. Aggressive whitespace at scale. Dark theme that is warm, not flat black. Restrained color palette: one accent for interaction, neutrals for everything else.

Borrowable: the typography rigor, the dark theme execution, the restraint with accent colors, the information density without clutter.

Brand-specific: the purple accent, the geometric icon system, the marketing copy style.

### 2.2 Polymarket (polymarket.com)

Strongest design idea: live market data treated as the primary visual element. Prices and percentages get the biggest type. Market cards as the fundamental unit. Clear information hierarchy: question, current odds, volume, resolution date.

Borrowable: the live-data-as-protagonist principle, the card-based market layout, the resolution timer prominence.

Brand-specific: the bright green/red on white background (too generic crypto), the political and sports topics framing.

### 2.3 Hyperliquid (https://app.hyperliquid.xyz/trade)

Strongest design idea: terminal-aesthetic trading interface that feels both crypto-native and premium. Heavy use of monospace fonts for numbers. Order book and chart placement. Performance metrics rendered like a Bloomberg dump.

Borrowable: the monospace numeric treatment, the terminal aesthetic, the willingness to show real complexity, the dark theme execution.

Brand-specific: the lime green brand color, the perp-DEX-specific layout, the on-chain trade history feed.

### 2.4 Trade[XYZ] (https://trade.xyz/)

The landing hero and CTA button catches my attention

### 2.5 Nof1.ai (nof1.ai)

This is the direct competitor. Study to differentiate, not to imitate.

Strongest design idea: academic credibility through restraint. Their Alpha Arena page treats the experiment as research, not a product. ModelChat reasoning logs given prominent placement. Simple typography. Heavy use of tables for clarity.

Borrowable: the academic framing, the per-model reasoning display, the willingness to be honest about losses.

Brand-specific: the static research-report feel, the lack of interactive speculation layer (this is precisely what trAIder adds). Do not borrow their static feel. trAIder must feel alive in a way they do not.

### 2.6 Cursor (cursor.com)

Strongest design idea: premium AI product positioning for technical buyers. Clean dark theme. Marketing pages that feel like a product launch from a company that already won. Smooth scroll-driven storytelling without it feeling overdone.

Borrowable: the premium AI product positioning template, the confident dark theme with restrained accents, the scroll-driven mechanism explainer pattern.

Brand-specific: the code editor visuals, the developer-tool framing.

### 2.7 Stripe (stripe.com)

Strongest design idea: institutional financial credibility communicated through design discipline. Generous whitespace. Subtle motion. Premium typography. Trust signals woven into layout (logos, numbers, certifications) without feeling salesy.

Borrowable: the institutional polish, the trust signal placement, the subtle motion philosophy, the marketing-site rhythm.

Brand-specific: the iridescent gradients (now overused), the API-first product framing.

### 2.8 Merged design direction (not a Frankenstein)

The synthesis. trAIder is Linear's type discipline + Polymarket's live-data-as-protagonist principle + Hyperliquid's terminal density + Nof1's academic credibility + Cursor's premium AI positioning + Stripe's institutional polish, resolved into a single point of view:

**trAIder is the Bloomberg Terminal for AI agent speculation, redesigned for 2026, with the academic gravitas of an AI research lab and the conversion rigor of a Stripe marketing page.**

Concrete implications:

1. Premium dark theme. Warm near-black, not flat #000.
2. Numbers get the biggest type. Prices, NAVs, Coliseum Scores, PnL all rendered in confident monospace.
3. Single accent color for live data and interaction. Recommended: a sharp electric green (#00E676 or similar) for positive movement, paired with a contrasting red (#FF3D3D) for negative. Use accent colors only on data. Never on chrome.
4. Generous whitespace, especially on the marketing surface. Tight density on the application surface. The two surfaces are distinguished by density rhythm, not by color or type.
5. Motion: subtle, purposeful, and tied to live data. Ticker updates, number transitions, chart redraws. No bouncy easings. Linear or slight ease-out. Animations should feel like consequence, not decoration.
6. Photography: avoided. Use generative or abstract data viz where imagery is needed. Charts, sparklines, scatter plots, tickers. The data is the imagery.
7. Personality: restrained but present. trAIder is theatrical because it is a coliseum, but the visual restraint says the spectacle is the data, not the chrome.

## Step 3: Design system foundation

Lock these before designing any screen.

### 3.1 Typography

**Type pairing:**
- **Sans-serif (primary):** Inter or Geist. Used for marketing copy, UI labels, body text, navigation.
- **Monospace (for data):** JetBrains Mono or Geist Mono. Used for all numbers: prices, NAV, Coliseum Score, PnL, percentages, timestamps, trade IDs.
- **No serif fonts.** Sans-serif and monospace only.

**Scale (recommend 1.25 ratio, modular):**
- 11px (mono, secondary data labels)
- 12px (small UI labels)
- 14px (body, default UI text)
- 16px (emphasized body)
- 20px (small headings)
- 28px (section headings)
- 40px (page headings)
- 56px (display, hero numbers)
- 80px (mega display, the headline NAV on a session card)

**Weights:**
- 400 (regular, default body)
- 500 (medium, UI emphasis)
- 600 (semibold, headings)
- 700 (bold, the loudest data display)
- **No italic weights.** Not in production CSS. Not anywhere.

**Justification:** Left-aligned for prose. Right-aligned for numbers in tables. Center justification is reserved for hero moments only.

### 3.2 Color

**Background ramps (dark theme is the only theme for v1):**
- `--bg-base`: #0A0B0E (deepest, page background)
- `--bg-elevated`: #14161B (cards, panels)
- `--bg-overlay`: #1C1F26 (modals, dropdowns)
- `--bg-subtle`: #232730 (hover states, dividers)

**Foreground ramps:**
- `--fg-primary`: #F5F6F8 (default text)
- `--fg-secondary`: #A8AEB8 (secondary labels)
- `--fg-tertiary`: #6B7280 (muted)
- `--fg-disabled`: #3F4651

**Accent (used only on live data, interaction states, and key emphasis):**
- `--accent-positive`: #00E676 (gains, mints, positive arbitrage)
- `--accent-negative`: #FF3D3D (losses, liquidations, negative arbitrage)
- `--accent-neutral`: #4D9FFF (informational, links, interaction primary)

**Critical rule:** the accent colors appear only on live data and interactive elements. Never on chrome. Never on dividers. Never as decoration. The discipline of accent restraint is what separates a premium financial product from a crypto launchpad.

### 3.3 Spacing and grid

**Base unit:** 4px. All spacing is a multiple of 4.
**Scale:** 4, 8, 12, 16, 20, 24, 32, 40, 48, 64, 80, 96, 128.

**Container widths:**
- Application max-width: 1440px (the dashboard).
- Marketing max-width: 1280px (the landing surface).
- Mobile breakpoint: 768px (responsive but desktop-first for v1).

**Vertical rhythm:** 64px between major marketing sections. 24-32px between application card groups. 16-24px within card content.

**Grid:** 12-column on marketing surfaces. Asymmetric / flex-based on application surfaces (the Coliseum page uses a 3-column flex with each model getting equal width on desktop).

### 3.4 Motion principles

1. **Animate consequence, not chrome.** A price update animates because it changed. A page transition does not need a flourish.
2. **No bouncy easings.** Linear or ease-out only. Durations 150ms (micro), 250ms (default), 400ms (hero).
3. **Number transitions are sacred.** When a price changes, the number transitions smoothly. Not a flash. Not a slot machine. A graceful interpolation, with a color flash (positive or negative) to signal direction.
4. **Charts redraw, not rebuild.** When new data arrives, the chart extends. It does not blank and redraw.
5. **Ticker scrolls continuously.** The trade ticker on the Coliseum page is the only continuous motion element. Everything else is event-driven.
6. **Reduced motion respected.** All non-essential animations honor `prefers-reduced-motion: reduce`.

### 3.5 Imagery and graphic language

1. **No photography.** No team headshots beyond an About page. No stock images. No abstract AI-themed photography.
2. **Generative data viz as the dominant graphic.** Sparklines, depth charts, NAV-vs-price overlays, scatter plots of historical performance. The data is the imagery.
3. **No 3D renders.** No generic AI orbs, no abstract isometric scenes.
4. **Iconography:** custom or pulled from a single restrained set (Lucide or Phosphor at single weight, never mixed). Icons are utility, not decoration.
5. **Model representation:** consistent across the product. Each of the three models gets a treatment: a name plate (typographic, not portrait), a color swatch (Claude in subdued orange, GPT in subdued teal, Gemini in subdued violet), and a status indicator. No anthropomorphic illustrations of the models.
6. **Charts:** built with Recharts or D3. Custom themed to the design system. No default chart library aesthetics visible.

## Hard constraints

1. **No italics.** Not in font-style. Not in font-weight. Not for emphasis. Use weight and size.
2. **No em dashes.** Use periods, commas, semicolons, colons.
3. **No generic AI-sounding copy.** No "revolutionizing," no "AI-powered transformation," no "unlocking potential." Write like a sharp founder, not a chatbot.
4. **No crypto-bro visuals.** No laser eyes. No rocket emojis. No moon imagery. No WAGMI. No gradient meme tokens.
5. **No accent color on chrome.** Accent colors appear only on data and interactive states.
6. **Numbered sections for content structure** where appropriate. Hierarchy through type weight and size, not through icon-bullet-header pyramids.

## Deliverables expected

1. **Marketing landing page (Slide 1 / page 1).** Hero, mechanism explainer, live session card, past sessions, roadmap, team, footer. Full design.
2. **Coliseum (application home).** Three model panels, live data, mTOKEN trade widget, journal feed. Full design.
3. **Model detail page.** Single model deep view. Trade history, journal log, performance breakdown. Full design.
4. **Arbitrage page.** Live NAV-AMM gap list, one-click execute, profit estimation, activity feed. Full design.
5. **Verifier page.** CID input, side-by-side payload-vs-replay comparison, verdict display. Full design.
6. **Portfolio page.** Connected wallet view, holdings, PnL, settlement claims. Full design.
7. **How it works / mechanism page.** Long-form mechanism explainer with the NAV-arbitrage diagram as the centerpiece. Full design.
8. **Design system documentation.** One reference page showing the typography scale, color ramps, spacing tokens, motion examples, and component library. Internal use.

## Output format

Single Next.js project or static HTML/CSS prototype, depending on your delivery format. Either:

1. A multi-page Next.js scaffold with all eight surfaces as routes, Tailwind for styling, mock data inline. Wireable to the production backend in week 3.
2. A multi-page static HTML/CSS prototype, no framework, modern CSS only. Same surfaces. Same mock data.

Recommend the Next.js path because the production frontend (per project.md) is Next.js 14 with App Router. Continuity into the build is cleaner.

## Success criteria

Verify before delivery:

1. The Coliseum page makes a first-time visitor understand the product in 30 seconds.
2. The mechanism explainer page makes a finance-literate visitor understand the NAV-arbitrage anchor in 90 seconds.
3. The interface feels alive on the Coliseum page (live data, ticker, number transitions) without feeling chaotic.
4. The Verifier page reads as a flex, not an afterthought.
5. The Arbitrage page is dense and terminal-like, distinct from the more conversational Coliseum.
6. Across the entire interface, the design feels like one coherent voice, not six referenced sites stitched together.
7. Zero italics rendered anywhere.
8. Zero crypto-bro cliches.
9. The design works at 1440px desktop. Mobile is graceful degradation, not parity, for v1.

## Open questions to resolve in your synthesis

1. Should the marketing landing page show a live ticker from the current session if one is running, or a static placeholder if no session is active? Recommendation: live if available, otherwise a poetic placeholder rather than nothing.
2. Should the journal entries on the Model Detail page show the full reasoning text inline, or a truncated preview with expand-to-read? Recommendation: truncated preview, expandable, since some entries will be 500+ words.
3. Should the Arbitrage page require wallet connection to view, or be readable without auth? Recommendation: readable without auth, action requires connection. Lower friction for first-time discovery.
4. Should the brand mark be a wordmark, a symbol, or both? Recommendation: wordmark only for v1. trAIder spelled with capital AI is itself a visual idea. Lean into it.

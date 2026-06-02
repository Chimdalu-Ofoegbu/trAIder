# Design Brief: trAIder Pitch Deck

## What you are building

A 10-page HTML pitch deck for **trAIder**, a speculation-market protocol on live AI trading performance. The deck has two simultaneous audiences:

1. **Buildathon judges** at the Arbitrum Open House London Buildathon (June 14, 2026 submission deadline). They evaluate on technical execution, product clarity, ecosystem alignment, and long-term potential.
2. **Investors, sponsors, and partners** reading the same deck as a pitch document. They evaluate on market opportunity, mechanism defensibility, team credibility, and roadmap.

Both audiences see the same deck. Tone must work for both. No hackathon-only references that read as small to investors. No investor-deck cliches that judges will see through.

The full technical specification for the protocol is in the attached `project.md`. Read it before designing. Reference it for the mechanism math and architecture so the slides do not contradict the build spec.

## What this product is, in one paragraph

trAIder runs 72-hour sessions where three frontier LLMs (Claude Opus 4.7, GPT-5.5, Gemini 3 Pro) autonomously trade crypto perpetuals on GMX with $10,000 each in operator-provided capital. Each model is wrapped in an ERC-4626 vault. The vault's NAV is the current trading capital divided by token supply. Speculators buy and sell mTOKEN (one per model) against USDC on Camelot. A permissionless arbitrage primitive lets anyone mint or burn mTOKEN at NAV, which keeps the AMM price anchored to live performance. Every trade is journaled publicly post-execution with the model's reasoning. Settlement at session end distributes vault USDC pro-rata to remaining holders.

The mechanism is structurally identical to how ETF Authorized Participants keep ETF prices pegged to NAV, applied for the first time to live AI trading performance.

## Hard constraints

1. **No italics anywhere.** Not in font-style declarations. Not in font-weight choices that resolve to italic variants. Not for emphasis. Use weight and size for hierarchy instead.
2. **No generic AI-sounding text.** No phrases like "revolutionizing the future of," "AI-powered transformation," "unlocking the potential of." Write like a sharp founder, not a chatbot. If a sentence could appear in any deck, rewrite it until it cannot.
3. **No em dashes.** Use periods, commas, semicolons, or colons.
4. **Numbered sections per page only.** Each slide has numbered sections (1, 2, 3) as its structural device. No bullet lists. No header/subheader pyramid. No icon rows. The numerical labels are the formatting.
5. **No crypto-bro visuals.** No laser eyes. No rocket emojis. No moon imagery. No "WAGMI" language. No degen aesthetics.
6. **Premium institutional polish.** The deck must be acceptable to a partner at Pantera or an Anthropic BD lead. Think Linear, Vercel, Stripe, or Bloomberg Terminal redesigned for 2026.

## Visual direction

- **Theme:** Premium dark mode. Near-black background with subtle warmth, not flat #000. Single accent color for live data elements (recommended: high-contrast electric green like #00FF85 for positive, sharp red like #FF3B3B for negative). Restrict accent use to data and key emphasis only.
- **Typography:** One sans-serif family, varied weights for hierarchy. Recommended: Inter, Geist, or IBM Plex Sans. No serif fonts. No display fonts. No italics.
- **Layout:** Generous whitespace. Asymmetric grids welcome. Numbers and data should feel like the protagonist. Text supports them.
- **Live data feeling:** Where possible, evoke a live trading dashboard. Sparkline shapes, ticker-style numerical displays, performance bars. Static deck but it should feel alive.
- **Page numbering:** Subtle, bottom-corner. Section numbers within pages are the prominent navigation.

## Slide-by-slide content

For each slide below, the content is finalized. Your job is to make it land visually. Treat the content as a brief you cannot modify, only present.

### Slide 1: Hook

**Numbered sections on page:**

1. Product name: **trAIder**
2. Single-line positioning: Speculation markets on live AI trading performance, anchored to NAV through continuous arbitrage.
3. Visual focal point: three model name plates, each with a placeholder live PnL ticker. Claude Opus 4.7. GPT-5.5. Gemini 3 Pro.

This page is the still-frame of the demo. Make it feel like the moment before a fight starts.

### Slide 2: The opening

**Numbered sections on page:**

1. The Alpha Arena phenomenon. October 2025. Nof1 gave six LLMs $10K each to autonomously trade crypto perpetuals on Hyperliquid. Public dashboards. Public reasoning logs. It went viral.
2. The retail response was immediate. Polymarket spun up a binary winner market that traded $29,707 in the first days. LMSYS-anchored "best model" contracts on Polymarket and Kalshi exceed $3.6M in cumulative volume. An unofficial Solana memecoin called ARENASOL launched on the back of the hype.
3. The gap. Polymarket gives you binary bets. ARENASOL gives you pure narrative. Nothing gives you a continuous, performance-anchored market that updates in real time as the models trade.

Visual suggestion: three small tiles or chips showing the existing options (Binary, Memecoin, none for "Continuous Anchored"), with the last one highlighted as the open space.

### Slide 3: What trAIder is

**Numbered sections on page:**

1. Three frontier LLMs trade autonomously on GMX perps for 72 hours. Operator-provided capital. No human intervention during the session.
2. One ERC-4626 vault per model. Each vault holds the model's trading capital plus any mint proceeds. mTOKEN is the vault share.
3. mTOKEN trades on Camelot against USDC. Speculators buy or short any model. Prices move based on what humans expect the model to do next.
4. Every trade is journaled publicly with the model's reasoning, posted after execution to prevent front-running.

Visual suggestion: three vault diagrams in a row, each labeled with a model, each showing USDC flowing in and out with a live NAV indicator at the top.

### Slide 4: The mechanism

**Numbered sections on page:**

1. Each mTOKEN vault publishes NAV onchain every block. NAV equals the vault's USDC balance plus mark-to-market value of open GMX positions, divided by mTOKEN supply.
2. The AMM price on Camelot moves with speculation, not performance directly. Speculators trade their expectation of where NAV will be by session end.
3. A permissionless arbitrage primitive lets anyone mint mTOKEN at NAV (depositing USDC) or burn mTOKEN at NAV (withdrawing USDC). When AMM price diverges from NAV by more than the arbitrage threshold, arbitrageurs close the gap and earn the spread.
4. This is how ETF Authorized Participants keep ETF prices pegged to NAV. We are applying that mechanism, for the first time, to live AI trading performance.

Visual suggestion: a single diagram showing NAV (anchor line) and AMM price (volatile line) converging over time as arbitrage acts. Two lines on one chart. This is the visual core of the entire deck.

### Slide 5: The Coliseum Score

**Numbered sections on page:**

1. Performance is reported as the **Coliseum Score**: a multi-factor metric designed for the 72-hour timescale.
2. The formula: `Score = 0.5 × normalized_pnl + 0.2 × inverse_max_drawdown + 0.2 × win_rate + 0.1 × survival_bonus`. Each component is bounded and computable from raw trade data.
3. Why not Sharpe. Sharpe ratio requires hundreds of return observations to be statistically distinguishable from noise. Over 72 hours and 30 to 100 trades, Sharpe is statistically meaningless. We do not pretend otherwise.
4. The Coliseum Score informs NAV calculations for narrative clarity but does not directly drive token price. Price comes from speculator demand against the NAV anchor. The score is what the dashboard shows in big numbers.

Visual suggestion: the formula as the centerpiece, with each component called out by what it captures (PnL = how much they made, drawdown = how reckless, win rate = quality of decisions, survival = did they blow up).

### Slide 6: Why this wins

**Numbered sections on page:**

1. Against Polymarket and Kalshi: continuous resolution, not binary. Live price reflects live performance, not endpoint settlement only.
2. Against ARENASOL and memecoin spinoffs: real performance anchor. Prices cannot drift indefinitely from the underlying. Arbitrage keeps the market honest.
3. Against Nof1's stated consumer platform plans: trAIder is the speculation layer Nof1 has not shipped. We are also deploying on Robinhood Chain testnet, where Nof1 has no presence.
4. Against everything else: the ETF creation/redemption mechanism applied to AI agent performance is a new market structure. It is the kind of primitive that becomes infrastructure once it ships.

Visual suggestion: a competitive landscape grid. X-axis: anchored to performance (no to yes). Y-axis: continuous resolution (no to yes). Place Polymarket, ARENASOL, Nof1, and trAIder. Only trAIder is in the upper-right quadrant.

### Slide 7: The demo

**Numbered sections on page:**

1. Five-minute pitch arc. Opens with three live PnL counters and a scrolling trade ticker. Three model portraits. Three Coliseum Scores updating.
2. The speculation layer activates. Three mTOKEN price charts. One spikes as its model lands a winning trade. Another collapses as its model gets margin-called. The arbitrage anchor pulls market price back toward NAV in real time.
3. Live audience trade. Judges and investors open their wallets and buy mTOKEN of their preferred model. Price moves on stage. This is the moment no NYC Buildathon winner had.
4. Settlement. Session closes. Vault USDC distributes pro-rata to remaining mTOKEN holders. Receipts onchain.

Visual suggestion: a four-quadrant storyboard showing each beat of the pitch. Make it look like a thriller poster.

### Slide 8: Roadmap and track stacking

**Numbered sections on page:**

1. Three-week build, locked. Week 1: core contracts and single-model trader. Week 2: three models, three markets, journaling system, stress test. Week 3: polish, Robinhood Chain parallel deploy, submission.
2. Hackathon prize stack. trAIder is positioned for all four pools simultaneously: Agentic Category ($15K), General Track ($70K), Robinhood Chain Innovation Award ($30K), and the guaranteed Robinhood Chain floor allocation in both top-three slots.
3. Post-hackathon trajectory. Q3 2026: mainnet deployment, recurring 72-hour sessions on a published schedule. Q4 2026: tokenized equity venues via Robinhood Chain mainnet, parallel sessions on multiple asset classes. 2027: institutional partnerships for branded model sessions.

Visual suggestion: a horizontal timeline with three phases of the build, then three future phases. Use the timeline width well. Do not waste it on a small element.

### Slide 9: Defensibility

**Numbered sections on page:**

1. Mechanism. The ETF-style creation/redemption applied to AI agent vaults is technically non-trivial to copy correctly. Anyone who attempts a lazy clone will likely build the scalar-outcome version (Polymarket-style) and lose the continuous-anchor property that makes trAIder work.
2. Network effects on the speculator side. More speculators means tighter spreads means better price discovery means more accurate signal means more speculators. The market deepens with use.
3. Integration depth. GMX trading flow, Chainlink price feeds for independent NAV, Camelot pools, IPFS journal storage, multi-model orchestration with signed audit logs. Each piece is small. Together they are a moat.
4. Timing. Nof1 has stated consumer platform plans but has not shipped. The window is closing. First-mover claim on the mechanism is available now.

Visual suggestion: four moat layers stacked, each with one sentence describing what it defends against. Style as concentric rings or layered shields. Premium and clean.

### Slide 10: Vision and ask

**Numbered sections on page:**

1. The bigger picture. As AI agents become economic actors managing real capital, the market needs a way to express continuous belief about their performance. Polymarket gave us binary bets on AI model rankings. ARENASOL gave us narrative trading. Neither scales as the agent economy grows. trAIder is the continuous speculation layer for the AI agent economy.
2. For judges. Evaluate on the mechanism design, the live demo, and the multi-prize alignment. trAIder competes for four pools simultaneously and produces a demo no other team can match.
3. For investors, sponsors, and partners. The category claim on continuous AI performance markets is open. The mechanism is structurally sound. The team has shipped sophisticated DeFi (DeepVault on Sui, Zeph payment routing) and has Claude Code as engineering execution lead. Reach out.
4. Contact. [Founder name, role, contact handle]. [GitHub or product link if available].

Visual suggestion: spacious closing slide. The product name and the single-line positioning from Slide 1 repeated as bookend. Contact info clean and confident.

## Output requirements

- Single HTML file with all 10 slides as sections.
- Use modern CSS (flexbox/grid, custom properties for the color system).
- Responsive at common deck dimensions (1920x1080 baseline, scales gracefully).
- No external dependencies beyond fonts (Google Fonts allowed for Inter, Geist, or IBM Plex Sans).
- Keyboard navigation between slides (arrow keys).
- Subtle slide-transition animations acceptable. No bouncy easings. Linear or slight ease-out only.
- All copy verbatim from this brief. Do not improvise content. Do not add taglines. Do not insert filler.

## Final check before delivery

Before exporting, verify the deck against this list. If any item fails, fix it.

1. Zero italics in the rendered output.
2. Zero em dashes in the copy.
3. Zero generic AI marketing phrases.
4. Numbered sections (1, 2, 3) as the only structural device per slide.
5. The mechanism diagram on Slide 4 (NAV line + AMM price line + arbitrage) is the visual centerpiece of the deck.
6. The competitive landscape on Slide 6 places trAIder alone in the upper-right.
7. The Coliseum Score formula on Slide 5 is rendered as math, not prose.
8. Both audience contexts (judges + investors) are addressable from the same slides.

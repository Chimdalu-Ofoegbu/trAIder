# trAIder Hero Animation Build Prompt: Concept 1, The Convergence

## What to build

An ambient, looping hero-section background animation for trAIder that renders the product's core mechanism in motion: a volatile market-price line converging toward a calm NAV anchor line via a pulse of arbitrage. It sits behind the hero headline and CTA, subtle enough to read text over, alive enough to draw the eye.

This is not decoration. It teaches the mechanism. A visitor watching for five seconds should intuit "a jumpy price gets pulled back to a steady line" before reading a word.

## Context: what trAIder is

trAIder runs sessions where AI models trade autonomously inside on-chain vaults. Each vault has a NAV (net asset value, the truth anchor). A token tracking each model trades on an AMM at a market price set by speculators. A permissionless arbitrage primitive pulls the market price back toward NAV whenever they diverge. The convergence of market price to NAV is the heartbeat of the protocol. This animation is that heartbeat.

## The animation, precisely

1. Two lines drawn left to right across the hero, on a near-black field.
   - The NAV line: calm, smooth, gently undulating, drawn in a restrained neutral blue. This is the anchor.
   - The market-price line: jagged, volatile, higher-frequency movement, drawn in a color that shifts subtly toward green when above NAV and red when below. This is the speculative price.

2. The loop cycle (roughly 6 to 9 seconds, varied so it does not feel mechanical):
   - Phase A (diverge): the market-price line drifts away from the NAV line, the gap between them widening. The area between the two lines fills with a very faint shade (the arbitrage opportunity becoming visible).
   - Phase B (signal): a soft pulse of light travels along the gap, left to right, signaling arbitrage firing.
   - Phase C (converge): the market-price line snaps and settles back toward the NAV line, the gap shading fading to nothing as they meet. A brief, restrained marker dot appears at the convergence point, then fades.
   - Phase D (rest): both lines travel near-together for a beat, then the next divergence begins with a different shape.

3. Each loop uses a different divergence profile (different amplitude, different direction, sometimes a sharp spike, sometimes a slow drift) so it never looks like a fixed GIF. Drive this with a seeded pseudo-random generator so it is varied but performant.

## Technical approach

- Render with Canvas 2D (preferred for smooth line animation and performance) or SVG with requestAnimationFrame. Not a video file, not a GIF. Code-driven so it loops seamlessly and stays crisp at any resolution.
- The lines are generated procedurally: NAV as a low-frequency sine-ish curve with slight noise, market price as NAV plus a higher-frequency noise function whose amplitude is driven by the loop phase.
- Use a single animation loop with delta-time timing so it runs smoothly across frame rates.
- The whole thing must sit behind hero content. Keep it visually quiet: thin lines, low-contrast against the background, the convergence pulse the only moment of slightly higher brightness.

## Design system tokens (use these exactly)

- Background: near-black, --bg-base #0A0B0E. The animation field is this color or transparent over it.
- NAV anchor line: --accent-neutral #4D9FFF, drawn thin, at reduced opacity (around 0.6) so it reads as calm.
- Market-price line: shifts between --accent-positive #00E676 (above NAV) and --accent-negative #FF3D3D (below NAV). Thin line, slightly higher opacity than NAV so it reads as the active element.
- Gap shading: a very low-opacity fill (around 0.06 to 0.10) between the two lines, tinted toward whichever accent the market line currently leans.
- Convergence pulse and marker: a brief brightness lift, no new color, just a soft glow along the existing lines.
- Foreground text (the hero headline and CTA layered on top) uses --fg-primary #F5F6F8. Ensure the animation never reduces text contrast below accessible levels behind the headline area; if needed, fade the animation opacity in the central text zone.

## Motion rules (from the trAIder design system)

- No bouncy easings. Linear or ease-out only. The convergence snap is a firm ease-out, not a spring.
- Motion is consequence, not decoration: the pulse causes the convergence, the convergence causes the gap to close. The visual cause-and-effect should be legible.
- The animation is continuous (it loops) but each phase is event-like, not a constant churn.

## Performance constraints

- Must hold 60fps on a mid-range laptop. Cap the number of points per line (a few hundred max) and reuse buffers; do not allocate per frame.
- Pause the animation when the hero is scrolled out of view (IntersectionObserver) to save CPU.
- Total animation code should be lightweight, no heavy libraries; vanilla Canvas/SVG plus requestAnimationFrame is enough.

## Reduced motion

- Respect prefers-reduced-motion: reduce. In that mode, render a single static frame showing the two lines mid-convergence with the gap faintly shaded, no looping motion. The static frame should still communicate the convergence idea.

## Hard guardrails

- No crypto-bro visuals. No particle fields, no floating polygons, no glowing orbs, no rocket or moon motifs, no network-graph node clouds. This is two lines and a gap, nothing more.
- No italics anywhere in any labels.
- No accent color used as chrome or decoration; accent appears only on the data lines and the gap, which ARE the data.
- Restraint over spectacle. The hero text is the message; the animation is the quiet, intelligent backdrop that happens to teach the mechanism.

## Deliverable

A self-contained component (React component using a canvas ref, or a standalone HTML/Canvas snippet) that renders the convergence animation as a hero background, with the hero headline and CTA layered over it. Include the IntersectionObserver pause and the reduced-motion static fallback. Provide the animation as a reusable piece that can be dropped behind the hero content.

## Success check

1. A first-time viewer intuits "volatile price being pulled back to a steady anchor" within five seconds, without reading copy.
2. The hero headline remains fully legible over the animation.
3. It holds 60fps and pauses when scrolled away.
4. Reduced-motion users get a meaningful static frame.
5. It looks like an intelligent financial instrument, not a crypto launchpad.

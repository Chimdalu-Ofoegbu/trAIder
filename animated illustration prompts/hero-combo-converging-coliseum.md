# trAIder Hero Animation Build Prompt: Combination of Concept 4 and Concept 1, The Converging Coliseum

## What to build

The flagship trAIder hero animation: an abstract 3D coliseum (Concept 4) whose three podium price ribbons each converge toward the arena's concentric anchor rings via a visible arbitrage pulse (Concept 1). It fuses the two strongest ideas: the coliseum names the product and shows three competitors, and the convergence-to-anchor teaches the core mechanism. One object, both meanings.

This is the highest-build-cost, highest-payoff option. It should be the single most memorable thing on the site. Restraint and art direction are what keep it premium rather than busy.

## Context: what trAIder is

trAIder runs sessions where three frontier AI models (Claude Opus 4.7, GPT-5.5, Gemini 3 Pro) trade autonomously inside on-chain vaults. The app home is the Coliseum (three models head to head). Each model's vault has a NAV anchor, and a permissionless arbitrage primitive pulls each model's speculative market price back toward its NAV. This animation shows both at once: three competitors in an arena, each one's price ribbon converging to the anchor rings through arbitrage.

## The animation, precisely

The coliseum structure (from Concept 4):

1. A circular arena floor as a faint wireframe/low-poly disc, three-quarter elevated view, carrying concentric rings radiating from center. These rings are the NAV anchor levels.

2. Three podiums arranged evenly around the arena, one per model, each in that model's subdued color (Claude subdued orange, GPT subdued teal, Gemini subdued violet).

3. The camera orbits the arena slowly and continuously.

The convergence mechanic (from Concept 1), applied to each podium:

4. Above each podium rise two vertical elements instead of one:
   - A calm anchor line at a steady height (that model's NAV anchor, tied to the concentric rings on the floor below it).
   - A volatile price ribbon that diverges from the anchor, drifting up or down with higher-frequency motion. The gap between the ribbon and the anchor faintly shades (the arbitrage opportunity).

5. Each podium runs its own convergence cycle, staggered so the three are never in phase:
   - Diverge: the price ribbon drifts from the anchor, the gap widening and faintly shading.
   - Signal: a soft pulse of light travels along that podium's gap (arbitrage firing for that model).
   - Converge: the ribbon snaps back toward the anchor, the gap shading fading, a brief restrained marker at the convergence point.
   - Rest: ribbon and anchor travel near-together for a beat before the next divergence.

6. When a ribbon converges, a faint pulse also ripples along the corresponding concentric anchor ring on the arena floor, tying the vertical convergence to the floor's anchor levels.

7. The three podiums' convergence cycles are staggered and use different divergence profiles, so at any moment one might be diverging while another is converging and a third rests. The arena is always alive but never synchronized.

## Technical approach

- Three.js (WebGL), r128-compatible APIs only.
- Arena floor and concentric rings: ring/disc geometry or custom concentric-circle wireframe, faint.
- Podiums: CylinderGeometry pillars or thin BoxGeometry platforms. Do NOT use CapsuleGeometry (r142+).
- Per podium: an anchor line (steady BufferGeometry line) and a price ribbon (animated line or thin plane strip), the ribbon's vertices driven by a per-podium noise function whose amplitude is gated by that podium's convergence-cycle phase. Gap shading is a faint translucent fill between anchor and ribbon.
- Convergence pulse: a soft moving glow along the gap; ring pulse: a brief brightness lift on the floor ring.
- Camera: manual slow continuous orbit (no OrbitControls; lerp around a circle).
- Single render loop, delta-time timing. Each podium tracks its own phase clock so the three cycles stay staggered.

## Design system tokens (use these exactly)

- Background: near-black, --bg-base #0A0B0E.
- Arena floor and concentric anchor rings: --fg-tertiary #6B7280 or --accent-neutral #4D9FFF at low opacity. Faint.
- Anchor lines above podiums: --accent-neutral #4D9FFF, thin, reduced opacity (calm).
- Price ribbons: shift toward --accent-positive #00E676 when above the anchor, --accent-negative #FF3D3D when below. Slightly higher opacity than the anchor so the active element reads.
- Podiums: that model's subdued color (subdued orange / teal / violet), quiet and structural.
- Gap shading: very low-opacity fill (around 0.06 to 0.10) between anchor and ribbon, tinted toward the ribbon's current accent.
- Convergence pulse, marker, and ring pulse: brief brightness lifts, no new colors.
- Foreground hero text uses --fg-primary #F5F6F8 and must stay legible; compose the arena so the headline sits in a calm zone, or reduce arena opacity behind it.

## Motion rules (from the trAIder design system)

- Slow continuous camera orbit; smooth, never fast.
- Convergence snaps use firm ease-out, not springs. No bouncy easings anywhere.
- The three podium cycles are staggered and out of phase (three-distinct-models principle), each with a different divergence profile.
- Every motion has meaning: ribbon height is price, anchor line and floor rings are NAV, the pulse is arbitrage firing, convergence is the gap closing. The whole object is the mechanism plus the competition in one.

## Performance constraints

- This is the heaviest concept (coliseum geometry plus three convergence systems plus camera orbit). Target 60fps on a mid-range laptop, degrade to 30fps gracefully on weaker hardware rather than stuttering.
- Keep geometry modest: simple podiums, capped-length ribbon trails, faint floor, a few hundred points per ribbon. Reuse buffers, no per-frame allocation.
- Pause rendering when scrolled out of view (IntersectionObserver).
- Detect WebGL; fall back to a static composition if unavailable.
- Lazy-load Three.js so it does not block initial paint; show a static first frame or fallback during load.
- If frame rate cannot hold even at reduced quality on a target device, fall back to the static frame rather than shipping a janky animation.

## Reduced motion

- Respect prefers-reduced-motion: reduce. Static frame: the arena from a fixed elegant angle, three podiums each showing an anchor line and a price ribbon frozen mid-convergence with faintly shaded gaps, concentric rings visible, no orbit, no animation. The frame should read as a coliseum of three competitors each converging to an anchor.
- Use this static frame as the no-WebGL fallback too.

## Hard guardrails

- No crypto-bro visuals. No glowing orbs, rotating coins/tokens, literal stone colosseum, laser effects, neon, particle fields, or network-graph clouds. Abstract wireframe/low-poly arena with line-based convergence, premium and quiet.
- Not game-y. Reference: elegant data visualization and architectural wireframe, not a video-game arena or sports broadcast.
- No italics anywhere.
- Subdued colors only; accents appear only on the data elements (ribbons, anchors, gaps, pulses), never on chrome.
- Orbit slow, cycles staggered, never synchronized, never fast. If it reads as a screensaver or a neon stadium, pull saturation, brightness, and speed down hard.
- Complexity discipline: this fuses two concepts, so the risk is busyness. If the arena ever looks cluttered, simplify (fewer rings, thinner ribbons, lower opacities) rather than adding. The headline message is the hero; this is the intelligent backdrop.

## Deliverable

A self-contained React component (canvas/WebGL ref, Three.js r128-compatible) rendering the converging coliseum as a hero background, with the hero headline and CTA layered over it. Include the per-podium staggered convergence cycles, the slow camera orbit, the IntersectionObserver pause, lazy Three.js load, no-WebGL fallback, and reduced-motion static frame.

## Success check

1. The structure reads as a coliseum with three competitors (names the product) AND each podium visibly shows a volatile ribbon converging to an anchor (teaches the mechanism).
2. The three convergence cycles are staggered and distinct, never synchronized.
3. It feels premium and architectural, not game-y, neon, or busy.
4. The orbit is slow and comfortable; the headline stays fully legible.
5. Smooth frame rate, pauses off-screen, degrades gracefully without WebGL, falls back to static rather than shipping jank.
6. Reduced-motion users get a meaningful static frame that conveys both the coliseum and the convergence.
7. A viewer comes away with both impressions: "three AIs competing" and "prices being pulled to a truth anchor," without reading a word.

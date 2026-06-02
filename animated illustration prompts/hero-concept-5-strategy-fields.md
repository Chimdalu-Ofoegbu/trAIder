# trAIder Hero Animation Build Prompt: Concept 5, Three Competing Strategy Fields

## What to build

An ambient, looping 3D hero-section background for trAIder: three translucent undulating surfaces, one per AI model, each moving to a distinct rhythm that represents that model's distinct trading behavior, all sharing a common baseline plane (the starting capital / NAV anchor). One surface rises while others dip, slowly, meditatively. Like watching three different minds think in three different shapes.

This is the most visually ambitious of the trAIder hero concepts. It must be beautiful and premium, never busy or game-y. Restraint is the whole game.

## Context: what trAIder is

trAIder runs sessions where three frontier AI models (Claude Opus 4.7, GPT-5.5, Gemini 3 Pro) trade autonomously inside on-chain vaults. The models have genuinely different behaviors: one might be aggressive, one conservative, one contrarian. The product is a head-to-head of three distinct intelligences. This animation embodies that: three surfaces, three rhythms, three shapes of thinking, competing over a shared baseline.

## The animation, precisely

1. Three translucent surfaces (think topographic terrain, flow fields, or rippling sheets), arranged across the hero space. Each surface belongs to one model and carries that model's subdued color:
   - Surface 1 (Claude): subdued orange.
   - Surface 2 (GPT): subdued teal.
   - Surface 3 (Gemini): subdued violet.

2. Each surface undulates with a distinct rhythm and character:
   - One moves in slow, broad, confident swells (the steady strategist).
   - One moves in faster, sharper, higher-frequency ripples (the active trader).
   - One moves in irregular, occasionally-still-then-sudden motion (the contrarian).
   These distinct motion signatures are the point. The three surfaces should never move in sync.

3. A shared baseline plane runs beneath or through all three (the NAV anchor / starting capital). Surfaces rise above and dip below this plane. Over the loop, the surfaces trade places: one rises to prominence (currently winning), others recede, then the standing shifts. Slow enough to be meditative, never a race.

4. The camera holds a fixed, elegant three-quarter view, or drifts extremely slowly. No fast orbiting, no dramatic camera moves. The surfaces do the moving.

## Technical approach

- Three.js (WebGL). This is a shader-driven surface animation.
- Each surface is a plane geometry with enough subdivision to deform smoothly, displaced by a vertex shader using layered noise (simplex/Perlin) with per-surface frequency and amplitude parameters that give each its distinct rhythm.
- Translucency via additive or normal blending with low opacity, so where surfaces overlap they create depth and subtle color mixing without muddiness.
- Use r128-compatible Three.js APIs only. Do not use THREE.CapsuleGeometry (r142+) or OrbitControls (not bundled); if any camera motion is needed, animate the camera manually with a slow lerp. Use PlaneGeometry for the surfaces.
- A single render loop with delta-time. Drive the surface deformation by a time uniform fed to the shaders.

## Design system tokens (use these exactly)

- Background: near-black, --bg-base #0A0B0E. The surfaces float in this dark space.
- Surface colors (subdued, not saturated):
  - Claude surface: a subdued orange (desaturated, around 40 to 50 percent saturation, not a bright safety orange).
  - GPT surface: a subdued teal.
  - Gemini surface: a subdued violet.
  - Keep all three at low opacity (around 0.25 to 0.4) so they read as translucent fields, not solid objects.
- Baseline plane: --accent-neutral #4D9FFF at very low opacity, or a faint grid in --fg-tertiary #6B7280, so the anchor is present but quiet.
- Foreground hero text uses --fg-primary #F5F6F8 and must remain fully legible; if the surfaces drift behind the headline zone, reduce their opacity there or keep the headline in a calmer region of the composition.

## Motion rules (from the trAIder design system)

- Slow and meditative. The full loop should breathe over 15 to 30 seconds.
- No bouncy easings. All transitions (a surface rising, the standing shifting) use linear or ease-out.
- Each surface's rhythm is distinct and the three are deliberately out of sync, because the differentiation between models IS the message.
- Motion has meaning: rising means winning, the shared plane is the anchor. The metaphor should hold together even though it is abstract.

## Performance constraints

- WebGL is heavier than 2D. Target 60fps on a mid-range laptop, degrade gracefully to 30fps on weaker hardware rather than stuttering.
- Cap surface subdivision at a level that looks smooth but does not melt the GPU (test and tune; a few thousand vertices per surface, not tens of thousands).
- Pause rendering when the hero scrolls out of view (IntersectionObserver). WebGL render loops are expensive; do not run them off-screen.
- Detect WebGL availability; if unavailable, fall back to a static gradient-and-line composition (see reduced motion).
- Lazy-load the Three.js bundle so it does not block initial page paint; show a static first frame or the fallback while it loads.

## Reduced motion

- Respect prefers-reduced-motion: reduce. In that mode, render a single static frame: the three surfaces frozen mid-undulation in an elegant composition, no animation, no render loop. The frozen frame should still convey three distinct fields over a shared baseline.
- Also use this static frame as the no-WebGL fallback.

## Hard guardrails

- No crypto-bro visuals. No particle systems, no glowing orbs, no rotating 3D coins or tokens, no wireframe globes, no network-graph node clouds. Three translucent surfaces over a plane, nothing more.
- No italics anywhere.
- Subdued colors only. If the surfaces look like a neon rave or a Windows screensaver, they are too saturated and too fast. Pull back. The reference is premium scientific visualization, not a music visualizer.
- The surfaces must never move in sync (that would lose the three-distinct-models meaning) and must never move fast (that would lose the premium, meditative quality).

## Deliverable

A self-contained React component (using a canvas/WebGL ref and Three.js r128-compatible code) that renders the three-surface animation as a hero background, with the hero headline and CTA layered over it. Include the IntersectionObserver pause, the lazy Three.js load, the no-WebGL fallback, and the reduced-motion static frame.

## Success check

1. The three surfaces read as three distinct entities with distinct behaviors (not three copies of the same motion).
2. The shared baseline reads as a common anchor the surfaces move relative to.
3. It feels premium and meditative, like scientific visualization, not a screensaver.
4. The hero headline stays fully legible.
5. It holds a smooth frame rate, pauses off-screen, and degrades gracefully without WebGL.
6. Reduced-motion users get a meaningful static composition.

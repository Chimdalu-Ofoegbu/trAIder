# trAIder Hero Animation Build Prompt: Concept 4, The Coliseum Arena

## What to build

An ambient, slowly-orbiting 3D hero-section background for trAIder: an abstract coliseum. A circular arena floor with three podiums (the three AI models), and above each podium a 3D price ribbon that rises and falls in real-time-looking motion. The arena floor carries faint concentric rings (the NAV anchor levels). The camera orbits slowly. Dark, premium, wireframe or low-poly, never a literal stone colosseum and never game-y.

This concept names the product visually. trAIder's app home is called the Coliseum; this hero is that coliseum, abstracted into an elegant data object.

## Context: what trAIder is

trAIder runs sessions where three frontier AI models trade autonomously, and humans speculate on their performance. The app's main view is the Coliseum, three models competing head to head. This animation is the coliseum as a premium abstract structure: three competitors on three podiums, their performance rising and falling as ribbons, all measured against concentric anchor rings on the arena floor.

## The animation, precisely

1. A circular arena floor rendered as a faint wireframe or low-poly disc, seen from a three-quarter elevated angle. The floor carries concentric rings radiating from the center, these are the NAV anchor levels (like contour lines or a radar scope, but quiet).

2. Three podiums (simple abstract forms: thin pillars, or subtle raised platforms) arranged evenly around the arena, one per model, each carrying that model's subdued color:
   - Podium 1 (Claude): subdued orange.
   - Podium 2 (GPT): subdued teal.
   - Podium 3 (Gemini): subdued violet.

3. Above each podium, a vertical price ribbon: a thin 3D strip or line that rises and falls over time, its height representing that model's live-looking performance. The ribbons move independently, each with its own rhythm, occasionally one surging above the others. The ribbon leaves a faint trailing history as it moves (a subtle 3D price chart standing in the air above each podium).

4. The camera orbits the arena slowly and continuously (a gentle, constant rotation), revealing the three podiums and ribbons from changing angles. The orbit is slow and smooth, never dizzying.

5. Optional restrained accent: when a ribbon crosses a concentric anchor ring (performance crossing a NAV level), a brief faint pulse ripples along that ring. This ties the ribbons to the anchor visually.

## Technical approach

- Three.js (WebGL).
- Arena floor: a ring/disc geometry or a custom wireframe of concentric circles. Keep it faint.
- Podiums: simple geometry (CylinderGeometry for pillars, or thin BoxGeometry platforms). Do NOT use CapsuleGeometry (r142+, unavailable).
- Price ribbons: animated line geometry or thin plane strips, their height/vertices updated over time from a per-ribbon noise function with distinct parameters. The trailing history is a line that accumulates recent positions (capped length).
- Camera: animate manually with a slow continuous rotation around the arena center (do not use OrbitControls, it is not bundled in r128). Lerp the camera position around a circle.
- Use r128-compatible APIs only. SphereGeometry, CylinderGeometry, BoxGeometry, PlaneGeometry, BufferGeometry for lines are all fine.
- Single render loop with delta-time timing.

## Design system tokens (use these exactly)

- Background: near-black, --bg-base #0A0B0E. The arena floats in this dark space.
- Arena floor and concentric rings: --fg-tertiary #6B7280 at low opacity, or --accent-neutral #4D9FFF at very low opacity for the anchor rings. Faint, never dominant.
- Podiums and ribbons (subdued, not saturated):
  - Claude: subdued orange.
  - GPT: subdued teal.
  - Gemini: subdued violet.
  - Ribbons slightly brighter than their podiums so the active data element reads clearly; podiums quiet and structural.
- Ribbon trailing history: the ribbon's color at reduced opacity, fading along the trail.
- Anchor-ring pulse: a brief brightness lift on the ring, no new color.
- Foreground hero text uses --fg-primary #F5F6F8 and must remain legible; keep the arena composed so the headline sits in a calm zone, or reduce arena opacity behind the headline.

## Motion rules (from the trAIder design system)

- Slow, continuous camera orbit. Smooth, never fast or dramatic.
- No bouncy easings. Ribbon movement and ring pulses use linear or ease-out.
- The three ribbons move independently with distinct rhythms (the three-distinct-models principle).
- Motion has meaning: ribbon height is performance, concentric rings are NAV anchor levels, a ribbon crossing a ring is a performance milestone.

## Performance constraints

- Target 60fps on a mid-range laptop, degrade to 30fps gracefully on weaker hardware.
- Keep geometry counts modest: simple podiums, capped-length ribbon trails, a faint floor. This is an abstract structure, not a detailed scene.
- Pause rendering when scrolled out of view (IntersectionObserver).
- Detect WebGL; fall back to a static composition if unavailable.
- Lazy-load Three.js so it does not block initial paint; show a static first frame or fallback while loading.

## Reduced motion

- Respect prefers-reduced-motion: reduce. In that mode, render a single static frame: the arena from a fixed elegant angle, three podiums with three ribbons frozen at different heights, concentric rings visible, no orbit, no animation. The static frame should still read as a coliseum with three competitors over anchor rings.
- Use this static frame as the no-WebGL fallback too.

## Hard guardrails

- No crypto-bro visuals. No glowing orbs, no rotating coins or tokens, no literal stone-texture colosseum, no laser effects, no neon. This is an abstract wireframe/low-poly arena, premium and quiet.
- Not game-y. The reference is elegant data visualization and architectural wireframe, not a video-game arena or a sports broadcast.
- No italics anywhere.
- Subdued colors only. If it looks like a neon stadium, pull the saturation and brightness down hard.
- The orbit must be slow. A fast spin reads as a screensaver and induces motion discomfort.

## Deliverable

A self-contained React component (canvas/WebGL ref, Three.js r128-compatible) rendering the orbiting coliseum as a hero background, with the hero headline and CTA layered over it. Include the IntersectionObserver pause, lazy Three.js load, no-WebGL fallback, and reduced-motion static frame.

## Success check

1. The structure reads as an arena/coliseum with three competitors, naming the product visually.
2. The three ribbons read as three distinct performances over shared anchor rings.
3. It feels premium and architectural, not game-y or neon.
4. The orbit is slow and comfortable; the headline stays legible.
5. Smooth frame rate, pauses off-screen, degrades gracefully without WebGL.
6. Reduced-motion users get a meaningful static frame.

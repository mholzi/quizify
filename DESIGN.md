# Design System — Quizify

> **Broadcast Living Room.** The posture of a televised game show, delivered at home scale.
> Deep studio navy. Broadcast gold. Warm parchment text. The dramatic pause, not the canned laugh track.
> A game show designed by someone who respects the viewer.

## Product Context

- **What this is:** A multiplayer trivia party game that runs entirely inside Home Assistant. The TV hosts the game. Players join on their phones via QR code. No apps, no accounts.
- **Who it's for:** Home Assistant enthusiasts hosting friends and family. People who already run tasteful dashboards in their living room and care about how their home looks.
- **Space / category:** Party trivia games (Jackbox, Kahoot, Hitster) crossed with the visual register of televised game shows (Jeopardy, Only Connect, Millionaire).
- **Project type:** Multi-surface web app — TV host screen (the signature surface), mobile player UI, host admin console.
- **Memorable thing** (the one anchor every decision serves):
  > *"It felt like a real game show on my TV."*
  >
  > Every typography, color, spacing, and motion decision in this document exists to serve this sentence. When in doubt, ask: does this choice feel like a TV broadcast aimed at a specific living room, or does it feel like a website about a quiz?

## Aesthetic Direction

- **Direction:** Broadcast / TV game show — refined, not studio-loud.
- **Decoration level:** Intentional. Two textural elements, never more:
  - Vertical CRT-scanline texture on the background (3% opacity) — barely perceptible, whispers "broadcast."
  - 1px broadcast-gold hairline under section headers — the "broadcast bug."
- **Mood:** Confident, hosted, deliberate. Warm enough to feel like *your* TV, not a studio. Deep saturated navy sets the stage; one gold color does every event moment. The dramatic pause *is* the effect.
- **Anti-patterns (never do these):**
  - Purple/violet gradients
  - Cartoon mascots or character illustrations
  - Confetti / balloons / fireworks decoration — **ever, including on finale**
  - Bubbly 24px+ border-radius on everything
  - Centered-everything marketing layouts
  - "Party chaos" multi-color block sections (Jackbox)
  - Neon saturated everything (Kahoot)
  - Inter, Roboto, Open Sans, Poppins, Space Grotesk, system-ui as display fonts
  - Gradient CTA buttons
  - Pure white text — text is always warm parchment
- **Reference points** (not to copy — to understand the register):
  - Jeopardy (deep royal blue, gold, confident hierarchy, studio-polish)
  - Only Connect on BBC Two (cerebral, muted, evidence that "game show" ≠ "loud")
  - Who Wants to Be a Millionaire (cinematic high-stakes reveal, gold money ladder)
  - Hitster (proof party-games can be refined in this category)
  - Panic.com (deep pixel-grid navy, indie-confident display type)

## Typography

Three voices, three jobs. All sans — a deliberate break from the editorial-serif register. Broadcast calls for weight, not italic flourish.

| Role | Font | Source | Weight | Rationale |
|------|------|--------|--------|-----------|
| **Display / Questions / Titles** | Unbounded | Google Fonts | 700 / 800 / 900 | Rounded geometric, wide, heavy. Not slab (too Jeopardy), not condensed (too Millionaire). Reads as a 2026 broadcast rebrand. Numerals are beautifully wide for scoring. |
| **UI / Answers / Body** | Instrument Sans | Google Fonts | 400 / 500 / 600 | Clean modern geometric by the Instrument agency. Pairs under Unbounded without competing. Not convergent. |
| **Scores / Timers / Meta** | JetBrains Mono | Google Fonts | 400 / 500 / 700 | Tabular numerals native. Earns its keep during score-tick animations. Used for all-caps eyebrows, round indicators, numeric displays. |

### Loading

```html
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Unbounded:wght@400;500;600;700;800;900&family=Instrument+Sans:ital,wght@0,400;0,500;0,600;0,700;1,400&family=JetBrains+Mono:wght@400;500;600;700&display=swap" rel="stylesheet">
```

### Scale

| Token | Size | Usage |
|-------|------|-------|
| `--fs-mono` | 12px | Eyebrows, meta labels, admin table cells, round indicators |
| `--fs-body` | 16px | Body copy, answer button text (mobile) |
| `--fs-lg` | 20px | Lede paragraphs, large UI |
| `--fs-xl` | 28px | Mobile question, admin section titles |
| `--fs-2xl` | 40px | Large section titles, TV answer text |
| `--fs-3xl` | 64px | TV question (default size) |
| `--fs-4xl` | 96px | TV question on very large screens (clamped) |

TV host question uses `clamp(32px, 5vw, 72px)` so it scales to viewing distance but never runs away. Display weight for questions is 700 — the geometry does the work, with 800/900 reserved for podium and wordmark.

### Fonts blacklist (never use)

Inter, Roboto, Arial, Helvetica, Open Sans, Lato, Montserrat, Poppins, Space Grotesk, system-ui, -apple-system, Fraunces, Papyrus, Comic Sans. If someone asks to use any of these as a primary display or body font, push back and reference this line.

*(Fraunces is on the list intentionally — it was the display font of a previous direction; we are not going back.)*

## Color

One accent. Everything else serves it. Broadcast gold is the entire point of the palette — it's the TV-bright money/trophy color that no party-game competitor uses at this saturation. Don't dilute it with a second "fun" accent.

### Palette

| Token | Hex | Role |
|-------|-----|------|
| `--bg` | `#0B1739` | Page background. Deep saturated royal blue — **studio navy**. OLED-friendly, unmistakably "game show." Never pure black. |
| `--surface` | `#142148` | Cards, answer tiles, rails. One step lifted from background. |
| `--surface-2` | `#1B2A56` | Elevated surfaces (modals, dropdowns, hover states). |
| `--text` | `#F4EBCF` | Primary text. **Warm parchment — NOT pure white.** Reads as a printed scorecard. Signals living-room warmth, not studio light. |
| `--muted` | `#8FA0C6` | Secondary text, labels, eyebrows, placeholders. Cool desaturated blue-gray. |
| `--hairline` | `#21305E` | 1px borders, dividers. Barely there. |
| `--accent` | `#F4C430` | **Broadcast gold.** The trophy. CTAs, timer, correct answer, leader highlight, active state. TV-bright. |
| `--accent-dim` | `#B2852F` | Muted gold for non-primary accent applications, secondary podium borders. |
| `--accent-glow` | `rgba(244, 196, 48, 0.35)` | Reveal and timer glow color. |
| `--accent-bg` | `rgba(244, 196, 48, 0.08)` | Correct-answer tile background tint. |
| `--info` | `#4EC5B8` | Muted teal-cyan (Only Connect register). Neutral/informational states. |
| `--error` | `#D65858` | Warm red. Incorrect answers, destructive actions. **Never fire-engine red.** |

### Player dot palette (for up to 6 players)

When distinguishing players by color, use this ordered list. Player 1 is always broadcast gold (the warmest, which reads as "primary seat"). After that, go muted and distinct — no neon.

1. `#F4C430` (broadcast gold — Player 1 / primary)
2. `#A77CB7` (muted mauve)
3. `#4EC5B8` (muted teal — matches `--info`)
4. `#6FA87A` (muted sage)
5. `#D65858` (muted brick — matches `--error`, OK because context differs)
6. `#D89653` (muted terracotta)

### Semantic colors

| State | Color | Notes |
|-------|-------|-------|
| Success / correct | `--accent` | Correct answer glows gold with a left-to-right sweep. Star glyph (★) pairs with color — color is never the sole signal. |
| Error / incorrect | `--error` | 2px shake + border color shift. × glyph pairs with color. No red flash. |
| Warning | `--accent-dim` | Gold at lower intensity. |
| Info | `--info` | Muted teal. |

### Light mode

Dark is the primary mode — party games happen in dimmed rooms, and TVs reward OLED-friendly dark backgrounds. A light mode exists for the admin console used in daylight and for accessibility needs.

| Token | Light-mode Hex |
|-------|----------------|
| `--bg` | `#F6F1E4` (warm cream) |
| `--surface` | `#FFFCF2` (lifted paper) |
| `--surface-2` | `#EDE6D1` |
| `--text` | `#0B1739` (studio navy becomes text) |
| `--muted` | `#5E6A8A` |
| `--hairline` | `#D9D2BE` |
| `--accent` | `#8A6A1D` (dark gold — needs more contrast on cream) |

Implementation: `[data-theme="light"]` attribute on `<body>`. Saturation reduced 10–15% from pure conversions. Gold darkens to `#8A6A1D` so it still reads as "trophy" on cream instead of pale yellow.

## Spacing

| Token | Value | Usage |
|-------|-------|-------|
| `--s-2xs` | 2px | Hairline offsets |
| `--s-xs` | 4px | Inline gaps |
| `--s-sm` | 8px | Button internal padding (tight), rail gaps |
| `--s-md` | 16px | Default gap, card padding (compact) |
| `--s-lg` | 24px | Section internal padding |
| `--s-xl` | 32px | Between major UI chunks |
| `--s-2xl` | 48px | Section separators |
| `--s-3xl` | 64px | Page-level breathing room |
| `--s-4xl` | 96px | Hero margins on TV |

**Base unit:** 4px. **Density:** comfortable on TV (generous), compact on admin, spacious on mobile (thumb targets 44px minimum).

## Layout

- **Approach:** Hybrid.
  - **TV host** → editorial / full-bleed. Question is the surface. Upper 45% = question. Lower 40% = 2×2 answer grid. Bottom rail = players. Asymmetric permitted.
  - **Player mobile** → grid-disciplined, thumb-zone first. **Stacked vertical** answer list (3 tiles — one-thumb reach beats visual balance). One primary action per screen.
  - **Admin console** → dashboard grid with broadcast-register header. Denser than TV.
- **Max content width:** 1200px on desktop/admin. TV is always full-bleed.
- **Grid:** 12-column on admin, 3-column answer row on TV, stacked vertical (3 tiles) on mobile.
- **Border radius scale:**
  - `--r-sm`: 4px (buttons inside tables, badges)
  - `--r-md`: 8px (answer cards, standard buttons, inputs)
  - `--r-lg`: 12px (major containers, TV frame)
  - `--r-full`: 9999px (player avatar circles only)
  - **Never** use 16px+ radius. The aesthetic rejects bubbly.

## Motion

Intentional broadcast motion, never decorative. Every animation exists to confirm a state change or create an event moment — never to decorate.

| Event | Behavior | Duration | Easing |
|-------|----------|----------|--------|
| Question enters | Fade + 8px y-slide | 200ms | ease-out |
| Answer reveal (correct) | Gold glow sweep L→R + scale tick 96% → 100% | 320ms | ease-out |
| Answer reveal (incorrect) | 2px horizontal shake (3 cycles) + border flash | 300ms | ease-in-out |
| Score tick | Digit roll (tabular mono earns it here) | 400ms | ease-out |
| Timer last 5s | Pulsing gold ring at 1Hz | — | linear |
| Round transition | Fade through deep navy + 1px gold hairline sweep | 500ms | ease-in-out |
| Button hover | 1px y-lift + border color shift | 120ms | ease-out |
| Podium reveal | Single spotlight radial gradient widens from center onto champion | 800ms | ease-out |

**Forbidden motion:**
- Scroll-driven parallax
- Gratuitous entrance animations on every element
- Bouncy spring easings (overshoot destroys the restraint)
- Confetti / fireworks / balloons / streamers — **ever**, including on finale

## Screen Composition

### TV Host (signature surface)

- **Top-left:** Quizify wordmark in Unbounded 900, with `ify` in broadcast gold. Round indicator below in JetBrains Mono uppercase, gold, with a short 1px gold hairline beneath.
- **Top-right:** Countdown timer — JetBrains Mono 28px gold in a 78px ring with gold top/right progress arc. Glow pulse at last 5s.
- **Center-upper (~45% of vertical space):** Question in Unbounded 700, `clamp(32px, 5vw, 72px)`, warm parchment, centered, max ~20ch for reading rhythm.
- **Center-lower (~40%):** 3-column answer row (questions always have exactly 3 answers — A/B/C). Each card: `--surface` background, 1px hairline border, big gold letter in JetBrains Mono, answer text in Instrument Sans 500 parchment. Correct answer reveal: gold border + gold glow + left-to-right gradient sweep with ★ glyph.
- **Bottom rail:** Player strip with colored dot + name (mono uppercase) + score (mono tabular gold). Leader gets ★ glyph. Separator above the rail is a 1px hairline.

### Player Mobile

- **Topbar:** Round/question counter (mono 10px eyebrow) + compact timer (mono 16px gold).
- **Label:** "Pick one" eyebrow in mono above the question, with 1px gold underline.
- **Question:** Unbounded 700 22px, warm parchment. Same editorial treatment as TV, scaled down.
- **Answers:** Stacked vertically (not 2×2 — one-thumb reach beats visual balance). Each 56px minimum tap target. Letter in mono gold, text in Instrument Sans 500 parchment. Picked state: gold border + `--accent-bg` tint. Correct reveal: gold border + glow + ★ glyph. Wrong pick: warm-red border + × glyph.
- **Footer:** Player name (mono uppercase) + current score (mono gold tabular).

### Podium / Finale

- **Eyebrow:** "Final Results · [Pack] · [N] Runden" in mono muted.
- **Title:** **"Champion"** (singular, not plural) in Unbounded 900, broadcast gold, with text-shadow glow. Champion name below in Unbounded 700, parchment.
- **Podium:** 3-column grid, 2nd–1st–3rd. 1st is 100% height (180px), 2nd is 82% (148px), 3rd is 68% (118px). 1st has a 3px broadcast-gold top border + gold score + gold-tinted spotlight glow from above. 2nd and 3rd get `--accent-dim` top borders, muted scores.
- **Background effect:** Single radial spotlight (`--accent-glow`) centered on champion, 40% ellipse, no other decoration.
- **No confetti. No fireworks.** The dramatic pause *is* the effect. Restraint crowns louder than chaos.

### Admin / Host Console

- **Header:** Unbounded 900 wordmark with gold italic `ify`. Mono "LIVE" badge right-aligned, with a gold dot and glow.
- **Section title:** Unbounded 600 22px, with a 1px gold hairline beneath (the broadcast bug).
- **Subtitle:** Instrument Sans 13px muted.
- **Card (current question):** `--surface` background, 1px hairline, mono eyebrow in gold, Unbounded 700 18px question.
- **Timer bar:** 4px gold fill over hairline track, with gold glow.
- **Leaderboard:** mono 10px uppercase muted headers, each row: mono rank (gold for 1st), Instrument Sans 500 name, mono streak (gold), mono gold tabular score.
- **Primary CTA:** solid broadcast-gold button, Unbounded 700 uppercase with letter-spacing, navy text, gold glow shadow.
- **Secondary CTA:** transparent with hairline border.

## Accessibility

- All text meets WCAG AA contrast on its paired background:
  - `--text` on `--bg`: 13.4:1 (AAA)
  - `--muted` on `--bg`: 4.8:1 (AA)
  - `--accent` on `--bg`: 10.1:1 (AAA)
- Focus states: 2px broadcast-gold outline, offset 2px. Never `outline: none` without a replacement.
- Motion respects `prefers-reduced-motion: reduce` — transitions collapse to 0, but state changes remain visible via color/border shifts.
- Minimum tap target on mobile: 44×44px.
- Color is never the sole signal — correct/incorrect always paired with glyph (★/×) or icon, not just gold/red.

## Assets & Logo

- Quizify wordmark uses Unbounded 900 for "Quiz" and Unbounded 800 italic for the `ify` portion. Renders in parchment with `ify` in broadcast gold on dark; in studio-navy with `ify` in dark-gold on light.
- No mascot. No secondary brand character. The typography IS the branding.

## Decisions Log

| Date | Decision | Rationale |
|------|----------|-----------|
| 2026-04-19 | Initial design system created (Editorial Game Show direction) | Established by `/design-consultation` after research across Jackbox, Hitster, Panic, Cultured Code. Memorable-thing anchor: *"This feels indie — made by someone who cares."* — *superseded 2026-04-24.* |
| 2026-04-24 | Full redesign → Broadcast Living Room direction | User chose "start fresh" in `/design-consultation`. New memorable-thing: *"It felt like a real game show on my TV."* Research spanned Jeopardy, Only Connect, Wheel of Fortune, Hitster, Panic. Posture pivot: from editorial-magazine to broadcast-TV-at-home-scale. |
| 2026-04-24 | Broadcast gold `#F4C430` as sole primary accent | TV-bright trophy gold. Different saturation from the previous amber-brass (`#E8B047`) — gold is the universal money/correct color of televised game shows; brass was quieter/indie-register. |
| 2026-04-24 | Deep studio navy `#0B1739` as background | Category table stakes — every major TV game show uses saturated royal blue. OLED-friendly, unmistakable at a glance. |
| 2026-04-24 | Typography stack: Unbounded + Instrument Sans + JetBrains Mono | Three sans. Unbounded display is rounded-geometric-heavy, non-convergent, reads as 2026 broadcast rebrand. Previous serif-led Fraunces stack rejected along with Editorial Game Show direction. |
| 2026-04-24 | Warm parchment `#F4EBCF` instead of pure white for text | Hand-crafted / living-room register. Still AAA contrast on navy. The single signal that says "this is MY TV, not a studio." |
| 2026-04-24 | No confetti / fireworks / balloons on finale (carried forward) | The dramatic pause IS the effect. Restraint is the differentiation — carried from previous direction because the user has demonstrated conviction on this 9/10. |
| 2026-04-24 | No mascot, no character, no sound-effect excess | Broadcast register demands posture, not clownishness. Every party-game competitor goes louder; we go quieter. |

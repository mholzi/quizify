# Design System — Quizify

> **Soft Parlor.** A party game that feels like a family board game — cozy,
> inviting, warm. Cream paper. Coral, sage, sky, sun. Rounded but never bubbly.
> The quiet confidence of a well-designed toy store, not the volume of a
> studio broadcast.

This file lives in `docs/` and is **not shipped** with the integration (HACS
installs `custom_components/quizify/` only). It came back into the repo with
#1178 so that build agents, reviewers and tests read the same source. Every
token name below is the name used in the code.

## Where the tokens live

| File | Role |
|------|------|
| [`design/tokens.json`](../design/tokens.json) | **The source.** Colours, type sizes, spacing, radii, shadows, motion, z-index, player palette. Not shipped. |
| [`scripts/build_tokens.py`](../scripts/build_tokens.py) | Generates the outputs below. Called by `scripts/build_css.py`. |
| `custom_components/quizify/www/css/src/00-tokens.css` | The generated `:root { … }` block. Everything outside it (font faces, focus ring, reduced-motion overrides) is hand-written. |
| `custom_components/quizify/www/js/tokens.js` | `window.QuizifyTokens` (player colours + every `:root` value). Generated, not loaded by any page yet. |
| `custom_components/quizify/game/palette.py` | `PLAYER_COLORS` for the server. Generated. |
| [`design/lint-baseline.json`](../design/lint-baseline.json) | Ratchet baseline: hard-coded colours, font sizes, radii, shadows, z-indexes, px spacing and durations per file. Counts may only fall. |

Change a token: edit `design/tokens.json`, run `python3 scripts/build_css.py`,
commit the JSON and the regenerated files. `tests/test_design_tokens_1178.py`
fails if they disagree. A branch that edited the `:root` block by hand runs
`python3 scripts/build_tokens.py --import` once to carry its tokens into the
JSON.

Use a token, not a literal. `tests/test_design_tokens_1178.py` counts
literals per file against the baseline; after lowering a count, run
`python3 scripts/design_lint.py --update-baseline`. Every `var(--…)` must name
a token that exists, in pages, CSS modules and JS style strings alike
(`tests/test_page_tokens_defined_705.py`).

Migration status: #1178 steps 1–3 (this file, the generator, the lint). The
remaining steps (accent text tokens, ink on accent surfaces, player colours,
type scale, radii/elevation/z-index, component merges, clean-up) are listed in
#1178. Where this document describes a target that is not built yet, it says so.

## Product Context

- **What this is:** A multiplayer trivia party game that runs entirely inside Home Assistant. The TV hosts the game. Players join on their phones via QR code. No apps, no accounts.
- **Who it's for:** Home Assistant enthusiasts hosting friends and family. People who already run tasteful dashboards in their living room and care about how their home looks.
- **Space / category:** Party trivia games (Jackbox, Kahoot, Hitster) — but deliberately softer, warmer, more inclusive than the category norm.
- **Project type:** Multi-surface web app — TV host screen, mobile player UI, host admin console, Lovelace host card.
- **Memorable thing** (the one anchor every decision serves):
  > *"Cozy and friendly — like a family board game."*
  >
  > Every typography, color, spacing, and motion decision in this document exists to serve this sentence. When in doubt, ask: does this choice feel like a family gathered around a board game, or does it feel like a TV production?

## Aesthetic Direction

- **Direction:** Soft Parlor — Nintendo eShop meets Japanese stationery store. Playful but not childish.
- **Decoration level:** Intentional, warm. Soft radial glows on the page backgrounds (coral top-right, sky bottom-left) at 6–10 % opacity. Soft drop shadows. Rounded corners (10–14 px). Never bubbly.
- **Mood:** Inviting. Warm. Kids and grandparents both read it. Generous whitespace so nothing feels cramped. The colors carry the joy — the typography stays restrained.
- **Anti-patterns (never do these):**
  - Neon / saturated everything (Kahoot)
  - Confetti / balloons / fireworks decoration — *ever, including on finale*
  - Dark backgrounds for the primary surfaces (Soft Parlor is light-only, see [Dark mode](#dark-mode))
  - Cartoon mascots or character illustrations
  - Bubbly radii: nothing above 14 px
  - Pure black text — always warm ink
  - Pure white surfaces on the body — use lifted paper tones
  - Inter, Roboto, Open Sans, Poppins, system-ui as display fonts
  - Gradient CTA buttons
  - Any CRT / scanline / phosphor / retro-tech decoration

## Typography

Three voices. Display is warm and geometric, body is modern and quiet, mono carries the numbers.

| Role | Font | Token | Weight | Rationale |
|------|------|-------|--------|-----------|
| **Display / Questions / Titles** | DM Sans | `--font-display` | 700 / 800 / 900 | Carries the display role at its heavy weights. Was Cabinet Grotesk — see the note below. |
| **UI / Answers / Body** | DM Sans | `--font-body` (`--font-system` is the same stack) | 400 / 500 / 600 / 700 | Clean modern sans. One family across both roles, separated by weight. |
| **Scores / Timers / Meta** | JetBrains Mono | `--font-mono` | 400 / 500 / 700 | Tabular numerals native — earns its keep on live score ticks. |

Weights: `--font-weight-normal` 400, `--font-weight-medium` 500,
`--font-weight-semibold` 600, `--font-weight-bold` 700,
`--font-weight-extrabold` 800.

**Why Cabinet Grotesk is gone (#738).** It is Indian Type Foundry's, under the ITF Free Font License — free to use, but the font files may not be redistributed, which is what shipping them in an MIT repo would be. Loading it from Fontshare on every page was the other half of the problem: it sent every guest's IP to a third party and blocked the first paint. So the face is dropped rather than fetched.

### Loading

The faces are served from the integration itself — `www/fonts/`, declared with
`@font-face` in `css/src/00-tokens.css`. No third-party host is contacted on
any page (#737, #738). `font-display: swap` on every face, so text paints in
the fallback immediately and never sits invisible.

```html
<link rel="preload" as="font" type="font/woff2" href="/quizify/static/fonts/dm-sans-latin.woff2" crossorigin>
<link rel="preload" as="font" type="font/woff2" href="/quizify/static/fonts/jetbrains-mono-latin.woff2" crossorigin>
```

Licence texts and the full file list live in [`www/fonts/README.md`](../custom_components/quizify/www/fonts/README.md).

### Scale (as built)

| Token | Size | `.a11y` (#372) |
|-------|------|----------------|
| `--font-size-xs` | 10px | 11px |
| `--font-size-sm` | 12px | 14px |
| `--font-size-base` | 14px | 16px |
| `--font-size-md` | 16px | 18px |
| `--font-size-lg` | 18px | 20px |
| `--font-size-xl` | 20px | 23px |
| `--font-size-2xl` | 24px | 27px |
| `--font-size-3xl` | 28px | 32px |
| `--font-size-4xl` | 32px | 36px |
| `--font-size-timer` | 64px | — |
| `--font-size-hero` | 72px | — |

Honest status: these tokens are used in about 18 rules. The CSS carries
roughly 150 different literal font sizes, 54 of them below 12 px, and the TV
uses its own `clamp()` expressions (the TV question is
`clamp(28px, 4.2vw, 56px)`). The lint baseline holds that number so it cannot
grow.

Target (#1178 step 8, not built): six steps `--fs-1 … --fs-6` in rem
(12 / 14 / 16 / 20 / 28 / 40 px at the default root), minimum 12 px, and the TV
reaching its sizes through a larger root size instead of its own ladder. The
earlier DESIGN.md ladder (`--fs-mono`, `--fs-body` … `--fs-4xl`) was never in
the code; those names do not exist.

### Fonts blacklist (never use)

Inter, Roboto, Arial, Helvetica, Open Sans, Lato, Montserrat, Poppins, Space Grotesk, system-ui, -apple-system (as display), Papyrus, Comic Sans, Fraunces, Unbounded. Those last two were previous directions' display fonts.

## Color

A four-color party palette at muted saturation, plus one warm cream neutral. The colors do the emotional work; the typography stays quiet underneath.

### Palette

The code has several aliases per value (left over from earlier themes). The
first token in each row is the one to use; the others are listed so a reader
recognises them, and go away in #1178 step 11.

| Role | Hex | Token (use) | Aliases with the same value |
|------|-----|-------------|-----------------------------|
| Page background, cream paper | `#FAF6EC` | `--color-bg-primary` | `--color-bg-light` |
| Surface (cards, tiles, rails) | `#FFFFFF` | `--color-bg-surface` | `--color-bg-white`, `--color-bg-card`, `--color-dark-surface`, `--color-dark-card`, `--color-surface-dark` |
| Surface 2 (hover, chips, modals) | `#F3EEDF` | `--color-bg-card-alt` | `--color-dark-surface-hover` |
| Text, warm ink | `#2A2820` | `--color-text-primary` | `--color-text-dark`, `--color-text-heading`, `--color-text-white` (misnamed: it is ink) |
| Text, secondary | `#3C3930` | `--color-text-secondary` | |
| Text, muted (labels, eyebrows) | `#6E6A5C` | `--color-text-muted` | `--color-text-neon-muted` |
| Text, light | `#8F8A78` | `--color-text-light` | Not a text colour (3.2 : 1). Since #1178 step 4 every text rule that used it uses muted; it remains on a few borders and the "missed" verdict disc |
| Hairline | `#E5DFCF` | `--color-border-light` | `--color-border-neon` |
| Border medium / dark | `#D4CEBC` / `#BDB597` | `--color-border-medium` / `--color-border-dark` | |
| **Coral**, primary accent | `#E88A7F` | `--color-accent-primary` | `--color-accent-brand` |
| Coral hover | `#D97568` | `--color-accent-brand-hover` | |
| **Sage** | `#7FA897` | `--color-accent-secondary` | `--color-success`, `--color-success-alt`, `--color-success-neon` |
| **Sky** | `#7FA8C4` | `--color-accent-blue` | hover `--color-accent-blue-hover` `#6A9AB6` |
| **Sun** | `#E8C47F` | `--color-warning` | `--color-warning-alt`, `--color-gold` |
| Error, warm brick (never fire-engine) | `#D66A6A` | `--color-error` | `--color-error-neon`; dark `--color-error-dark` `#BB5353` |
| Mauve | `#B78FC7` | `--color-purple-primary` | `--color-neon-purple` |
| Medals | `#E8C47F` / `#B8B2A0` / `#C99466` | `--color-gold` / `--color-silver` / `--color-bronze` | |

Tints and glows: `--color-success-light` / `-bg` (sage 18 %),
`--color-warning-light` (sun 20 %), `--color-error-light` (15 %),
`--color-error-bg` (14 %), `--color-accent-brand-bg` (coral 16 %), and the
connection-dot halos `--color-*-glow` (45 %, #983).

**Accents are surfaces, not text.** Coral, sage, sky and sun on cream reach
only 1.5–2.5 : 1 (see [Accessibility](#accessibility)). Use them for fills,
borders, dots and bars, never in `color:` on a light ground.

### Colour roles (#1178 steps 4–5)

| Role | Token | Value | Rule |
|------|-------|-------|------|
| Accent surface | `--color-accent-primary` (coral), `--color-accent-secondary` / `--color-success` (sage), `--color-accent-blue` (sky), `--color-warning` (sun), `--color-error`, `--color-purple-primary` (mauve) | unchanged | Fill, border, dot, bar. Never text on a light ground. |
| Accent text | `--coral-ink` · `--sage-ink` · `--sky-ink` · `--sun-ink` · `--error-ink` · `--mauve-ink` | `#A4524A` · `#4C7262` · `#46708C` · `#86621F` · `#AC4444` · `#805D8E` | When text (or an icon stroke) has to be coloured: eyebrows, scores, timer warning, "Correct", deltas. ≥ 4.5 : 1 on cream, white, surface 2 and the accent's own tint. |
| Medal text | `--gold-ink` · `--silver-ink` · `--bronze-ink` | `#86621F` · `#736C58` · `#8E5E33` | Rank numerals and podium digits. The medal fills stay `--color-gold` / `--color-silver` / `--color-bronze`. They replace the one-off rank colours `#B08930` / `#6F7C8A` / `#A35A2B`. `--bronze-ink` also strokes the clay "mixed" category icon. |
| Text on an accent surface | `--on-accent` | `#2A2820` (ink) | Primary buttons, "Resume", answer letters, rank discs, verdict and step badges, the error toast. Decided 2026-11-02: ink, not white (white on coral is 2.5 : 1). |
| Body text | `--color-text-primary` · `--color-text-muted` | `#2A2820` · `#6E6A5C` | Everything else. `--color-text-light` is not a text colour. |

Exceptions, on purpose: the wordmark's coral `ify` (a logotype, which WCAG
exempts) and accent text on the dark grounds — the power-up banner (sun on ink,
8.9 : 1) and the finale countdown overlay — keep the accent itself.

### Player dot palette

As built, there are three lists, and the visible one is not Soft Parlor:

- **Server** — `PLAYER_COLORS`, generated from `players` in `design/tokens.json` into `game/palette.py`. Twenty Flat-UI colours (`#FF6B6B`, `#4ECDC4`, `#45B7D1`, …). This is what the lobby, TV and leaderboard show.
- **Host console** — `_LOBBY_COLORS` in `admin.js` mirrors the first ten.
- **Phone reveal** — `_PLAYER_COLORS` in `player-reveal.js`, eight Soft Parlor tones.

Target (#1178 step 6, decided 2026-11-02, not built): one list for every
surface, from the token file:

1. `#E88A7F` coral
2. `#7FA897` sage
3. `#7FA8C4` sky
4. `#E8C47F` sun
5. `#B78FC7` mauve
6. `#D89E6F` terracotta
7. `#9DB38A` moss
8. `#C7A3A0` rosé

Initials on a player dot in ink.

### Semantic colors

| State | Token | Notes |
|-------|-------|-------|
| Success / correct | `--color-success` (sage) | Correct answer glows sage with a warm halo. Paired with a ★ glyph (color is never the sole signal). |
| Error / incorrect | `--color-error` | Shake + warm-red border. × glyph pairs with color. No red flash. |
| Warning | `--color-warning` (sun) | |
| Info | `--color-accent-blue` (sky) | |

### Dark mode

**Removed.** There is no dark mode, and none is planned (maintainer decision,
2026-11-02, #1178).

The dark tokens and the `prefers-color-scheme` block described in earlier
versions of this file were dead code: `player.html`, `admin.html` and
`dashboard.html` all carry `<html data-theme="light">` and nothing flips it.
#383 / #397 removed the unreachable CSS, and
`tests/test_ux_darkmode_deadcode_383.py` fails if a `prefers-color-scheme: dark`
block or a `[data-theme="dark"]` rule comes back. A phone in OS dark mode shows
the cream page.

Bringing it back would be a deliberate design change: the generator would
write a dark block, the pages would drop `data-theme="light"`, and the #383
guard would be inverted. The dark values from the old spec failed AA for muted
text on surface 2 and for error text, so they would need correcting first.

The **Lovelace host card** is the exception by design: it uses only Home
Assistant theme variables (`--primary-color`, `--secondary-text-color`,
`--divider-color`, …) and follows the dashboard theme, light or dark. It takes
no Quizify tokens beyond fallback values.

## Spacing

| Token | Value |
|-------|-------|
| `--space-xs` | 4px |
| `--space-sm` | 8px |
| `--space-md` | 16px |
| `--space-lg` | 24px |
| `--space-xl` | 32px |
| `--space-2xl` | 48px |

**Base unit:** 4 px. **Density:** comfortable on TV (generous), comfortable on admin, spacious on mobile (thumb targets 44 px minimum). The earlier `--s-*` names (with 2 / 64 / 96 px) were never in the code. The TV does not use the spacing tokens yet.

## Layout

- **Approach:** Hybrid, with more breathing room than Broadcast Living Room.
  - **TV host** → centered editorial composition. Question takes the upper third with large margins. 3-column answer row, stacked on phones.
  - **Player mobile** → grid-disciplined, thumb-zone first. **Stacked vertical** answer list (3 tiles).
  - **Admin console** → softer dashboard grid. Sections are card-shaped with shadows, not flat.
- **Max content width:** 1200 px on desktop/admin. TV is always full-bleed.
- **Grid:** single-column on mobile, 12-column on admin with 4-unit gutter, 3-column answer row on TV.

### Radii

| Token | Value | Use |
|-------|-------|-----|
| `--radius-sm` | 6px | chips, badges |
| `--radius-md` | 10px | buttons, answer tiles, inputs |
| `--radius-lg` | 12px | cards (merges into 14 px, see below) |
| `--radius-xl` | 14px | cards, modals, panels |
| `--radius-2xl` | 14px | duplicate of `--radius-xl` |
| `--radius-full` | 9999px | player dots, pills |

Decided 2026-11-02 (#1178 step 9, not built): three radii only — 6, 10, 14 —
plus full. 12 px merges into 14 px. **Nothing above 14 px**; eight rules in the
code still use 16–22 px.

### Elevation

| Token | Value |
|-------|-------|
| `--shadow-sm` | `0 1px 2px rgba(42, 40, 32, 0.06)` |
| `--shadow-md` | `0 2px 8px rgba(42, 40, 32, 0.08)` |
| `--shadow-lg` | `0 6px 20px rgba(42, 40, 32, 0.10)` |
| `--shadow-xl` | `0 16px 40px rgba(42, 40, 32, 0.14)` |
| `--glow-primary` / `-secondary` / `-success` / `-warning` / `-error` | `0 4px 12–14px` accent at 22–28 % |

### Z-index

| Token | Value |
|-------|-------|
| `--z-control-bar` | 100 |
| `--z-modal` | 1000 |
| `--z-overlay` | 1100 |

Literal z-indexes up to 9999 still exist (toasts); the baseline holds them.

## Motion

Warm, soft, welcoming. Ease-out curves only, nothing overshoots (a spring
fights the warm posture). Every animation from #1169 / #1170 reads its
duration **and** its delays from tokens, which is what lets
`prefers-reduced-motion` and the `.a11y` mode zero all of them at once
(`00-tokens.css` below `:root`, and `09-a11y.css`). Lightning and Hot Seat run
under `.motion-fast` with roughly halved values.

Easings: `--ease-out` `cubic-bezier(0.22, 0.61, 0.36, 1)`,
`--ease-in-out` `cubic-bezier(0.45, 0, 0.25, 1)`.
Older transition shorthands: `--transition-fast` 120ms ease-out,
`--transition-normal` 220ms ease-out, `--transition-slow` 400ms ease-in-out.

### Round 1 (#1169, shipped in #1171)

| Event | Behaviour | Tokens | Values | Surface |
|-------|-----------|--------|--------|---------|
| Question enters | Headline fade + 10 px rise, tiles 50 ms apart | `--dur-enter`, `--dur-enter-lead`, `--dur-enter-stagger` | 240 / 160 / 50 ms | TV, phone |
| Round transition | One warm band, card fades in behind it | `--dur-round`, `--dur-round-card`, `--dur-round-card-delay` | 500 / 260 / 240 ms | TV |
| Score tick | Digit roll, ones land last | `--dur-roll`, `--dur-roll-stagger` | 400 / 60 ms | TV, admin |
| Answer distribution | Bars grow after the correct/wrong colour lands; halo on the correct bar | `--dur-bar`, `--dur-bar-lead`, `--dur-bar-stagger`, `--dur-halo`, `--dur-halo-lead` | 520 / 320 / 70 / 900 / 520 ms | TV |
| Podium | 3 → 2 → 1, then the champion's glow widens (~2.5 s) | `--dur-podium-rise`, `--dur-podium-second`, `--dur-podium-first`, `--dur-podium-glow-delay`, `--dur-podium-glow` | 320 / 700 / 1500 / 1750 / 800 ms | TV, phone |
| Lock-in | Tile locks, check draws, "Sent" line | `--dur-lock`, `--dur-check`, `--dur-check-delay`, `--dur-status`, `--dur-status-delay` | 220 / 280 / 120 / 200 / 300 ms | phone |

### Round 2 (#1170, shipped in #1172)

| Event | Tokens | Values | Surface |
|-------|--------|--------|---------|
| Leaderboard rows move to their new place; sage tint on a row that moved up | `--dur-flip`, `--dur-flip-tint` | 520 / 1200 ms | TV |
| Lightning splash: sun underline, title / underline / rules 120 ms apart | `--dur-underline`, `--dur-splash-stagger` | 360 / 120 ms | phone, TV |
| Streak badge bump + sun flush; a broken streak greys out | `--dur-streak`, `--dur-streak-break` | 420 / 500 ms | phone, TV |
| Hot Seat settlement lands | `--dur-settle` | 420 ms | phone, TV |
| Phone colour drains while the socket is down; connection pill stays sage | `--dur-desat`, `--dur-conn-back` | 400 / 1200 ms | phone |

### Other motion

| Event | Behaviour | Duration | Token |
|-------|-----------|----------|-------|
| Answer reveal (correct) | Sage halo + scale 0.96 → 1.00 | 320 ms | none yet |
| Answer reveal (incorrect) | Horizontal shake + border flash | spec 300 ms, code 0.5 s (`@keyframes shake`) | none yet |
| Timer last 5 s | Soft coral pulse at 1 Hz (opacity 1.0 → 0.7) | — | — |
| Button hover | 1 px y-lift + coral-tint shadow | 120 ms | `--transition-fast` |

About 100 literal durations remain in CSS and JS (99 when the lint landed), mostly 0.12 s / 0.2 s hover
transitions; the baseline holds them.

**Forbidden motion:**
- Scroll-driven parallax
- Gratuitous entrance animations on every element
- Bouncy spring easings (overshoot fights the warm posture)
- Confetti / fireworks / balloons / streamers — **ever**, including on finale

## Screen Composition

### TV Host (signature surface)

- **Top-left:** Quizify wordmark in display 800, `ify` in coral. Round indicator below in JetBrains Mono in a soft sun-yellow pill.
- **Top-right:** Countdown timer — JetBrains Mono 28 px in coral, in a soft white-bg rounded pill with 12 px drop shadow.
- **Center-upper:** Question in display 700, `clamp(28px, 4.2vw, 56px)`, warm ink, centered, max 22 ch for reading rhythm.
- **Center-lower:** 3-column answer row. Each tile: white surface, 1 px soft-cream border, big circled letter in a colored pill (coral / sage / sky), answer text in DM Sans 500 warm ink. Correct reveal: sage halo + ★ glyph + gentle scale tick. Layered soft drop shadow.
- **Bottom rail:** centered player strip with colored dot + name (DM Sans 600 warm ink) + score (mono tabular). Leader has a ★ glyph. No rail separator — the page's warm breathing does the work.

### Player Mobile

- **Topbar:** Round counter (mono eyebrow) + timer pill (mono 16 px in a white pill).
- **Label:** "Pick one" eyebrow in mono.
- **Question:** display 700 22 px, warm ink.
- **Answers:** Stacked vertically. Each 56 px min tap target. White bg, soft cream border, colored letter pill on the left (ink letter), DM Sans 500 text. Picked: coral outlined + coral-tint bg. Correct: sage outlined + sage-tint bg + ★. Wrong pick: warm-red outlined + ×.
- **Footer:** Player name (DM Sans 600 warm ink) + current score (mono tabular).

### Podium / Finale

- **Eyebrow:** "Final Results · [Pack] · [N] Runden" in mono muted olive.
- **Title:** **"Champion"** (singular) in display 900, warm coral, with a soft warm drop shadow. Champion name below in display 700, warm ink.
- **Podium:** 3-column grid, 2nd–1st–3rd. No trophy emojis — numbers speak. Two surface treatments:
  - **Player phone ("Podium Reborn", approved 2026-06-09):** bolder rising blocks with a warm *tonal* gradient fill (light tint → base hue, same color — 1st coral, 2nd sage, 3rd sky), numerals centered, 14 px rounded top + 6 px foot, soft lift shadow. Top edge keeps the medal accent: 1st sun-yellow, 2nd silver, 3rd bronze. A warm radial halo (sun + coral) rises from the champion's foot. Tonal gradients here are a deliberate, user-approved exception to the general no-gradient rule; they stay single-hue and muted.
  - **Admin / TV (host screen):** cream "shelf" planks (no gradient) — 1st 200 px with a 3 px sun-yellow top border and a soft warm-gradient halo above, 2nd silver top border, 3rd bronze. The numeral is the medal.
- **Background effect:** Single soft warm radial glow behind the champion (coral + sun). No confetti.

### Admin / Host Console

- **Header:** display 800 wordmark with coral `ify`. Mono "LIVE" badge right-aligned, with a sage dot (calm, not urgent).
- **Section title:** display 600 22 px.
- **Section card:** white surface, 1 px soft-cream border, 10 px radius, soft drop shadow.
- **Card (current question):** white surface, mono eyebrow, display 700 18 px question, warm ink.
- **Timer bar:** 4 px coral fill over cream hairline track.
- **Leaderboard:** mono uppercase muted headers, each row: mono rank, DM Sans 500 name, mono streak, mono tabular score.
- **Primary CTA:** solid coral button, DM Sans 700, soft coral shadow. Sentence-case, not ALL CAPS. The label is white today; it becomes ink (decided 2026-11-02, #1178 step 5) because white on coral is 2.5 : 1.
- **Secondary CTA:** white bg, cream hairline border, warm-ink text.

### Welcome / Setup (host) — "Categories-forward" (approved 2026-06-09)

- **Featured pack ("Soft Spotlight"):** white card with a coral hairline border. SVG line trophy in a sun-tinted rounded badge, mono "Empfohlen · Neu" eyebrow, display 700 title, muted desc, and a round coral selection indicator on the right (outline → filled coral check when the pack is selected). The whole card toggles the pack; Start is separate.
- **Category picker:** two-column grid of selectable tiles. Each tile: an **SVG line icon** (never emoji) in a theme-tinted rounded disc, display 700 name, mono question count. Icon disc tint + icon stroke cycle the four accents by theme (coral / sage / sky / sun; "Gemischt" stays neutral clay). Default = white + cream hairline; hover = lifts; selected = coral border + coral-wash + corner check.
- **Icons are keyed by `data-theme`** so both languages share one glyph set (globe, leaf, clapperboard, music note, flag, flask, scroll, fork, bulb, dice, trophy). Tints are flat (no gradients).

### Icon system — "Rounded Duotone" (approved 2026-06-10, #212)

- **No emoji as a standalone UI icon.** Emoji render differently per OS, don't inherit the Soft Parlor palette, and clash with the SVG hero. App-wide the category/theme icons are SVG line glyphs from one shared set (`www/js/icons.js`, global `window.QuizifyIcons`), consumed by both admin and player JS.
- **Style = "Rounded Duotone" (Option 2).** A 2px round-capped, round-joined glyph (`stroke:currentColor; fill:none`) over a **soft accent-tinted rounded backing disc**. CSS class `.qz-icon` owns the disc; `.qz-icon--{mix,coral,sage,sky,sun}` set the flat tint + stroke color.
- **Tints cycle the four accents by theme** (coral / sage / sky / sun; `mixed`/`worldcup` use clay/sun). Flat — **no gradients**.
- **Where applied (P1):** welcome hero category tiles, detail-view pack cards (`#category-chips .pack-card-icon`), and theme filter tabs. i18n label strings hold **text only** — the icon is rendered separately, never embedded in the translated string.

## Accessibility

Contrast is WCAG 2.x relative luminance, computed from the token values
(recomputed 2026-11-02 for #1178; the earlier values in this file — 12.2, 4.6,
3.1 and 3.4 — were wrong). Targets: 4.5 : 1 for text, 3 : 1 for large text
(24 px, or 18.7 px bold) and for icons and input borders.

| Pair | Ratio | Result |
|------|-------|--------|
| Ink `#2A2820` on cream `#FAF6EC` | 13.7 : 1 | AAA |
| Ink on surface `#FFFFFF` | 14.8 : 1 | AAA |
| Ink on surface 2 `#F3EEDF` | 12.7 : 1 | AAA |
| Muted `#6E6A5C` on cream | 5.0 : 1 | AA |
| Muted on surface 2 | 4.7 : 1 | AA |
| Text-light `#8F8A78` on cream | 3.2 : 1 | **fails** AA |
| Coral `#E88A7F` as text on cream | 2.3 : 1 | **fails**, even as large text |
| Coral as text on white | 2.5 : 1 | **fails**, even as large text |
| White on coral (primary CTA today) | 2.5 : 1 | **fails**, even AA large |
| Cream on coral (answer letter, admin/TV) | 2.3 : 1 | **fails** |
| Ink on coral (answer letter, phone) | 5.9 : 1 | AA |
| Sage `#7FA897` as text on cream | 2.5 : 1 | **fails** |
| White on sage ("Resume") | 2.6 : 1 | **fails** |
| Ink on sage | 5.6 : 1 | AA |
| Sun `#E8C47F` as text on cream | 1.5 : 1 | **fails** |
| Ink on sun | 8.9 : 1 | AAA |
| Error `#D66A6A` as text on cream | 3.2 : 1 | **fails** AA (large only) |
| Error-dark `#BB5353` on white | 4.7 : 1 | AA |
| Hairline `#E5DFCF` on cream | 1.2 : 1 | decorative only; not enough for an input border |

The text tokens that replace the failing pairs (#1178 step 4):

| Pair | Ratio | Result |
|------|-------|--------|
| `--coral-ink` `#A4524A` on cream / white / coral tint 16 % | 5.0 / 5.4 / 4.7 : 1 | AA |
| `--sage-ink` `#4C7262` on cream / white / sage tint 18 % | 5.0 / 5.4 / 4.6 : 1 | AA |
| `--sky-ink` `#46708C` on cream / sky tint 18 % | 4.9 / 4.6 : 1 | AA |
| `--sun-ink` `#86621F` on cream / sun tint 24 % | 5.2 / 4.9 : 1 | AA |
| `--error-ink` `#AC4444` on cream / white / error tint 15 % | 5.3 / 5.7 / 4.9 : 1 | AA |
| `--mauve-ink` `#805D8E` on cream / violet tint 12 % | 5.0 / 4.7 : 1 | AA |
| `--gold-ink` / `--silver-ink` / `--bronze-ink` on their rank tints | 4.7 / 4.7 / 4.8 : 1 | AA |
| Muted `#6E6A5C` on surface 2 (was text-light, 3.0) | 4.7 : 1 | AA |
| `--on-accent` (ink) on coral / sage / sky / sun | 5.9 / 5.6 / 5.8 / 8.9 : 1 | AA |
| `--on-accent` on moss `#9DB38A` / rosé `#C7A3A0` / mauve / terracotta | 6.5 / 6.5 / 5.5 / 6.4 : 1 | AA |
| `--on-accent` on error `#D66A6A` (error toast, `.btn-danger`) | 4.3 : 1 | **short of AA** for text; fine for the ✓ / × badges (icons, 3 : 1) |

Rules that follow from this:

- Body text is ink or muted. Coloured text uses the `--*-ink` of its accent,
  never the accent itself.
- Text on an accent surface is `--on-accent` (ink; decided 2026-11-02). The
  phone did this first, for the answer letters; admin and TV now match it.
- Error surfaces are the one open pair: ink on `#D66A6A` is 4.3 : 1. A fix
  needs a decision (a darker error fill, or white on `--error-ink`, 5.7 : 1).
- Focus states: 2 px coral outline, offset 2 px. Never `outline: none` without a replacement.
- Motion respects `prefers-reduced-motion: reduce` — durations and the motion delay tokens collapse to 0; `.a11y` does the same.
- Minimum tap target on mobile: 44 × 44 px.
- Color is never the sole signal — correct/incorrect always paired with glyph (★ / ×).

## Assets & Logo

- Quizify wordmark uses display 800 for "Quiz" and display 800 for `ify` in coral.
- No mascot, no secondary brand character. The typography + palette IS the branding.

## Security model (LAN-first; remote exposure caveats)

> Reviewed 2026-06-11 (#259, part of the #252 code review). Captures the
> *intentional* exposure posture so future changes don't accidentally weaken it
> or "fix" something that is a deliberate trade-off.

**Threat model: a trusted home LAN.** Quizify is a party game played by people
in the same room. Players join from their own phones by scanning a QR / opening
a URL — **they have no Home Assistant login** and must not need one. Because of
that, the player-facing HTTP and WebSocket endpoints are intentionally **open on
the LAN**: they are registered directly on HA's aiohttp router (not as
auth-gated `HomeAssistantView`s), so they answer without HA credentials. This is
by design and must stay that way — gating the game WebSocket, `flag-question`,
or `pack-submit` behind HA auth would lock every player out of the game.

**Endpoint exposure map** (all reachable without HA auth on the LAN):

| Endpoint | Method | Effect | Why it stays open |
|----------|--------|--------|-------------------|
| `/quizify/api/.../ws` (game WebSocket) | WS | Play the game; admin-as-player join (see below) | Players have no HA login. |
| `/api/quizify/flag-question` | POST | **Writes** a flag record to disk | A player must be able to report a bad question mid-game. |
| `/api/quizify/flags` | GET | **Discloses** flagged questions | Low-sensitivity game metadata; host reads it from the same un-authed admin page. |
| `/api/quizify/pack-submit` | POST | Proxies a composed pack to the worker (rate-limited, optional shared secret) | Composed by the host in the un-authed admin UI; inert until `community_submit_url` is configured. |
| `/api/quizify/analytics/data`, `/all-time`, `/question-stats` | GET | **Discloses** game/leaderboard analytics | Shown on the un-authed TV/analytics surfaces. |

**Remote exposure rule (CRITICAL).** Because these endpoints are un-authed,
**remote exposure must be fronted by Home Assistant auth — or not done at all.**
- **Nabu Casa Remote UI does not satisfy this requirement.** Quizify registers
  its routes as raw handlers, which HA's auth middleware does not check, and the
  origin guard allow-lists the Nabu Casa host. Anyone holding the join link can
  reach the game routes through the tunnel without an HA login.
- A **self-hosted reverse proxy** (nginx / Traefik / Cloudflare Tunnel) that
  forwards `/quizify/*` or `/api/quizify/*` to HA **without** an auth layer in
  front would expose the write/disclosure endpoints above to the internet. Do
  not do this. If you proxy, require auth at the proxy.
- When a reverse proxy *is* in play, configure HA's
  `http.use_x_forwarded_for` + `trusted_proxies` so the pack-submit rate-limiter
  keys on the real client IP rather than the proxy's single address. We
  deliberately do **not** parse `X-Forwarded-For` ourselves (an attacker-set
  header would let any client spoof the bucket key) — see
  `server/pack_submission.py`.

**Admin-as-player join (`is_admin: true`) — accepted #208 trade-off.** The
server trusts `is_admin: true` in the join message at face value rather than
requiring the admin session token on the player join (see the 2026-04-28
Decisions Log entry). A malicious LAN client can therefore claim the single
admin slot. This is an accepted LAN trade-off, not a bug: the home LAN is
trusted, remote access is gated by HA auth per the rule above, and only one
admin slot exists per game. Behaviour is intentionally left unchanged.

**Hardened in #259** (defence-in-depth, no behaviour change for legitimate use):
the admin session-token comparison now uses `hmac.compare_digest`
(constant-time, no timing oracle — `server/connection.py`), and the pack-submit
rate-limit IP resolution is documented as proxy-aware via HA's trusted-proxy
config.

## Decisions Log

| Date | Decision | Rationale |
|------|----------|-----------|
| 2026-04-19 | Initial design system created (Editorial Game Show) | First pass. Superseded. |
| 2026-04-24 | Full redesign → Broadcast Living Room | TV-broadcast posture at home scale. Shipped as v1.1.0-beta.1 through beta.3. |
| 2026-04-24 | Full redesign → **Soft Parlor** (CURRENT) | User pivoted from broadcast register to family-board-game register after seeing the 5-theme shotgun. Memorable-thing moves from "real game show on my TV" to "cozy and friendly — like a family board game". |
| 2026-04-24 | Coral `#E88A7F` as primary accent | Warm, hospitable, non-gendered. Differentiated from category norms (Jackbox primary = purple, Kahoot = hot pink). |
| 2026-04-24 | Cream `#FAF6EC` as primary background | Explicit break from Broadcast Living Room's dark navy. Soft Parlor is light-primary. Warm paper reads as "home", not "studio". |
| 2026-04-24 | No confetti on finale (carried forward, 3rd time confirmed) | Restraint has been the consistent preference across three directions. |
| 2026-04-28 | Admin-as-player trust model (Beatify pattern) | Server trusts `is_admin: true` in the join message at face value. **Trade-off:** a malicious LAN client can spoof admin. Mitigations: trusted home LAN; remote access gated by HA auth; only one admin slot per game. v1.1.2. |
| 2026-06-09 | Welcome/setup hero → "Categories-forward" | SVG-icon tinted category tiles + "Soft Spotlight" featured card, no emoji. |
| 2026-06-09 | Player-phone podium → "Podium Reborn" | Rising blocks with muted single-hue tonal gradients; admin/TV keep the cream shelf. Scoped exception to the no-gradient rule. |
| 2026-06-10 | App-wide icon system → "Rounded Duotone" SVG line icons (#212) | Shared `www/js/icons.js`; emoji pulled out of `theme.*` i18n strings. |
| 2026-06-11 | Security model documented; constant-time admin-token compare (#259) | LAN-first exposure posture is intentional; remote exposure must be fronted by HA auth. |
| 2026-07 | Dark mode CSS removed (#383, #397) | All pages force `data-theme="light"`; the dark CSS could never apply. |
| 2026-09-05 | Display face drops to DM Sans; all faces bundled (#737, #738) | Cabinet Grotesk's licence forbids redistributing the files, and fetching it leaked every guest's IP. |
| 2026-11-02 | Motion spec finished: round 1 (#1171) and round 2 (#1172) | Every duration and delay is a `--dur-*` token so reduced motion and `.a11y` can zero them together. |
| 2026-11-02 | Design-system review (UX review 8) accepted; migration plan #1178 | One token source (`design/tokens.json`) with a generator and a ratchet lint; this file back in the repo under `docs/`, not shipped. Decisions: text on accent surfaces is ink (5.9 : 1 on coral), coral stays; dark mode removed from the spec, not built; host card keeps the HA theme; three radii (12 → 14, nothing above 14); player colours 7 and 8 are moss `#9DB38A` and rosé `#C7A3A0`. Contrast values recomputed — coral on cream is 2.3 : 1 and white on coral 2.5 : 1, not 3.1 and 3.4. |

# Design System — Quizify

> **Soft Parlor.** A party game that feels like a family board game — cozy,
> inviting, warm. Cream paper. Coral, sage, sky, sun. Rounded but never bubbly.
> The quiet confidence of a well-designed toy store, not the volume of a
> studio broadcast.

## Product Context

- **What this is:** A multiplayer trivia party game that runs entirely inside Home Assistant. The TV hosts the game. Players join on their phones via QR code. No apps, no accounts.
- **Who it's for:** Home Assistant enthusiasts hosting friends and family. People who already run tasteful dashboards in their living room and care about how their home looks.
- **Space / category:** Party trivia games (Jackbox, Kahoot, Hitster) — but deliberately softer, warmer, more inclusive than the category norm.
- **Project type:** Multi-surface web app — TV host screen, mobile player UI, host admin console.
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
  - Dark backgrounds for the primary surfaces (Soft Parlor is light-primary)
  - Cartoon mascots or character illustrations
  - Bubbly 20 px+ border-radius on everything
  - Pure black text — always warm ink
  - Pure white surfaces on the body — use lifted paper tones
  - Inter, Roboto, Open Sans, Poppins, system-ui as display fonts
  - Gradient CTA buttons
  - Any CRT / scanline / phosphor / retro-tech decoration

## Typography

Three voices. Display is warm and geometric, body is modern and quiet, mono carries the numbers.

| Role | Font | Source | Weight | Rationale |
|------|------|--------|--------|-----------|
| **Display / Questions / Titles** | DM Sans | bundled, SIL OFL 1.1 | 700 / 800 / 900 | Carries the display role at its heavy weights. Was Cabinet Grotesk — see the note below. |
| **UI / Answers / Body** | DM Sans | bundled, SIL OFL 1.1 | 400 / 500 / 600 / 700 | Clean modern sans. One family across both roles, separated by weight. |
| **Scores / Timers / Meta** | JetBrains Mono | bundled, SIL OFL 1.1 | 400 / 500 / 700 | Tabular numerals native — earns its keep on live score ticks. |

**Why Cabinet Grotesk is gone (#738).** It is Indian Type Foundry's, under the ITF Free Font License — free to use, but the font files may not be redistributed, which is what shipping them in an MIT repo would be. Loading it from Fontshare on every page was the other half of the problem: it sent every guest's IP to a third party and blocked the first paint. So the face is dropped rather than fetched. The screens that visibly change are the ones that lean on display type: TV wordmark and question headline, podium and champion block, pack card titles, admin pack-news heading.

### Loading

The faces are served from the integration itself — `www/fonts/`, declared with
`@font-face` in `css/src/00-tokens.css`. No third-party host is contacted on
any page (#737, #738). `font-display: swap` on every face, so text paints in
the fallback immediately and never sits invisible.

```html
<!-- Every page: preload the two faces every screen needs, so they download
     alongside styles.css instead of after it. No blocking stylesheet link. -->
<link rel="preload" as="font" type="font/woff2" href="/quizify/static/fonts/dm-sans-latin.woff2" crossorigin>
<link rel="preload" as="font" type="font/woff2" href="/quizify/static/fonts/jetbrains-mono-latin.woff2" crossorigin>
```

Licence texts and the full file list live in [`www/fonts/README.md`](custom_components/quizify/www/fonts/README.md).

### Scale

| Token | Size | Usage |
|-------|------|-------|
| `--fs-mono` | 12px | Eyebrows, meta labels, admin table cells, round indicators |
| `--fs-body` | 16px | Body copy, answer button text (mobile) |
| `--fs-lg` | 20px | Lede paragraphs, large UI |
| `--fs-xl` | 28px | Mobile question, admin section titles |
| `--fs-2xl` | 40px | Large section titles, TV answer text |
| `--fs-3xl` | 56px | TV question (default size) |
| `--fs-4xl` | 72px | TV question on very large screens (clamped) |

TV host question uses `clamp(28px, 4.2vw, 56px)`. Display weight for questions is 700 — warm but not heavy.

### Fonts blacklist (never use)

Inter, Roboto, Arial, Helvetica, Open Sans, Lato, Montserrat, Poppins, Space Grotesk, system-ui, -apple-system, Papyrus, Comic Sans, Fraunces, Unbounded. Those last two were previous directions' display fonts — this direction went elsewhere.

## Color

A four-color party palette at muted saturation, plus one warm cream neutral. The colors do the emotional work; the typography stays quiet underneath.

### Palette

| Token | Hex | Role |
|-------|-----|------|
| `--bg` | `#FAF6EC` | Page background. Warm cream paper. NOT pure white. |
| `--surface` | `#FFFFFF` | Cards, answer tiles, rails. Lifted paper on the cream ground. |
| `--surface-2` | `#F3EEDF` | Elevated surfaces (modals, dropdowns, hover). |
| `--text` | `#2A2820` | Primary text. Warm ink — NOT pure black. |
| `--muted` | `#6E6A5C` | Secondary text, labels, eyebrows, placeholders. Warm olive-gray. |
| `--hairline` | `#E5DFCF` | 1 px borders, dividers. |
| `--coral` | `#E88A7F` | **Primary accent.** CTAs, player-1 dot, wordmark `ify`. Warm and hospitable. |
| `--sage` | `#7FA897` | Secondary accent. Correct answers, player-2 dot. |
| `--sky` | `#7FA8C4` | Tertiary accent. Player-3 dot, informational states. |
| `--sun` | `#E8C47F` | Fourth accent. Round-indicator pill, player-4 dot, podium-1st. |
| `--error` | `#D66A6A` | Warm brick red. Incorrect answers, destructive actions. **Never fire-engine red.** |

### Player dot palette (up to 6 players)

When distinguishing players by color (player dots in the rail, avatar rings), use this ordered list. The first four are the core palette; players 5–6 extend with warm mauve and terracotta.

1. `#E88A7F` (coral — Player 1 / primary)
2. `#7FA897` (sage)
3. `#7FA8C4` (sky)
4. `#E8C47F` (sun)
5. `#B78FC7` (muted mauve)
6. `#D89E6F` (warm terracotta)

### Semantic colors

| State | Color | Notes |
|-------|-------|-------|
| Success / correct | `--sage` | Correct answer glows sage with a warm halo. Paired with a ★ glyph (color is never the sole signal). |
| Error / incorrect | `--error` | 2 px shake + warm-red border. × glyph pairs with color. No red flash. |
| Warning | `--sun` | Sun yellow at full saturation. |
| Info | `--sky` | Sky blue for neutral information. |

### Dark mode

Dark mode is explicitly **not the primary mode** for Soft Parlor. The aesthetic anchor ("cozy family board game") is a light-primary register — a cream paper feels warmer than a dim room. A dark mode is provided for late-night play but defers to the light system:

| Token | Dark-mode Hex |
|-------|----------------|
| `--bg` | `#1C1A14` (dark roasted coffee — warm, not blue) |
| `--surface` | `#2A2720` |
| `--surface-2` | `#342F26` |
| `--text` | `#FAF6EC` (inverted cream) |
| `--muted` | `#9A9484` |
| `--hairline` | `#3E382D` |
| Accents stay at the light-mode saturation, unchanged. |

Implementation (revised 2026-05-27, #50):

- **Auto-detect**: dark tokens apply automatically when the device's OS reports `prefers-color-scheme: dark`, via a `@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) }` block in `styles.css`. This is the new default for the player + admin UIs (personal devices — guests / host's phone).
- **TV escape**: `dashboard.html` ships with `<html data-theme="light">` so the TV stays Soft Parlor cream regardless of the host's OS preference. The across-the-room cream aesthetic stays the canonical brand image, which is what the prior "never gate on prefers-color-scheme" rule was protecting.
- **Manual force-dark**: `[data-theme="dark"]` on `<html>` or `<body>` still works and beats both default light and the media query — use it if a flow needs forced dark.
- Accents don't desaturate — keeping their warmth is the whole point.

Earlier policy (before 2026-05-27) was: "never gated on `prefers-color-scheme` — a guest whose OS is dark should still see Soft Parlor cream". That was revised because (a) personal devices reasonably defer to OS preference and (b) the TV view is now explicitly locked light via `data-theme="light"`, so the cream-across-the-room aesthetic is preserved by mechanism rather than by blanket policy.

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

**Base unit:** 4 px. **Density:** comfortable on TV (generous), comfortable on admin (not compressed like Broadcast's admin), spacious on mobile (thumb targets 44 px minimum).

## Layout

- **Approach:** Hybrid, with more breathing room than Broadcast Living Room.
  - **TV host** → centered editorial composition. Question takes the upper third with large margins. 3-column answer row, stacked on phones.
  - **Player mobile** → grid-disciplined, thumb-zone first. **Stacked vertical** answer list (3 tiles).
  - **Admin console** → softer dashboard grid. Sections are card-shaped with shadows, not flat.
- **Max content width:** 1200 px on desktop/admin. TV is always full-bleed.
- **Grid:** single-column on mobile, 12-column on admin with 4-unit gutter, 3-column answer row on TV.
- **Border radius scale:**
  - `--r-sm`: 6 px (chips, badges)
  - `--r-md`: 10 px (buttons, answer tiles, inputs)
  - `--r-lg`: 12 px (major containers, cards)
  - `--r-xl`: 14 px (modals, TV frame inner corners)
  - `--r-full`: 9999 px (player-dot circles only)
  - **Never** 20 px+ — Soft Parlor is rounded but never bubbly.

## Motion

Warm, soft, welcoming. Easings are longer than Broadcast Living Room, transforms are gentler, there's no rim-light glow flicker.

| Event | Behavior | Duration | Easing |
|-------|----------|----------|--------|
| Question enters | Fade + 10 px y-slide | 240 ms | ease-out |
| Answer reveal (correct) | Sage halo glow + scale 0.96 → 1.00 | 320 ms | ease-out |
| Answer reveal (incorrect) | 2 px horizontal shake (3 cycles) + border flash | 300 ms | ease-in-out |
| Score tick | Digit roll (mono earns it) | 400 ms | ease-out |
| Timer last 5 s | Soft coral pulse at 1 Hz (opacity 1.0 → 0.7) | — | ease-in-out |
| Round transition | Fade through cream + warm-gradient sweep | 500 ms | ease-in-out |
| Button hover | 1 px y-lift + coral-tint shadow | 120 ms | ease-out |
| Podium reveal | Single warm gradient widens from champion's block | 800 ms | ease-out |

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
- **Bottom rail:** centered player strip with colored dot + name (DM Sans 600 warm ink) + score (mono coral tabular). Leader has a ★ glyph. No rail separator — the page's warm breathing does the work.

### Player Mobile

- **Topbar:** Round counter (mono 10 px eyebrow) + timer pill (mono 16 px coral in a white pill).
- **Label:** "Pick one" eyebrow in mono coral.
- **Question:** display 700 22 px, warm ink.
- **Answers:** Stacked vertically. Each 56 px min tap target. White bg, soft cream border, colored letter pill on the left, DM Sans 500 text. Picked: coral outlined + coral-tint bg. Correct: sage outlined + sage-tint bg + ★. Wrong pick: warm-red outlined + ×.
- **Footer:** Player name (DM Sans 600 warm ink) + current score (mono coral tabular).

### Podium / Finale

- **Eyebrow:** "Final Results · [Pack] · [N] Runden" in mono muted olive.
- **Title:** **"Champion"** (singular) in display 900, warm coral, with a soft warm drop shadow. Champion name below in display 700, warm ink.
- **Podium:** 3-column grid, 2nd–1st–3rd. No trophy emojis — numbers speak. Two surface treatments:
  - **Player phone ("Podium Reborn", approved 2026-06-09):** bolder rising blocks with a warm *tonal* gradient fill (light tint → base hue, same color — 1st coral, 2nd sage, 3rd sky), white numerals centered, 14 px rounded top + 6 px foot, soft lift shadow. Top edge keeps the medal accent: 1st sun-yellow, 2nd silver, 3rd bronze. A warm radial halo (sun + coral) rises from the champion's foot. Tonal gradients here are a deliberate, user-approved exception to the general no-gradient rule; they stay single-hue and muted.
  - **Admin / TV (host screen):** cream "shelf" planks (no gradient) — 1st 200 px with a 3 px sun-yellow top border and a soft warm-gradient halo above, 2nd silver top border, 3rd bronze. The coral/silver/bronze numeral is the medal.
- **Background effect:** Single soft warm radial glow behind the champion (coral + sun). No confetti.

### Admin / Host Console

- **Header:** display 800 wordmark with coral `ify`. Mono "LIVE" badge right-aligned, with a sage dot (calm, not urgent).
- **Section title:** display 600 22 px.
- **Section card:** white surface, 1 px soft-cream border, 10 px radius, soft drop shadow.
- **Card (current question):** white surface, mono eyebrow in coral, display 700 18 px question, warm ink.
- **Timer bar:** 4 px coral fill over cream hairline track.
- **Leaderboard:** mono 10 px uppercase muted headers, each row: mono rank (coral for 1st, silver/bronze for 2/3), DM Sans 500 name, mono streak (sun), mono coral tabular score.
- **Primary CTA:** solid coral button, DM Sans 700 in white, soft coral shadow. Sentence-case, not ALL CAPS.
- **Secondary CTA:** white bg, cream hairline border, warm-ink text.

### Welcome / Setup (host) — "Categories-forward" (approved 2026-06-09)

- **Featured pack ("Soft Spotlight"):** white card with a coral hairline border. SVG line trophy in a sun-tinted rounded badge, mono "Empfohlen · Neu" eyebrow in coral, display 700 title, muted desc, and a round coral selection indicator on the right (outline → filled coral check when the pack is selected). The whole card toggles the pack; Start is separate.
- **Category picker:** two-column grid of selectable tiles. Each tile: an **SVG line icon** (never emoji) in a theme-tinted rounded disc, display 700 name, mono question count. Icon disc tint + icon stroke cycle the four accents by theme (coral / sage / sky / sun; "Gemischt" stays neutral clay). Default = white + cream hairline; hover = lifts; selected = coral border + coral-wash + corner check.
- **Icons are keyed by `data-theme`** so both languages share one glyph set (globe, leaf, clapperboard, music note, flag, flask, scroll, fork, bulb, dice, trophy). Tints are flat (no gradients).

### Icon system — "Rounded Duotone" (approved 2026-06-10, #212)

- **No emoji as a standalone UI icon.** Emoji render differently per OS, don't inherit the Soft Parlor palette, and clash with the SVG hero. App-wide the category/theme icons are SVG line glyphs from one shared set (`www/js/icons.js`, global `window.QuizifyIcons`), consumed by both admin and player JS.
- **Style = "Rounded Duotone" (Option 2).** A 2px round-capped, round-joined glyph (`stroke:currentColor; fill:none`) over a **soft accent-tinted rounded backing disc**. CSS class `.qz-icon` owns the disc; `.qz-icon--{mix,coral,sage,sky,sun}` set the flat tint + stroke color (mirrors the hero's `.hct-icon` / `.hpt-*`). This is the warmer, cozier "family board game" register — chosen over Option 1 (thin hairline) and Option 3 (geometric bold) from the #212 shotgun.
- **Tints cycle the four accents by theme** (coral / sage / sky / sun; `mixed`/`worldcup` use clay/sun). Flat — **no gradients**.
- **Where applied (P1):** welcome hero category tiles, detail-view pack cards (`#category-chips .pack-card-icon`), and theme filter tabs. i18n label strings hold **text only** — the icon is rendered separately, never embedded in the translated string.

## Accessibility

- All text meets WCAG AA contrast on its paired background:
  - `--text` on `--bg`: 12.2 : 1 (AAA)
  - `--muted` on `--bg`: 4.6 : 1 (AA)
  - `--coral` on `--bg`: 3.1 : 1 (UI large text only — body text must use `--text`)
  - White on `--coral`: 3.4 : 1 (AA large — OK for primary CTA)
- Focus states: 2 px coral outline, offset 2 px. Never `outline: none` without a replacement.
- Motion respects `prefers-reduced-motion: reduce` — transitions collapse to 0.
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
- **Nabu Casa Remote UI** already requires HA login to reach *any* integration
  HTTP path, so it satisfies this requirement out of the box.
- A **self-hosted reverse proxy** (nginx / Traefik / Cloudflare Tunnel) that
  forwards `/quizify/*` or `/api/quizify/*` to HA **without** an auth layer in
  front would expose the write/disclosure endpoints above to the internet. Do
  not do this. If you proxy, require auth at the proxy (or rely on Nabu Casa).
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
| 2026-04-24 | Coral `#E88A7F` as primary accent | Warm, hospitable, non-gendered, works on both dark hair and light hair avatars. Differentiated from category norms (Jackbox primary = purple, Kahoot = hot pink). |
| 2026-04-24 | Cream `#FAF6EC` as primary background | Explicit break from Broadcast Living Room's dark navy. Soft Parlor is light-primary. Warm paper reads as "home", not "studio". |
| 2026-04-24 | Cabinet Grotesk + DM Sans + JetBrains Mono | Typography stack pivots away from Unbounded (too broadcast). Cabinet Grotesk has the warm-geometric feel Soft Parlor requires. |
| 2026-09-05 | Display face drops to DM Sans; all faces bundled | Cabinet Grotesk's ITF Free Font License forbids redistributing the files, so it cannot ship in an MIT repo, and fetching it from Fontshare leaked every guest's IP and blocked the first paint (#737, #738). DM Sans at 700-900 takes the display role. |
| 2026-04-24 | No confetti on finale (carried forward, 3rd time confirmed) | Restraint has been the consistent preference across three directions. |
| 2026-06-09 | Welcome/setup hero → "Categories-forward" (SVG-icon tinted category tiles + F1 featured spotlight) | Design-shotgun explored category-pill directions, then full welcome-screen directions; user picked A (categories-forward) with SVG icons (no emoji) and the F1 "Soft Spotlight" featured card. Category icons are now SVG line glyphs keyed by theme, in per-theme accent-tinted discs. |
| 2026-06-10 | App-wide icon system → **"Rounded Duotone" SVG line icons** (#212) | Follow-up to #211's hero icons: the rest of the app still used emoji as UI icons (inconsistent per-OS, off-palette, clash with the SVG hero). A shared icon helper (`www/js/icons.js`, `window.QuizifyIcons`) now serves the theme glyph set to both admin and player JS. Style = Option 2 from the #212 shotgun: 2px rounded strokes over a soft accent-tinted backing disc (warmer than Option 1 hairline, softer than Option 3 geometric-bold). P1 surfaces (pack cards + theme tabs) shipped first; emoji pulled out of `theme.*` i18n strings (text-only). P2 (presets/awards) + P3 (reveal-feedback strings) descoped to follow-ups. |
| 2026-06-09 | Player-phone podium → "Podium Reborn" (gradient rising blocks) | Design-shotgun explored 4 phone-finale directions; user picked B over the shipped cream-shelf ("Family Trophy Shelf"). Player phone now uses rising blocks with muted single-hue tonal gradient fills + a halo rising from the champion, for a more celebratory finale payoff. Admin/TV keeps the cream shelf. Tonal gradients are a scoped, user-approved exception to the no-gradient rule. |
| 2026-06-11 | Security model documented; constant-time admin-token compare (#259) | Code review (#252/#259) confirmed the LAN-first exposure posture is intentional: player endpoints stay un-authed (players have no HA login), remote exposure must be fronted by HA auth (Nabu Casa or a proxy auth layer). Added a "Security model" section with an endpoint exposure map + remote-exposure rule. Hardened the admin session-token compare with `hmac.compare_digest` and documented the proxy-aware rate-limit IP resolution. No player-facing behaviour changed. |
| 2026-04-28 | Admin-as-player trust model (Beatify pattern) | Server trusts `is_admin: true` in the join message at face value. No more cryptographic token threading through player joins. The persisted admin token is still validated for the pure admin-dashboard WebSocket connect (`?role=admin&token=...`), but player joins are simpler. **Trade-off:** a malicious LAN client can spoof admin by sending `is_admin: true`. Mitigations: (a) the user's home LAN is generally trusted; (b) Nabu Casa already requires HA auth to reach the integration; (c) "first admin claims it" still applies — only one admin slot per game. Adopting the same pattern as Beatify (which has shipped this without issues for years) closed 8 betas worth of admin-as-player lockout bugs. v1.1.2. |

# Changelog

All notable changes to Quizify are documented here. This project follows
[Semantic Versioning](https://semver.org/).

## [1.1.0-beta.3] — 2026-04-24

Critical hotfix. **Please upgrade from beta.1 or beta.2 immediately** —
the player flow was broken in both and this release restores it.

### 🚨 Critical regression fix

- **Player flow was fully broken in beta.1 and beta.2.** The redesign
  accidentally dropped the `utils.js` `<script>` include from
  `player.html`, which caused `player-utils.js` to fail with
  `TypeError: utils.escapeHtml is not a function` the moment the server
  broadcast a `player_joined` event. Symptom: the Join button stayed on
  "JOINING..." forever and no players could enter the lobby. This exact
  same drop happened once before (see commits `86025d1`, `4fe6712`) and
  is now marked with a `DO NOT REMOVE` comment so it doesn't happen a
  third time.

### 🐛 Fixes

- **Admin-join modal was rendering in light-mode palette** (cream
  background, dark navy text, peach "Starten & Beitreten" button) because
  `.modal-content` CSS referenced `--color-bg-white` and
  `--color-text-primary`, which the redesign repurposed for light-mode
  support. The modal styles now explicitly use the dark surface tokens
  (`--color-dark-surface-hover` navy background, `--color-text-white`
  parchment text, hairline border, box-shadow) so the modal matches the
  rest of the dark-mode UI regardless of how the light/dark tokens are
  wired.
- `.confirm-modal-title` / `.confirm-modal-message` also fixed — same
  root cause.
- Modal input field now uses studio navy background with parchment text
  and broadcast-gold focus ring (was light gray bg, dark text, old
  purple focus shadow).

### 🧹 Internal

- Cache-busters bumped to `?v=1.1.0-beta.3` and service worker
  `CACHE_VERSION` bumped to `quizify-v1.1.0-beta.3`.

---

## [1.1.0-beta.2] — 2026-04-24

Follow-up on beta.1 after auditing the live deployment. Six color-drift
bugs where hardcoded JavaScript bypassed the design-system tokens.

### 🐛 Fixes — color drift missed in beta.1

- **Connection status dot** (top-right of admin + player headers) rendered
  teal-green `#00b894`. Fixed in `admin.js` and `player-utils.js` to use
  broadcast gold (connected), dim gold (reconnecting), warm red (disconnected),
  each with matching rim-light glow.
- **Answer-distribution chart** showed the correct-answer bar in teal-green
  instead of broadcast gold. Fixed in `admin.js`.
- **Residual teal / red / orange** in `styles.css` (`.result-value.is-correct`,
  `.result-value.is-wrong`, `.result-value.is-streak`, `.dist-correct-icon`, etc.)
  all mapped to broadcast palette tokens.
- **Question-pack update banner** rendered in a SaaS-blue scheme
  (`#1e3a5f` background with `#3b82f6` border, `#93c5fd` heading). Rewritten
  as a navy surface card with a 3px gold left-accent and an Unbounded heading
  in broadcast gold.
- **Red validation borders** on name inputs (`#ff4757` fire-engine red) now
  use warm red `#D65858` from the broadcast palette — matches the rest of
  the error register.
- **Inline `color:#a4a4b8`** on the admin setup hint text now uses
  `var(--color-text-neon-muted)` so it follows the token.

### 🧹 Internal

- Cache-busters bumped to `?v=1.1.0-beta.2` so browsers pick up the fixed
  CSS / JS on next load.
- Service worker `CACHE_VERSION` bumped to `quizify-v1.1.0-beta.2`.

---

## [1.1.0-beta.1] — 2026-04-24

### A note on this release

This is the first beta of the 1.1 line. It bundles a substantial visual
redesign, two sizeable new features, and the security / remote-access fixes
that landed since `1.0.45`. HACS users need to opt into beta releases to
receive it.

### ⭐ Visual — Broadcast Living Room redesign

A complete visual rework. The aesthetic direction is now **"Broadcast Living Room"**:
the posture of a televised game show delivered at home scale. Every surface
reads like a game show broadcasting on your TV, not a website about a quiz.

- **New palette** — deep studio navy `#0B1739` as background, **broadcast gold** `#F4C430`
  as the single accent, warm parchment `#F4EBCF` for text. No more pink / cyan / purple.
- **New typography** — Unbounded (display), Instrument Sans (body/UI), JetBrains Mono
  (scores / timers / numerics). Outfit and Inter are gone.
- **Three answer columns on TV** — dashboard answer grid now correctly renders 3 tiles
  (A / B / C) side-by-side instead of a 2×2 grid with one empty slot.
- **No more confetti on finale** — the dramatic pause IS the effect. Champion reveal
  uses a single spotlight gradient and gold digit-roll.
- **Broadcast-gold primary CTA** — all `btn-primary` elements now render as solid gold
  buttons with studio-navy text and a rim-light glow, in uppercase Unbounded.
- **Subtle CRT-scanline texture** on dashboard and launcher backgrounds at 3% opacity.
- New [DESIGN.md](./DESIGN.md) is the source of truth for future UI work.

### ✨ Features

- **Question pack versioning with update check** ([#51](https://github.com/mholzi/quizify/pull/51))
  — question packs now carry a version, and the integration checks for updates.
- **Multi-category mode** ([#21](https://github.com/mholzi/quizify/pull/21)) — hosts can
  select multiple categories for a single game, not just one.
- **Answer distribution chart on reveal** ([#66](https://github.com/mholzi/quizify/pull/66),
  [#32](https://github.com/mholzi/quizify/pull/32),
  [#139](https://github.com/mholzi/quizify/pull/139)) — reveal screen now shows how
  players distributed across the three answer options, with the correct answer highlighted.

### 🔒 Security

- **Closed WebSocket admin privilege-escalation vulnerabilities**
  ([#140](https://github.com/mholzi/quizify/pull/140),
  [#142](https://github.com/mholzi/quizify/pull/142)) — two related issues where a
  player could upgrade their session to admin capabilities have been fixed.

### 🐛 Fixes — Home Assistant Cloud (Nabu Casa) remote UI

A cluster of issues that prevented Quizify from working correctly when accessed
through Home Assistant Cloud's remote UI have all been resolved:

- Admin self-join no longer stuck on "Joining..." when accessed via Nabu Casa.
- Blank player screen on admin self-join via Nabu Casa.
- `requires_auth=False` now set on all views (fixes 401 responses through remote UI).
- `LauncherView` specifically now also has `requires_auth=False` (was still 401'ing).

### 🧹 Internal

- Design system tokens (`:root`) fully rewritten in `styles.css`.
- Confetti library removed from `dashboard.html` and `player.html`; all
  `confetti()` calls in `player-end.js` and `player-reveal.js` removed.
- Share-image canvas generator (`player-end.js`) rewritten to broadcast palette —
  no more purple→pink→cyan gradient in shared score images.
- Cache-busters on CSS / JS imports bumped to `?v=1.1.0-beta.1`.
- Service worker `CACHE_VERSION` bumped to `quizify-v1.1.0-beta.1`.

### ⚠️ Known limitations (beta)

- The full game-in-progress styling (answer tiles, timer glow, leaderboard,
  podium) needs real-world testing inside a Home Assistant session. Standalone
  HTML preview confirms typography, color, and first-render layout.
- No breaking API or config-flow changes, so existing installations should
  pick this up cleanly on upgrade. If you hit issues, please file at
  [github.com/mholzi/quizify/issues](https://github.com/mholzi/quizify/issues).

---

Earlier versions were released without a maintained changelog. Full git
history is available on [GitHub](https://github.com/mholzi/quizify/commits/main).

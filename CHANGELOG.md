# Changelog

All notable changes to Quizify are documented here. This project follows
[Semantic Versioning](https://semver.org/).

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

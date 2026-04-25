# Changelog

All notable changes to Quizify are documented here. This project follows
[Semantic Versioning](https://semver.org/).

## [1.1.0-beta.5] — 2026-04-25

Soft Parlor finishing pass. Beta.4 shipped with the redesign in `styles.css`
and HTML, but ~25 hardcoded color values in JS and ~20 `rgba(244, 196, 48, …)`
glows in CSS were never migrated from Broadcast Living Room. With cream-paper
backgrounds and broadcast-gold rim-lights side by side, the result was a
half-migrated UI. This release closes that drift.

### 🐛 Visual drift cleanup

- **Connection status dot** (`admin.js:800`, `player-utils.js:168`) was still
  Broadcast gold `#F4C430`. Now sage `#7FA897` for connected, sun for
  reconnecting, brick `#D66A6A` for disconnected — Soft Parlor palette.
- **Distribution chart correct-answer bar** (`admin.js:497`) was still
  Broadcast gold. Now sage. Wrong-answer bars use the cream hairline.
- **Question-pack update banner** (`admin.js:850-880`) was a navy + gold
  card. Rewritten as a white surface with coral left-accent, warm-ink
  text, and a soft drop shadow on the cream-ground page.
- **Score-share image canvas** (`player-end.js:313-345`) was still painting
  studio navy + parchment + broadcast gold. Rewritten as cream paper +
  warm ink + coral, matching what's actually on screen so shared images
  reflect the live UI.
- **CSS broadcast-gold glows** — ~20 `rgba(244, 196, 48, X)` in
  `styles.css` (focus rings, badge backgrounds, gradient halos) all
  swapped to coral `rgba(232, 138, 127, X)`.
- **`<meta name="theme-color">`** in `admin.html` was still `#1a1a2e`
  (the original dark surface color). Now `#FAF6EC` cream so iOS / Chrome
  mobile status bar tints to match.
- **Stale Broadcast comment** in `player.html` updated to Soft Parlor
  language.

### 🧹 Internal

- Cache-busters bumped to `?v=1.1.0-beta.5`.
- Service worker `CACHE_VERSION` to `quizify-v1.1.0-beta.5`.

### ⚠️ Upgrade notes

- This release is purely visual cleanup — no logic changes from beta.4.
- All security fixes from beta.4 (admin token persistence, name-collision
  reconnect, etc.) carry forward unchanged.
- Hard-reload tabs after upgrade.

---

## [1.1.0-beta.4] — 2026-04-24

Substantial release: a full logical review surfaced ~23 bugs in the state
machine / auth / reconnect layers. All CRITICAL and HIGH-severity findings
are fixed in this release. Plus the design system pivots from **Broadcast
Living Room** to **Soft Parlor** after a 5-theme shotgun comparison.

### 🔒 Security (CRITICAL)

- **LAN admin takeover after HA restart is closed.** Admin session tokens
  now persist to HA storage (`Store(..., "quizify_admin_token")`) and
  survive restarts. The "first admin wins" rule only fires on fresh
  installs where no token has ever been issued. On all subsequent restarts
  the legitimate admin's stored token validates immediately, and any other
  LAN client trying `?role=admin` is rejected. A bootstrap warning is
  logged prominently on first-claim so the host knows if someone else
  beat them to it.
- **Dashboard disconnect no longer races the admin timeout.** The
  `_handle_disconnect` OR clause that let any non-player disconnect
  trigger `schedule_admin_timeout()` has been removed. Only real admin
  connection closures now start the grace timer.

### 🔒 Security (HIGH)

- **Name-collision impersonation during gameplay closed.** A disconnected
  player's slot can now ONLY be reclaimed via the token-based
  `reconnect` message during gameplay phases. Reconnection by re-typing
  the name is still allowed in LOBBY (where scores are zero and
  impersonation is cosmetic). Previously, during an active round, a
  malicious LAN user could type a disconnected player's name and inherit
  their score.
- **Player session tokens now have a 24-hour TTL** and are wiped entirely
  on `reset_to_lobby`, preventing cross-game token reuse.
- **Name validation now NFKC-normalizes and strips all control / format
  characters** (U+2000–U+206F). Zero-width joiners, RTL overrides, and
  other invisible-character spoofing vectors are removed before the name
  is registered.

### 🎮 Gameplay correctness (HIGH)

- **Round-summary broadcast no longer fires twice** on the timer-expiry /
  last-submit race. The state machine's `_fire_broadcast("round_evaluated")`
  is now the single authoritative path. Players no longer see the reveal
  flash or leaderboard re-animate.
- **Timer broadcast is now authoritative per-player.** Each player's
  `QuestionTimer` is broadcast to them directly (every 500 ms), so
  time-boost and freeze power-ups are now visible on the affected
  player's UI. Before, the broadcast used a single shared
  `asyncio.sleep(1.0)` counter that ignored per-player deltas — power-ups
  were silently dead.
- **Admin self-join race fixed.** Clicking "Starten & Beitreten" no
  longer navigates away before the server receives `start_game`. The
  redirect now waits for the server's phase-change broadcast (with a 3 s
  safety fallback) so the game always starts.
- **Admin-as-player authority now wired correctly.** When the admin
  self-joins, `PlayerSession.is_admin` is set to True so the
  `_is_authorized_admin` fallback works. Before, the check was dead code
  and admin controls on the player page silently failed.
- **`reset_to_lobby` now cleans up all pending tasks** — previously,
  `schedule_player_removal` tasks from the previous game could fire
  mid-new-lobby and silently drop players. `conn.cleanup()` is called
  first, then all player tokens wiped.
- **`start_game` now requires at least one connected player.** Prevents
  the phantom-rounds bug where an admin double-click before self-join
  lands starts a 0-player game that runs N rounds of empty countdowns.

### 🎮 Gameplay correctness (MEDIUM)

- **Late joiners no longer prevent `all_submitted()` early-advance.**
  Players who join mid-round are excluded from the all-submitted check
  so an early-arriving player doesn't force the round to run its full
  duration.
- **Mid-round reconnect preserves submitted state.** If a player
  reconnects after already submitting, their client now correctly locks
  the answer buttons (new `lockSubmitted()` API) based on the server's
  leaderboard state, instead of resetting to a "tap an answer" UI that
  then errors with `ALREADY_SUBMITTED`.
- **`broadcast_to_admins` now excludes admin-as-player by `is_admin`
  flag, not stale WebSocket identity.** Previously, after an admin-
  as-player reconnect, a stale `ws` reference let the admin see the
  correct-answer spoiler message meant for pure-admin connections.

### 🐛 UX (MEDIUM)

- **Admin buttons disable on click** (`nextQuestionBtn`, `continueBtn`,
  `adminJoinBtn`) to prevent double-advance "Cannot advance now" toasts
  from racing the state transition. Re-enabled on next phase message or
  after a 1.5 s safety timeout.
- **Server error codes are translated to user-friendly German** strings
  on both admin and player clients. Raw codes like
  `"INVALID_ACTION Admin only"` no longer leak as opaque toast text.
- **Malformed WebSocket JSON now returns a structured error** to the
  client instead of being silently swallowed. Players get an explicit
  "Malformed message" toast if their client sends garbage; the submit
  isn't lost in silence.
- **Rate-limited WebSocket messages now get an explicit error toast**
  and the first message counts toward the window, closing the
  rapid-first-message DoS vector.

### ⭐ Visual — Soft Parlor redesign

Complete visual pivot from Broadcast Living Room to **Soft Parlor**.

- **New palette** — warm cream paper `#FAF6EC` background, coral
  `#E88A7F` primary accent, sage `#7FA897` / sky `#7FA8C4` / sun `#E8C47F`
  secondary accents at equal muted saturation. Warm ink `#2A2820` text
  (not pure black). Dark mode defers to the light-primary design.
- **New typography** — Cabinet Grotesk (display, via Fontshare) +
  DM Sans (body) + JetBrains Mono (numeric). Unbounded and Instrument
  Sans are out.
- **Decoration is soft glows, not CRT scanlines.** Two radial gradients
  on the page background (coral top-right, sky bottom-left) at 6–10 %
  opacity. All CRT / scanline / phosphor decoration from Broadcast
  Living Room is gone.
- **Soft drop shadows and rounded 10–14 px corners** — Soft Parlor
  rejects bubble-rounded (20 px+) as hard as Broadcast Living Room did.
- **CTAs are sentence-case, not ALL CAPS.** Soft Parlor uses quieter
  typography; the warmth is in the palette.
- **No more confetti on finale** (carried forward — third time this
  preference has been confirmed across three design directions).
- New [DESIGN.md](./DESIGN.md) with the full Soft Parlor system.

### 🧹 Internal

- Cache-busters bumped to `?v=1.1.0-beta.4`, service worker
  `CACHE_VERSION` to `quizify-v1.1.0-beta.4`.
- `ConnectionManager` gained `async_load_admin_token()` / persistent
  HA storage for admin token, `clear_admin_token()`, `clear_all_player_tokens()`.
- `QuizifyGameState.get_player_timer(name)` exposed for the per-player
  timer broadcast.
- `PlayerRegistry._sanitize_name()` helper centralizes name validation.

### ⚠️ Upgrade notes

- **The first admin connection after installing beta.4 will "bootstrap"
  the new persisted token**, logged as `ADMIN BOOTSTRAP: granting admin
  to first connection`. From that point forward, only clients with the
  stored token can claim admin, even after HA restarts. If you see a
  bootstrap log entry and it wasn't you, someone on your LAN beat you
  to it — remove the integration and reinstall to reset.
- **Beta.1 through beta.3 users:** your stored admin token in the
  browser's sessionStorage is still valid; reconnect works seamlessly.
- Hard-reload admin / player tabs after upgrade so the service worker
  picks up the new bundle.

---

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

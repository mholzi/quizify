# Changelog

All notable changes to Quizify are documented here. This project follows
[Semantic Versioning](https://semver.org/).

## [1.1.9] — 2026-05-25

Solo play + an i18n sweep that cleans up every hardcoded German string
that was leaking into English UI (and a handful of hardcoded English
strings that never reached German users). Plus a real cache-bust on the
translation JSON so future i18n changes actually surface in browsers.

### Added

- **Solo play** — a single player can now host and play a full round.
  `MIN_PLAYERS` is 1 (was 2); comparative end-of-game awards still
  require at least two players via the new `MIN_PLAYERS_FOR_AWARDS`
  constant, so solo runs just show the personal stats card.
- Lobby now hides the "Ready at N players · still need X more"
  countdown line once the threshold is met — "still need 0 more" was
  noise once the Start button appeared.

### Fixed

- **i18n leaks: every visible string now respects the active language.**
  The admin hero subtitle, the three preset meta lines
  (`5 rounds · Easy · 20 s` etc.), the lobby `game-settings-summary`,
  the lobby "Waiting for players…" placeholder, and the player lobby
  difficulty pill no longer show German when the UI is English (or
  vice-versa). All static HTML defaults are now English (the i18n
  source language); `de.json` ships every translation. Touched files:
  `admin.html`, `player.html`, `dashboard.html`, `launcher.html`,
  `analytics.html`, plus the JS modules that build the affected lines.
- **TV dashboard now uses i18n at all.** `dashboard.html` imported
  `i18n.js` but never called it — every label was baked in German.
  "Frage X / Y", "Pkt.", "Wusstest du?", "Rangliste", "Ergebnis", and
  "Warte auf Spielstart…" are now translated.
- **Pack-update banner, streak toasts, timer aria-labels, analytics
  empty states, "Joining…" button, and launcher popup-blocked hint**
  are all wired through `t()` instead of hardcoded strings. German
  users now see those in German.
- `sw-update.js` fallback strings flipped from German to English —
  consistent with English being the i18n source language.
- `setup.back` value capitalized: `"back"` → `"Back"` (was rendering
  lowercase, looked like a raw key).
- Version badges in `admin.html` / `player.html` had been stuck at
  `v1.1.4` since 1.1.5 because `bump_version.sh` only finds the
  immediately previous version. They're now back in sync.

### Internal

- `i18n.js` now cache-busts `en.json` / `de.json` fetches using the
  same `?v=<version>` query it reads from its own `<script>` tag.
  Before: every release shipped new translation keys but browsers
  served stale JSON forever. Now: bumping the integration version
  invalidates the translation cache too.
- New i18n keys (24 added, parity preserved): `setup.preset.fastMeta` /
  `classicMeta` / `marathonMeta`, `admin.packUpdateTitle` /
  `packUpdateBody`, `game.streakToast3/5/7`, `game.timerRemainingAria`
  / `timerUpAria`, `dashboard.didYouKnow` / `result` /
  `questionCounter` / `pointsShort` / `awards`, `analytics.emptyData`
  / `emptyPlayers` / `emptyGames`, `launcher.clickToFocus` /
  `focusQuizify` / `focusedTab` / `popupBlocked` / `allowPopups`,
  `join.joining`.

## [1.1.8] — 2026-05-24

Fix the "Spiel startet nicht zuverlässig" bug. When the admin clicked
Start, every other player's phone briefly showed the question then
switched to "Lost connection to the host" for ~1 second before the
question came back. Root cause: the admin's intentional redirect from
`/quizify/admin` to `/quizify/player` (so the admin can answer along)
closes the admin WebSocket. The server used to react instantly by
pausing the whole game with reason `admin_disconnected` and pushing
that to every other client — what users saw as "not connected".

### Fixed

- **Admin-start no longer flashes "Lost connection to the host" on
  every other player's screen.** The deferred-pause path now gives the
  admin's redirect a 4-second grace window to reconnect via the player
  WebSocket. Normal redirects complete in ~1 second, so the pause never
  fires and the question round starts cleanly. Real admin disconnects
  (closed tab, lost wifi) still pause the game after the 4-second
  grace, just as before — only the spurious flash on every Start is
  suppressed.

### Added

- `tests/test_admin_redirect_pause.py` — 6 regression cases pinning the
  fix: disconnect-during-question doesn't pause inline, reconnect within
  grace cancels the pause, no-reconnect still pauses after grace,
  non-admin disconnect is a no-op, lobby disconnect is a no-op, and
  rapid disconnect/reconnect/disconnect doesn't stack tasks.

## [1.1.7] — 2026-05-24

Cache-buster system rewrite. Bumping the integration version now
propagates everywhere — HTML asset URLs, service-worker cache name,
`/api/quizify/status` payload, and a new `<meta name="quizify-version">`
tag — without ever touching another file by hand. Fixes the drift that
let `dashboard.html` ship referencing `?v=1.1.5` while everything else
was at `1.1.6`, and let `/api/quizify/status` keep reporting `1.0.13`
months after the real version moved on.

### Added

- `<meta name="quizify-version" content="...">` in every HTML head, so
  in-browser tooling and support requests can read the live version
  without parsing JS.
- `tests/test_version_cachebuster.py` — 11 cases pinning the new pipeline:
  manifest read, `AppContext.version` default-factory wiring, template
  substitution, drift guard (no `{{VERSION}}` ever reaches the browser),
  HTML no-cache headers, service-worker MIME + headers, and live version
  in `/api/quizify/status`.

### Changed

- `manifest.json` is now the single source of truth for the cache-buster
  version. `server/views.py::_serve_html` substitutes `{{VERSION}}` in
  the response body at serve time; templates use `?v={{VERSION}}` on
  every asset reference.
- `sw.js` now declares `CACHE_VERSION = 'quizify-v{{VERSION}}'` and is
  served by a new Python view (`sw_view`) ahead of the static handler,
  so its template gets substituted too. Served as
  `application/javascript` with `no-cache` headers so browsers always
  revalidate (and the SW that controls every other asset cache stays in
  step with the deployed version).
- `AppContext` gained a `version` field, populated at startup via
  `read_manifest_version()`. Existing call sites stay unchanged thanks
  to a `default_factory`.

### Fixed

- `/api/quizify/status` now reports the live integration version from
  `manifest.json` instead of the hardcoded `1.0.13` that had drifted
  since v1.0.

## [1.1.4] — 2026-04-28

QA-driven cleanup: 18 findings from a live admin/player audit, grouped
into one release. The headlines are a critical lockout fix and an
end-to-end i18n sweep — the rest is polish.

### 🔴 Fixed (critical)

- **Admin lockout when joining a game already in FINALE.** When the
  server sent its initial state snapshot to a re-joining admin, the
  leaderboard inside it dropped the `is_admin` flag (only the dedicated
  `serialize_leaderboard` had it; `state.get_leaderboard` did not).
  The reveal client gated the *Start New Game* button on
  `currentPlayer.is_admin`, so it stayed hidden — admin had no way to
  start a fresh game and the only recovery was the
  `quizify.reset_admin_session` HA service. Two-pronged fix:
  - `state.py:get_leaderboard()` now includes `is_admin`.
  - `player-end.js:updateEndView` also trusts `state.isAdmin` (set
    from the URL `?admin=true` flag and confirmed by the server's
    `joined` reply), so either source unlocks the button.

- **Admin sees setup screen even when server is in FINALE.** Clicking
  *Spiel starten* would silently fail (server rejected with
  `INVALID_ACTION`) but the optimistic UI still flipped to lobby.
  `admin.js:handleGameState` now lands the admin directly on the
  finale view when the server reports phase=`FINALE` on first state,
  so the *Neues Spiel starten* button is reachable in one click.

### 🟠 Fixed (i18n / data leaks)

- **`<html lang>` now follows the active translation language.**
  Previously stuck on `lang="en"` regardless of UI language; broke
  screen readers and translation extensions.
- **Settings summary chip is no longer stale on first paint.** Was
  showing `Mittel · 10 Runden` without the timer; now renders the full
  summary (incl. `30s`) on init via an explicit `updateSettingsSummary()`
  call after i18n boot.
- **Whole finale screen is now translatable.** `Game Over!`,
  `Thanks for playing Quizify`, `YOUR RESULT`, `0 points`, the stat
  labels (`Best Streak`, `Rounds Played`, `Power-Ups Used`),
  `Full Rankings`, the highlights row (`Top Score`, `Best Streak`,
  `Most Correct` + their unit suffixes), and the
  `Wait for the host…` hint all flow through `data-i18n` keys now,
  and both `en.json` and `de.json` have the full set (203 keys at
  parity).

### 🟡 Fixed (mixed-language drift)

- **Admin setup screen** is now fully consistent in the picked
  language. Was a salad of EN headings (`Category`,
  `Difficulty`, `Rounds`, `Invite Players`) and DE chip text
  (`Mittel`, `Schwer`, `Spieleinstellungen`). Every label now resolves
  through i18n; the bilingual `Gemischt / Mixed` chip is gone.
- **Lobby** translates fully too — `Spieler`, `Warte auf Spieler…`,
  `Als Spieler beitreten` etc. now have proper keys.
- **Player join error banner** ("Name bereits vergeben" + "Enter your
  name to play" on the same screen) is consistent now: the language is
  whatever the game picked, applied uniformly.
- **Page title** (`<title>`) now reflects the actual phase
  (`Quizify — Beitreten` / `…— Lobby` / `…— Frage 3` / `…— Auflösung` /
  `…— Endergebnis`) rather than being stuck on `Quizify - Join Game`
  forever. Helps users with multiple tabs open.
- **Language picker** in admin actually re-translates the visible page
  on click (was previously a no-op for everything except the
  category-chip filter).

### 🟡 Fixed (copy / state)

- **Modal copy is context-aware.** Click *Spiel starten* from the
  setup screen → modal says *Spiel starten* / *Starten & Beitreten*.
  Click *Als Spieler beitreten* mid-lobby → modal says *Beitreten* /
  *Beitreten*. Was previously stuck on "Spiel starten" even when the
  game had already started.
- **Setup-screen hint** updated from the misleading *"Gib deinen Namen
  ein, dann startet das Spiel"* (no name field is on the setup screen)
  to *"Du gibst deinen Namen im nächsten Schritt ein"*.
- **Disconnected players from the previous game are dropped on
  `start_game`.** Previously, `sdfsdf`, `ewrwe` etc. lingered in the
  new game's lobby with zeroed scores. Now `state.start_game()`
  removes any non-connected player before resetting.
- **Player-side language sync.** When the player joins a game whose
  `language` is German (sent in the state snapshot), the player UI
  switches to German automatically — even if the player's browser is
  English. Previously the per-game language only affected questions,
  not the surrounding UI.

### 🟢 Fixed (polish)

- Removed the default `class="theme-dark"` on `<body>` for both
  `admin.html` and `player.html`. Soft Parlor is light-primary; the
  class was stale leftover from the Broadcast Living Room direction
  and could confuse theme-aware widgets. Dark mode (when implemented)
  toggles the class explicitly.
- `i18n.js` now also resolves `data-i18n-aria-label` (used by the
  reaction emoji buttons), and accepts an optional `root` parameter
  for scoped re-translation.
- Reveal/finale dynamic labels (`✅ Richtig`, `⏱️ Keine Antwort`,
  `❌ Falsch`, the bonus chips like *⚡ Speed* / *🔥 Streak* / *⭐
  Difficulty*) now flow through i18n keys instead of hardcoded German.

### 📚 Files touched

`game/state.py`, `game/player_registry.py`, `server/websocket.py`,
`server/serializers.py`, `www/admin.html`, `www/player.html`,
`www/css/styles.css`, `www/js/admin.js`, `www/js/player-core.js`,
`www/js/player-end.js`, `www/js/i18n.js`, `www/i18n/en.json`,
`www/i18n/de.json`, `manifest.json`, `sw.js`, `CHANGELOG.md`.

## [1.1.3] — 2026-04-25

Polish release: a missing-button bug found in live play, plus two
admin-UX requests.

### 🐛 Fixed

- **Next Round button hidden after a finished round.** The reveal
  client renders the host's "Next Round" button only when it can find
  the current player in `data.players` and read `is_admin: true`. The
  `round_summary` broadcast didn't include `players` at all — it sent
  `leaderboard`, which strips `is_admin`. Result: admin saw the reveal
  but no way to advance. Fixed by adding `players` (full player list,
  preserving `is_admin`) and `last_round` to the `round_summary` payload
  via `serialize_round_summary` and `_broadcast_round_summary`.

### ✨ Changed

- **Removed the Rematch button** from the finale screen. Quizify is
  not a rematch-style game — categories and difficulty are picked per
  session, so "Rematch" was just a confusingly-named "Start New Game".
  Now there's only **Start New Game**, which returns to the lobby with
  fresh settings. (`player.html`, `player-end.js`,
  `player-core.js`, `styles.css`.)

### ✨ Added

- **Timer length picker** in admin setup: 20s / 30s / 45s chips next to
  Difficulty / Rounds / Language. The chosen value flows
  `admin.js → start_game → websocket._handle_start_game →
  state.start_game(timer_duration=…) → _round_duration` and overrides
  the difficulty-derived TIME_LIMITS lookup for the whole game. Server
  validates the value (must be int, 5–300s) and falls back to the
  difficulty default if invalid. Default remains 30s.

  i18n keys: `admin.timer` ("Zeit pro Frage" / "Time per Question") and
  `admin.startNewGame` ("Neues Spiel starten" / "Start New Game").

## [1.1.2] — 2026-04-28

Beatify-pattern fix for the admin-as-player flow. Beatify (Quizify's
sister app, see DESIGN.md attribution) has shipped this same flow for
years without the lockout bugs we hit during the v1.1.0 release cycle.
Inspecting Beatify's code revealed Quizify had been over-engineering
the wrong layer.

### 🔧 The architectural fix

Quizify was cryptographically validating an admin token threaded
through the player's join message. Beatify trusts `is_admin: true` in
the join message at face value. Both apps have the same effective
threat model (LAN-trusted, HA-auth-gated tunnel for remote access),
and Beatify's simpler approach has been bulletproof in production
while Quizify's chained-token validation produced 8 betas worth of
lockout bugs.

**Server side** (`websocket.py`):
- `_handle_join` no longer reads `admin_token` from the message body.
  It reads `is_admin: true` and trusts it. `player.is_admin = True`
  is set directly, no token validation, no add to `_admin_connections`.
- `start_game` now uses `_is_authorized_admin(ws, is_admin, game_state)`
  instead of just the WS-level `is_admin`. This accepts both pure-admin
  WS connections AND player WS connections whose player session has
  `is_admin = True`. Without this change, the admin-as-player can't
  start the game because their player WS isn't tagged at the connect
  level. (Was a separate bug from the join-validation issue.)
- `joined` response now includes `is_admin` so the client can confirm.

**Client side** (`player-core.js`):
- Auto-join code path (lines 70-85): drops the `sessionStorage.getItem('quizify_admin_session_token')` lookup and the `joinMsg.admin_token = adminToken` assignment. Just sends `is_admin: true`.
- Manual `handleJoinClick`: same simplification.

The `quizify_admin_session_token` in sessionStorage is still used —
the admin tab's WS connection (`?role=admin&token=...`) still validates
it for the pure admin-dashboard auth path. That path closes the
original `#140`/`#142` LAN-takeover vulnerability.

**Trade-off**, documented in DESIGN.md: a malicious client on the LAN
can now claim admin by sending `is_admin: true` in the join message.
Mitigations: home LAN is trusted; Nabu Casa already gates remote
access; "first admin claims it" still applies. Acceptable given
the practical attack surface (a guest at your trivia party).

### 🎉 Closes 8 betas of bugs

Direct consequences of this change:
- BUG #6 (stale-token-lockout) — no longer reachable, the join path
  doesn't validate tokens. Recovery service is still useful for the
  admin-dashboard WS path but the catastrophic blocked state is gone.
- The bug pattern of "browser sessionStorage and server storage drift"
  is structurally impossible for player joins now.
- The ~50 lines of token-threading client+server code that we kept
  fixing across betas 6, 7, 8, 10 are deleted.

### 🧹 Internal

- Cache-busters and SW `CACHE_VERSION` bumped to `1.1.2`.

---

## [1.1.1] — 2026-04-27

Patch release closing five code-review findings from the gameplay state
machine plus the service-worker caching issue. All bugs were filed
independently from the v1.1.0 release work; verified still relevant on
main before fixing.

### 🐛 Fixes

- **#143 Race condition: double round evaluation between timer and submit_answer.** `submit_answer` now routes through the guarded `evaluate_round()` instead of calling `_do_evaluate_round()` directly. Closes the window where the last-submit and timer-expiry paths could both transition the phase. The user-visible double-broadcast was already neutralized in 1.1.0 (centralized via `_fire_broadcast`), but the underlying state race is now also closed. Single-line fix.
- **#144 `_do_evaluate_round` may return None on error path.** The `# type: ignore[return-value]` was masking a silent crash potential. Now raises `RuntimeError` instead, so the invariant violation is visible in logs. State machine bug, not user-visible bug, but defensive-correctness matters.
- **#145 Double `validate_answer` + `current_answer or -1` scoring bug.** Two compounding bugs: `validate_answer` was called once in `submit_answer` and again in `_do_evaluate_round`, redundantly. Worse, the second call used `player.current_answer or -1` — and `0 or -1` is `-1` in Python, so any player who picked **answer index 0** (the first option, A) was misclassified as "no answer" in the round summary. **Fix:** added `last_answer_correct` field to `PlayerSession`, cached at submit time, read back in `_do_evaluate_round` — eliminates both the double-call and the falsy-zero bug. Real scoring fix that affected every game where someone picked A.
- **#146 `get_correct_answer` falls back to `answers[0]`.** If a malformed question had no answer marked correct, the fallback returned the first answer (almost certainly wrong) and players saw the wrong answer displayed as correct. Now raises `ValueError` so the bug surfaces in logs instead of corrupting the game.
- **#147 Service worker caching stale CSS / JS.** The SW used cache-first for static assets, which meant fresh CSS / JS didn't land after Quizify updates until users manually unregistered the SW. Switched to network-first for `/quizify/static/`; cache is now offline fallback only. Symptom was *"CSS works in incognito but not normal Chrome"* — exactly what I hit during the v1.1.0 testing session. Fixed.

### 🧹 Internal

- Cache-busters and SW `CACHE_VERSION` bumped to `1.1.1`.

### ✅ PR cleanup

- **#148 closed as superseded.** PR proposed the same admin-as-player fix that v1.1.0 already shipped via betas 6–8 and 10. Both implementations land at the same destination (admin token threaded through the join message; `is_admin` exposed in the player list serializer; `_is_authorized_admin` accepts player WS once the player is marked admin). Thanked the author and pointed to the merged commits.

---

## [1.1.0] — 2026-04-27

First stable release of the 1.1 line. Consolidates 10 betas worth of
work into one shipping release. The version bumps from 1.0.45 to 1.1.0
because this is the first release with the new design system, the new
auth model, and a substantially expanded gameplay flow.

### 🎯 What's in this release

**Visual identity — Soft Parlor.** Cream paper background `#FAF6EC`,
warm coral `#E88A7F` primary accent, sage / sky / sun secondary
accents at equal muted saturation, warm ink `#2A2820` text. Cabinet
Grotesk + DM Sans + JetBrains Mono typography stack. Soft drop shadows,
rounded 10–14 px corners. Light-primary; dark mode defers. Memorable-
thing anchor: *"Cozy and friendly — like a family board game."*
See [DESIGN.md](./DESIGN.md) for the full system.

**Security — admin auth hardened.** Admin session tokens persist to
HA storage and survive restarts (closes the LAN-takeover window that
previously reopened on every reboot). 24-hour TTL on player tokens,
wiped on game reset. Name validation NFKC-normalizes and strips control /
format characters (RTL override + zero-width impersonation closed).
Name-collision impersonation during gameplay requires the original
session token (no more "type the disconnected player's name to
inherit their score"). Recovery hatch via the
`quizify.reset_admin_session` HA service for stuck states.

**Gameplay correctness.** Round-summary broadcast no longer fires
twice on the timer-expiry / last-submit race. Per-player timer
broadcast (time-boost and freeze power-ups now visible on the
affected player's UI). Admin self-join race fixed (start_game no
longer dropped by the navigation-before-send race). Admin-as-player
authority correctly propagated through the join message
(`is_admin` flag now travels). `reset_to_lobby` cleans up all
pending tasks. Late joiners excluded from `all_submitted()`.
Mid-round reconnect preserves submitted state. `broadcast_to_admins`
excludes admin-as-player by `is_admin` flag, not stale ws identity.

**UX.** Admin buttons disable on click (no more double-advance toasts).
Server error codes translated to user-friendly German on both clients.
Malformed JSON returns structured error. Connection status dot uses
sage / sun / warm-red palette (was broadcast-gold). Empty
`lobby-difficulty-badge` pill now populated with German labels.
Admin-join modal no longer renders in light-mode palette.

**Features carried forward from earlier branch work.** Question pack
versioning with update check (#51). Multi-category mode (#21).
Answer distribution chart on reveal (#66, #32, #139). WebSocket
admin privilege-escalation fixes (#140, #142). Nabu Casa remote-UI
fixes (#11 family).

### ⚠️ Upgrade notes

- HACS users on a beta will pick this up automatically. Stable
  channel users see it as a regular update from 1.0.45.
- After upgrade: hard-reload admin / player / dashboard tabs so
  the service worker picks up the new bundle.
- The first admin connection on a fresh install bootstraps the
  persisted token. Look for `ADMIN BOOTSTRAP: granting admin to
  first connection` in HA logs — if you see this and it wasn't
  you, someone on your LAN beat you to it; remove + readd the
  integration to reset.
- If you ever get locked out of admin (“Admin only” errors and
  refresh doesn't help), call the new
  `quizify.reset_admin_session` service from Developer Tools —
  see CHANGELOG for beta.10.

### 📝 Known limitations

- The full game-in-progress flow (question → reveal → leaderboard
  → podium → finale) was not exercised end-to-end during this
  release cycle due to repeated admin-auth lockouts during testing.
  The lobby flow is verified; the gameplay loop is shipping on
  the strength of the underlying logical-review fixes (23 findings
  from the audit) and the per-fix manual review of each commit.
  File any gameplay bugs at
  [github.com/mholzi/quizify/issues](https://github.com/mholzi/quizify/issues).

---

## [1.1.0-beta.10] — 2026-04-27

The reset_admin_session service in beta.9 didn't actually work. Two
bugs combined to silently keep the persisted token alive even after
the service was called.

### 🐛 The two bugs

1. **`Store.async_save(None)` silently fails.** HA's Store doesn't
   accept `None` as a value to save — it expects a dict. The save call
   raised a TypeError that was caught by my try/except and logged as
   a warning. So the in-memory token got cleared, but the persisted
   file kept the old token. Next HA restart loaded the old token back.
   Fix: when clearing, call `Store.async_remove()` to delete the file
   outright instead of save(None).
2. **The service handler called the sync `clear_admin_token()` which
   used `asyncio.ensure_future()` for the storage write — fire and
   forget.** The service call returned before the storage write
   completed. Any admin connection that fired between the service
   return and the actual write would hit the still-persisted token,
   re-bootstrap, and overwrite the in-flight delete with a new save.
   Fix: added `async_clear_admin_token()` that the service awaits.

### 🧹 Internal

- Cache-busters and SW `CACHE_VERSION` bumped to `1.1.0-beta.10`.

---

## [1.1.0-beta.9] — 2026-04-26

Recovery hatch for the stale-admin-token lockout. Beta.4 added admin
token persistence; once the browser's sessionStorage gets out of sync
with the server's persisted token, there's no in-product way to recover.
This release adds an HA service to reset the persisted token.

### 🛠 New: `quizify.reset_admin_session` service

- Wipes the server's persisted admin token from HA storage.
- Callable from Developer Tools → Services. Only available to HA
  admins (HA's normal service auth).
- Next admin connection bootstraps a fresh token via the
  no-existing-token path.
- Use when: the admin tab silently fails to connect (Admin only error
  in console) and clearing browser sessionStorage doesn't help.

### 🧹 Internal

- Cache-busters and SW `CACHE_VERSION` bumped to `1.1.0-beta.9`.
- Added `services.yaml` so the service shows up in the Dev Tools UI.

---

## [1.1.0-beta.8] — 2026-04-25

The actual actual fix for admin-as-player. Beta.7 added is_admin to the
serializer (correct), but the admin_token was never reaching the server
because the client's URL-prefill code path didn't include it.

### 🐛 The right code path this time

- `?name=Markus` URL param set the input value but didn't set
  `state.playerName`, so the auto-join path that included `admin_token`
  never fired. Users had to click the Join Game button manually, and
  `handleJoinClick()` didn't carry `admin_token` either. Two bugs
  combined: an auto-join that didn't auto-fire, and a manual-join that
  didn't authenticate.
- Fixed both: URL prefill now also sets `state.playerName` (auto-join
  fires), AND `handleJoinClick()` now reads the admin token from
  sessionStorage and passes it in the join message.

### 🧹 Internal

- Cache-busters and SW `CACHE_VERSION` bumped to `1.1.0-beta.8`.

---

## [1.1.0-beta.7] — 2026-04-25

The actual root cause of admin-as-player. Beta.6 fixed the server-side
flag-setting (player.is_admin) but the bug was downstream in the
serializer.

### 🐛 The real fix

- `serialize_player_list()` was stripping `is_admin` and `submitted` out
  of the broadcast payload. Server set `player.is_admin = True`
  correctly (per beta.6), but the `player_joined` broadcast never
  included that field, so the client's `currentPlayer.is_admin` stayed
  `undefined`, and `updateAdminControls()` kept the Start Game button
  hidden. Now both serializers (`serialize_player_list` and
  `serialize_leaderboard`) include `is_admin` and `submitted`.

### 🧹 Internal

- Cache-busters and SW `CACHE_VERSION` bumped to `1.1.0-beta.7`.

---

## [1.1.0-beta.6] — 2026-04-25

Three regressions caught during the live beta.5 game-flow test. All
admin-as-player blockers; without these the admin can't actually play
through a game.

### 🐛 Critical regressions from beta.4

- **`MIN_PLAYERS` check in `start_game` blocked the admin-as-player flow
  entirely.** The check (added in beta.4 for the original "phantom rounds
  with 0 players" concern) rejected every `start_game` because the
  admin's player tab joins AFTER the redirect — so at the moment the
  admin tab fires `start_game`, there are 0 connected players. With the
  check active, the game stayed in LOBBY indefinitely. Removed the check
  and added a long comment explaining why phantom-rounds isn't actually
  a problem in the normal flow (the admin's player tab joins within
  ~1 second of redirect; if it doesn't, the round just runs no-answer
  and evaluates harmlessly).
- **Admin-as-player `is_admin` flag was never set on the player session.**
  The beta.4 fix keyed off `is_admin_connection(ws)`, but the player
  WebSocket isn't admin-tagged — only the admin tab's WebSocket is, and
  they're separate connections. Result: `currentPlayer.is_admin` stayed
  `false`, the "Start Game" button never appeared in the lobby, and the
  player view showed "Waiting for the host..." even though the current
  player IS the host.

  Fixed by passing the admin's persisted session token in the join
  message: `player-core.js` now reads `quizify_admin_session_token` from
  sessionStorage when `?admin=true` is in the URL and includes it as
  `admin_token` in the join payload. Server validates the token and, if
  valid, sets `player.is_admin = True` AND adds the player WS to the
  admin connections set so subsequent admin actions from the player tab
  (start_game, next_question, end_game) are authorized.

### 🐛 UX

- **Empty `lobby-difficulty-badge` pill** in player view. The HTML
  element existed but no JavaScript ever wrote to it — it rendered as
  a hollow coral-tinted pill next to the player count. Now populated by
  `renderLobby()` with German labels: `🌱 Einfach` / `🎯 Mittel` /
  `🔥 Schwer`. Hidden if no difficulty is broadcast.

### 🧹 Internal

- Cache-busters and SW `CACHE_VERSION` bumped to `1.1.0-beta.6`.

---

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

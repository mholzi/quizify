# Changelog

All notable changes to Quizify are documented here. This project follows
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [1.3.0-RC6] — 2026-06-10

Sixth release candidate for 1.3.0. Mobile polish found in live testing.

### Fixed

- **Start (and other primary-button) icons are legible again.** The P4 SVG
  icons sat in a tinted disc that washed out on the coral `.btn-primary` fill
  (notably the "Start Game" play glyph). Icons inside a filled primary button
  now drop the disc and render white, matching the button's white label;
  secondary / outline buttons keep their tinted discs.
- **Top content no longer clips under the iOS status bar (#229 / #233).** On a
  scrolled player/end screen (e.g. the podium or a question), content slid under
  the status bar. The player header is now sticky + opaque, so scrolled content
  is masked by the header instead of the bare status bar — works in a Safari tab
  regardless of safe-area insets; the standalone PWA also gets the Apple status
  metas + notch inset.

## [1.3.0-RC5] — 2026-06-10

Fifth release candidate for 1.3.0. Finishes the emoji→SVG icon sweep and fixes a
blank-screen edge found while live-testing P4.

### Changed

- **Remaining emoji UI icons replaced with SVG line icons — P4 (#225).** The
  standalone emoji-as-icon surfaces missed by the original #212 P1 inventory now
  use the shared Rounded Duotone set (`window.QuizifyIcons`): the admin lobby
  (Cast to TV, Join as Player, Start), the player nav/section icons (controller,
  target, brain, trophy, party, hourglass, sparkle, bulb) and error/hero states,
  the game control bar (skip, pause, resume, end, finish) and the paused screen,
  plus the status/utility glyphs (connection-lost antenna, invite-copy clipboard)
  and the lightning bolt icons. A new `UI_ICON_SVG` map + `uiIcon(name)` accessor
  back these; a `paintUiIcons()` pass swaps the `data-ui-icon` spans on init.
  Language flags and the floating reaction bar emoji are intentionally retained;
  emoji embedded in translated strings (P3 #220) and the setup presets/awards
  (P2 #219) are unchanged.

### Fixed

- **Player no longer shows a blank screen after a failed reconnect (#227).** The
  `reconnect_failed` handler cleared the session but never routed to a view, so a
  dead/stale reconnect (no joinable game) left every view hidden. It now returns
  to the join screen, and a fallback was added so an unmapped game-state phase
  always shows a usable view instead of nothing (same class as #221).

## [1.3.0-RC4] — 2026-06-10

Fourth release candidate for 1.3.0. Completes the event-loop-blocking cleanup
started in RC3.

### Fixed

- **Asset fingerprint no longer blocks the event loop (#213).** The cache-buster
  `scandir` over the `www/` tree now runs in an executor thread instead of on the
  loop (with the existing 5 s cache retained), so it can't stall the WebSocket
  server. Pairs with the RC3 history write/read fix (#222) to close out the whole
  blocking-I/O-on-the-loop class.

## [1.3.0-RC3] — 2026-06-10

Third release candidate for 1.3.0. Fixes two issues found while live-testing RC2.

### Fixed

- **Lightning Round no longer renders a blank player screen (#221).** A
  `game_state` snapshot sent during the lightning phase (e.g. the live
  leaderboard-refresh broadcast) omitted the `lightning` sub-state, so the
  client landed on an empty lightning view. The snapshot builder now carries the
  same `lightning` payload the reconnect path already had (splash-pending state
  or the current question).
- **`end_game` no longer blocks the event loop writing `question_history.json`
  (#222).** The history write — and the analogous history read at setup — now run
  in an executor thread instead of on the loop, so finishing a game no longer
  stalls the WebSocket server for all clients (same class as #213).

## [1.3.0-RC2] — 2026-06-10

Second release candidate for 1.3.0 (the first `v1.3.0` pre-release is RC1).
Adds a shared, app-wide SVG line-icon system that replaces the last emoji used
as UI icons, plus the welcome-screen redesign and two live-test fixes that
landed after the RC1 tag.

### Added

- **App-wide SVG line-icon system — "Rounded Duotone" (#212).** A shared icon
  helper (`www/js/icons.js`, `window.QuizifyIcons`) is now the single source of
  truth for the category/theme glyphs, consumed by both the admin and player
  JS. Style chosen from a design shotgun: a 2 px round-stroked glyph over a
  soft accent-tinted backing disc (flat tints, no gradients). First applied to
  the detail-view pack cards and the theme filter tabs; the emoji are pulled out
  of the `theme.*` labels so the strings hold text only. (Presets, end-game
  awards, and reveal-feedback strings are tracked as follow-ups #219 / #220.)

### Changed

- **Welcome screen redesign ("Categories-forward").** The host setup screen now
  leads with the category picker as a two-column grid of color-tinted tiles, each
  with an **SVG line icon** (replacing the emoji), the category name, and its
  question count. Tiles tint by theme across the four Soft Parlor accents and show
  a coral border + check when selected. The featured pack (World Cup / WM) gets a
  refreshed "Soft Spotlight" card: an SVG trophy in a sun-tinted badge, an
  "Empfohlen · Neu" eyebrow, and a round coral selection control. Selection wiring,
  language filtering, and the start payload are unchanged — the grid is still built
  from `#category-chips` as the single source of truth.

### Fixed

- **Host reset is authorized again; orphaned admin crown recovered (#207).** The
  single-admin invariant compared only by name, so the admin-as-player redirect
  could re-join as a second host and orphan the crown, making the legitimate
  host's Reset (and Pause/Skip) a silent no-op. Only a connected admin now blocks
  a re-claim, and Reset has an explicit recovery path.
- **Safe service-worker auto-reload on idle screens (#215).** The PWA now only
  auto-reloads on idle screens, avoiding a refresh mid-interaction.

## [1.3.0] — 2026-06-09

Feature release on top of the 1.2.7 hardening: a new Lightning Round bonus
mode, in-app community pack submission, optional question images, group
adaptive difficulty, lobby music, sound effects, a dramatic finale countdown,
plus a large internal refactor of the game server and two live-test bug fixes.

### Added

- **Lightning Round bonus mode (#42).** A fast bonus round the host can trigger
  from the finale (or standalone from the lobby): an intro splash explains the
  rules, then **5 quick questions at 15 s each** with no reveals between, and an
  end recap that shows the **correct answer** for every question (plus your own
  wrong pick where you missed). Admin-only trigger; players see a "waiting for
  host" hold on the splash until the host starts.
- **Sound effects with a mute toggle (#177).** Correct / wrong / last-5-seconds
  audio cues on the player device, with a mute control in the player header that
  persists across reloads.
- **Submit a community pack in-app (#180).** Hosts can now compose or paste a
  community question pack as JSON directly in the admin setup screen, get it
  validated field-by-field (a per-row ✓/✗ check of the pack name, language,
  question list and every question's shape — mirroring the on-disk pack schema
  from #179), and submit it for review. A submitted pack is handed to a small
  worker that turns it into a GitHub issue in `mholzi/quizify`; the GitHub
  token lives only in that worker, never in the browser or the integration.
  Each submission's status is reconciled server-side against the GitHub issue
  state (throttled to ~hourly): a closed-as-completed issue shows as
  *Accepted*, a closed-as-not-planned issue as *Declined*. Error messages are
  localized (`INVALID_FORMAT` / `RATE_LIMITED` / `GITHUB_ERROR`) with a
  fallback to the raw worker message. The whole feature is **inert by
  default**: a new optional "Community pack submission URL" option must be set
  before the section appears — empty means the UI stays hidden and the
  endpoints accept nothing. (The worker route itself is separate
  infrastructure and is not part of this integration.)
- **Group adaptive difficulty (#40).** A new **Auto** difficulty option that
  tunes the whole table together within a single game. The game still serves
  one shared question to everyone per round; after each round Quizify looks at
  the group's overall correct-rate and nudges the difficulty of *upcoming*
  questions up (group acing it) or down (group struggling). It is deliberately
  conservative: it starts at medium, only steps one rung at a time, averages
  over the last few rounds to avoid swinging on a single lucky/brutal question,
  and stays put until enough rounds of signal exist. Fixed Easy/Medium/Hard
  picks are untouched — calibration only runs in Auto mode. Per-player adaptive
  difficulty (personalised question streams) is tracked separately in #186.
- **Waiting-room music (#56).** Optional ambient background audio in the
  lobby, played through a real Home Assistant speaker. A new "Lobby music URL"
  option lets you point Quizify at an audio file you supply yourself
  (e.g. `/local/quizify-lobby.mp3` from your `config/www` folder); Quizify
  loops it on the **same `media_player` entity used for TTS** while waiting for
  players (via `media_player.play_media` + best-effort `repeat_set`). Music
  stops automatically once the game starts, so it never overlaps in-game TTS
  announcements that share the same speaker. The mechanism is inert by default
  — no audio file ships with the integration, and nothing plays until both a
  media player and a URL are configured.
- **Optional question images (#25).** Questions may now carry an optional
  `image_url` field in their pack JSON. When present, the shared dashboard
  renders the image above the question text and player screens show a
  thumbnail — handy for "What film is this from?" or visual geography
  questions. Only absolute `http(s)` URLs are accepted; relative paths,
  `data:`/`javascript:` schemes and non-strings are dropped at parse time.
  Questions without an image render exactly as before.
- **Dramatic finale countdown (#182).** Before the final question of a game, the
  shared dashboard plays a short suspenseful countdown so the last round lands
  with more weight. On any error the question still appears — the countdown
  never blocks the game.

### Changed

- **Finale podium redesign on player phones ("Podium Reborn").** The end-of-game
  ranking on each player's phone now shows the top three as bolder rising blocks
  with a warm tonal fill (1st coral, 2nd sage, 3rd sky), white numerals, and the
  medal accent kept on the top edge (sun-yellow / silver / bronze). A soft warm
  halo rises from the champion's block for a more celebratory finish. Scoped to
  the player end screen only — the admin / TV host podium keeps its cream-shelf
  look. Picked from a four-direction design exploration; the rest of the finale
  (your-result stats, awards, highlights timeline, full rankings) is unchanged.
- **Image questions side-by-side (#195).** Image questions present the picture
  beside the question text with tap-to-zoom, instead of a small thumbnail.
- **Finale countdown styling (#196)** refined to a "Spotlight-Marquee" look, and
  the **sound mute control (#197)** folded into a tidy header cluster.

### Fixed

- **Reset now fully clears the game (#207).** Pressing reset removes all players
  *and* the host and returns every screen to the initial setup — previously the
  reset signal was sent after connections were already closed, so screens froze
  in the old state.
- **Only one host per game (#208).** A second admin claim is now rejected, so
  two crowned hosts can no longer coexist in one lobby.
- **i18n name validation (#171)** deduplicated into one shared rule across the
  player and admin join paths.
- **Surfaced swallowed errors (#170)** in broad `except` loops so failures are
  logged instead of silently dropped.

### Internal

- Large game-server refactor splitting the state/websocket "god objects" into
  focused units — ScoringEngine + BroadcastDispatcher (#187), RoundMessageBuilder
  (#189), PhaseController (#188) and the timer-tick relocation (#203) — all
  behaviour-preserving with regression tests (part of #184).
- CSS split into per-screen source modules with a concat build (#185), test
  event-loop isolation to fix cross-module pollution (#198), and perf/leak
  invariant tests (#169).

## [1.2.7] — 2026-06-09

Backend hardening release from the 2026-06-09 automated code review. No
user-facing UI changes — concurrency and security fixes in the game server,
each with regression tests (162 passing).

### Fixed

- **Reaction-bonus cross-game leak (#167).** The inbound reaction-bonus
  counter was never cleared between games, so a player capped on reactions in
  round N of one game could be wrongly blocked from earning the bonus in
  round N of the next game. It now resets with every new game.
- **STEAL on a vanished target (#167).** If the steal target left the game
  before the power-up applied, the client still played a "successful" steal
  animation for zero points. STEAL now returns an error instead of
  broadcasting a hollow effect.
- **Admin-bootstrap race (#168).** On a fresh install, two simultaneous first
  connections could both be granted admin while only one token persisted —
  silently locking the other admin out. Bootstrap now grants exactly one admin
  atomically under a lock.
- **Session-token memory growth (#168).** Issued-but-never-validated session
  tokens were only evicted on lookup, so they could accumulate unbounded (a
  DoS surface). Expired tokens are now swept opportunistically on issue.
- **Malformed pack file 500 (#168).** A pack file that was valid JSON but not
  an object (e.g. a list) crashed the admin setup screen. It now degrades
  gracefully to the default icon.

### Verified (no code change needed)

- Double round-evaluation, double-submit, pending-removal-on-reset (#167) and
  the shuffle/answer-index bound (#168) were confirmed already safe under the
  cooperative single-threaded asyncio model and locked in with regression
  tests against future refactors.

## [1.2.6] — 2026-06-08

### Changed

- **Removed the "New version available" reload banner.** Now that the asset
  cache-buster is a content fingerprint, the next page load already pulls the
  fresh version, so the banner was redundant. Dropping it (and its auto-reload)
  also means an always-on host screen never reloads itself mid-game. The service
  worker still handles PWA install and offline caching, and refreshes silently.

## [1.2.5] — 2026-06-08

Consolidated release of the 1.2.x line under a fresh version number for clean
distribution. Bundles everything since 1.1.0: the World Cup packs, the
first-screen pack picker (with World Cup as a selectable pack), host-screen
language handling, and the self-healing asset cache-buster. See the 1.2.0–1.2.3
entries below for the detailed history.

## [1.2.3] — 2026-06-08

### Fixed

- **Host screens follow your Home Assistant language.** The launcher, dashboard,
  and analytics pages flashed English and then flipped to the browser language,
  ignoring the HA setting. They now resolve the HA language first (like the admin
  screen), so an English Home Assistant stays English. Player phones still follow
  the guest's own browser language.
- **World Cup is now a selectable pack, not an instant start.** Tapping the World
  Cup card on the setup screen used to launch the game immediately. It now toggles
  the pack on/off with a checkmark, exactly like the other categories — pick it (or
  any pack) and start with the “Start Game” button.

## [1.2.2] — 2026-06-08

### Fixed

- **Asset cache-buster no longer depends on bumping the version.** Static
  assets are served immutable, so the `?v=` query string is the only way to
  force a refetch — and it was keyed only to the manifest version. A reused or
  forgotten version bump left `?v=` unchanged, so browsers kept serving old
  CSS/JS/i18n (the World Cup card showed English on a German setup). The `?v=`
  params and the service-worker cache key now derive from a fingerprint of the
  asset files, so any CSS/JS/i18n change invalidates caches automatically. The
  displayed version stays clean. Resolves the recurrence of #147.

## [1.2.1] — 2026-06-08

### Fixed

- **Stale assets after updating.** While in beta, 1.2.0 was built twice under
  the same version number, so the `?v=` cache-buster never changed and browsers
  kept serving the old CSS/JS — the World Cup card showed English text on a
  German setup and the new pack chips didn't render. 1.2.1 bumps the version so
  clients fetch the current assets. No functional change beyond 1.2.0.

## [1.2.0] — 2026-06-08

### Added

- **World Cup quiz packs (English + German).** A new Men's FIFA World Cup
  category in the surprising-trivia style — `World Cup` (100 questions) and
  `Weltmeisterschaft` (99). Generated and reviewed for factual accuracy;
  selectable from the category list like any other pack.
- **Pick your pack from the first screen.** The setup screen now carries the
  pack picker itself — a featured World Cup spotlight card plus every pack as a
  chip — so the host chooses what to play in one tap, without opening “Adjust
  settings”. Game settings (difficulty, rounds, timer) stay there. The featured
  card and chips follow the active language (World Cup ↔ Weltmeisterschaft).

### Fixed

- **Category cards show the right question counts again.** After the +50-per-pack
  update the setup-screen cards still read ~100; they now reflect the real
  counts (150, or 148–149 where review removed an ambiguous question).

## [1.1.0] — 2026-06-08

### Added

- **+50 questions per category — the library grows from 1,800 to ~2,690.**
  Every one of the 18 packs (9 themes × German/English) gained 50 fresh
  "Unnützes Wissen" questions: surprising, counter-intuitive, weird-but-true,
  never capital-of-X or year-of-Y lookups. Each new batch was deduplicated
  against the existing questions in its pack, then run through a factual /
  distractor / fun-fact review — 6 ambiguous or disputed questions were
  dropped rather than shipped, so a few packs land at 148–149.
- Every pack version bumped `1.0 → 1.1`, and `versions.json` now tracks all
  18 packs (was 6) so existing installs are offered the new questions via the
  in-app pack update-check.

## [1.0.1] — 2026-06-08

### Fixed

- **Admin UI now follows your Home Assistant language.** The setup screen
  defaulted to German on first visit, so English speakers saw a flash of
  English that switched to German and stayed there with no obvious way back
  ([#152](https://github.com/mholzi/quizify/issues/152)). The admin interface
  now uses Home Assistant's configured language (Settings → General). Any
  non-German language (French, Spanish, …) falls back to English, since the UI
  ships in German and English only. The 🇩🇪/🇬🇧 toggle still switches the UI for
  the current session.

## [1.0.0] — 2026-06-07

The first official release of Quizify — a multiplayer trivia party game that
lives entirely inside Home Assistant. The TV is the host, phones are the
buzzers, and there's nothing to install for your guests.

### 🎉 Everything in 1.0.0

- **Scan and play.** The TV shows a QR code; everyone joins on their phone.
  No apps, no accounts, no logins. It all runs on your local network.
- **1,800 questions across 18 packs.** Nine themes — Geography, Animals &
  Nature, Pop Culture, Sport, Music, Science, History, Food & Drink, and
  Technology — each a clean 100 questions, in both German and English.
- **Pick your game.** Quick round, Classic, or Marathon presets, or go custom:
  choose your own topics, difficulty, round count, and timer.
- **Power-ups.** Joker, Double Points, Freeze, Steal, and a time boost turn a
  quiz into a party.
- **Streaks and bonuses.** Speed bonuses, difficulty multipliers, and streak
  fireworks at 3, 5, and 7 in a row.
- **A finale worth waiting for.** A podium, per-player superlatives, a
  highlights reel, and a full ranked leaderboard.
- **Soft Parlor design.** Warm cream paper, a four-color palette, and
  typography built to read across the room — cozy and friendly, like a family
  board game.

---

**18 packs · 1,800 questions · 2 languages · runs entirely on your local network**

[Report a Bug](https://github.com/mholzi/quizify/issues) · [Discussions](https://github.com/mholzi/quizify/discussions)

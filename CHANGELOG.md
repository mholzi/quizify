# Changelog

All notable changes to Quizify are documented here. This project follows
[Semantic Versioning](https://semver.org/).

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

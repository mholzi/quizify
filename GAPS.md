# Quizify — Gap Analysis vs. Beatify
> Full comparison of missing/pending capabilities
> **Date:** 2026-03-30

---

## Summary

Quizify has ~70% of Beatify's capabilities. The remaining 30% falls into 3 categories:
1. **Critical** — must have before first real game session
2. **Important** — needed for polished v1.0 release
3. **Nice-to-have** — future milestones

---

## Phase 6 — Critical (Pre-Launch)
**Goal:** Make Quizify robust enough for a real game session.

### 6.1 QR Code — Local Vendor (no CDN dependency)
- **Problem:** Admin page loads qrcodejs from CDN. Offline HA setups break.
- **Fix:** Copy `vendor/qrcode.min.js` from Beatify into `www/js/vendor/`, load locally.
- **Effort:** 30 min

### 6.2 Service Worker + PWA Manifest
- **Problem:** No service worker → slow reloads, no offline cache. No manifest → not installable as app.
- **Fix:** Port Beatify's `sw.js` (update cache name + static paths), create `site.webmanifest`.
- **Effort:** 2h

### 6.3 Dashboard / Shared Screen
- **Problem:** No TV/tablet view for shared display. Beatify has `dashboard.html` with full leaderboard, current song, podium.
- **Fix:** Create `www/dashboard.html` showing: current question (large), timer bar, live leaderboard, answer reveal, podium finale.
- **Effort:** 4h

### 6.4 WebSocket Reconnect (Session-based)
- **Problem:** Quizify has basic reconnect but no session restore. If a player's phone disconnects briefly they lose their score display.
- **Fix:** Port Beatify's session-based reconnect (player sends session token on reconnect → state restored).
- **Effort:** 3h

### 6.5 Admin Reconnect
- **Problem:** If admin closes/refreshes tab, game is lost. Beatify supports admin reconnect with game resume.
- **Fix:** Track admin session token, allow admin to rejoin and resume.
- **Effort:** 2h

### 6.6 App Icons
- **Problem:** No icons → ugly browser tab, no homescreen icon.
- **Fix:** Create icon-256.png and icon-512.png (Quizify logo/brain emoji style).
- **Effort:** 1h (design) + 30 min (wiring)

---

## Phase 7 — Important (v1.0 Polish)
**Goal:** Production-quality release ready for HACS.

### 7.1 i18n System
- **Problem:** UI strings are hardcoded DE/EN mixed. No language switching.
- **Fix:** Port Beatify's `i18n.js` system. Create `www/i18n/` folder with de.json, en.json. Wire up all UI strings.
- **Effort:** 1 day

### 7.2 Shared Utils Module
- **Problem:** `escapeHtml`, WebSocket factory, and view switching are duplicated across admin.js and player.js.
- **Fix:** Create `www/js/utils.js` (port from Beatify), import in all pages.
- **Effort:** 2h

### 7.3 Analytics + Stats Service
- **Problem:** No game history, no stats tracking. Beatify stores 90 days of game records with per-player analytics.
- **Fix:** Port `analytics.py` + `services/stats.py`. Store: game date, category, rounds, player scores, duration.
- **Effort:** 1 day

### 7.4 Analytics Dashboard
- **Problem:** No way to view past game history.
- **Fix:** Create `www/analytics.html` showing: recent games, top players, category stats, average scores.
- **Effort:** 1 day

### 7.5 Shareable Result Cards
- **Problem:** No post-game sharing. Beatify generates Wordle-style emoji grids.
- **Fix:** Adapt `game/share.py` for quiz results. Generate per-player result card (✅❌ per round).
- **Effort:** 4h

### 7.6 Asset Minification — done differently (#792)
- **Problem:** Only source JS/CSS, no minified versions. Slower load times.
- **What shipped:** pre-compressed `.gz` siblings instead of minification.
  aiohttp serves `<file>.gz` in place of `<file>` on its own, so `scripts/build_gzip.py`
  (stdlib `gzip`, no npm) buys 3.7-4x with no server change and no toolchain.
  Minifying on top would add maybe another 15% for a build dependency.
- **Effort:** done

### 7.7 Error Handling & Validation
- **Problem:** Edge cases not handled: game starts with 0 questions, player joins mid-game, WS message with missing fields.
- **Fix:** Defensive checks throughout websocket.py and state.py. Graceful error messages to frontend.
- **Effort:** 1 day

### 7.8 README + HACS Metadata
- **Problem:** No README, no HACS-ready metadata.
- **Fix:** Write README.md with install instructions, screenshots, gameplay description. Add HACS badges.
- **Effort:** 2h

---

## Phase 8 — Nice-to-Have (v1.1+)
**Goal:** Feature parity with Beatify's best features + Quizify-specific innovations.

### 8.1 Question Requests
- **Equivalent of Beatify's playlist-requests.js**
- Players can "request" a category mid-lobby (vote on next category)
- **Effort:** 1 day

### 8.2 Party Lights Integration
- **Equivalent of Beatify's party-lights.js / services/lights.py**
- Flash correct color when answer revealed (green/red)
- Strobe on finale
- **Effort:** 4h

### 8.3 Highlights / Superlatives
- **Equivalent of Beatify's game/highlights.py**
- End-of-game awards: "Fastest Finger", "Comeback King", "Perfect Round", "Hot Streak"
- **Effort:** 4h

### 8.4 Team Mode
- 2v2 or 3v3 — teams share score
- Already planned in REQUIREMENTS.md as v2.0
- **Effort:** 2 days

### 8.5 Community Question Packs
- **Equivalent of Beatify's community/ playlists**
- User-submitted question packs
- Version-controlled JSON format
- **Effort:** 1 day (infrastructure) + community contributions

### 8.6 Question Editor UI
- Admin can add/edit questions in the UI without editing JSON
- **Effort:** 2 days

### 8.7 Difficulty Voting
- Players vote on difficulty before game starts
- **Effort:** 4h

### 8.8 Daily Challenge Mode
- One curated set of questions per day, global leaderboard
- Already tracked in Beatify as #324
- **Effort:** 2 days

### 8.9 Power-Up Balancing & Testing
- Joker / Freeze / Time Boost may need tuning after playtesting
- **Effort:** ongoing

---

## Feature Comparison Table

| Feature | Beatify | Quizify | Phase |
|---|---|---|---|
| Core game loop | ✅ | ✅ | — |
| WebSocket real-time | ✅ | ✅ | — |
| QR code join | ✅ | ✅ | — |
| Player scoring | ✅ | ✅ | — |
| Streak system | ✅ | ✅ | — |
| Power-ups | ✅ | ✅ quiz-specific | — |
| Podium finale | ✅ | ✅ | — |
| Timer per round | ❌ n/a | ✅ | — |
| Fun fact reveal | ❌ n/a | ✅ | — |
| Multi-select categories | ❌ | ✅ | — |
| Admin self-join | ❌ | ✅ | — |
| QR code local vendor | ✅ | ❌ | **Phase 6** |
| Service Worker | ✅ | ❌ | **Phase 6** |
| PWA Manifest | ✅ | ❌ | **Phase 6** |
| Dashboard/TV screen | ✅ | ❌ | **Phase 6** |
| Session reconnect | ✅ | ⚠️ basic | **Phase 6** |
| Admin reconnect | ✅ | ❌ | **Phase 6** |
| App icons | ✅ | ❌ | **Phase 6** |
| i18n system | ✅ | ❌ | **Phase 7** |
| Shared utils | ✅ | ❌ | **Phase 7** |
| Analytics | ✅ | ❌ | **Phase 7** |
| Analytics dashboard | ✅ | ❌ | **Phase 7** |
| Share cards | ✅ | ❌ | **Phase 7** |
| Minified assets | ✅ | ❌ | **Phase 7** |
| Error handling | ✅ | ⚠️ basic | **Phase 7** |
| README/HACS | ✅ | ❌ | **Phase 7** |
| Party lights | ✅ | ❌ | **Phase 8** |
| Superlatives | ✅ | ❌ | **Phase 8** |
| Team mode | ❌ (v3) | ❌ (v2) | **Phase 8** |
| Community packs | ✅ | ❌ | **Phase 8** |
| Question editor UI | ❌ | ❌ | **Phase 8** |
| Daily challenge | ❌ (planned) | ❌ | **Phase 8** |

---

## Timeline Estimate

| Phase | Focus | Effort |
|---|---|---|
| Phase 6 | Critical pre-launch fixes | 2-3 days |
| Phase 7 | v1.0 polish + HACS ready | 1 week |
| Phase 8 | v1.1+ innovations | ongoing |

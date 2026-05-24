<div align="center">

# Quizify

<img src="images/quizify-logo.png" alt="Quizify Logo" width="430">

### **Multiplayer Useless Knowledge Quiz Game for Home Assistant**

Turn any gathering into a trivia battle. Players scan, questions fly, everyone competes. No app needed.

[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2024.1+-41BDF5?style=for-the-badge&logo=homeassistant&logoColor=white)](https://www.home-assistant.io/)
[![Version](https://img.shields.io/badge/Version-1.1.5-ff00ff?style=for-the-badge)](https://github.com/mholzi/quizify/releases)
[![License](https://img.shields.io/badge/License-MIT-green?style=for-the-badge)](LICENSE)

[**Get Started**](#installation) • [**How to Play**](#how-to-play) • [**Question Packs**](#question-packs)

---

</div>

<br>

## What Is Quizify?

**Quizify is an open-source trivia quiz game for Home Assistant** — a multiplayer party game that runs entirely on your local network.

A question appears. Everyone races to answer. Points fly. Streaks build. Champions emerge.

No apps to download. No accounts to create. Just scan a QR code and play.

---

<br>

## Why Parties Are Better With Quizify

**Zero Friction Entry** — Players scan a QR code. That's it. No apps. No accounts. 10 seconds from scan to playing.

**Runs Fully Local** — No cloud. No subscription. No data leaves your network. Fast, private, reliable.

**Everyone Competes** — Points, speed bonuses, streaks, power-ups, and a dramatic finale with podium and awards. Real competition, real laughs.

**Works on Any Screen** — Dashboard mode for the TV. Players use their phones. No extra hardware needed.

---

<br>

## Installation

### Via HACS (Recommended)

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=mholzi&repository=quizify&category=integration)

Or manually:
```
HACS → ⋮ Menu → Custom Repositories
→ URL: https://github.com/mholzi/quizify
→ Category: Integration
→ Install "Quizify"
→ Restart Home Assistant
```

### Manual
```bash
cd /config/custom_components
git clone https://github.com/mholzi/quizify.git quizify
# Restart Home Assistant
```

### Configure

[![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=quizify)

Or manually:
```
Settings → Devices & Services → Add Integration → "Quizify"
```

---

<br>

## How to Play

### For the Host (Admin)

1. Open `/quizify/admin` on your device
2. Select **language**, **category**, **difficulty**, and **round count**
3. Share the QR code or join URL with players
4. Enter your name to join as a player (optional)
5. Hit **▶️ Spiel starten** — the game begins

### For Players

1. Scan the QR code or visit the join URL on your phone
2. Enter your name
3. Answer questions before the timer runs out
4. Watch the live leaderboard after each round

### Dashboard / TV Mode

Open `/quizify/dashboard` on your TV for a shared screen view showing the leaderboard, question, and live player activity.

---

<br>

## Scoring

**Base Formula (correct answers only):**

```
Points = (Base + Speed Bonus) × Difficulty × Streak × (×2 if Double Points power-up)
```

| Component | Value |
|-----------|-------|
| **Base points** | 10 |
| **Speed bonus** | up to +5 (linear, decays over time limit) |
| **Difficulty multiplier** | Easy ×1.0 / Medium ×1.5 / Hard ×2.0 |
| **Streak multiplier** | +10% per correct answer in a row, max +50% at 5× |
| **Wrong / timeout** | 0 pts, streak resets |

**Example:** Medium question, instant answer, 3× streak:
`(10 + 5) × 1.5 × 1.3 = **29 pts**`

### Time Limits by Difficulty

| Difficulty | Time |
|------------|------|
| Easy | 20s |
| Medium | 15s |
| Hard | 10s |

---

<br>

## Power-Ups

One random player receives a random power-up at the start of each round. Use it during the question phase — power-ups are consumed immediately.

| Power-Up | Effect |
|----------|--------|
| 🃏 **Joker** | Removes one wrong answer (25% → 33% chance of guessing right) |
| ✌️ **Double Points** | This round's points count double |
| 🥶 **Freeze** | Pauses an opponent's timer for 5 seconds |
| ⏱️ **Time Boost** | Adds +5 seconds to your own timer |
| 🥷 **Steal** | Takes 50% of a target player's current round points |

Freeze and Steal require selecting a target player — only available with 2+ players.

---

<br>

## Question Packs

Quizify ships with 300 questions across 6 categories in two languages:

| Category | Language | Questions |
|----------|----------|-----------|
| Geographie | 🇩🇪 DE | 50 |
| Tiere & Natur | 🇩🇪 DE | 50 |
| Popkultur | 🇩🇪 DE | 50 |
| Geography | 🇬🇧 EN | 50 |
| Animals & Nature | 🇬🇧 EN | 50 |
| Pop Culture | 🇬🇧 EN | 50 |

The language selector in the admin UI filters both the category chips and the question pool — no mixing of languages mid-game.

### Adding Custom Question Packs

Question packs are simple JSON files stored in `custom_components/quizify/questions/`:

```json
{
  "name": "Science",
  "language": "en",
  "version": "1.0",
  "questions": [
    {
      "id": "sci_001",
      "question": "What is the speed of light?",
      "answers": [
        {"text": "299,792 km/s", "correct": true},
        {"text": "150,000 km/s", "correct": false},
        {"text": "500,000 km/s", "correct": false}
      ],
      "difficulty": "medium",
      "fun_fact": "Light takes about 8 minutes to travel from the Sun to Earth.",
      "category": "Science"
    }
  ]
}
```

Rules:
- Exactly **3 answers** per question
- Exactly **1 correct** answer
- `difficulty`: `easy`, `medium`, or `hard`
- `language`: `de`, `en`, or any ISO code
- File goes in the `questions/` directory — picked up automatically on next game start

---

<br>

## Analytics

Quizify includes a built-in analytics dashboard at `/quizify/analytics`:

- Total games played
- Average players per game
- Top players by cumulative score
- Category breakdown with average scores
- Games-over-time chart (7d / 30d / 90d / all)
- Recent game history

Data is stored locally in `config/quizify/analytics.json` with a 90-day retention window.

---

<br>

## Network Setup

Quizify runs on Home Assistant's HTTP server — **no extra ports needed**.

| Protocol | Port | Purpose |
|----------|------|---------|
| HTTP/HTTPS | 8123 (default) | Game UI, API, static assets |
| WebSocket | 8123 (same port) | Real-time game sync |

If players are on a separate WiFi/VLAN, add a single firewall rule:
```
Guest VLAN → HA IP : TCP 8123
```

> **⚠️ Fritzbox users:** The Fritzbox guest WiFi fully isolates clients from your home network — players must be on the main WiFi, or use a VLAN-capable router with selective LAN access.

---

<br>

## FAQ

<details>
<summary><strong>How many players can join?</strong></summary>
<br>
Tested with 20+ players. Your WiFi is the only real constraint.
</details>

<details>
<summary><strong>Can someone join mid-game?</strong></summary>
<br>
Yes! Late joiners inherit the current average score so they can compete fairly from round one.
</details>

<details>
<summary><strong>What if a player disconnects?</strong></summary>
<br>
Players can reconnect using the session token stored in their browser. The game continues for everyone else and the reconnected player picks up from the current state.
</details>

<details>
<summary><strong>Can the admin also play?</strong></summary>
<br>
Yes! Click "Als Spieler beitreten" / "Join as Player" in the lobby. You'll see answer buttons inline and compete alongside other players, while retaining admin controls.
</details>

<details>
<summary><strong>Can I add my own questions?</strong></summary>
<br>
Yes — see <a href="#adding-custom-question-packs">Adding Custom Question Packs</a> above.
</details>

<details>
<summary><strong>What languages are supported?</strong></summary>
<br>
German (🇩🇪) and English (🇬🇧) question packs are included. The language selector in admin filters question packs automatically.
</details>

---

<br>

## What's New

### v1.0.32 — Language Selector & English Packs 🇬🇧
- **Language selector in admin UI** — 🇩🇪 Deutsch / 🇬🇧 English chip toggle in Game Settings
- Category chips filter automatically based on selected language; resets to Mixed on language switch
- Language passed in `start_game` WebSocket payload

### v1.0.31 — English Question Packs
- 150 English questions across 3 new categories: Geography, Pop Culture, Animals & Nature (50 each)
- Backend `QuestionBank` now stores and filters by language from pack metadata
- `start_game` and `reset` accept a `language` parameter; default remains `de`

### v1.0.30 — Rescaled Scoring
- Base points: 1000 → **10**
- Max speed bonus: 500 → **5**
- Cleaner, more readable scores for a party game context

### v1.0.29 — Fix Score Shown as 0 in Reveal
- Fixed correct answers showing as +0 pts in the all_answers reveal card — `player.submitted` (a bool) was incorrectly used as an answer index instead of `player.current_answer`
- Added guard against double round evaluation (race condition between timer tick and `all_submitted`)

### v1.0.28 — Complete Player Page CSS
- Full CSS rework for player.html: responsive layout, mobile-first, dark theme consistency

### v1.0.11 — Version Badge & Cache Busting
- Version badge displayed bottom-right on admin.html and player.html
- All CSS/JS assets stamped with `?v=x.x.x` for reliable cache invalidation
- `scripts/bump_version.sh` added for future version bumps

### v1.0.10 — Emotion & Beatify-Style Reveal UI
- Emotion text (NAILED IT! / Wrong! / Too slow!) with random variants
- Correct answer hero display with cyan glow
- Personal result card with full score breakdown (base, speed, streak, difficulty)
- `round_score`, `correct`, bonuses included in leaderboard payload

### v1.0.9 — Score Breakdown in Reveal
- `speed_bonus`, `streak_bonus`, `difficulty_multiplier` now stored on `PlayerSession` after scoring
- Breakdown included in `all_answers` payload and shown on reveal cards

### v1.0.8 — Beatify-Style Reveal Cards
- Per-player reveal cards with left accent bar, sorted by round score
- Replaces the plain table from v1.0.7

### v1.0.7 — Per-Player Answers Table in Reveal
- Backend already sent `all_answers` in round_summary — frontend now renders it
- Admin reveal shows player name, chosen answer, ✅/❌/⏱️, and points

### v1.0.6 — Admin Answer Buttons Fix
- Fixed `is_admin` flag being `false` when question first rendered for admin-as-player
- Stores last question payload, re-renders on `joined` event confirmation

### v1.0.5 — Admin Gameplay Fix
- Hid correct answer from admin during `QUESTION_ACTIVE` phase
- Added answer buttons to admin game view so admin can play inline
- "End Game" hidden during question phase, shown again on reveal

### v1.0.0 — Initial Release 🎉
- WebSocket-based multiplayer trivia game
- 150 German questions across 3 categories
- Timer-based scoring with speed bonuses, streaks, and difficulty multipliers
- 5 power-ups: Joker, Double Points, Freeze, Time Boost, Steal
- Reconnect support with session tokens
- Admin dashboard with QR code, player list, and game controls
- Player mobile UI with emoji avatar picker
- Leaderboard with delta arrows and sort animation
- Score counter animation and floating points popups
- Collapsible leaderboard on reveal (collapsed by default)
- Rematch button on finale screen
- Dashboard / TV mode at `/quizify/dashboard`
- Analytics tracking at `/quizify/analytics`

[View all releases →](https://github.com/mholzi/quizify/releases)

---

<br>

## Technical Details

### Requirements
- **Home Assistant** 2024.1+
- **HACS** (recommended) or manual installation

### Architecture
```
Home Assistant
    └── Quizify Integration
            ├── Game State Manager    (game/state.py)
            ├── WebSocket Handler     (server/websocket.py)
            ├── Question Bank         (game/questions.py)
            ├── Power-Up Manager      (game/powerups.py)
            ├── Analytics Service     (analytics.py)
            └── Web UI (Admin + Player + Dashboard)
```

### How It Works
- Native Home Assistant integration — no extra services
- WebSocket-based real-time sync for all clients
- Local processing — no cloud required
- Session token reconnect for mid-game drops
- Up to 20 concurrent players

---

<br>

## Contributing

Contributions welcome! Bug fixes, new question packs, translations, or features — all appreciated.

Quick start: Fork → Branch → PR.

See [open issues](https://github.com/mholzi/quizify/issues) for ideas and [good first issues](https://github.com/mholzi/quizify/issues?q=is%3Aopen+label%3A%22good+first+issue%22) for easy starting points.

---

<br>

## License

MIT License. See [LICENSE](LICENSE) for details.

---

<br>

<div align="center">

## Ready to Play?

The next trivia battle is one QR scan away.

[**Install Quizify Now**](#installation)

---

**The open-source trivia quiz for Home Assistant. Built for fun.**

[Report Bug](https://github.com/mholzi/quizify/issues) · [Request Feature](https://github.com/mholzi/quizify/issues)

<br>

<sub>Made with ❤️ for the Home Assistant community</sub>

</div>

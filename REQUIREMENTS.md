# Quizify — Requirements Document
> Multiplayer Useless Knowledge Quiz Game for Home Assistant
> **Version:** 0.1 (Draft)
> **Date:** 2026-03-29

---

## 1. Vision

Quizify is a HACS integration for Home Assistant that turns any smart home into a
multiplayer trivia quiz game. Inspired by Beatify, it reuses the same core architecture
(WebSocket, QR-code join, local-first) but replaces music rounds with general knowledge
questions — with a focus on fun, surprising "useless knowledge" facts.

---

## 2. Core Gameplay

### Round Flow
1. Admin starts a game via the Quizify UI
2. Players join by scanning a QR code (phone browser, no app needed)
3. Each round: a question is displayed on all player screens simultaneously
4. Players choose one of **3 answer options** within the time limit
5. Points are awarded based on correctness and speed
6. After all rounds: podium reveal with winner animation

### Scoring
- **Correct answer:** Base points (e.g. 1000)
- **Speed bonus:** Additional points for faster answers (linear decay over time limit)
- **Streak bonus:** Multiplier for consecutive correct answers
- **Difficulty multiplier:** Easy ×1, Medium ×1.5, Hard ×2

### Timer
- **Per difficulty:**
  - Easy: 20 seconds
  - Medium: 15 seconds
  - Hard: 10 seconds
- Timer is visible to all players (countdown bar)
- No answer within time = 0 points for that round

---

## 3. Question Database

### Format
Similar to Beatify's playlist JSON files. Questions live in:
```
custom_components/quizify/questions/<category>.json
```

### Question JSON Schema
```json
{
  "name": "Geographie",
  "language": "de",
  "version": "1.0",
  "questions": [
    {
      "id": "geo_001",
      "question": "Welches Land hat die längste Küstenlinie der Welt?",
      "answers": [
        { "text": "Norwegen", "correct": false },
        { "text": "Kanada", "correct": true },
        { "text": "Australien", "correct": false }
      ],
      "difficulty": "medium",
      "fun_fact": "Kanada hat eine Küstenlinie von über 202.000 km — das ist mehr als 5× der Erde.",
      "image_url": "https://example.com/images/coastline.jpg",
      "source": "Wikipedia"
    }
  ]
}
```

`image_url` is **optional**. When set to an absolute `http(s)` URL the
dashboard renders the image above the question text and player screens
show a thumbnail. Relative paths and non-`http(s)` schemes are ignored.

### Categories (initial set)
| Kategorie | Datei | Sprachen |
|---|---|---|
| Geographie | `geographie.json` | DE |
| Tiere & Natur | `tiere-natur.json` | DE |
| Geschichte | `geschichte.json` | DE |
| Popkultur | `popkultur.json` | DE |
| Wissenschaft | `wissenschaft.json` | DE |
| Essen & Trinken | `essen-trinken.json` | DE |
| Sport | `sport.json` | DE |
| Technik | `technik.json` | DE |
| Geography | `geography.json` | EN |
| Animals & Nature | `animals-nature.json` | EN |
| History | `history.json` | EN |
| Pop Culture | `pop-culture.json` | EN |

### Difficulty Distribution (per category)
- Easy: ~30%
- Medium: ~50%
- Hard: ~20%

---

## 4. Game Modes (v1.0)

### Solo Mode
- Single player against the clock
- Personal high score tracking
- Good for practice or solo fun

### Multiplayer Mode (local)
- 2–12 players, all on same local network
- Each player on their own phone (QR code join)
- Real-time leaderboard visible during game
- No accounts, no apps

### Team Mode *(v2.0 — future)*
- Players divided into teams
- Team score = sum of individual scores
- Team podium at the end

---

## 5. Admin UI

### Game Setup Screen
- Select **category** (single or mixed)
- Select **difficulty** (Easy / Medium / Hard / Mixed)
- Set **number of rounds** (5 / 10 / 15 / 20 / custom)
- Set **time limit override** (use defaults or custom seconds)
- Show QR code for players to join
- Start game button

### In-Game View
- Current question + 3 answer buttons (large, tap-friendly)
- Countdown timer (animated bar)
- Current round number (e.g. "Round 3 / 10")
- Player count indicator

### Post-Round View
- Correct answer reveal
- **Fun Fact** displayed after each round
- Points earned this round per player
- Running leaderboard

### Finale
- Podium (1st / 2nd / 3rd) with animation
- Full results table
- Option to play again (same or new settings)

---

## 6. Technical Architecture

### Reused from Beatify
- **WebSocket server** (real-time state sync)
- **QR code generation** (player join flow)
- **Player session management** (name, score, streak)
- **Streak & bonus system** (scoring engine)
- **Power-up framework** (can be extended)
- **Finale animation** (podium reveal)
- **HACS integration structure** (manifest, config flow)
- **Multi-language support** (i18n strings)
- **Admin panel** (adapted for quiz flow)

### New Components
- **Question engine** (load JSON, shuffle questions, validate answers)
- **Timer engine** (countdown with speed-bonus calculation)
- **Category selector** (UI + backend)
- **Difficulty filter** (per-question weighting)
- **Fun fact display** (post-round reveal)
- **HA entities** (game state exposed as sensors — reuse #441 pattern)

### Stack
- **Backend:** Python (Home Assistant integration)
- **Frontend:** Vanilla JS + HTML/CSS (served by HA)
- **Communication:** WebSocket (real-time)
- **Storage:** JSON files (question database, game state)
- **No cloud dependencies**

---

## 7. HA Integration

### Config Flow
- Integration name: `quizify`
- Minimal setup: just install via HACS, no API keys needed

### Entities (exposed to HA)
- `sensor.quizify_current_round`
- `sensor.quizify_leader`
- `sensor.quizify_top_score`
- `binary_sensor.quizify_game_active`
- `sensor.quizify_player_count`
- `sensor.quizify_current_category`

### Services
- `quizify.start_game` — start a game with parameters
- `quizify.end_game` — end current game
- `quizify.next_question` — advance to next question (admin control)

---

## 8. Roadmap

### v1.0 — MVP
- [ ] HACS integration scaffold (reuse Beatify structure)
- [ ] Question JSON loader + shuffler
- [ ] WebSocket game engine (adapted from Beatify)
- [ ] Player join via QR code
- [ ] 3-answer multiple choice UI
- [ ] Timer with speed bonus
- [ ] 3 categories in DE (Geographie, Tiere, Popkultur)
- [ ] Scoring engine (correct + speed + streak + difficulty)
- [ ] Post-round fun fact
- [ ] Finale + podium

### v1.1
- [ ] Full category set (8 DE + 4 EN)
- [ ] Difficulty filter in setup
- [ ] Custom round count
- [ ] HA entities

### v2.0
- [ ] Team Mode
- [ ] Mixed category mode (random across all)
- [ ] Community question submissions
- [ ] Question editor in admin UI

---

## 9. Decisions

| # | Topic | Decision |
|---|---|---|
| 1 | Fun Facts | Included as `fun_fact` field directly in question JSON ✅ |
| 2 | Power-Ups | Quiz-specific power-ups (see below) ✅ |
| 3 | Min. questions per category | **50 questions** before a category ships ✅ |
| 4 | Answer format | Forced 3-choice only, no "I don't know" option ✅ |

### Power-Ups (Quiz-specific)

| Power-Up | Effect | Inspired by |
|---|---|---|
| **Joker** | Eliminates one wrong answer (2 choices remain) | Who Wants to Be a Millionaire |
| **Double Points** | This round counts double | Beatify streak bonus |
| **Freeze** | Opponent is locked out of answering for 5 seconds (lockout, not a pause — their clock keeps running) | Beatify (reused) |
| **50/50** | Same as Joker — removes one wrong answer | Classic quiz |
| **Time Boost** | Add 5 seconds to your timer | New |

Each player starts with 1 power-up per game (type randomly assigned). Power-ups are single-use.

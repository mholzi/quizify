# Quizify — Implementation Plan
> Based on Beatify architecture · Target: v1.0 MVP
> **Date:** 2026-03-29

---

## Overview

Quizify reuses ~70% of Beatify's codebase. The implementation is structured in 5 phases,
starting with scaffolding from Beatify and progressively replacing music-specific logic
with question/answer logic.

---

## Phase 1 — Scaffold (copy + rename from Beatify)
**Goal:** A working HACS integration that installs and loads, with no game logic yet.
**Estimated effort:** 1-2 days

### Tasks

#### 1.1 Copy Beatify structure
```
custom_components/quizify/
  __init__.py          ← copy + rename references
  manifest.json        ← update domain, name, version
  const.py             ← update constants
  config_flow.py       ← copy (minimal changes needed)
  translations/        ← copy, update strings
```

#### 1.2 Remove Beatify-specific modules
- Remove `services/media_player.py` (no music playback)
- Remove `services/lights.py` (v1.0 — no light integration)
- Remove `game/playlist.py` → replace with `game/questions.py`
- Remove `game/highlights.py` → replace with `game/fun_facts.py`
- Keep `game/player.py`, `game/player_registry.py`, `game/powerups.py`, `game/scoring.py`

#### 1.3 Create question data directory
```
custom_components/quizify/questions/
  geographie.json      ← 50+ questions
  tiere-natur.json
  popkultur.json
  (more categories in v1.1)
```

#### 1.4 Verify HACS install
- `hacs.json` + `manifest.json` configured
- Integration loads without errors in HA

---

## Phase 2 — Question Engine
**Goal:** Load, shuffle, and serve questions from JSON files.
**Estimated effort:** 2-3 days

### New file: `game/questions.py`
Replaces `game/playlist.py`. Responsibilities:
- Load question JSON files from `questions/` directory
- Validate schema (required fields: id, question, answers, difficulty)
- Shuffle questions for each game session
- Filter by category and difficulty
- Track which questions have been shown (no repeats in a session)
- Expose: `get_next_question()`, `get_categories()`, `get_question_count()`

### Question JSON loader
```python
class QuestionBank:
    def load_category(self, category: str) -> list[Question]
    def get_next_question(self) -> Question | None
    def get_categories(self) -> list[str]
    def reset(self) -> None
```

### New file: `game/types.py` (extend Beatify's)
Add quiz-specific types:
```python
@dataclass
class Question:
    id: str
    question: str
    answers: list[Answer]
    difficulty: Difficulty  # easy | medium | hard
    fun_fact: str
    category: str
    correct_answer: Answer  # derived

@dataclass
class Answer:
    text: str
    correct: bool
```

---

## Phase 3 — Game Engine (adapt Beatify's state.py)
**Goal:** Full round flow with timer, answer submission, scoring.
**Estimated effort:** 3-4 days

### Adapt `game/state.py`
Beatify's `state.py` is 2046 lines — extract the reusable core and replace music-specific logic.

**Keep (reuse directly):**
- Player join/leave/reconnect logic
- Streak tracking
- Power-up framework
- Phase state machine (lobby → playing → results → finale)
- Admin controls (start/stop/next round)
- WebSocket event dispatch

**Replace:**
- `start_round()`: instead of playing a song → send question + start timer
- `submit_guess()`: instead of year guess → validate answer choice (A/B/C)
- `evaluate_round()`: instead of proximity scoring → correct/incorrect binary + speed bonus
- Remove all media player calls
- Remove intro/outro music logic

**New methods:**
```python
def start_question(self, question: Question) -> None
def submit_answer(self, player_id: str, answer_index: int, timestamp: float) -> None
def evaluate_answer(self, player_id: str) -> AnswerResult
def reveal_fun_fact(self) -> str
def get_timer_remaining(self) -> float
```

### Adapt `game/scoring.py`
**Keep:** streak multiplier, power-up bonuses, podium calculation

**Replace:** proximity scoring → binary correct/incorrect

**Add:**
```python
def calculate_speed_bonus(elapsed: float, time_limit: float) -> int
    # Linear decay: full points at 0s, 0 bonus at time_limit
def calculate_difficulty_multiplier(difficulty: Difficulty) -> float
    # Easy: 1.0, Medium: 1.5, Hard: 2.0
```

### Adapt `game/powerups.py`
Replace Beatify power-ups with quiz-specific ones:

| Power-Up | Implementation |
|---|---|
| `joker` | Remove one wrong answer from current question |
| `double_points` | Set flag → scoring engine doubles points this round |
| `freeze` | Pause opponent's timer for 5 seconds |
| `time_boost` | Add 5 seconds to own timer |

### New: `game/timer.py`
```python
class QuestionTimer:
    def start(self, duration: float) -> None
    def get_remaining(self) -> float
    def is_expired(self) -> bool
    def pause_for_player(self, player_id: str, seconds: float) -> None  # freeze power-up
    def add_time_for_player(self, player_id: str, seconds: float) -> None  # boost power-up
```

---

## Phase 4 — Frontend (adapt Beatify's HTML/JS)
**Goal:** Working player UI and admin panel.
**Estimated effort:** 3-4 days

### Files to adapt from Beatify's `www/`

#### `player.html` → Quiz player view
- **Keep:** QR join flow, streak display, leaderboard, power-up button, podium finale
- **Replace:** Year slider → 3 answer buttons (A/B/C), large tap targets
- **Add:** Timer bar (animated countdown), fun fact reveal after round, correct/wrong feedback animation

#### `admin.html` → Game setup + control
- **Keep:** QR code display, player list, start/stop controls
- **Replace:** Playlist selector → Category + difficulty selector
- **Add:** Round count selector, time limit override

#### `dashboard.html` → Shared screen display
- **Keep:** Leaderboard, finale animation
- **Replace:** Song info → Question text display (large font for TV/screen)
- **Add:** Show correct answer after round ends

#### `launcher.html` → Entry point
- Minimal changes needed

#### New JS modules:
```
www/js/
  timer.js         ← countdown bar component
  answers.js       ← 3-button answer UI
  fun-fact.js      ← post-round reveal animation
```

---

## Phase 5 — WebSocket Protocol (adapt Beatify's websocket.py)
**Goal:** Real-time sync of questions, answers, timer, and results.
**Estimated effort:** 2 days

### Adapt `server/websocket.py`
Beatify's websocket.py is 1479 lines — adapt message types:

**Keep message types:**
- `join` / `leave` / `reconnect`
- `player_update` (score, streak)
- `game_state` (phase, round number)
- `powerup_used`
- `leaderboard_update`
- `finale`

**Replace message types:**
- `song_started` → `question_started` (includes question text, shuffled answers, timer duration)
- `guess_submitted` → `answer_submitted` (includes answer index)
- `round_result` → `answer_result` (correct/wrong, points, fun fact)

**New message types:**
- `timer_tick` (remaining seconds, sent every second)
- `timer_expired` (round ends — no more answers accepted)
- `joker_applied` (one answer removed for requesting player)

---

## Phase 6 — Question Content
**Goal:** 50+ questions per category for v1.0 launch.
**Estimated effort:** parallel to development

### v1.0 Categories (3 × 50 questions minimum)
1. **Geographie** (DE) — 50 questions
2. **Tiere & Natur** (DE) — 50 questions
3. **Popkultur** (DE) — 50 questions

### Content generation strategy
- Use AI to generate initial question sets with fun facts
- Human review to verify accuracy
- Each question: id, question text, 3 answers (1 correct), difficulty, fun_fact, source

---

## File Structure (final v1.0)

```
custom_components/quizify/
├── __init__.py
├── manifest.json
├── const.py
├── config_flow.py
├── translations/
│   ├── en.json
│   └── de.json
├── game/
│   ├── __init__.py
│   ├── player.py          ← reused from Beatify
│   ├── player_registry.py ← reused from Beatify
│   ├── scoring.py         ← adapted from Beatify
│   ├── powerups.py        ← adapted (quiz power-ups)
│   ├── state.py           ← adapted from Beatify
│   ├── questions.py       ← NEW (replaces playlist.py)
│   ├── timer.py           ← NEW
│   ├── types.py           ← adapted from Beatify
│   └── fun_facts.py       ← NEW (post-round reveal)
├── server/
│   ├── __init__.py
│   ├── views.py           ← minimal changes
│   ├── websocket.py       ← adapted from Beatify
│   └── serializers.py     ← adapted
├── services/
│   └── stats.py           ← reused (optional)
├── questions/
│   ├── geographie.json    ← 50+ questions
│   ├── tiere-natur.json   ← 50+ questions
│   └── popkultur.json     ← 50+ questions
└── www/
    ├── player.html        ← adapted
    ├── admin.html         ← adapted
    ├── dashboard.html     ← adapted
    ├── launcher.html      ← minimal changes
    ├── css/               ← reused + quiz theme
    └── js/
        ├── (existing Beatify JS adapted)
        ├── timer.js       ← NEW
        ├── answers.js     ← NEW
        └── fun-fact.js    ← NEW
```

---

## Summary Timeline

| Phase | What | Effort |
|---|---|---|
| 1 | Scaffold + HACS install | 1-2 days |
| 2 | Question engine | 2-3 days |
| 3 | Game engine (state + scoring + timer) | 3-4 days |
| 4 | Frontend (player + admin UI) | 3-4 days |
| 5 | WebSocket protocol | 2 days |
| 6 | Question content (150 questions) | parallel |
| **Total** | | **~2-3 weeks** |

---

## Recommended Start Order
1. Phase 1 — get a "hello world" HACS integration running
2. Phase 2 — get questions loading from JSON
3. Phase 3 — get a single-player round working end-to-end (no UI yet, test via WS)
4. Phase 4 — build UI on top of working backend
5. Phase 5 — refine WebSocket protocol
6. Content — generate questions in parallel during phases 3-5

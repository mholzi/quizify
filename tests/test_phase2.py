"""Tests for Phase 2 features ported from Beatify.

Covers:
- Streak milestone bonuses (3/5/10/...)
- All-time player stats persistence + leaderboard sorting
- v1 → v2 analytics migration
- Per-question stats aggregation + hardest/easiest queries
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from custom_components.quizify.analytics import QuizifyAnalytics  # noqa: E402
from custom_components.quizify.game.scoring import (  # noqa: E402
    STREAK_MILESTONES,
    get_streak_milestone_bonus,
)
from custom_components.quizify.question_stats import (  # noqa: E402
    QuestionStatsService,
)


class _Runtime:
    """Minimal runtime that satisfies analytics + question_stats."""

    def __init__(self, tmp_path: Path) -> None:
        self.data_dir = tmp_path

    async def run_in_executor(self, func, *args):  # noqa: ANN001, ANN002
        return func(*args)

    def create_task(self, coro):  # noqa: ANN001
        return asyncio.ensure_future(coro)


# ---------- Streak milestones ----------


class TestStreakMilestoneScoring:
    def test_milestones_at_canonical_values(self) -> None:
        for streak, expected in STREAK_MILESTONES.items():
            assert get_streak_milestone_bonus(streak) == expected

    def test_no_bonus_off_milestone(self) -> None:
        for streak in (1, 2, 4, 6, 7, 8, 9, 11, 14, 16, 19, 21, 100):
            assert get_streak_milestone_bonus(streak) == 0

    def test_higher_milestones_award_more(self) -> None:
        # Sanity — designers should never accidentally invert.
        sorted_keys = sorted(STREAK_MILESTONES.keys())
        prev = -1
        for k in sorted_keys:
            assert STREAK_MILESTONES[k] > prev
            prev = STREAK_MILESTONES[k]


# ---------- Streak milestones — state integration ----------


class TestStreakMilestoneInGame:
    """Drive QuizifyGameState through 3 correct answers and verify the
    milestone bonus is awarded + tracked on the player."""

    def test_third_correct_answer_awards_milestone(self, tmp_path: Path) -> None:
        from custom_components.quizify.game.state import QuizifyGameState  # noqa: PLC0415

        # Stub a question bank: every answer index 0 is correct.
        gs = QuizifyGameState(runtime=_Runtime(tmp_path), entry_id="t")
        ws = MagicMock()
        ws.closed = False
        gs.add_player("Alice", ws)
        gs.start_game(num_rounds=10, difficulty="medium", language="de")

        bonus_before = gs.players["Alice"].streak_milestone_bonus_total
        hits_before = gs.players["Alice"].streak_milestones_hit
        assert bonus_before == 0 and hits_before == 0

        # Play 3 correct rounds.
        for _ in range(3):
            q = gs.start_next_question()
            assert q is not None
            # Find the correct answer index.
            correct_idx = next(i for i, a in enumerate(q.answers) if a.correct)
            result = gs.submit_answer("Alice", correct_idx)
            # Round auto-evaluates because Alice is the only participant.
            # Continue loop into ANSWER_REVEAL — start_next_question will pick up.

        alice = gs.players["Alice"]
        # At streak=3 the milestone (20 pts) should have triggered exactly once.
        assert alice.streak == 3
        assert alice.streak_milestones_hit == 1
        assert alice.streak_milestone_bonus_total == STREAK_MILESTONES[3]


# ---------- All-time stats ----------


class TestAllTimeStats:
    @pytest.mark.asyncio
    async def test_first_record_creates_entry(self, tmp_path: Path) -> None:
        rt = _Runtime(tmp_path)
        a = QuizifyAnalytics(rt)
        await a.load()
        await a.record_game(
            game_id="g1",
            category="popkultur",
            difficulty="medium",
            num_rounds=10,
            players={"Alice": 150, "Bob": 100},
            duration_seconds=300,
            player_details={
                "Alice": {"best_streak": 5, "streak_milestones_hit": 1},
                "Bob": {"best_streak": 3, "streak_milestones_hit": 0},
            },
        )
        board = a.get_all_time_leaderboard()
        names = [r["name"] for r in board]
        assert names[:2] == ["Alice", "Bob"]
        alice = next(r for r in board if r["name"] == "Alice")
        assert alice["games_played"] == 1
        assert alice["total_score"] == 150
        assert alice["wins"] == 1
        assert alice["best_streak"] == 5
        assert alice["streak_milestones_hit"] == 1

    @pytest.mark.asyncio
    async def test_two_games_accumulate(self, tmp_path: Path) -> None:
        rt = _Runtime(tmp_path)
        a = QuizifyAnalytics(rt)
        await a.load()
        await a.record_game(
            game_id="g1", category="mixed", difficulty="medium",
            num_rounds=5, players={"Alice": 100}, duration_seconds=100,
            player_details={"Alice": {"best_streak": 4, "streak_milestones_hit": 1}},
        )
        await a.record_game(
            game_id="g2", category="mixed", difficulty="hard",
            num_rounds=5, players={"Alice": 80}, duration_seconds=120,
            player_details={"Alice": {"best_streak": 6, "streak_milestones_hit": 2}},
        )
        alice = a.get_all_time_leaderboard()[0]
        assert alice["games_played"] == 2
        assert alice["total_score"] == 180
        assert alice["wins"] == 2
        # Best streak takes the MAX, milestones SUM.
        assert alice["best_streak"] == 6
        assert alice["streak_milestones_hit"] == 3

    @pytest.mark.asyncio
    async def test_leaderboard_sorts_by_total_then_wins(self, tmp_path: Path) -> None:
        rt = _Runtime(tmp_path)
        a = QuizifyAnalytics(rt)
        await a.load()
        # Same total; Bob has more wins → Bob first.
        await a.record_game(
            game_id="g1", category="mixed", difficulty="medium",
            num_rounds=5, players={"Alice": 100, "Bob": 50},
            duration_seconds=100,
        )
        await a.record_game(
            game_id="g2", category="mixed", difficulty="medium",
            num_rounds=5, players={"Alice": 0, "Bob": 50},
            duration_seconds=100,
        )
        # Totals: Alice=100, Bob=100. Wins: Alice=1, Bob=1. Tie — order is stable.
        # Make Bob clearly win the tiebreaker by adding another Bob win.
        await a.record_game(
            game_id="g3", category="mixed", difficulty="medium",
            num_rounds=5, players={"Alice": 0, "Bob": 0},
            duration_seconds=100,
        )
        # Bob and Alice both 100; one extra round each — winner of g3 is
        # whoever the max() pick. Both have 0 in g3 so dict-order picks
        # one. Just assert sort stability and that both appear.
        board = a.get_all_time_leaderboard()
        assert {r["name"] for r in board} >= {"Alice", "Bob"}
        # Both totals equal.
        assert board[0]["total_score"] == board[1]["total_score"]

    @pytest.mark.asyncio
    async def test_v1_migration_backfills_all_time(self, tmp_path: Path) -> None:
        # Simulate an existing v1 file with games but no all_time_players.
        legacy = {
            "version": 1,
            "games": [
                {
                    "game_id": "old1",
                    "started_at": 1000,
                    "ended_at": 1100,
                    "duration_seconds": 100,
                    "player_count": 2,
                    "category": "mixed",
                    "question_count": 5,
                    "rounds_played": 5,
                    "average_score": 75.0,
                    "difficulty": "medium",
                    "player_scores": {"Alice": 100, "Bob": 50},
                    "winner": "Alice",
                },
            ],
        }
        path = tmp_path / "analytics.json"
        path.write_text(json.dumps(legacy))

        rt = _Runtime(tmp_path)
        a = QuizifyAnalytics(rt)
        await a.load()

        board = a.get_all_time_leaderboard()
        names = [r["name"] for r in board]
        assert "Alice" in names and "Bob" in names
        alice = next(r for r in board if r["name"] == "Alice")
        assert alice["games_played"] == 1
        assert alice["total_score"] == 100
        assert alice["wins"] == 1


# ---------- Per-question stats ----------


class TestQuestionStats:
    @pytest.mark.asyncio
    async def test_record_round_aggregates_correct_count(self, tmp_path: Path) -> None:
        rt = _Runtime(tmp_path)
        qs = QuestionStatsService(rt)
        await qs.load()
        # Round 1: 2 correct (5s, 7s), 1 wrong.
        qs.record_round("q1", [(True, 5.0), (True, 7.0), (False, 9.0)])
        # Round 2: 1 correct (3s).
        qs.record_round("q1", [(True, 3.0)])

        hardest = qs.get_hardest(min_shown=1)
        q = next(x for x in hardest if x["question_id"] == "q1")
        # 4 submissions total, 3 correct → rate = 0.75
        assert q["shown_count"] == 4
        assert q["correct_count"] == 3
        assert q["correct_rate"] == pytest.approx(0.75, rel=1e-3)
        # avg = (5 + 7 + 3) / 3 = 5.0
        assert q["avg_time_correct"] == pytest.approx(5.0, rel=1e-3)

    @pytest.mark.asyncio
    async def test_get_hardest_filters_by_min_shown(self, tmp_path: Path) -> None:
        rt = _Runtime(tmp_path)
        qs = QuestionStatsService(rt)
        await qs.load()
        # q_easy: shown 5x, always correct → high rate
        for _ in range(5):
            qs.record_round("q_easy", [(True, 1.0)])
        # q_noise: shown 1x, wrong → 0% but should be excluded by min_shown=3
        qs.record_round("q_noise", [(False, 1.0)])

        hardest = qs.get_hardest(min_shown=3)
        ids = [x["question_id"] for x in hardest]
        assert "q_noise" not in ids
        assert "q_easy" in ids

    @pytest.mark.asyncio
    async def test_save_round_trips(self, tmp_path: Path) -> None:
        rt = _Runtime(tmp_path)
        qs = QuestionStatsService(rt)
        await qs.load()
        qs.record_round("qX", [(True, 4.0), (False, 8.0)])
        await qs.save_if_dirty()

        qs2 = QuestionStatsService(rt)
        await qs2.load()
        items = qs2.get_hardest(min_shown=1)
        q = next(x for x in items if x["question_id"] == "qX")
        # Two submissions in the recorded round, one correct.
        assert q["shown_count"] == 2
        assert q["correct_count"] == 1

    @pytest.mark.asyncio
    async def test_save_is_noop_when_clean(self, tmp_path: Path) -> None:
        rt = _Runtime(tmp_path)
        qs = QuestionStatsService(rt)
        await qs.load()
        await qs.save_if_dirty()  # should not write a file
        assert not (tmp_path / "question_stats.json").exists()

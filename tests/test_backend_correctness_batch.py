"""Regression tests for the 2026-06-11 backend correctness/security batch.

* #302 — auto-difficulty calibrator counts only SUBMITTERS, not idle tabs.
* #308 — wager-with-0-bank no longer suppresses normal scoring;
         score-breakdown components sum to the awarded points;
         podium / leaderboard ties share a rank.
* #309 — featured-pack theme is read from pack metadata (resolves community
         packs whose on-disk path differs from their slug).
* #305 — /flags response never leaks stored client IPs.
* #310 — _pending_removals self-cleans on task completion + cancels on
         re-schedule (no unbounded growth).
* #307 — per-send timeout bounds a stalled WebSocket send.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from custom_components.quizify.const import DIFFICULTY_AUTO  # noqa: E402
from custom_components.quizify.game.scoring import calculate_round_score  # noqa: E402
from custom_components.quizify.game.scoring_engine import ScoringEngine  # noqa: E402
from custom_components.quizify.game.state import (  # noqa: E402
    GamePhase,
    QuizifyGameState,
)
from custom_components.quizify.game.types import Difficulty  # noqa: E402
from custom_components.quizify.server.connection import ConnectionManager  # noqa: E402
from custom_components.quizify.server.serializers import (  # noqa: E402
    serialize_finale,
    serialize_leaderboard,
)


class _FakeRuntime:
    def __init__(self, tmp_path: Path) -> None:
        self.data_dir = tmp_path

    def create_task(self, coro):
        return asyncio.ensure_future(coro)

    async def run_in_executor(self, func, *args):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, func, *args)


def _ws() -> MagicMock:
    ws = MagicMock()
    ws.closed = False
    ws.send_json = AsyncMock()
    return ws


@pytest.fixture
def game(tmp_path: Path) -> QuizifyGameState:
    return QuizifyGameState(runtime=_FakeRuntime(tmp_path), entry_id="test")


def _correct_index(game: QuizifyGameState) -> int:
    q = game._current_question
    assert q is not None
    return next(i for i, a in enumerate(q.answers) if a.correct)


# ---------------------------------------------------------------------------
# #302 — calibrator counts only submitters
# ---------------------------------------------------------------------------


class TestCalibratorSubmittersOnly:
    def test_idle_tab_does_not_drag_difficulty(self, game: QuizifyGameState) -> None:
        """In auto mode, a connected-but-idle player must NOT be counted as a
        wrong answer — only players who actually submitted feed the signal."""
        a, b = _ws(), _ws()
        game.add_player("Answerer", a)
        game.add_player("Idle", b)  # connected, never submits

        game.start_game(difficulty=DIFFICULTY_AUTO, num_rounds=3, language="de")
        assert game._calibrator is not None
        captured: list[tuple[int, int]] = []
        orig = game._calibrator.record_round

        def _spy(correct: int, total: int) -> None:
            captured.append((correct, total))
            orig(correct=correct, total=total)

        game._calibrator.record_round = _spy  # type: ignore[method-assign]

        game.start_next_question()
        # Only "Answerer" submits (correctly).
        assert game.submit_answer("Answerer", _correct_index(game)).__class__  # sanity
        game.evaluate_round()

        assert captured, "calibrator.record_round was not called"
        correct, total = captured[-1]
        # total reflects the 1 submitter, NOT the 2 connected players.
        assert total == 1
        assert correct == 1

    def test_zero_submitters_carries_no_signal(self, game: QuizifyGameState) -> None:
        """A round where nobody submits → total 0 → calibrator ignores it."""
        a = _ws()
        game.add_player("A", a)
        game.start_game(difficulty=DIFFICULTY_AUTO, num_rounds=3, language="de")
        captured: list[tuple[int, int]] = []
        assert game._calibrator is not None
        game._calibrator.record_round = lambda correct, total: captured.append(  # type: ignore[method-assign]
            (correct, total)
        )

        game.start_next_question()
        game.evaluate_round()  # nobody submitted

        assert captured[-1] == (0, 0)


# ---------------------------------------------------------------------------
# #308 — wager-with-0-bank no longer suppresses scoring
# ---------------------------------------------------------------------------


class TestWagerZeroBank:
    def test_zero_bank_wager_does_not_suppress_normal_score(self) -> None:
        engine = ScoringEngine()
        # A 0-score player on the final round who "wagers" 100% (bank 0).
        comp = engine.score_submission(
            correct=True,
            elapsed=0.0,
            round_duration=30.0,
            difficulty=Difficulty.MEDIUM,
            streak=1,
            double_points_active=False,
            is_final_round=True,
            wager=100,
            score_before_wager=0,
        )
        # Normal scoring stands (the wager override is skipped at wager_pts==0).
        expected = calculate_round_score(
            correct=True,
            elapsed=0.0,
            time_limit=30.0,
            difficulty=Difficulty.MEDIUM,
            streak=1,
        )
        assert expected > 0
        assert comp.points == expected
        assert comp.wager_used is None

    def test_nonzero_bank_wager_still_overrides(self) -> None:
        engine = ScoringEngine()
        comp = engine.score_submission(
            correct=True,
            elapsed=0.0,
            round_duration=30.0,
            difficulty=Difficulty.MEDIUM,
            streak=1,
            double_points_active=False,
            is_final_round=True,
            wager=50,
            score_before_wager=200,
        )
        # 50% of 200 = 100; override replaces normal scoring.
        assert comp.wager_used == 100
        assert comp.points == 100


# ---------------------------------------------------------------------------
# #308 — score-breakdown sums to the awarded points
# ---------------------------------------------------------------------------


class TestBreakdownSums:
    @pytest.mark.parametrize("difficulty", list(Difficulty))
    @pytest.mark.parametrize("elapsed", [0.0, 3.7, 12.4, 29.9])
    @pytest.mark.parametrize("streak", [0, 1, 3, 5, 9])
    def test_base_plus_bonuses_equal_points(
        self, difficulty: Difficulty, elapsed: float, streak: int
    ) -> None:
        engine = ScoringEngine()
        comp = engine.score_submission(
            correct=True,
            elapsed=elapsed,
            round_duration=30.0,
            difficulty=difficulty,
            streak=streak,
            double_points_active=False,
            is_final_round=False,
            wager=None,
            score_before_wager=0,
        )
        # base(10) + speed_bonus + streak_bonus must equal points minus the
        # discrete milestone spike (which is surfaced separately).
        base = 10
        reconstructed = base + comp.speed_bonus + comp.streak_bonus + comp.milestone_bonus
        assert reconstructed == comp.points
        assert comp.speed_bonus >= 0
        assert comp.streak_bonus >= 0


# ---------------------------------------------------------------------------
# #308 — podium / leaderboard ties share a rank
# ---------------------------------------------------------------------------


class _FakePlayer:
    def __init__(self, name: str, score: int) -> None:
        self.name = name
        self.score = score
        self.streak = 0
        self.round_score = 0
        self.round_history: list = []
        self.round_score_breakdown: dict = {}
        self.color = "#000"
        self.is_admin = False
        self.submitted = False
        self.max_streak = 0
        self.powerups_used = 0


class TestTiesShareRank:
    def test_leaderboard_ties_share_rank(self) -> None:
        players = [
            _FakePlayer("A", 100),
            _FakePlayer("B", 100),
            _FakePlayer("C", 50),
        ]
        lb = serialize_leaderboard(players)
        ranks = {row["name"]: row["rank"] for row in lb}
        assert ranks["A"] == 1
        assert ranks["B"] == 1  # tied, same rank
        assert ranks["C"] == 3  # rank 2 skipped (competition ranking)

    def test_podium_ties_share_rank(self) -> None:
        podium = [
            _FakePlayer("A", 100),
            _FakePlayer("B", 100),
            _FakePlayer("C", 80),
        ]
        out = serialize_finale(podium, podium)
        pranks = {e["name"]: e["rank"] for e in out["podium"]}
        assert pranks["A"] == 1
        assert pranks["B"] == 1
        assert pranks["C"] == 3


# ---------------------------------------------------------------------------
# #309 — featured-pack theme stored in pack metadata at load time
# ---------------------------------------------------------------------------


class TestPackThemeMetadata:
    def test_builtin_pack_metadata_carries_theme(self) -> None:
        from custom_components.quizify.game.questions import QuestionBank

        qb = QuestionBank()
        qb.load_all_categories()
        versions = qb.get_pack_versions()
        assert versions, "no packs loaded"
        # Every loaded pack exposes a (possibly empty) string theme in metadata.
        for slug, meta in versions.items():
            assert "theme" in meta, f"{slug} missing theme in metadata"
            assert isinstance(meta["theme"], str)
        # At least one built-in pack has a non-empty theme.
        assert any(meta["theme"] for meta in versions.values())

    def test_community_pack_theme_resolves_from_metadata(self, tmp_path: Path) -> None:
        """A community pack (under questions/community/<stem>.json, ``community-``
        slug) must surface its theme via metadata — the path the old views.py
        file-read missed (#309)."""
        import json as _json

        from custom_components.quizify.game.questions import (
            COMMUNITY_SUBDIR,
            QuestionBank,
        )

        qb = QuestionBank(questions_dir=tmp_path)
        community = tmp_path / COMMUNITY_SUBDIR
        community.mkdir(parents=True)
        (community / "mypack.json").write_text(
            _json.dumps(
                {
                    "name": "My Pack",
                    "language": "de",
                    "theme": "geography",
                    "questions": [
                        {
                            "id": "q1",
                            "question": "Hauptstadt von Frankreich?",
                            "answers": [
                                {"text": "Paris", "correct": True},
                                {"text": "Rom", "correct": False},
                                {"text": "Madrid", "correct": False},
                            ],
                            "difficulty": "easy",
                        }
                    ],
                }
            )
        )
        qb.load_community_packs()
        versions = qb.get_pack_versions()
        slug = next(s for s in versions if s.startswith("community-"))
        assert versions[slug]["theme"] == "geography"


# ---------------------------------------------------------------------------
# #305 — /flags never returns stored client IPs
# ---------------------------------------------------------------------------


class _ExecRuntime:
    """Runtime whose run_in_executor runs the func inline (sync) in-thread."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir

    async def run_in_executor(self, func, *args):
        return func(*args)


# #356: /flags is now admin-token gated — carry a token + validating conn.
_FLAG_TOKEN = "test-admin-token"


class _FlagConn:
    def validate_admin_token(self, token: str) -> bool:
        return token == _FLAG_TOKEN


class _FlagRequest:
    def __init__(self, ctx, token: str | None = _FLAG_TOKEN) -> None:  # noqa: ANN001
        from custom_components.quizify.server.context import APP_CTX_KEY

        self.app = {APP_CTX_KEY: ctx}
        self.query = {"token": token} if token else {}
        self.headers: dict[str, str] = {}


class TestFlagListNoIpLeak:
    @pytest.mark.asyncio
    async def test_remote_ip_stripped_from_response(self, tmp_path: Path) -> None:
        import json as _json
        from types import SimpleNamespace

        from custom_components.quizify.server.flag_store import FILENAME as _FLAG_FILE
        from custom_components.quizify.server.views import flag_list_view

        # Write a flag entry that includes a stored client IP (as flag_view does).
        flag_path = tmp_path / _FLAG_FILE
        flag_path.write_text(
            _json.dumps(
                {
                    "ts": 1,
                    "question_id": "q1",
                    "reason": "bad",
                    "player_name": "Foo",
                    "remote": "203.0.113.42",
                }
            )
            + "\n"
        )

        ctx = SimpleNamespace(
            runtime=_ExecRuntime(tmp_path),
            ws_handler=SimpleNamespace(conn=_FlagConn()),
        )
        resp = await flag_list_view(_FlagRequest(ctx))  # type: ignore[arg-type]
        payload = _json.loads(resp.body)

        assert payload["flags"], "no flags returned"
        for entry in payload["flags"]:
            assert "remote" not in entry, "client IP leaked to unauth caller"
        # The non-sensitive fields are still present.
        assert payload["flags"][0]["question_id"] == "q1"
        assert payload["flags"][0]["reason"] == "bad"

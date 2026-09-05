"""The next round's picture is warmed during the reveal (#736).

Nothing preloaded a question image. ``renderQuestionImageBanner`` set
``img.src`` on ``question_started``, the television did the same, and
``player-core`` stamps the countdown deadline from ``timer_duration`` in that
very message — so the download started with the clock already draining, and up
to twenty-one clients pulled the same file at the same instant. The 67 pack
images average 99 KB and the largest is 331 KB, which makes the worst round a
~7 MB burst out of Home Assistant at the one moment the round cannot absorb it.

The fix moves the fetch one round earlier, into the reveal — the only stretch
of a round with an idle network. ``round_summary`` carries a
``next_image_url`` hint and every client warms it.

Two failure modes are worth more than the speed-up, so they are pinned first:

1. **A hint that is a guess.** Adaptive difficulty (#40) does not serve the
   queue head, and a Lightning Round (#285) or Hot Seat auction (#616) fires
   before the next queued question. Warming the wrong file spends exactly the
   bandwidth this change exists to save.
2. **A picture shown early.** The reveal keeps the question view's ``<img>``
   rather than re-rendering it, so a preload that touched that element would
   paint the next question over the current reveal — and a progressive-reveal
   question (#434) would arrive unblurred a whole round early, because the blur
   is applied by the render path and that has not run yet. Every client must
   preload into a DETACHED ``new Image()``.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from custom_components.quizify.game.questions import (  # noqa: E402
    Answer,
    Question,
)
from custom_components.quizify.game.state import (  # noqa: E402
    QuizifyGameState,
    RoundSummary,
)
from custom_components.quizify.server.round_message_builder import (  # noqa: E402
    RoundMessageBuilder,
)
from custom_components.quizify.server.serializers import (  # noqa: E402
    serialize_round_summary,
)

WWW = _REPO_ROOT / "custom_components" / "quizify" / "www"

IMG_NEXT = "/quizify/static/img/packs/picture-round/next.webp"


def _fake_ws() -> MagicMock:
    ws = MagicMock()
    ws.closed = False
    return ws


class _Runtime:
    def __init__(self, tmp_path: Path) -> None:
        self.data_dir = tmp_path

    async def run_in_executor(self, func, *args):  # noqa: ANN001, ANN002
        return func(*args)

    def create_task(self, coro):  # noqa: ANN001
        import asyncio

        return asyncio.ensure_future(coro)


@pytest.fixture
def state(tmp_path: Path) -> QuizifyGameState:
    st = QuizifyGameState(runtime=_Runtime(tmp_path), entry_id="test")
    st.add_player("A", _fake_ws())
    return st


def _question(qid: str, image_url: str = "") -> Question:
    return Question(
        id=qid,
        question=f"Q {qid}?",
        answers=[Answer("a", True), Answer("b", False), Answer("c", False)],
        image_url=image_url,
    )


def _pin_queue(state: QuizifyGameState, questions: list[Question]) -> None:
    """Replace the drawn queue with an explicit one, nothing served yet."""
    state._question_bank._queue = list(questions)
    state._question_bank._queue_index = 0


# ---------------------------------------------------------------------------
# The peek itself — read-only, and only where the head is really next
# ---------------------------------------------------------------------------


class TestPeekNextQuestion:
    def test_peek_returns_the_head_without_consuming_it(
        self, state: QuizifyGameState
    ) -> None:
        state.start_game(num_rounds=10)
        _pin_queue(state, [_question("q1"), _question("q2")])
        bank = state._question_bank

        assert bank.peek_next_question().id == "q1"
        # The whole point: peeking twice must not move the queue on, or the
        # hint would silently eat the round it is describing.
        assert bank.peek_next_question().id == "q1"
        assert bank.get_next_question().id == "q1"
        assert bank.peek_next_question().id == "q2"

    def test_peek_on_an_exhausted_queue_is_none(
        self, state: QuizifyGameState
    ) -> None:
        state.start_game(num_rounds=10)
        _pin_queue(state, [_question("q1")])
        state._question_bank.get_next_question()
        assert state._question_bank.peek_next_question() is None


class TestPeekNextImageUrl:
    def test_hint_is_the_next_rounds_picture(self, state: QuizifyGameState) -> None:
        state.start_game(num_rounds=10)
        _pin_queue(state, [_question("q2", IMG_NEXT)])
        state.round = 1

        assert state.peek_next_image_url() == IMG_NEXT

    def test_a_next_question_without_a_picture_yields_no_hint(
        self, state: QuizifyGameState
    ) -> None:
        state.start_game(num_rounds=10)
        _pin_queue(state, [_question("q2")])
        state.round = 1

        assert state.peek_next_image_url() is None

    def test_no_hint_on_the_last_round(self, state: QuizifyGameState) -> None:
        """There is no next round — the finale follows."""
        state.start_game(num_rounds=5)
        _pin_queue(state, [_question("q6", IMG_NEXT)])
        state.round = 5

        assert state.peek_next_image_url() is None

    def test_no_hint_in_adaptive_mode(self, state: QuizifyGameState) -> None:
        """#40 serves by calibrated target, not by queue head.

        The head is a guess there, and a preload of the wrong file costs the
        room the bandwidth this whole change is meant to give back.
        """
        state.start_game(num_rounds=10, difficulty="auto")
        _pin_queue(state, [_question("q2", IMG_NEXT)])
        state.round = 1

        assert state.peek_next_image_url() is None

    def test_no_hint_when_a_lightning_round_comes_first(
        self, state: QuizifyGameState
    ) -> None:
        """#285 fires BEFORE the next queued question, from its own pool."""
        state.start_game(num_rounds=10, lightning_seed=14)
        assert state.lightning_target_round == 3
        _pin_queue(state, [_question("q3", IMG_NEXT)])

        state.round = 2  # about to enter round 3 — the Lightning target
        assert state.peek_next_image_url() is None

        # Once it has fired, the queued question really is next again.
        state._lightning_fired = True
        assert state.peek_next_image_url() == IMG_NEXT

    def test_no_hint_when_the_hot_seat_auction_comes_first(
        self, state: QuizifyGameState
    ) -> None:
        state.start_game(num_rounds=10, lightning_enabled=False, hot_seat_seed=14)
        target = state._hot_seat_target_round
        assert target is not None
        _pin_queue(state, [_question("qN", IMG_NEXT)])

        state.round = target - 1
        assert state.peek_next_image_url() is None

        state._hot_seat_fired = True
        assert state.peek_next_image_url() == IMG_NEXT


# ---------------------------------------------------------------------------
# The wire: an optional key, absent when there is nothing to warm
# ---------------------------------------------------------------------------


def _summary(**extra) -> dict:
    return serialize_round_summary(
        correct_answer_index=0,
        correct_answer_text="a",
        fun_fact="",
        leaderboard=[],
        round_num=1,
        total_rounds=10,
        **extra,
    )


class TestRoundSummaryPayload:
    def test_hint_rides_the_round_summary(self) -> None:
        assert _summary(next_image_url=IMG_NEXT)["next_image_url"] == IMG_NEXT

    @pytest.mark.parametrize("value", [None, ""])
    def test_key_is_absent_when_there_is_nothing_to_warm(self, value) -> None:
        """Absent, not null.

        Every other payload assertion in the suite stays exact, and a client
        tests one thing (``if (msg.next_image_url)``) rather than two.
        """
        assert "next_image_url" not in _summary(next_image_url=value)


class _FakeGameState:
    """Minimal stand-in exposing only what RoundMessageBuilder touches."""

    def __init__(self, question: Question, *, hint: str | None) -> None:
        self._question = question
        self.round = 3
        self.total_rounds = 10
        self.round_duration = 20.0
        self.shuffle_map = [0, 1, 2]
        self.shuffled_answers = [a.text for a in question.answers]
        self._summary = RoundSummary(
            question=question,
            correct_answer=question.answers[0],
            fun_fact=question.fun_fact,
        )
        if hint is not None:
            self.peek_next_image_url = lambda: hint  # noqa: E731

    def get_players(self) -> list:
        return []

    def get_ranked_participants(self) -> list:
        return []

    def get_player_shuffle(self, player_name: str) -> list[int]:
        return self.shuffle_map

    def get_round_summary(self) -> RoundSummary:
        return self._summary


class TestBuilderWiring:
    def test_builder_puts_the_hint_on_the_broadcast(self) -> None:
        msg = RoundMessageBuilder().build_round_summary(
            _FakeGameState(_question("q1"), hint=IMG_NEXT)
        )
        assert msg["next_image_url"] == IMG_NEXT

    def test_a_state_without_the_peek_still_builds(self) -> None:
        """Several tests drive the builder with a lightweight double.

        They implement only what they exercise, so the hint is resolved
        through ``getattr`` — those states simply get no hint rather than an
        AttributeError mid-broadcast.
        """
        msg = RoundMessageBuilder().build_round_summary(
            _FakeGameState(_question("q1"), hint=None)
        )
        assert "next_image_url" not in msg


# ---------------------------------------------------------------------------
# The clients: warm it, never show it
# ---------------------------------------------------------------------------


PLAYER_GAME = WWW / "js" / "player-game.js"
PLAYER_CORE = WWW / "js" / "player-core.js"
DASHBOARD = WWW / "dashboard.html"


class TestClientsPreload:
    def test_player_core_warms_the_hint_on_round_summary(self) -> None:
        src = PLAYER_CORE.read_text("utf-8")
        block = src.split("case 'round_summary':", 1)[1][:800]
        assert "next_image_url" in block
        assert "preloadNextImage" in block

    def test_player_exposes_the_preload_helper(self) -> None:
        src = PLAYER_GAME.read_text("utf-8")
        assert "function preloadNextImage(" in src
        assert "preloadNextImage: preloadNextImage" in src

    def test_television_warms_the_hint_on_round_summary(self) -> None:
        src = DASHBOARD.read_text("utf-8")
        assert "function preloadNextImage(" in src
        assert "preloadNextImage(msg.next_image_url)" in src

    @pytest.mark.parametrize("path", [PLAYER_GAME, DASHBOARD])
    def test_preload_uses_a_detached_image(self, path: Path) -> None:
        """The one rule that keeps a progressive-reveal round honest (#434).

        A preload that wrote to the banner element would paint the NEXT
        question over the reveal the room is still looking at, unblurred —
        the blur is applied by the render path, and that has not run yet.
        ``new Image()`` never enters the document, so there is nothing to
        show and nothing to blur.
        """
        body = _function_body(path.read_text("utf-8"), "preloadNextImage")
        assert "new Image()" in body
        # Comments stripped: both helpers *name* the banner element to explain
        # why they stay away from it, and the point is that the code does not
        # touch it.
        code = _strip_comments(body)
        assert "getElementById" not in code
        assert "questionImage" not in code
        assert "question-image" not in code
        assert "image-zoom-img" not in code

    @pytest.mark.parametrize("path", [PLAYER_GAME, DASHBOARD])
    def test_preload_sanitizes_the_url(self, path: Path) -> None:
        """A hint off the wire gets no more trust than image_url (#536/#540)."""
        body = _function_body(path.read_text("utf-8"), "preloadNextImage")
        assert "safeImageUrl" in body

    @pytest.mark.parametrize("path", [PLAYER_GAME, DASHBOARD])
    def test_preload_keeps_a_reference_to_the_image(self, path: Path) -> None:
        """A detached Image with no live reference can be collected mid-request.

        Some browsers then abandon the fetch, which turns the whole preload
        into a no-op that still looks correct in review.
        """
        body = _function_body(path.read_text("utf-8"), "preloadNextImage")
        assert re.search(r"preloadedImage\s*=\s*img", body, re.IGNORECASE)


def _strip_comments(src: str) -> str:
    """Drop ``//`` line comments so prose about an element isn't read as use."""
    return "\n".join(re.sub(r"//.*$", "", line) for line in src.splitlines())


def _function_body(src: str, name: str) -> str:
    """Extract ``function <name>(...) { ... }`` by brace balance."""
    start = src.index(f"function {name}(")
    depth = 0
    for i in range(src.index("{", start), len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start : i + 1]
    raise AssertionError(f"unbalanced braces in {name}")

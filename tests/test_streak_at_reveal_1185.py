"""The streak comes with the reveal, never before it (#1185, P1).

The server scores an answer on the tap. A streak milestone used to go out right
there, on four channels at once: the phone toasted "3 in a row!", the voice
said "Lena has 3 in a row!", the lights double-pulsed and the speaker rang the
streak chime — while the others were still answering. Anyone in the room knew
Lena's answer was right.

Now the WS handler holds the milestone until the reveal: the voice appends it
to the reveal sentence, and ``quizify_streak_milestone`` fires right after
``quizify_answer_revealed`` (same payload, later moment — a breaking change for
automations, which is why it is in the changelog).

These tests run the real consumers (announcer, emitter, lights, sounds) against
a fake HA that records everything instead of executing it.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from custom_components.quizify.game.questions import Answer, Question  # noqa: E402
from custom_components.quizify.game.state import (  # noqa: E402
    AnswerResult,
    GamePhase,
    RoundSummary,
)
from custom_components.quizify.game_events import QuizifyEventEmitter  # noqa: E402
from custom_components.quizify.house_settings import HouseSettings  # noqa: E402
from custom_components.quizify.lights import QuizifyPartyLights  # noqa: E402
from custom_components.quizify.server.round_message_builder import (  # noqa: E402
    _streak_milestones,
)
from custom_components.quizify.server.websocket import (  # noqa: E402
    QuizifyWebSocketHandler,
)
from custom_components.quizify.sound_effects import QuizifySoundEffects  # noqa: E402
from custom_components.quizify.tts import QuizifyTTSAnnouncer  # noqa: E402
from tests.signal_fakes import FakeHass  # noqa: E402

_JS = _REPO_ROOT / "custom_components" / "quizify" / "www" / "js"


class _FakeRuntime:
    def __init__(self, tmp_path: Path) -> None:
        self.data_dir = tmp_path


def _ws() -> MagicMock:
    ws = MagicMock()
    ws.closed = False
    ws.send_json = AsyncMock()
    ws.close = AsyncMock()
    return ws


def _question() -> Question:
    return Question(
        id="q3",
        question="Welcher Fisch wechselt zwischen Salz- und Süßwasser?",
        answers=[Answer("Europäischer Aal", True), Answer("Wels", False)],
    )


def _result(name: str, *, streak: int, milestone: int) -> AnswerResult:
    return AnswerResult(
        player_id=name,
        correct=True,
        points_earned=48,
        new_streak=streak,
        new_total=90,
        milestone_bonus=milestone,
        milestone_streak=streak if milestone else 0,
    )


class _Game:
    """The slice of ``QuizifyGameState`` the answer path and the reveal read."""

    def __init__(self, results: list[AnswerResult]) -> None:
        self.phase = GamePhase.QUESTION_ACTIVE
        self.round = 3
        self.total_rounds = 10
        self.language = "de"
        self.game_id = "g1"
        self._results = results
        self._question = _question()
        self._player = SimpleNamespace(name="Lena")
        self._next: AnswerResult | None = None

    # answer path
    def get_player_by_connection(self, _ws):
        return self._player

    def get_current_question(self):
        return self._question

    def get_player_shuffle(self, _name):
        return [0, 1]

    def submit_answer(self, name, _index):
        return next(r for r in self._results if r.player_id == name)

    # reveal
    def get_round_summary(self) -> RoundSummary:
        return RoundSummary(
            question=self._question,
            correct_answer=self._question.answers[0],
            fun_fact="",
            results=self._results,
            leaderboard=[SimpleNamespace(name="Lena", score=90)],
        )

    # announcer / lights phase hooks
    def register_state_callback(self, _cb):
        pass

    def unregister_state_callback(self, _cb):
        pass

    def __getattr__(self, name):
        # Everything else the handler's side paths read (the progress
        # tracker, the scoring status) — inert.
        return MagicMock()


@pytest.fixture
def room(tmp_path: Path):
    """A handler wired to the real consumers, all talking to one FakeHass."""
    hass = FakeHass()
    game = _Game([_result("Lena", streak=3, milestone=25)])
    settings = HouseSettings(
        house_enabled=True,
        light_entities=["light.wohnzimmer"],
        media_player="media_player.wohnzimmer",
        tts_entity="tts.cloud",
        cue_urls={
            "correct": "http://x/correct.mp3",
            "wrong": "http://x/wrong.mp3",
            "streak": "http://x/streak.mp3",
            "winner": "http://x/winner.mp3",
        },
    )
    announcer = QuizifyTTSAnnouncer(hass, game, settings=settings)
    announcer.configure(
        enabled=True,
        announce_question=True,
        announce_reveal=True,
        announce_standings=True,
    )
    emitter = QuizifyEventEmitter(hass, game, settings=settings)
    lights = QuizifyPartyLights(hass, game, settings=settings)
    lights.attach_events()
    sounds = QuizifySoundEffects(hass, game, settings=settings)
    sounds.attach_events()

    handler = QuizifyWebSocketHandler(
        runtime=_FakeRuntime(tmp_path), game_state_provider=lambda: game
    )
    handler._conn = MagicMock()
    handler._conn.send = AsyncMock()
    handler._conn.broadcast = AsyncMock()
    handler.set_tts_announcer(announcer)
    handler.set_event_emitter(emitter)
    handler.set_party_lights(lights)
    handler.set_sound_effects(sounds)
    return SimpleNamespace(
        hass=hass, game=game, handler=handler, announcer=announcer, lights=lights
    )


async def _answer(room) -> None:
    await room.handler._handle_submit_answer(_ws(), {"answer_index": 0}, room.game)
    await room.hass.settle()


async def _cleanup(room) -> None:
    task = room.handler._progress_flush_task
    if task is not None:
        task.cancel()
    await room.hass.drain()


@pytest.mark.asyncio
async def test_no_verdict_signal_while_question_live(room) -> None:
    """The rule from the review's section 3: while a question is live, no
    channel says anything about right or wrong."""
    try:
        await _answer(room)
        assert room.game.phase is GamePhase.QUESTION_ACTIVE
        assert room.hass.room() == [], room.hass.names()
    finally:
        await _cleanup(room)


@pytest.mark.asyncio
async def test_the_answer_result_frame_still_names_the_milestone(room) -> None:
    """The phone stores it silently for the reveal card; the frame itself is
    private to the player who tapped."""
    try:
        await _answer(room)
        frame = room.handler._conn.send.await_args_list[0].args[1]
        assert frame["type"] == "answer_result"
        assert frame["milestone_streak"] == 3
    finally:
        await _cleanup(room)


@pytest.mark.asyncio
async def test_the_reveal_carries_the_streak(room) -> None:
    try:
        await _answer(room)
        room.game.phase = GamePhase.ANSWER_REVEAL
        room.handler.reveal(room.game)
        await room.hass.settle()

        speech = room.hass.of("tts.speak")
        assert len(speech) == 1, room.hass.names()
        message = speech[0].data["message"]
        assert message.startswith("Die richtige Antwort ist Europäischer Aal.")
        assert "Lena hatte recht." in message
        assert "Lena hat jetzt 3 in Folge." in message

        events = [s.name for s in room.hass.signals if s.kind == "event"]
        assert events == ["quizify_answer_revealed", "quizify_streak_milestone"]
        assert room.hass.of("quizify_streak_milestone")[0].data == {
            "player_name": "Lena",
            "streak": 3,
            "bonus": 25,
        }
    finally:
        await _cleanup(room)


@pytest.mark.asyncio
async def test_a_milestone_is_said_once(room) -> None:
    try:
        await _answer(room)
        room.handler.reveal(room.game)
        room.handler.reveal(room.game)
        await room.hass.settle()
        assert len(room.hass.of("quizify_streak_milestone")) == 1
        spoken = " ".join(s.data["message"] for s in room.hass.of("tts.speak"))
        assert spoken.count("in Folge") == 1
    finally:
        await _cleanup(room)


@pytest.mark.asyncio
async def test_a_stale_milestone_never_reaches_a_later_reveal(room) -> None:
    """Held in round 3, but the next question (round 4) went out before any
    reveal of round 3 — a reset or a skipped reveal. It is dropped, not said
    about a different question."""
    try:
        await _answer(room)
        room.handler.question_shown(_question(), 4, 10, ["A", "B"])
        room.game.round = 4
        room.hass.clear()
        room.handler.reveal(room.game)
        await room.hass.settle()
        assert room.hass.of("quizify_streak_milestone") == []
        assert all("in Folge" not in s.data["message"] for s in room.hass.of("tts.speak"))
    finally:
        await _cleanup(room)


def test_two_players_on_one_level_share_one_line() -> None:
    hass = FakeHass()
    game = _Game(
        [_result("Lena", streak=3, milestone=25), _result("Tom", streak=3, milestone=25)]
    )
    announcer = QuizifyTTSAnnouncer(
        hass, game, tts_entity_id="tts.x", media_player_entity_id="media_player.y"
    )
    announcer.configure(
        enabled=True,
        announce_question=True,
        announce_reveal=True,
        announce_standings=True,
    )
    frags = announcer._streak_fragments("de", [("Lena", 3), ("Tom", 3), ("Ida", 5)])
    assert frags == [
        "Lena und Tom haben jetzt 3 in Folge.",
        "Ida hat jetzt 5 in Folge.",
    ]


def test_the_milestone_toggle_silences_the_streak_clause() -> None:
    game = _Game([])
    announcer = QuizifyTTSAnnouncer(
        FakeHass(), game, tts_entity_id="tts.x", media_player_entity_id="media_player.y"
    )
    announcer.configure(
        enabled=True,
        announce_question=True,
        announce_reveal=True,
        announce_standings=True,
        announce_milestone=False,
    )
    assert announcer._streak_fragments("de", [("Lena", 3)]) == []


@pytest.mark.parametrize("lang", ["de", "en", "es"])
def test_the_streak_clause_exists_in_every_language(lang: str) -> None:
    from custom_components.quizify.game import tts_phrases

    single = tts_phrases.phrase(lang, "streak_now_single", names="Lena", streak=3)
    multi = tts_phrases.phrase(lang, "streak_now_multi", names="Lena y Tom", streak=3)
    assert "3" in single and "Lena" in single
    assert "3" in multi and single != multi


def test_the_tv_learns_the_streaks_from_the_round_summary() -> None:
    game = _Game(
        [_result("Lena", streak=3, milestone=25), _result("Ben", streak=1, milestone=0)]
    )
    assert _streak_milestones(game.get_round_summary()) == [
        {"name": "Lena", "streak": 3, "streak_active": True}
    ]


def test_the_tv_shows_the_streak_only_from_the_round_summary() -> None:
    """The TV strip and chip are painted from ``round_summary`` and cleared
    when the next question starts."""
    source = (_JS / "dashboard.js").read_text("utf-8")
    start = source.index("function handleRoundSummary(msg) {")
    assert "showStreakMilestones(summary.streak_milestones)" in source[start : start + 1400]
    q = source.index("function handleQuestionStarted(msg) {")
    assert "revealMilestones = {};" in source[q : q + 200]


def test_no_room_signal_is_scheduled_by_the_answer_path() -> None:
    """Belt and braces for the handler: the answer path only queues."""
    source = (
        _REPO_ROOT / "custom_components" / "quizify" / "server" / "answer_handlers.py"
    ).read_text("utf-8")
    assert "announce_milestone" not in source
    assert "notify_streak_milestone" not in source


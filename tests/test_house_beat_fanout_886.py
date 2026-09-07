"""The handler's house-beat fan-out table (#886).

One game moment used to travel through three differently-named layers inside
``server/websocket.py``: a tick arrived as ``time_running_out``, became
``_notify_countdown``, and ended in ``_notify_tts_countdown`` plus
``_notify_house_time_running_out``. The ten leaves were the same eight lines
each, and two of the six beats skipped the middle layer, so nobody could tell
whether that layer was a contract or an accident.

It is one method per beat now, each one delivering through ``_fire``. That
collapses a lot of typing — and with it the compile-time check that each
forwarder called the right method on the right consumer, because ``_fire``
looks the method up by name. This file is that check: the table below is the
whole fan-out, written out as data, so dropping a leg of it (or renaming a
consumer method without renaming the beat that calls it) fails here rather
than going quiet in production.

``_fire`` swallows and logs whatever the consumer raises, which is exactly why
a wrong method name would otherwise be invisible: ``getattr`` raises
``AttributeError``, ``_fire`` catches it, and the beat looks delivered. The
recorders below therefore declare the real method names and nothing else.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from custom_components.quizify.server.websocket import (  # noqa: E402
    QuizifyWebSocketHandler,
)

_WS_LOGGER = "custom_components.quizify.server.websocket"


class _FakeRuntime:
    def __init__(self, tmp_path: Path) -> None:
        self.data_dir = tmp_path

    def create_task(self, coro):
        return asyncio.ensure_future(coro)


class _Recorder:
    """Records ``(method, args)`` for exactly the methods it declares.

    Declaring them explicitly is the point: an undeclared name raises
    ``AttributeError`` inside ``_fire``, which is swallowed, so the beat that
    called it records nothing and its assertion fails.
    """

    _METHODS: tuple[str, ...] = ()

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple]] = []

    def __init_subclass__(cls, **kwargs) -> None:
        super().__init_subclass__(**kwargs)
        for name in cls._METHODS:
            setattr(cls, name, cls._make(name))

    @staticmethod
    def _make(name: str):
        def _record(self, *args) -> None:
            self.calls.append((name, args))

        _record.__name__ = name
        return _record


class _Announcer(_Recorder):
    """The narrator's surface (``tts.QuizifyTTSAnnouncer``)."""

    _METHODS = (
        "announce_question",
        "announce_countdown",
        "announce_reveal",
        "announce_milestone",
        "announce_join",
    )


class _Emitter(_Recorder):
    """The HA bus emitter's surface (``game_events.QuizifyEventEmitter``)."""

    _METHODS = (
        "notify_question_shown",
        "notify_time_running_out",
        "notify_answer_revealed",
        "notify_streak_milestone",
        "notify_game_ended",
    )


class _Question:
    """Stand-in — the beats pass the question through without reading it."""


_QUESTION = _Question()
_GAME_STATE = object()

# beat name -> (args it is called with, what the narrator hears, what the bus hears)
_FANOUT: list[tuple[str, tuple, list[tuple[str, tuple]], list[tuple[str, tuple]]]] = [
    (
        "question_shown",
        (_QUESTION, 3, 10, ["Berlin", "Paris"]),
        [("announce_question", (_QUESTION, 3, 10, ["Berlin", "Paris"]))],
        # The bus deliberately does NOT get the option texts: that would leak
        # the answer board to automations before the players see it.
        [("notify_question_shown", (_QUESTION, 3, 10))],
    ),
    (
        "time_running_out",
        (7.5,),
        [("announce_countdown", (7.5,))],
        [("notify_time_running_out", (7.5,))],
    ),
    (
        "reveal",
        (_GAME_STATE,),
        [("announce_reveal", (_GAME_STATE,))],
        [("notify_answer_revealed", (_GAME_STATE,))],
    ),
    (
        "_streak_milestone",
        ("Anna", 5, 40),
        # The announcer has no use for the bonus points; the bus event carries
        # them because an automation might.
        [("announce_milestone", ("Anna", 5))],
        [("notify_streak_milestone", ("Anna", 5, 40))],
    ),
    # Narration only — there is no quizify_player_joined event.
    ("_player_joined", ("Anna", False), [("announce_join", ("Anna", False))], []),
    # Bus only — the finale has no spoken line of its own.
    ("_game_ended", (_GAME_STATE,), [], [("notify_game_ended", (_GAME_STATE,))]),
]

_IDS = [beat for beat, _args, _tts, _bus in _FANOUT]


@pytest.fixture
def handler(tmp_path: Path) -> QuizifyWebSocketHandler:
    return QuizifyWebSocketHandler(
        runtime=_FakeRuntime(tmp_path), game_state_provider=lambda: None
    )


@pytest.mark.parametrize(("beat", "args", "tts", "bus"), _FANOUT, ids=_IDS)
def test_each_beat_reaches_exactly_its_consumers(
    handler: QuizifyWebSocketHandler, beat, args, tts, bus
) -> None:
    announcer = _Announcer()
    emitter = _Emitter()
    handler.set_tts_announcer(announcer)
    handler.set_event_emitter(emitter)

    getattr(handler, beat)(*args)

    assert announcer.calls == tts
    assert emitter.calls == bus


@pytest.mark.parametrize(("beat", "args", "tts", "bus"), _FANOUT, ids=_IDS)
def test_every_beat_is_a_silent_no_op_with_nothing_wired(
    handler: QuizifyWebSocketHandler, beat, args, tts, bus
) -> None:
    """The standalone dev server and an HA install without TTS (#281/#366).

    ``None`` is a normal state for both consumers, so a beat must not raise and
    must not need an Optional threaded through the dispatch point that fires it.
    """
    getattr(handler, beat)(*args)


@pytest.mark.parametrize(("beat", "args", "tts", "bus"), _FANOUT, ids=_IDS)
def test_one_broken_consumer_does_not_cost_the_other_its_beat(
    handler: QuizifyWebSocketHandler, beat, args, tts, bus, caplog
) -> None:
    """A broken TTS entity is not a reason to stall a game loop.

    The guard is per consumer, not per beat: the narrator blowing up must not
    take the bus event with it, and neither may reach the caller — the
    ``MilestoneSink`` contract says these never raise.
    """

    class _Exploding(_Announcer):
        # Empty, so __init_subclass__ leaves the raising methods below in place
        # instead of replacing them with recorders.
        _METHODS = ()

        def announce_question(self, *args) -> None:
            raise RuntimeError("the TTS entity is gone")

        announce_countdown = announce_question
        announce_reveal = announce_question
        announce_milestone = announce_question
        announce_join = announce_question

    emitter = _Emitter()
    handler.set_tts_announcer(_Exploding())
    handler.set_event_emitter(emitter)

    with caplog.at_level(logging.ERROR, logger=_WS_LOGGER):
        getattr(handler, beat)(*args)

    assert emitter.calls == bus
    if tts:
        assert "House beat" in caplog.text

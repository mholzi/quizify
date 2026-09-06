"""Tests for the HA event backbone — "The House Plays Along" (#366).

QuizifyEventEmitter fires ``quizify_*`` events on ``hass.bus`` at every game
milestone so the host can drive automations. Here a fake hass records
``bus.async_fire`` calls so we assert *intent* (event type + payload keys)
without real Home Assistant.

Covers: every milestone fires the right event with the right payload; the
whole thing no-ops when ``hass is None`` (standalone dev server); phase-driven
events fire only on transitions and only for the relevant phases; and the
options-reload phase snapshot survives a rebuild.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from custom_components.quizify.game.questions import Answer, Question  # noqa: E402
from custom_components.quizify.game.state import (  # noqa: E402
    AnswerResult,
    GamePhase,
    RoundSummary,
)
from custom_components.quizify.game_events import (  # noqa: E402
    EVENT_ANSWER_REVEALED,
    EVENT_FINALE_STARTED,
    EVENT_GAME_ENDED,
    EVENT_GAME_STARTED,
    EVENT_QUESTION_SHOWN,
    EVENT_STREAK_MILESTONE,
    EVENT_WINNER_DECIDED,
    QuizifyEventEmitter,
)
from custom_components.quizify.house_settings import HouseSettings  # noqa: E402


class _FakeBus:
    """Records async_fire calls."""

    def __init__(self) -> None:
        self.fired: list[tuple[str, dict]] = []

    def async_fire(self, event_type, data):  # noqa: ANN001
        self.fired.append((event_type, dict(data)))


class _FakeHass:
    def __init__(self) -> None:
        self.bus = _FakeBus()


class _FakePlayer:
    def __init__(self, name: str, score: int) -> None:
        self.name = name
        self.score = score


class _FakeGame:
    """Minimal game stand-in exposing only what the emitter reads.

    Avoids spinning a full QuizifyGameState (which needs websockets to add
    players) — the emitter only touches phase / round / game_id / total_rounds
    / get_players / leader / get_round_summary / get_leaderboard.
    """

    def __init__(self) -> None:
        self.phase = GamePhase.LOBBY
        self.round = 0
        self.total_rounds = 10
        self.game_id: str | None = None
        self._players: list[_FakePlayer] = []
        self.leader: _FakePlayer | None = None
        self._summary: RoundSummary | None = None
        self._leaderboard: list[dict] = []
        # register/unregister are exercised by attach()/detach().
        self.callbacks: list = []

    def register_state_callback(self, cb):  # noqa: ANN001
        self.callbacks.append(cb)

    def unregister_state_callback(self, cb):  # noqa: ANN001
        if cb in self.callbacks:
            self.callbacks.remove(cb)

    def get_players(self):
        return list(self._players)

    def get_round_summary(self):
        return self._summary

    def get_leaderboard(self):
        return list(self._leaderboard)


def _emitter(hass, game):
    return QuizifyEventEmitter(hass=hass, game_state=game)


def _question(qtype="multiple_choice"):
    return Question(
        id="q1",
        question="Wer malte die Mona Lisa?",
        answers=[
            Answer(text="Michelangelo", correct=False),
            Answer(text="da Vinci", correct=True),
            Answer(text="Raffael", correct=False),
        ],
        type=qtype,
    )


def _summary(*, results=None):
    return RoundSummary(
        question=_question(),
        correct_answer=Answer(text="da Vinci", correct=True),
        fun_fact="",
        results=results or [],
    )


def _result(name, correct):
    return AnswerResult(
        player_id=name,
        correct=correct,
        points_earned=100 if correct else 0,
        new_streak=1 if correct else 0,
        new_total=100 if correct else 0,
    )


# ---------------------------------------------------------------------------
# No-op posture (hass is None)
# ---------------------------------------------------------------------------


def test_no_hass_is_noop_everywhere():
    """Every path must no-op cleanly on the standalone dev server (no hass)."""
    game = _FakeGame()
    t = _emitter(None, game)
    assert t.is_configured is False

    # Phase-driven path.
    game.game_id = "g1"
    game.round = 1
    game.phase = GamePhase.QUESTION_ACTIVE
    t._on_state_changed()

    # WS-dispatch path.
    t.notify_question_shown(_question(), 1, 10)
    t.notify_streak_milestone("Alice", 5, 50)
    game._summary = _summary(results=[_result("Alice", True)])
    t.notify_answer_revealed(game)
    game._leaderboard = [{"name": "Alice", "score": 100}]
    t.notify_game_ended(game)
    # No bus at all → nothing to assert but that it didn't raise.


# ---------------------------------------------------------------------------
# Master toggle (CONF_HOUSE_EVENTS_ENABLED, default off) — #366
# ---------------------------------------------------------------------------


def test_disabled_emitter_is_not_configured():
    """enabled=False → not configured even with a real hass/bus."""
    t = QuizifyEventEmitter(hass=_FakeHass(), game_state=_FakeGame(), enabled=False)
    assert t.is_configured is False


def test_disabled_emitter_fires_nothing_on_any_path():
    """The master toggle gates every path — phase-driven and WS forwarders."""
    hass = _FakeHass()
    game = _FakeGame()
    game._players = [_FakePlayer("Alice", 0)]
    game.game_id = "g1"
    game.leader = _FakePlayer("Alice", 100)
    t = QuizifyEventEmitter(hass=hass, game_state=game, enabled=False)

    # Phase-driven path: game start + finale/winner.
    game.round = 1
    game.phase = GamePhase.QUESTION_ACTIVE
    t._on_state_changed()
    game.phase = GamePhase.FINALE
    t._on_state_changed()

    # WS-dispatch path: every forwarder.
    t.notify_question_shown(_question(), 1, 10)
    t.notify_streak_milestone("Alice", 5, 50)
    game._summary = _summary(results=[_result("Alice", True)])
    t.notify_answer_revealed(game)
    game._leaderboard = [{"name": "Alice", "score": 100}]
    t.notify_game_ended(game)

    assert hass.bus.fired == []


def test_enabled_by_default_still_fires():
    """The constructor default stays on so the default helper keeps firing;
    product 'off by default' is enforced at the config layer, not here."""
    hass = _FakeHass()
    game = _FakeGame()
    game.game_id = "g1"
    t = QuizifyEventEmitter(hass=hass, game_state=game)  # no enabled= → default
    assert t.is_configured is True
    game.round = 1
    game.phase = GamePhase.QUESTION_ACTIVE
    t._on_state_changed()
    assert [e for e, _ in hass.bus.fired] == [EVENT_GAME_STARTED]


# ---------------------------------------------------------------------------
# Phase-driven milestones (state-callback path)
# ---------------------------------------------------------------------------


def test_game_started_fires_on_first_active_round():
    hass = _FakeHass()
    game = _FakeGame()
    game._players = [_FakePlayer("Alice", 0), _FakePlayer("Bob", 0)]
    game.game_id = "g1"
    game.total_rounds = 8
    t = _emitter(hass, game)

    # LOBBY → QUESTION_ACTIVE round 1.
    game.round = 1
    game.phase = GamePhase.QUESTION_ACTIVE
    t._on_state_changed()

    assert len(hass.bus.fired) == 1
    event, data = hass.bus.fired[0]
    assert event == EVENT_GAME_STARTED
    assert data == {"game_id": "g1", "player_count": 2, "round_count": 8}


def test_game_started_only_once_no_refire_on_flap():
    hass = _FakeHass()
    game = _FakeGame()
    game.round = 1
    game.phase = GamePhase.QUESTION_ACTIVE
    t = _emitter(hass, game)
    t._on_state_changed()
    t._on_state_changed()  # same phase → no re-fire
    assert [e for e, _ in hass.bus.fired] == [EVENT_GAME_STARTED]


def test_no_game_started_mid_game():
    """A later round entering QUESTION_ACTIVE must not fire game_started."""
    hass = _FakeHass()
    game = _FakeGame()
    t = _emitter(hass, game)
    # Simulate reveal → next question at round 5.
    game.round = 5
    game.phase = GamePhase.ANSWER_REVEAL
    t._on_state_changed()
    game.phase = GamePhase.QUESTION_ACTIVE
    t._on_state_changed()
    assert EVENT_GAME_STARTED not in [e for e, _ in hass.bus.fired]


def test_finale_fires_finale_and_winner():
    hass = _FakeHass()
    game = _FakeGame()
    game.leader = _FakePlayer("Bob", 420)
    game.phase = GamePhase.QUESTION_ACTIVE  # prime prev phase
    t = _emitter(hass, game)
    t._on_state_changed()
    hass.bus.fired.clear()

    game.phase = GamePhase.FINALE
    t._on_state_changed()

    events = [e for e, _ in hass.bus.fired]
    assert events == [EVENT_FINALE_STARTED, EVENT_WINNER_DECIDED]
    _, winner = hass.bus.fired[1]
    assert winner == {"winner_name": "Bob", "score": 420}


def test_finale_without_leader_skips_winner():
    hass = _FakeHass()
    game = _FakeGame()
    game.leader = None
    game.phase = GamePhase.FINALE
    t = _emitter(hass, game)
    t._on_state_changed()
    assert [e for e, _ in hass.bus.fired] == [EVENT_FINALE_STARTED]


def test_irrelevant_phase_fires_nothing():
    """PAUSED / plain reveal transitions carry no house event of their own."""
    hass = _FakeHass()
    game = _FakeGame()
    game.round = 3
    game.phase = GamePhase.QUESTION_ACTIVE
    t = _emitter(hass, game)
    t._on_state_changed()
    hass.bus.fired.clear()

    game.phase = GamePhase.PAUSED
    t._on_state_changed()
    game.phase = GamePhase.QUESTION_ACTIVE
    t._on_state_changed()
    game.phase = GamePhase.ANSWER_REVEAL
    t._on_state_changed()
    assert hass.bus.fired == []


# ---------------------------------------------------------------------------
# Per-event milestones (WS-dispatch path)
# ---------------------------------------------------------------------------


def test_question_shown_payload():
    hass = _FakeHass()
    game = _FakeGame()
    t = _emitter(hass, game)
    t.notify_question_shown(_question("estimate"), 3, 10)
    event, data = hass.bus.fired[0]
    assert event == EVENT_QUESTION_SHOWN
    assert data == {"round": 3, "total_rounds": 10, "question_type": "estimate"}


def test_answer_revealed_payload():
    hass = _FakeHass()
    game = _FakeGame()
    game.round = 4
    game._summary = _summary(
        results=[
            _result("Alice", True),
            _result("Bob", False),
            _result("Cara", True),
        ]
    )
    t = _emitter(hass, game)
    t.notify_answer_revealed(game)
    event, data = hass.bus.fired[0]
    assert event == EVENT_ANSWER_REVEALED
    assert data == {
        "round": 4,
        "correct_option_index": 1,  # "da Vinci" is index 1 in answers
        "correct_option_text": "da Vinci",
        "correct_count": 2,
        "total_players": 3,
    }


def test_answer_revealed_no_summary_noop():
    hass = _FakeHass()
    game = _FakeGame()
    game._summary = None
    t = _emitter(hass, game)
    t.notify_answer_revealed(game)
    assert hass.bus.fired == []


def test_streak_milestone_payload():
    hass = _FakeHass()
    game = _FakeGame()
    t = _emitter(hass, game)
    t.notify_streak_milestone("Alice", 5, 50)
    event, data = hass.bus.fired[0]
    assert event == EVENT_STREAK_MILESTONE
    assert data == {"player_name": "Alice", "streak": 5, "bonus": 50}


def test_game_ended_leaderboard_trimmed():
    hass = _FakeHass()
    game = _FakeGame()
    # The real serialize_leaderboard carries many keys; the event keeps only
    # name+score.
    game._leaderboard = [
        {"name": "Bob", "score": 420, "rank": 1, "streak": 3, "submitted": True},
        {"name": "Alice", "score": 300, "rank": 2, "streak": 0},
    ]
    t = _emitter(hass, game)
    t.notify_game_ended(game)
    event, data = hass.bus.fired[0]
    assert event == EVENT_GAME_ENDED
    assert data == {
        "leaderboard": [
            {"name": "Bob", "score": 420},
            {"name": "Alice", "score": 300},
        ]
    }


# ---------------------------------------------------------------------------
# Lifecycle: attach/detach + options-reload snapshot
# ---------------------------------------------------------------------------


def test_attach_detach_registers_callback():
    game = _FakeGame()
    t = _emitter(_FakeHass(), game)
    t.attach()
    assert t._on_state_changed in game.callbacks
    t.detach()
    assert t._on_state_changed not in game.callbacks


def test_options_reload_does_not_re_fire_game_started():
    """An options reload must not replay ``quizify_game_started`` (#411/#789).

    The emitter used to be rebuilt from the config entry on every options
    change, which reset ``_last_phase`` — so a reload landing exactly on the
    first active round could fire the start event a second time. It carried an
    ``export/restore_runtime_state`` pair purely to close that gap. The emitter
    now survives the reload (only the settings it reads are refreshed), so the
    phase tracking is simply never lost.
    """
    hass = _FakeHass()
    game = _FakeGame()
    settings = HouseSettings(house_enabled=True)
    t = QuizifyEventEmitter(hass=hass, game_state=game, settings=settings)
    game.round = 1
    game.phase = GamePhase.QUESTION_ACTIVE
    t._on_state_changed()  # sets _last_phase, fires game_started
    assert [e for e, _ in hass.bus.fired] == ["quizify_game_started"]

    # The options reload: the shared settings are updated in place.
    hass.bus.fired.clear()
    settings.update_from_options({"house_events_enabled": True})

    t._on_state_changed()
    assert hass.bus.fired == []


def test_options_reload_keeps_the_panel_master():
    """A panel master set mid-game outlives an options change, and an untouched
    one leaves the config-entry switch in charge (#494 P4 / #789)."""
    settings = HouseSettings(house_enabled=False)
    t = QuizifyEventEmitter(
        hass=_FakeHass(), game_state=_FakeGame(), settings=settings
    )
    assert t._master_enabled is False

    t.configure(enabled=True)  # the panel turns the house on mid-game
    settings.update_from_options({})  # an unrelated options change
    assert t._master_enabled is True
    assert t._enabled_override is True

    # A never-panelled emitter follows the options switch immediately.
    fresh_settings = HouseSettings(house_enabled=False)
    fresh = QuizifyEventEmitter(
        hass=_FakeHass(), game_state=_FakeGame(), settings=fresh_settings
    )
    assert fresh._enabled_override is None
    fresh_settings.update_from_options({"house_events_enabled": True})
    assert fresh._master_enabled is True


@pytest.mark.parametrize(
    "event_name",
    [
        EVENT_GAME_STARTED,
        EVENT_QUESTION_SHOWN,
        EVENT_ANSWER_REVEALED,
        EVENT_STREAK_MILESTONE,
        EVENT_FINALE_STARTED,
        EVENT_WINNER_DECIDED,
        EVENT_GAME_ENDED,
    ],
)
def test_event_names_are_namespaced(event_name):
    assert event_name.startswith("quizify_")

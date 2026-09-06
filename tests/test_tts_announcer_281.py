"""Tests for the v1 narration paths of QuizifyTTSAnnouncer (issue #281).

Covers: the master switch gates everything; question narration renders the
right phrase in de+en; the reveal is a SINGLE combined utterance assembled
from correct-answer / who-got-it / standings fragments, each gated by its own
per-event toggle; leader-change is suppressed on round 1, fires on a real
change, and yields to the tie path; and the global throttle still suppresses a
rapid second utterance.

The announcer's audio routing is HA's job; here a fake hass records the
``tts.speak`` calls so we assert *intent* (message + entity payload) without
real Home Assistant.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from custom_components.quizify.game.questions import Answer, Question  # noqa: E402
from custom_components.quizify.game.state import (  # noqa: E402
    AnswerResult,
    QuizifyGameState,
    RoundSummary,
)
from custom_components.quizify.tts import QuizifyTTSAnnouncer  # noqa: E402


class _FakeHass:
    """Records tts.speak calls so tests can assert message/entity payload."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict]] = []
        self.states = MagicMock()
        self.states.get = lambda eid: MagicMock(state="on")
        self.services = MagicMock()

        async def _async_call(domain, service, data, blocking=False):  # noqa: ANN001
            self.calls.append((domain, service, dict(data)))

        self.services.async_call = _async_call

    def async_create_task(self, coro):  # noqa: ANN001
        return asyncio.ensure_future(coro)


class _FakeRuntime:
    def __init__(self, tmp_path: Path) -> None:
        self.data_dir = tmp_path


@pytest.fixture
def game(tmp_path):
    return QuizifyGameState(runtime=_FakeRuntime(tmp_path), entry_id="t")


def _announcer(hass, game):
    return QuizifyTTSAnnouncer(
        hass=hass,
        tts_entity_id="tts.cloud",
        media_player_entity_id="media_player.kitchen",
        game_state=game,
    )


def _configure(t, *, enabled=True, question=True, reveal=True, standings=True):
    t.configure(
        enabled=enabled,
        announce_question=question,
        announce_reveal=reveal,
        announce_standings=standings,
    )


def _question(text="Wer malte die Mona Lisa?"):
    return Question(id="q1", question=text,
                    answers=[Answer(text="da Vinci", correct=True)])


def _summary(*, answer="da Vinci", results=None, leaderboard=None):
    return RoundSummary(
        question=_question(),
        correct_answer=Answer(text=answer, correct=True),
        fun_fact="",
        results=results or [],
        leaderboard=leaderboard or [],
    )


def _result(name, correct, points=100):
    return AnswerResult(
        player_id=name, correct=correct, points_earned=points,
        new_streak=1, new_total=points,
    )


class _Standing:
    """One row of ``RoundSummary.leaderboard``.

    Participants, not wire rows, since #747 — the narrator reads ``name`` and
    ``score`` off whichever object the ranking is about (a team in team mode,
    a player otherwise).
    """

    def __init__(self, name, score):  # noqa: ANN001
        self.name = name
        self.score = score


def _lb(*pairs):
    """Build the standings for a round, highest score first."""
    ranked = sorted(pairs, key=lambda p: p[1], reverse=True)
    return [_Standing(n, s) for n, s in ranked]


def _last_message(hass):
    assert hass.calls, "expected a tts.speak call"
    domain, service, data = hass.calls[-1]
    assert (domain, service) == ("tts", "speak")
    return data["message"]


class TestMasterSwitchGate:
    @pytest.mark.asyncio
    async def test_disabled_question_emits_nothing(self, game) -> None:
        hass = _FakeHass()
        t = _announcer(hass, game)
        _configure(t, enabled=False)
        t.announce_question(_question(), 1, 10)
        await asyncio.sleep(0)
        assert hass.calls == []

    @pytest.mark.asyncio
    async def test_disabled_reveal_emits_nothing(self, game) -> None:
        hass = _FakeHass()
        t = _announcer(hass, game)
        _configure(t, enabled=False)
        game._round_summary = _summary(
            results=[_result("Alice", True)], leaderboard=_lb(("Alice", 100))
        )
        t.announce_reveal(game)
        await asyncio.sleep(0)
        assert hass.calls == []

    @pytest.mark.asyncio
    async def test_default_state_is_disabled(self, game) -> None:
        # Without any configure() call the master switch defaults OFF.
        hass = _FakeHass()
        t = _announcer(hass, game)
        t.announce_question(_question(), 1, 10)
        await asyncio.sleep(0)
        assert hass.calls == []


class TestQuestionNarration:
    @pytest.mark.asyncio
    async def test_question_phrase_de(self, game) -> None:
        game.language = "de"
        hass = _FakeHass()
        t = _announcer(hass, game)
        _configure(t)
        t.announce_question(_question("Hauptstadt von Frankreich?"), 2, 8)
        await asyncio.sleep(0)
        assert _last_message(hass) == "Frage 2 von 8: Hauptstadt von Frankreich?"

    @pytest.mark.asyncio
    async def test_question_phrase_en(self, game) -> None:
        game.language = "en"
        hass = _FakeHass()
        t = _announcer(hass, game)
        _configure(t)
        t.announce_question(_question("Capital of France?"), 3, 12)
        await asyncio.sleep(0)
        assert _last_message(hass) == "Question 3 of 12: Capital of France?"

    @pytest.mark.asyncio
    async def test_question_toggle_off_suppresses(self, game) -> None:
        hass = _FakeHass()
        t = _announcer(hass, game)
        _configure(t, question=False)
        t.announce_question(_question(), 1, 10)
        await asyncio.sleep(0)
        assert hass.calls == []


class TestRevealCombinedUtterance:
    @pytest.mark.asyncio
    async def test_single_got_it(self, game) -> None:
        game.language = "en"
        hass = _FakeHass()
        t = _announcer(hass, game)
        _configure(t)
        game._round_summary = _summary(
            answer="Berlin",
            results=[_result("Alice", True), _result("Bob", False)],
            leaderboard=_lb(("Alice", 100), ("Bob", 0)),
        )
        t.announce_reveal(game)
        await asyncio.sleep(0)
        # ONE utterance combining answer + who-got-it (round 1 → no standings).
        assert len(hass.calls) == 1
        msg = _last_message(hass)
        assert "The correct answer is Berlin." in msg
        assert "Alice got it right." in msg

    @pytest.mark.asyncio
    async def test_multi_got_it(self, game) -> None:
        game.language = "en"
        hass = _FakeHass()
        t = _announcer(hass, game)
        _configure(t)
        game._round_summary = _summary(
            answer="Berlin",
            results=[_result("Alice", True), _result("Bob", True)],
            leaderboard=_lb(("Alice", 100), ("Bob", 100)),
        )
        t.announce_reveal(game)
        await asyncio.sleep(0)
        assert "Alice and Bob got it right." in _last_message(hass)

    @pytest.mark.asyncio
    async def test_nobody_got_it(self, game) -> None:
        game.language = "en"
        hass = _FakeHass()
        t = _announcer(hass, game)
        _configure(t)
        game._round_summary = _summary(
            answer="Berlin",
            results=[_result("Alice", False), _result("Bob", False)],
            leaderboard=_lb(("Alice", 0), ("Bob", 0)),
        )
        t.announce_reveal(game)
        await asyncio.sleep(0)
        msg = _last_message(hass)
        assert "Nobody got it this round." in msg

    @pytest.mark.asyncio
    async def test_reveal_toggle_off_drops_answer_and_who(self, game) -> None:
        game.language = "en"
        hass = _FakeHass()
        t = _announcer(hass, game)
        # reveal off, standings on. Round 1 has no leader change, so with the
        # answer/who fragments gated out there is nothing to say.
        _configure(t, reveal=False)
        game._round_summary = _summary(
            results=[_result("Alice", True)], leaderboard=_lb(("Alice", 100))
        )
        t.announce_reveal(game)
        await asyncio.sleep(0)
        assert hass.calls == []


class TestStandingsFragment:
    @pytest.mark.asyncio
    async def test_leader_change_suppressed_round_1(self, game) -> None:
        game.language = "en"
        hass = _FakeHass()
        t = _announcer(hass, game)
        # Only standings on, reveal off, so the message is standings-only.
        _configure(t, reveal=False)
        game._round_summary = _summary(leaderboard=_lb(("Alice", 100)))
        t.announce_reveal(game)
        await asyncio.sleep(0)
        # First scored round: leader "changes" from nobody → suppressed.
        assert hass.calls == []

    @pytest.mark.asyncio
    async def test_leader_change_fires_on_real_change(self, game) -> None:
        game.language = "en"
        hass = _FakeHass()
        t = _announcer(hass, game)
        _configure(t, reveal=False)
        # Round 1: Alice leads (suppressed, sets _previous_leader=Alice).
        game._round_summary = _summary(leaderboard=_lb(("Alice", 100), ("Bob", 0)))
        t.announce_reveal(game)
        await asyncio.sleep(0)
        assert hass.calls == []
        # Round 2: Bob overtakes. Reset throttle so the utterance isn't dropped.
        t._last_spoken_at = None
        game._round_summary = _summary(leaderboard=_lb(("Bob", 200), ("Alice", 100)))
        t.announce_reveal(game)
        await asyncio.sleep(0)
        assert "Bob takes the lead!" in _last_message(hass)

    @pytest.mark.asyncio
    async def test_tie_at_top(self, game) -> None:
        game.language = "en"
        hass = _FakeHass()
        t = _announcer(hass, game)
        _configure(t, reveal=False)
        # Establish a leader first so the tie isn't on round 1.
        game._round_summary = _summary(leaderboard=_lb(("Alice", 100), ("Bob", 0)))
        t.announce_reveal(game)
        await asyncio.sleep(0)
        t._last_spoken_at = None
        # Now Alice and Bob are tied at the top.
        game._round_summary = _summary(leaderboard=_lb(("Alice", 200), ("Bob", 200)))
        t.announce_reveal(game)
        await asyncio.sleep(0)
        assert "It's a tie at the top." in _last_message(hass)

    @pytest.mark.asyncio
    async def test_standings_toggle_off_drops_leader_change(self, game) -> None:
        game.language = "en"
        hass = _FakeHass()
        t = _announcer(hass, game)
        # reveal off + standings off → nothing; but detection still tracks.
        _configure(t, reveal=False, standings=False)
        game._round_summary = _summary(leaderboard=_lb(("Alice", 100), ("Bob", 0)))
        t.announce_reveal(game)
        await asyncio.sleep(0)
        t._last_spoken_at = None
        game._round_summary = _summary(leaderboard=_lb(("Bob", 200), ("Alice", 100)))
        t.announce_reveal(game)
        await asyncio.sleep(0)
        # standings toggle off → no leader-change utterance even on real change.
        assert hass.calls == []


class TestEntityOverrides:
    """Per-game entity overrides from the admin dropdowns (#281).

    ``configure(tts_entity=..., media_player=...)`` lets the host pick the TTS
    engine + speaker for this game; empty/None falls back to the
    construction-time config-entry entities. is_configured stays true if EITHER
    source provides both.
    """

    @pytest.mark.asyncio
    async def test_override_used_in_service_call(self, game) -> None:
        game.language = "en"
        hass = _FakeHass()
        t = _announcer(hass, game)  # config-entry: tts.cloud / media_player.kitchen
        _configure(t)
        t.configure(
            enabled=True,
            announce_question=True,
            announce_reveal=True,
            announce_standings=True,
            tts_entity="tts.piper",
            media_player="media_player.living_room",
        )
        t.announce_question(_question("Q?"), 1, 10)
        await asyncio.sleep(0)
        _, _, data = hass.calls[-1]
        assert data["entity_id"] == "tts.piper"
        assert data["media_player_entity_id"] == "media_player.living_room"

    @pytest.mark.asyncio
    async def test_empty_override_falls_back_to_config_entry(self, game) -> None:
        game.language = "en"
        hass = _FakeHass()
        t = _announcer(hass, game)
        # Empty strings (host left the dropdown on "Use default").
        t.configure(
            enabled=True,
            announce_question=True,
            announce_reveal=True,
            announce_standings=True,
            tts_entity="",
            media_player="",
        )
        t.announce_question(_question("Q?"), 1, 10)
        await asyncio.sleep(0)
        _, _, data = hass.calls[-1]
        assert data["entity_id"] == "tts.cloud"
        assert data["media_player_entity_id"] == "media_player.kitchen"

    @pytest.mark.asyncio
    async def test_whitespace_override_falls_back(self, game) -> None:
        """A whitespace-only value must not mask the config-entry fallback."""
        game.language = "en"
        hass = _FakeHass()
        t = _announcer(hass, game)
        t.configure(
            enabled=True,
            announce_question=True,
            announce_reveal=True,
            announce_standings=True,
            tts_entity="   ",
            media_player="   ",
        )
        t.announce_question(_question("Q?"), 1, 10)
        await asyncio.sleep(0)
        _, _, data = hass.calls[-1]
        assert data["entity_id"] == "tts.cloud"
        assert data["media_player_entity_id"] == "media_player.kitchen"

    @pytest.mark.asyncio
    async def test_partial_override_mixes_with_default(self, game) -> None:
        """Override one entity, leave the other empty → mixed pair."""
        game.language = "en"
        hass = _FakeHass()
        t = _announcer(hass, game)
        t.configure(
            enabled=True,
            announce_question=True,
            announce_reveal=True,
            announce_standings=True,
            tts_entity="tts.piper",
            media_player="",
        )
        t.announce_question(_question("Q?"), 1, 10)
        await asyncio.sleep(0)
        _, _, data = hass.calls[-1]
        assert data["entity_id"] == "tts.piper"
        assert data["media_player_entity_id"] == "media_player.kitchen"

    def test_is_configured_true_from_override_only(self, game) -> None:
        """No config-entry entities, but overrides supply both → configured."""
        hass = _FakeHass()
        t = QuizifyTTSAnnouncer(
            hass=hass,
            tts_entity_id=None,
            media_player_entity_id=None,
            game_state=game,
        )
        assert t.is_configured is False
        t.configure(
            enabled=True,
            announce_question=True,
            announce_reveal=True,
            announce_standings=True,
            tts_entity="tts.piper",
            media_player="media_player.living_room",
        )
        assert t.is_configured is True

    def test_is_configured_true_from_config_entry_only(self, game) -> None:
        """No overrides, config-entry supplies both → still configured."""
        hass = _FakeHass()
        t = _announcer(hass, game)
        assert t.is_configured is True

    def test_is_configured_false_when_neither_complete(self, game) -> None:
        """Override supplies only one entity, config-entry none → not configured."""
        hass = _FakeHass()
        t = QuizifyTTSAnnouncer(
            hass=hass,
            tts_entity_id=None,
            media_player_entity_id=None,
            game_state=game,
        )
        t.configure(
            enabled=True,
            announce_question=True,
            announce_reveal=True,
            announce_standings=True,
            tts_entity="tts.piper",
            media_player="",
        )
        assert t.is_configured is False


class TestThrottle:
    @pytest.mark.asyncio
    async def test_rapid_second_speak_suppressed(self, game) -> None:
        game.language = "en"
        hass = _FakeHass()
        t = _announcer(hass, game)
        _configure(t)
        t.announce_question(_question("Q1?"), 1, 10)
        await asyncio.sleep(0)
        t.announce_question(_question("Q2?"), 2, 10)
        await asyncio.sleep(0)
        # Second call inside TTS_MIN_INTERVAL → dropped.
        assert len(hass.calls) == 1

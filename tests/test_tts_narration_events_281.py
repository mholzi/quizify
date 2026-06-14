"""Tests for the three later narration events of QuizifyTTSAnnouncer (#281).

Covers the events added after the v1 "Quizmaster core" set:
  * answer-options readout (appended to the question utterance, own toggle,
    skipped for non-multiple-choice rounds, lettered to match the TV grid),
  * player-join welcome (own toggle, master gate, host's own tab skipped),
  * the once-per-round "time running out" countdown (threshold + one-shot
    guard that resets at each question start).

Like the v1 suite, a fake hass records ``tts.speak`` calls so we assert intent
(message text) without real Home Assistant.
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
from custom_components.quizify.game.state import QuizifyGameState  # noqa: E402
from custom_components.quizify.tts import (  # noqa: E402
    COUNTDOWN_THRESHOLD_SECONDS,
    QuizifyTTSAnnouncer,
)


class _FakeHass:
    """Records tts.speak calls so tests can assert the spoken message."""

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


def _configure(t, **overrides):
    """Configure with everything on by default; override individual flags."""
    cfg = {
        "enabled": True,
        "announce_question": True,
        "announce_options": True,
        "announce_reveal": True,
        "announce_standings": True,
        "announce_join": True,
        "announce_countdown": True,
    }
    cfg.update(overrides)
    t.configure(**cfg)


def _question(text="Q?"):
    return Question(id="q1", question=text,
                    answers=[Answer(text="da Vinci", correct=True)])


def _last_message(hass):
    assert hass.calls, "expected a tts.speak call"
    domain, service, data = hass.calls[-1]
    assert (domain, service) == ("tts", "speak")
    return data["message"]


class TestOptionsReadout:
    @pytest.mark.asyncio
    async def test_options_appended_en(self, game) -> None:
        game.language = "en"
        hass = _FakeHass()
        t = _announcer(hass, game)
        _configure(t)
        t.announce_question(_question("Capital?"), 1, 10,
                            ["Paris", "London", "Berlin"])
        await asyncio.sleep(0)
        msg = _last_message(hass)
        assert "Question 1 of 10: Capital?" in msg
        assert "Your options are: A, Paris. B, London. C, Berlin." in msg

    @pytest.mark.asyncio
    async def test_options_appended_de(self, game) -> None:
        game.language = "de"
        hass = _FakeHass()
        t = _announcer(hass, game)
        _configure(t)
        t.announce_question(_question("Hauptstadt?"), 1, 10, ["Paris", "London"])
        await asyncio.sleep(0)
        msg = _last_message(hass)
        assert "Die Antwortmöglichkeiten sind: A, Paris. B, London." in msg

    @pytest.mark.asyncio
    async def test_options_toggle_off_keeps_question(self, game) -> None:
        game.language = "en"
        hass = _FakeHass()
        t = _announcer(hass, game)
        _configure(t, announce_options=False)
        t.announce_question(_question("Capital?"), 1, 10, ["Paris", "London"])
        await asyncio.sleep(0)
        msg = _last_message(hass)
        assert msg == "Question 1 of 10: Capital?"
        assert "options" not in msg.lower()

    @pytest.mark.asyncio
    async def test_options_skipped_for_estimate(self, game) -> None:
        # Estimate rounds carry < 2 options → nothing to read out.
        game.language = "en"
        hass = _FakeHass()
        t = _announcer(hass, game)
        _configure(t)
        t.announce_question(_question("How many?"), 1, 10, [])
        await asyncio.sleep(0)
        assert _last_message(hass) == "Question 1 of 10: How many?"

    @pytest.mark.asyncio
    async def test_options_suppressed_when_question_off(self, game) -> None:
        # Options ride the question utterance; question toggle off → silent.
        game.language = "en"
        hass = _FakeHass()
        t = _announcer(hass, game)
        _configure(t, announce_question=False)
        t.announce_question(_question("Capital?"), 1, 10, ["Paris", "London"])
        await asyncio.sleep(0)
        assert hass.calls == []


class TestJoinNarration:
    @pytest.mark.asyncio
    async def test_join_en(self, game) -> None:
        game.language = "en"
        hass = _FakeHass()
        t = _announcer(hass, game)
        _configure(t)
        t.announce_join("Alice")
        await asyncio.sleep(0)
        assert _last_message(hass) == "Alice joined the game."

    @pytest.mark.asyncio
    async def test_join_de(self, game) -> None:
        game.language = "de"
        hass = _FakeHass()
        t = _announcer(hass, game)
        _configure(t)
        t.announce_join("Bob")
        await asyncio.sleep(0)
        assert _last_message(hass) == "Bob ist dabei!"

    @pytest.mark.asyncio
    async def test_join_toggle_off(self, game) -> None:
        hass = _FakeHass()
        t = _announcer(hass, game)
        _configure(t, announce_join=False)
        t.announce_join("Alice")
        await asyncio.sleep(0)
        assert hass.calls == []

    @pytest.mark.asyncio
    async def test_join_master_off(self, game) -> None:
        hass = _FakeHass()
        t = _announcer(hass, game)
        _configure(t, enabled=False)
        t.announce_join("Alice")
        await asyncio.sleep(0)
        assert hass.calls == []

    @pytest.mark.asyncio
    async def test_admin_join_skipped(self, game) -> None:
        # The host's own admin-as-player tab must not announce itself.
        hass = _FakeHass()
        t = _announcer(hass, game)
        _configure(t)
        t.announce_join("Host", is_admin=True)
        await asyncio.sleep(0)
        assert hass.calls == []


class TestCountdown:
    # Question narration is turned off in these tests so ``hass.calls`` holds
    # only countdown utterances. announce_question still RESETS the one-shot
    # guard even with its own toggle off (the reset runs before the gate), so
    # it remains the per-round seam.

    @pytest.mark.asyncio
    async def test_fires_once_under_threshold(self, game) -> None:
        game.language = "en"
        hass = _FakeHass()
        t = _announcer(hass, game)
        _configure(t, announce_question=False)
        t.announce_question(_question(), 1, 10)  # resets the one-shot guard
        t.announce_countdown(float(COUNTDOWN_THRESHOLD_SECONDS))
        await asyncio.sleep(0)
        assert _last_message(hass) == f"{COUNTDOWN_THRESHOLD_SECONDS} seconds left!"

    @pytest.mark.asyncio
    async def test_not_fired_above_threshold(self, game) -> None:
        hass = _FakeHass()
        t = _announcer(hass, game)
        _configure(t, announce_question=False)
        t.announce_question(_question(), 1, 10)
        t.announce_countdown(COUNTDOWN_THRESHOLD_SECONDS + 5)
        await asyncio.sleep(0)
        assert hass.calls == []

    @pytest.mark.asyncio
    async def test_fires_at_most_once_per_round(self, game) -> None:
        hass = _FakeHass()
        t = _announcer(hass, game)
        _configure(t, announce_question=False)
        t.announce_question(_question(), 1, 10)
        t.announce_countdown(8)
        await asyncio.sleep(0)
        t._last_spoken_at = None  # rule out the throttle suppressing #2
        t.announce_countdown(6)
        await asyncio.sleep(0)
        assert len(hass.calls) == 1

    @pytest.mark.asyncio
    async def test_guard_resets_on_new_question(self, game) -> None:
        hass = _FakeHass()
        t = _announcer(hass, game)
        _configure(t, announce_question=False)
        t.announce_question(_question(), 1, 10)
        t.announce_countdown(7)
        await asyncio.sleep(0)
        assert len(hass.calls) == 1
        # Next round starts → guard resets → countdown can fire again.
        t.announce_question(_question(), 2, 10)
        t._last_spoken_at = None
        t.announce_countdown(7)
        await asyncio.sleep(0)
        assert len(hass.calls) == 2

    @pytest.mark.asyncio
    async def test_countdown_toggle_off(self, game) -> None:
        hass = _FakeHass()
        t = _announcer(hass, game)
        _configure(t, announce_question=False, announce_countdown=False)
        t.announce_question(_question(), 1, 10)
        t.announce_countdown(3)
        await asyncio.sleep(0)
        assert hass.calls == []

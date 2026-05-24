"""Tests for Phase 3 HA integrations (party lights + TTS announcer).

These services depend on a Home Assistant instance for the actual
service calls; the tests use a hand-rolled stub that records calls so we
can assert *intent* without touching real HA. Both services are
expected to no-op gracefully when not configured — those paths matter
because that's the standalone dev-server's experience.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from custom_components.quizify.game.state import GamePhase, QuizifyGameState  # noqa: E402
from custom_components.quizify.lights import QuizifyPartyLights  # noqa: E402
from custom_components.quizify.tts import QuizifyTTSAnnouncer, TTS_MIN_INTERVAL  # noqa: E402


class _FakeHass:
    """Records service calls + create_task invocations so tests can assert."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict]] = []
        self.states = MagicMock()
        self.states.get = lambda eid: MagicMock(state="on")  # always available
        self.services = MagicMock()
        async def _async_call(domain, service, data, blocking=False):  # noqa: ANN001
            self.calls.append((domain, service, dict(data)))
        self.services.async_call = _async_call

    def async_create_task(self, coro):  # noqa: ANN001
        # Schedule on the current event loop. Tests await asyncio.sleep(0)
        # twice (once for the create_task to schedule, once for the
        # awaited service call inside it) to let it run.
        return asyncio.ensure_future(coro)


class _FakeRuntime:
    def __init__(self, tmp_path: Path) -> None:
        self.data_dir = tmp_path


@pytest.fixture
def game(tmp_path):
    return QuizifyGameState(runtime=_FakeRuntime(tmp_path), entry_id="t")


# ---------- Party Lights ----------


class TestPartyLightsConfigGuard:
    def test_unconfigured_when_no_entities(self, game) -> None:
        pl = QuizifyPartyLights(hass=_FakeHass(), entity_ids=[], game_state=game)
        assert pl.is_configured is False

    def test_unconfigured_when_no_hass(self, game) -> None:
        pl = QuizifyPartyLights(hass=None, entity_ids=["light.x"], game_state=game)
        assert pl.is_configured is False

    def test_strips_blank_entries(self, game) -> None:
        pl = QuizifyPartyLights(
            hass=_FakeHass(),
            entity_ids=["light.a", "", "  ", "light.b"],
            game_state=game,
        )
        assert pl._entity_ids == ["light.a", "light.b"]

    def test_deduplicates_entries(self, game) -> None:
        pl = QuizifyPartyLights(
            hass=_FakeHass(),
            entity_ids=["light.a", "light.b", "light.a"],
            game_state=game,
        )
        assert pl._entity_ids == ["light.a", "light.b"]


class TestPartyLightsPhaseMapping:
    @pytest.mark.asyncio
    async def test_phase_change_triggers_light_call(self, game) -> None:
        hass = _FakeHass()
        pl = QuizifyPartyLights(hass=hass, entity_ids=["light.dining"], game_state=game)
        pl.attach()
        # Force a phase that the recipe maps to QUESTION_ACTIVE.
        game.phase = GamePhase.QUESTION_ACTIVE
        game._notify_state_callbacks()
        # Allow the create_task coroutine to run.
        await asyncio.sleep(0)
        assert len(hass.calls) == 1
        domain, service, data = hass.calls[0]
        assert (domain, service) == ("light", "turn_on")
        assert data["entity_id"] == ["light.dining"]
        # Coral RGB per DESIGN.md.
        assert data["rgb_color"] == [232, 138, 127]

    @pytest.mark.asyncio
    async def test_no_call_on_same_phase_twice(self, game) -> None:
        hass = _FakeHass()
        pl = QuizifyPartyLights(hass=hass, entity_ids=["light.x"], game_state=game)
        pl.attach()
        game.phase = GamePhase.ANSWER_REVEAL
        game._notify_state_callbacks()
        await asyncio.sleep(0)
        game._notify_state_callbacks()  # same phase again
        await asyncio.sleep(0)
        assert len(hass.calls) == 1

    @pytest.mark.asyncio
    async def test_paused_is_no_op(self, game) -> None:
        hass = _FakeHass()
        pl = QuizifyPartyLights(hass=hass, entity_ids=["light.x"], game_state=game)
        pl.attach()
        game.phase = GamePhase.PAUSED
        game._notify_state_callbacks()
        await asyncio.sleep(0)
        # PAUSED maps to None — leave the room alone.
        assert hass.calls == []


# ---------- TTS ----------


class TestTTSConfigGuard:
    def test_unconfigured_when_missing_tts(self, game) -> None:
        t = QuizifyTTSAnnouncer(
            hass=_FakeHass(),
            tts_entity_id=None,
            media_player_entity_id="media_player.kitchen",
            game_state=game,
        )
        assert t.is_configured is False

    def test_unconfigured_when_missing_media_player(self, game) -> None:
        t = QuizifyTTSAnnouncer(
            hass=_FakeHass(),
            tts_entity_id="tts.cloud",
            media_player_entity_id=None,
            game_state=game,
        )
        assert t.is_configured is False

    def test_unconfigured_when_no_hass(self, game) -> None:
        t = QuizifyTTSAnnouncer(
            hass=None,
            tts_entity_id="tts.cloud",
            media_player_entity_id="media_player.kitchen",
            game_state=game,
        )
        assert t.is_configured is False


class TestTTSMilestoneAnnounce:
    @pytest.mark.asyncio
    async def test_announce_milestone_fires_speak(self, game) -> None:
        hass = _FakeHass()
        t = QuizifyTTSAnnouncer(
            hass=hass,
            tts_entity_id="tts.cloud",
            media_player_entity_id="media_player.kitchen",
            game_state=game,
        )
        t.announce_milestone("Alice", 5)
        await asyncio.sleep(0)
        assert len(hass.calls) == 1
        domain, service, data = hass.calls[0]
        assert (domain, service) == ("tts", "speak")
        assert "Alice" in data["message"]
        assert "5-streak" in data["message"]
        assert data["entity_id"] == "tts.cloud"
        assert data["media_player_entity_id"] == "media_player.kitchen"

    @pytest.mark.asyncio
    async def test_throttle_blocks_rapid_announcements(self, game) -> None:
        hass = _FakeHass()
        t = QuizifyTTSAnnouncer(
            hass=hass,
            tts_entity_id="tts.cloud",
            media_player_entity_id="media_player.kitchen",
            game_state=game,
        )
        t.announce_milestone("Alice", 3)
        await asyncio.sleep(0)
        t.announce_milestone("Bob", 5)
        await asyncio.sleep(0)
        # Within TTS_MIN_INTERVAL — second call should drop.
        assert len(hass.calls) == 1
        # Sanity: the interval is meaningful enough to matter.
        assert TTS_MIN_INTERVAL >= 1.0

    @pytest.mark.asyncio
    async def test_same_milestone_not_repeated(self, game) -> None:
        hass = _FakeHass()
        t = QuizifyTTSAnnouncer(
            hass=hass,
            tts_entity_id="tts.cloud",
            media_player_entity_id="media_player.kitchen",
            game_state=game,
        )
        t.announce_milestone("Alice", 3)
        await asyncio.sleep(0)
        # Even after the throttle window, a repeated (player, streak) pair
        # shouldn't re-announce — protects against snapshot replays.
        t._last_spoken_at = None
        t.announce_milestone("Alice", 3)
        await asyncio.sleep(0)
        assert len(hass.calls) == 1

    @pytest.mark.asyncio
    async def test_unconfigured_announcer_does_not_call(self, game) -> None:
        hass = _FakeHass()
        t = QuizifyTTSAnnouncer(
            hass=hass,
            tts_entity_id=None,  # not configured
            media_player_entity_id="media_player.kitchen",
            game_state=game,
        )
        t.announce_milestone("Alice", 10)
        await asyncio.sleep(0)
        assert hass.calls == []


class TestTTSPhaseAnnouncements:
    @pytest.mark.asyncio
    async def test_first_question_says_starting(self, game) -> None:
        hass = _FakeHass()
        t = QuizifyTTSAnnouncer(
            hass=hass,
            tts_entity_id="tts.cloud",
            media_player_entity_id="media_player.kitchen",
            game_state=game,
        )
        t.attach()
        # Simulate "game started, round 1 active".
        game.round = 1
        game.phase = GamePhase.QUESTION_ACTIVE
        game._notify_state_callbacks()
        await asyncio.sleep(0)
        assert len(hass.calls) == 1
        assert "starting" in hass.calls[0][2]["message"].lower()

    @pytest.mark.asyncio
    async def test_finale_announces_winner(self, game) -> None:
        hass = _FakeHass()
        t = QuizifyTTSAnnouncer(
            hass=hass,
            tts_entity_id="tts.cloud",
            media_player_entity_id="media_player.kitchen",
            game_state=game,
        )
        t.attach()
        # Add a player so leader is non-None.
        ws = MagicMock()
        ws.closed = False
        game.add_player("Alice", ws)
        game.players["Alice"].score = 250
        # Reset last_spoken_at so we're not throttle-blocked.
        t._last_spoken_at = None
        # Simulate transition into FINALE.
        game.phase = GamePhase.FINALE
        game._notify_state_callbacks()
        await asyncio.sleep(0)
        winner_msgs = [c for c in hass.calls if "Alice" in c[2].get("message", "")]
        assert len(winner_msgs) == 1
        assert "250" in winner_msgs[0][2]["message"]

"""Tests for the room sound effects — Variant C "Hybrid" (#494 Phase 3).

QuizifySoundEffects subscribes to the ``quizify_*`` HA bus events (#366
backbone) and one-shots a short sound on the shared ``media_player`` at each
game milestone. Source per cue is hybrid: a host-supplied override URL wins,
else the bundled default at ``www/sfx/<cue>.mp3`` (only if the file physically
exists). A cue is dropped entirely while the shared speaker is ``playing`` so it
never talks over a live TTS announcement.

A fake hass records bus listeners, exposes a controllable ``states.get`` for the
TTS-contention check, and captures service calls (``fire_and_forget_service`` is
monkeypatched). ``get_url`` and ``WWW_DIR`` are injected so the bundled-default
resolution is deterministic — no real Home Assistant, no real audio files.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from custom_components.quizify import sound_effects as sfx_mod  # noqa: E402
from custom_components.quizify.game_events import (  # noqa: E402
    EVENT_ANSWER_REVEALED,
    EVENT_STREAK_MILESTONE,
    EVENT_WINNER_DECIDED,
)
from custom_components.quizify.sound_effects import QuizifySoundEffects  # noqa: E402

_BASE = "http://ha.local:8123"
_PREFIX = sfx_mod.STATIC_URL_PREFIX  # "/quizify/static"


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeEvent:
    def __init__(self, data: dict) -> None:
        self.data = data


class _FakeState:
    def __init__(self, state: str) -> None:
        self.state = state


class _FakeStates:
    """Controllable ``hass.states`` — the test sets the media_player state."""

    def __init__(self) -> None:
        self._states: dict[str, _FakeState] = {}

    def set(self, entity_id: str, state: str | None) -> None:
        if state is None:
            self._states.pop(entity_id, None)
        else:
            self._states[entity_id] = _FakeState(state)

    def get(self, entity_id: str):  # noqa: ANN201
        return self._states.get(entity_id)


class _FakeBus:
    def __init__(self) -> None:
        self.listeners: dict[str, list] = {}

    def async_listen(self, event_type, cb):  # noqa: ANN001
        self.listeners.setdefault(event_type, []).append(cb)

        def _unsub() -> None:
            lst = self.listeners.get(event_type, [])
            if cb in lst:
                lst.remove(cb)

        return _unsub

    def dispatch(self, event_type, data):  # noqa: ANN001
        for cb in list(self.listeners.get(event_type, [])):
            cb(_FakeEvent(data))


class _FakeHass:
    def __init__(self) -> None:
        self.bus = _FakeBus()
        self.states = _FakeStates()


class _FakeGame:
    """SFX never touches the game state — a bare stand-in is enough."""


@pytest.fixture
def calls(monkeypatch):
    """Capture (domain, service, data) tuples from every SFX service call."""
    recorded: list[tuple[str, str, dict]] = []

    def _record(_hass, domain, service, data, _ctx):  # noqa: ANN001
        recorded.append((domain, service, dict(data)))

    monkeypatch.setattr(sfx_mod, "fire_and_forget_service", _record)
    return recorded


@pytest.fixture
def with_base(monkeypatch):
    """Deterministic HA base URL so bundled-default URLs are predictable."""
    monkeypatch.setattr(sfx_mod, "_ha_get_url", lambda _hass: _BASE)


def _sfx(hass, *, media_player="media_player.tv", cue_urls=None):
    return QuizifySoundEffects(
        hass=hass,
        media_player_entity_id=media_player,
        game_state=_FakeGame(),
        cue_urls=cue_urls or {},
    )


def _bundle(monkeypatch, tmp_path: Path, *cues: str) -> None:
    """Point WWW_DIR at a tmp tree and drop empty <cue>.mp3 files there."""
    sfx_dir = tmp_path / "sfx"
    sfx_dir.mkdir(parents=True, exist_ok=True)
    for cue in cues:
        (sfx_dir / f"{cue}.mp3").write_bytes(b"")
    monkeypatch.setattr(sfx_mod, "WWW_DIR", tmp_path)


def _played(calls):
    return [
        data
        for (domain, service, data) in calls
        if domain == "media_player" and service == "play_media"
    ]


def _default_url(cue: str) -> str:
    return f"{_BASE}{_PREFIX}/sfx/{cue}.mp3"


# ---------------------------------------------------------------------------
# Cue mapping (answer_revealed majority / minority / edge cases)
# ---------------------------------------------------------------------------


def test_answer_revealed_majority_plays_correct(
    calls, with_base, monkeypatch, tmp_path
):
    _bundle(monkeypatch, tmp_path, "correct", "wrong")
    hass = _FakeHass()
    sfx = _sfx(hass)
    sfx.attach_events()
    hass.bus.dispatch(EVENT_ANSWER_REVEALED, {"correct_count": 3, "total_players": 4})
    played = _played(calls)
    assert len(played) == 1
    assert played[0]["media_content_id"] == _default_url("correct")
    assert played[0]["media_content_type"] == "music"
    assert played[0]["entity_id"] == "media_player.tv"


def test_answer_revealed_minority_plays_wrong(calls, with_base, monkeypatch, tmp_path):
    _bundle(monkeypatch, tmp_path, "correct", "wrong")
    hass = _FakeHass()
    sfx = _sfx(hass)
    sfx.attach_events()
    hass.bus.dispatch(EVENT_ANSWER_REVEALED, {"correct_count": 1, "total_players": 4})
    assert _played(calls)[0]["media_content_id"] == _default_url("wrong")


def test_answer_revealed_exact_half_plays_wrong(
    calls, with_base, monkeypatch, tmp_path
):
    """Half-right is not a *strict* majority → wrong cue."""
    _bundle(monkeypatch, tmp_path, "correct", "wrong")
    hass = _FakeHass()
    sfx = _sfx(hass)
    sfx.attach_events()
    hass.bus.dispatch(EVENT_ANSWER_REVEALED, {"correct_count": 2, "total_players": 4})
    assert _played(calls)[0]["media_content_id"] == _default_url("wrong")


def test_answer_revealed_zero_players_no_crash_wrong(
    calls, with_base, monkeypatch, tmp_path
):
    _bundle(monkeypatch, tmp_path, "correct", "wrong")
    hass = _FakeHass()
    sfx = _sfx(hass)
    sfx.attach_events()
    # total_players == 0 must not divide-by-zero; treated as minority (wrong).
    hass.bus.dispatch(EVENT_ANSWER_REVEALED, {"correct_count": 0, "total_players": 0})
    assert _played(calls)[0]["media_content_id"] == _default_url("wrong")


def test_streak_plays_streak_cue(calls, with_base, monkeypatch, tmp_path):
    _bundle(monkeypatch, tmp_path, "streak")
    hass = _FakeHass()
    sfx = _sfx(hass)
    sfx.attach_events()
    hass.bus.dispatch(EVENT_STREAK_MILESTONE, {"player_name": "Alice", "streak": 5})
    assert _played(calls)[0]["media_content_id"] == _default_url("streak")


def test_winner_plays_winner_cue(calls, with_base, monkeypatch, tmp_path):
    _bundle(monkeypatch, tmp_path, "winner")
    hass = _FakeHass()
    sfx = _sfx(hass)
    sfx.attach_events()
    hass.bus.dispatch(EVENT_WINNER_DECIDED, {"winner_name": "Bob", "score": 420})
    assert _played(calls)[0]["media_content_id"] == _default_url("winner")


# ---------------------------------------------------------------------------
# Source resolution (hybrid: override > bundled default > silence)
# ---------------------------------------------------------------------------


def test_override_url_wins_over_default(calls, with_base, monkeypatch, tmp_path):
    _bundle(monkeypatch, tmp_path, "correct")  # default exists…
    hass = _FakeHass()
    # …but an override is set, so it wins.
    sfx = _sfx(hass, cue_urls={"correct": "http://custom/ding.mp3"})
    sfx.attach_events()
    hass.bus.dispatch(EVENT_ANSWER_REVEALED, {"correct_count": 3, "total_players": 4})
    assert _played(calls)[0]["media_content_id"] == "http://custom/ding.mp3"


def test_override_used_when_no_default_file(calls, with_base, monkeypatch, tmp_path):
    _bundle(monkeypatch, tmp_path)  # no files at all
    hass = _FakeHass()
    sfx = _sfx(hass, cue_urls={"streak": "http://custom/streak.mp3"})
    sfx.attach_events()
    hass.bus.dispatch(EVENT_STREAK_MILESTONE, {"streak": 3})
    assert _played(calls)[0]["media_content_id"] == "http://custom/streak.mp3"


def test_no_override_no_default_is_silent(calls, with_base, monkeypatch, tmp_path):
    _bundle(monkeypatch, tmp_path)  # no bundled files
    hass = _FakeHass()
    sfx = _sfx(hass)  # no overrides
    sfx.attach_events()
    hass.bus.dispatch(EVENT_ANSWER_REVEALED, {"correct_count": 3, "total_players": 4})
    hass.bus.dispatch(EVENT_STREAK_MILESTONE, {"streak": 3})
    hass.bus.dispatch(EVENT_WINNER_DECIDED, {"winner_name": "Bob", "score": 1})
    assert _played(calls) == []


def test_default_ignored_when_only_other_cue_file_present(
    calls, with_base, monkeypatch, tmp_path
):
    """Only wrong.mp3 exists → a majority answer (correct cue) stays silent."""
    _bundle(monkeypatch, tmp_path, "wrong")
    hass = _FakeHass()
    sfx = _sfx(hass)
    sfx.attach_events()
    hass.bus.dispatch(EVENT_ANSWER_REVEALED, {"correct_count": 3, "total_players": 4})
    assert _played(calls) == []


def test_no_default_when_base_url_unavailable(calls, monkeypatch, tmp_path):
    """get_url returning None (no configured base) → defaults can't resolve."""
    _bundle(monkeypatch, tmp_path, "correct")
    monkeypatch.setattr(sfx_mod, "_ha_get_url", lambda _hass: (_ for _ in ()).throw(
        RuntimeError("no url configured")
    ))
    hass = _FakeHass()
    sfx = _sfx(hass)
    sfx.attach_events()
    hass.bus.dispatch(EVENT_ANSWER_REVEALED, {"correct_count": 3, "total_players": 4})
    assert _played(calls) == []


# ---------------------------------------------------------------------------
# TTS contention (hold during TTS)
# ---------------------------------------------------------------------------


def test_cue_skipped_while_media_player_playing(
    calls, with_base, monkeypatch, tmp_path
):
    _bundle(monkeypatch, tmp_path, "correct")
    hass = _FakeHass()
    hass.states.set("media_player.tv", "playing")  # a TTS announcement is live
    sfx = _sfx(hass)
    sfx.attach_events()
    hass.bus.dispatch(EVENT_ANSWER_REVEALED, {"correct_count": 3, "total_players": 4})
    assert _played(calls) == []


def test_cue_plays_when_media_player_idle(calls, with_base, monkeypatch, tmp_path):
    _bundle(monkeypatch, tmp_path, "correct")
    hass = _FakeHass()
    hass.states.set("media_player.tv", "idle")
    sfx = _sfx(hass)
    sfx.attach_events()
    hass.bus.dispatch(EVENT_ANSWER_REVEALED, {"correct_count": 3, "total_players": 4})
    assert len(_played(calls)) == 1


def test_cue_plays_when_state_missing(calls, with_base, monkeypatch, tmp_path):
    """states.get() → None (unknown entity) means nothing is playing → proceed."""
    _bundle(monkeypatch, tmp_path, "correct")
    hass = _FakeHass()  # no state registered for media_player.tv
    sfx = _sfx(hass)
    sfx.attach_events()
    hass.bus.dispatch(EVENT_ANSWER_REVEALED, {"correct_count": 3, "total_players": 4})
    assert len(_played(calls)) == 1


# ---------------------------------------------------------------------------
# Not configured / lifecycle
# ---------------------------------------------------------------------------


def test_not_configured_no_hass_registers_nothing(calls):
    sfx = _sfx(None)  # hass is None → not configured
    sfx.attach_events()
    assert sfx._event_unsubs == []
    assert not sfx.is_configured
    # Direct handler call must be a no-op too.
    sfx._on_streak_milestone(_FakeEvent({}))
    assert calls == []


def test_not_configured_no_media_player_registers_nothing(calls):
    hass = _FakeHass()
    sfx = _sfx(hass, media_player=None)  # no target speaker → not configured
    sfx.attach_events()
    assert hass.bus.listeners == {}
    hass.bus.dispatch(EVENT_ANSWER_REVEALED, {"correct_count": 3, "total_players": 4})
    assert calls == []


def test_attach_events_idempotent(with_base, monkeypatch, tmp_path):
    _bundle(monkeypatch, tmp_path, "correct")
    hass = _FakeHass()
    sfx = _sfx(hass)
    sfx.attach_events()
    sfx.attach_events()  # second call must not double-register
    assert all(len(v) == 1 for v in hass.bus.listeners.values())
    assert len(sfx._event_unsubs) == 3


def test_attach_events_registers_three_listeners(with_base, monkeypatch, tmp_path):
    _bundle(monkeypatch, tmp_path)
    hass = _FakeHass()
    sfx = _sfx(hass)
    sfx.attach_events()
    assert set(hass.bus.listeners) == {
        EVENT_ANSWER_REVEALED,
        EVENT_STREAK_MILESTONE,
        EVENT_WINNER_DECIDED,
    }


def test_detach_unregisters_listeners(calls, with_base, monkeypatch, tmp_path):
    _bundle(monkeypatch, tmp_path, "correct")
    hass = _FakeHass()
    sfx = _sfx(hass)
    sfx.attach_events()
    sfx.detach()
    assert all(len(v) == 0 for v in hass.bus.listeners.values())
    assert sfx._event_unsubs == []
    # Events after detach do nothing.
    hass.bus.dispatch(EVENT_ANSWER_REVEALED, {"correct_count": 3, "total_players": 4})
    assert _played(calls) == []


def test_detach_without_attach_is_safe():
    """detach() must be safe even if attach_events() was never called."""
    sfx = _sfx(_FakeHass())
    sfx.detach()  # no listeners → must not raise
    assert sfx._event_unsubs == []

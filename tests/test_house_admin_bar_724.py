"""The house and TTS config need WS-level admin, and their ids need a domain (#724).

Two separate holes, one payload:

  1. ``_is_authorized_admin`` accepts ``player.is_admin`` — the ``is_admin: true``
     join claim accepted in #208 as "claim the single admin slot of the quiz".
     A host who runs the evening from the ``?role=admin`` tab is a WS-admin and
     never takes that player slot, so it stays free for any guest all evening.
     Through ``configure_house`` / ``configure_tts`` (and the ``house``/``tts``
     blocks of ``start_game``) that slot reaches ``light.turn_on``,
     ``scene.turn_on``, ``media_player.play_media`` and ``tts.speak``.
  2. Even for a legitimate host the ids were only stripped and deduped
     (``lights._clean_entity_ids``) — no domain check anywhere on the path.

So the fix has two halves and this file pins both: the WS-admin bar on the way
in, and the domain allowlist on the way out.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from custom_components.quizify.game.state import (  # noqa: E402
    GamePhase,
    QuizifyGameState,
)
from custom_components.quizify.server.connection import ConnectionManager  # noqa: E402
from custom_components.quizify.server.websocket import (  # noqa: E402
    QuizifyWebSocketHandler,
)


class _FakeRuntime:
    def __init__(self, tmp_path: Path) -> None:
        self.data_dir = tmp_path

    def create_task(self, coro):  # noqa: ANN001, ANN202
        return asyncio.ensure_future(coro)


def _ws() -> MagicMock:
    ws = MagicMock()
    ws.closed = False
    ws.send_json = AsyncMock()
    ws.close = AsyncMock()
    return ws


@pytest.fixture
def game(tmp_path: Path) -> QuizifyGameState:
    return QuizifyGameState(runtime=_FakeRuntime(tmp_path), entry_id="test")


@pytest.fixture
def handler(game: QuizifyGameState, tmp_path: Path, monkeypatch):
    runtime = _FakeRuntime(tmp_path)
    h = QuizifyWebSocketHandler(runtime=runtime, game_state_provider=lambda: game)
    h._conn = ConnectionManager(runtime, lambda: game)
    h._get_game_state = lambda: game  # type: ignore[assignment]
    h.START_REDIRECT_GRACE = 0

    errors: dict[int, list[dict]] = {}

    async def _send_error(ws, code, message) -> None:
        errors.setdefault(id(ws), []).append({"code": code, "message": message})

    async def _noop(*args, **kwargs) -> None:
        return None

    h._conn.broadcast = _noop  # type: ignore[assignment]
    h._conn.send_error = _send_error  # type: ignore[assignment]
    h._conn._safe_send = _noop  # type: ignore[assignment]
    h._conn.broadcast_to_admins_and_dashboards = _noop  # type: ignore[assignment]
    monkeypatch.setattr(h, "_start_timer_tick", lambda *a, **k: None)

    h.set_party_lights(MagicMock())
    h.set_sound_effects(MagicMock())
    h.set_event_emitter(MagicMock())
    h.set_tts_announcer(MagicMock())

    h._errors = errors  # type: ignore[attr-defined]
    return h


def _player_admin_ws(h: QuizifyWebSocketHandler, game: QuizifyGameState) -> MagicMock:
    """A guest who claimed the free player-admin slot: ``player.is_admin`` is
    True while the connection itself was never opened with ``?role=admin``.

    This is exactly the state a host leaves behind by running the game from the
    admin tab — the player slot nobody occupies.
    """
    ws = _ws()
    h._conn.add_connection(ws, is_admin=False, is_dashboard=False)
    game.add_player("Guest", ws)
    game.get_player("Guest").is_admin = True
    return ws


_HOUSE = {
    "enabled": True,
    "light_entities": ["light.living_room"],
    "media_player": "media_player.kitchen",
    "winner_scene_entity": "scene.party",
}


# ---------------------------------------------------------------------------
# 1. The WS-admin bar
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("msg_type", ["configure_house", "configure_tts"])
async def test_player_admin_cannot_configure_the_house(
    msg_type: str, handler, game: QuizifyGameState
) -> None:
    """A player-admin is refused, and no consumer is reconfigured."""
    ws = _player_admin_ws(handler, game)

    await handler._handle_message(ws, {"type": msg_type, **_HOUSE}, is_admin=False)

    errs = handler._errors.get(id(ws), [])
    assert errs and errs[-1]["code"] == "ADMIN_REQUIRED", (
        f"{msg_type} from a player-admin was not refused: {errs}"
    )
    handler._party_lights.configure.assert_not_called()
    handler._sound_effects.configure.assert_not_called()
    handler._tts_announcer.configure.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("msg_type", ["configure_house", "configure_tts"])
async def test_ws_admin_still_configures_the_house(
    msg_type: str, handler, game: QuizifyGameState
) -> None:
    """The real ``?role=admin`` tab keeps working — the bar must not lock the host
    out of the panel it exists for."""
    ws = _ws()
    handler._conn.add_connection(ws, is_admin=True, is_dashboard=False)

    await handler._handle_message(ws, {"type": msg_type, **_HOUSE}, is_admin=True)

    errs = handler._errors.get(id(ws), [])
    assert all(e["code"] != "ADMIN_REQUIRED" for e in errs), errs
    if msg_type == "configure_house":
        handler._party_lights.configure.assert_called_once()
    else:
        handler._tts_announcer.configure.assert_called_once()


@pytest.mark.asyncio
async def test_player_admin_starts_the_game_without_touching_the_room(
    handler, game: QuizifyGameState
) -> None:
    """A player-admin may still start the game (that is the #208 flow), but the
    ``house`` / ``tts`` blocks of the payload are dropped: the lights, speakers
    and scenes stay on whatever the host configured."""
    ws = _player_admin_ws(handler, game)

    await handler._handle_message(
        ws,
        {
            "type": "start_game",
            "num_rounds": 1,
            "house": _HOUSE,
            "tts": {"enabled": True, "tts_entity": "tts.google"},
        },
        is_admin=False,
    )

    assert game.phase != GamePhase.LOBBY, "the #208 admin-as-player start broke"
    handler._party_lights.configure.assert_not_called()
    handler._sound_effects.configure.assert_not_called()
    # TTS is applied unconditionally on start (an empty dict disarms it), so the
    # assertion is on the payload, not on the call count.
    if handler._tts_announcer.configure.called:
        kwargs = handler._tts_announcer.configure.call_args.kwargs
        assert kwargs["enabled"] is False
        assert not kwargs["tts_entity"]


# ---------------------------------------------------------------------------
# 2. The domain allowlist
# ---------------------------------------------------------------------------


def test_light_entities_outside_the_light_domain_are_dropped(handler) -> None:
    handler._apply_house_config(
        {**_HOUSE, "light_entities": ["light.living_room", "lock.front_door"]}
    )
    kwargs = handler._party_lights.configure.call_args.kwargs
    assert kwargs["light_entities"] == ["light.living_room"]


def test_media_player_and_scene_overrides_are_domain_checked(handler) -> None:
    handler._apply_house_config(
        {
            **_HOUSE,
            "media_player": "switch.garage_door",
            "winner_scene_entity": "script.disarm_alarm",
        }
    )
    assert handler._sound_effects.configure.call_args.kwargs["media_player"] == ""
    assert (
        handler._party_lights.configure.call_args.kwargs["winner_scene_entity"] == ""
    )


def test_tts_entity_overrides_are_domain_checked(handler) -> None:
    handler._apply_tts_config(
        {
            "enabled": True,
            "tts_entity": "notify.everyone",
            "media_player": "lock.front_door",
        }
    )
    kwargs = handler._tts_announcer.configure.call_args.kwargs
    assert kwargs["tts_entity"] is None
    assert kwargs["media_player"] is None


def test_valid_overrides_still_reach_their_consumers(handler) -> None:
    """The allowlist must not eat the legitimate case — this is the regression
    the domain check itself could cause."""
    handler._apply_house_config(_HOUSE)
    lights_kw = handler._party_lights.configure.call_args.kwargs
    assert lights_kw["light_entities"] == ["light.living_room"]
    assert lights_kw["winner_scene_entity"] == "scene.party"
    assert (
        handler._sound_effects.configure.call_args.kwargs["media_player"]
        == "media_player.kitchen"
    )

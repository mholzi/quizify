"""Server/WS plumbing for the "House Plays Along" admin panel (#494 Phase 4).

The panel persists ONE flat settings dict in localStorage and pushes it to the
backend — on ``ws.onopen`` (right after ``admin_connect``) as a ``configure_house``
message, and again on the ``start_game`` payload under a ``house`` key. Presets
are a frontend-only concept: the backend only ever sees resolved booleans.

Covered here:
  * ``configure_house`` is admin-gated (a non-admin frame is rejected outright);
  * ``_apply_house_config`` fans the dict out to ALL THREE consumers — party
    lights, sound effects, and the bus-event emitter — since the panel's single
    master switch sits over three internally-independent subsystems;
  * a malformed / partial / hostile dict degrades to defaults instead of raising,
    and one exploding consumer cannot take the other two (or the frame) down;
  * the ``start_game`` payload path applies ``house`` like it applies ``tts``,
    while an ABSENT block leaves the config-entry options in force;
  * the lobby-time path works with no game in progress (that's the whole point of
    the on-connect push — join-time cues fire before ``start_game``);
  * ``snapshot_house_entities`` returns the three sorted lists and degrades to
    empty lists on the standalone dev server (no hass);
  * the lists ride the authenticated admin-connect frame (#502 lesson: a lone
    token-gated HTTP fetch races the admin token and 401s).
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from custom_components.quizify.game.state import (  # noqa: E402
    GamePhase,
    QuizifyGameState,
)
from custom_components.quizify.server import WS_PATH, views  # noqa: E402
from custom_components.quizify.server.connection import ConnectionManager  # noqa: E402
from custom_components.quizify.server.context import APP_CTX_KEY  # noqa: E402
from custom_components.quizify.server.serializers import (  # noqa: E402
    snapshot_house_entities,
)
from custom_components.quizify.server.websocket import (  # noqa: E402
    MSG_CONFIGURE_HOUSE,
    QuizifyWebSocketHandler,
)

# The full contract the admin panel persists + pushes. Every child toggle ON,
# master ON, no entity overrides — the "everything armed, use the config-entry
# entities" shape.
_FULL_HOUSE = {
    "enabled": True,
    "light_question": True,
    "light_countdown": True,
    "light_reveal": True,
    "light_streak": True,
    "light_winner": True,
    "winner_scene": True,
    "sfx_correct": True,
    "sfx_wrong": True,
    "sfx_streak": True,
    "sfx_winner": True,
    "light_entities": [],
    "media_player": "",
    "winner_scene_entity": "",
}


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


class _FakeRuntime:
    def __init__(self, tmp_path: Path, hass=None) -> None:  # noqa: ANN001
        self.data_dir = tmp_path
        # Mirrors HARuntime.hass — the WS handler reads it via getattr, so a
        # missing attribute (standalone dev server) degrades to empty lists.
        if hass is not None:
            self.hass = hass

    def create_task(self, coro):  # noqa: ANN001, ANN202
        return asyncio.ensure_future(coro)

    async def run_in_executor(self, func, *args):  # noqa: ANN001, ANN002
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, func, *args)


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
def handler(game, tmp_path: Path, monkeypatch):
    """Handler with the three house consumers stubbed as recording mocks.

    Deliberately mocks (rather than wires the real QuizifyPartyLights /
    QuizifySoundEffects / QuizifyEventEmitter): the unit under test is the WS
    applier's fan-out + defensiveness, not the consumers' own behaviour. Mocks
    also let us make one of them explode on demand.
    """
    runtime = _FakeRuntime(tmp_path)
    h = QuizifyWebSocketHandler(runtime=runtime, game_state_provider=lambda: game)
    h._conn = ConnectionManager(runtime, lambda: game)
    h._conn.broadcast = AsyncMock()
    h._conn.send_error = AsyncMock()
    h._conn.broadcast_to_admins_and_dashboards = AsyncMock()
    h.START_REDIRECT_GRACE = 0
    monkeypatch.setattr(h, "_start_timer_tick", lambda *a, **k: None)

    h.set_party_lights(MagicMock())
    h.set_sound_effects(MagicMock())
    h.set_event_emitter(MagicMock())
    return h


# ---------------------------------------------------------------------------
# Setters
# ---------------------------------------------------------------------------


def test_setters_wire_and_clear(handler) -> None:
    """set_party_lights / set_sound_effects mirror set_tts_announcer: they wire
    a consumer and ``None`` clears it back to the no-op path. __init__.py calls
    these at setup AND on every options reload."""
    lights, effects = MagicMock(), MagicMock()
    handler.set_party_lights(lights)
    handler.set_sound_effects(effects)
    assert handler._party_lights is lights
    assert handler._sound_effects is effects

    handler.set_party_lights(None)
    handler.set_sound_effects(None)
    assert handler._party_lights is None
    assert handler._sound_effects is None
    # And applying against the cleared consumers must not raise.
    handler._apply_house_config(_FULL_HOUSE)


# ---------------------------------------------------------------------------
# Fan-out to all three consumers
# ---------------------------------------------------------------------------


def test_apply_fans_out_to_all_three_consumers(handler) -> None:
    handler._apply_house_config(_FULL_HOUSE)

    lights_kw = handler._party_lights.configure.call_args.kwargs
    assert lights_kw["enabled"] is True
    assert lights_kw["light_question"] is True
    assert lights_kw["light_countdown"] is True
    assert lights_kw["light_reveal"] is True
    assert lights_kw["light_streak"] is True
    assert lights_kw["light_winner"] is True
    assert lights_kw["winner_scene"] is True
    # Empty selections mean "no override" → the consumer falls back to the
    # config-entry defaults (CONF_PARTY_LIGHT_ENTITIES / CONF_FINALE_SCENE).
    assert lights_kw["light_entities"] == []
    assert lights_kw["winner_scene_entity"] == ""

    sfx_kw = handler._sound_effects.configure.call_args.kwargs
    assert sfx_kw["enabled"] is True
    assert sfx_kw["sfx_correct"] is True
    assert sfx_kw["sfx_wrong"] is True
    assert sfx_kw["sfx_streak"] is True
    assert sfx_kw["sfx_winner"] is True
    assert sfx_kw["media_player"] == ""

    # The emitter honours the master only — the bus events are the substrate the
    # lights + SFX subscribe to.
    handler._event_emitter.configure.assert_called_once_with(enabled=True)


def test_master_off_reaches_every_consumer(handler) -> None:
    """The panel's master switch must silence all three subsystems in one frame
    — otherwise the emitter keeps spamming quizify_* at the host's automations."""
    handler._apply_house_config({**_FULL_HOUSE, "enabled": False})

    assert handler._party_lights.configure.call_args.kwargs["enabled"] is False
    assert handler._sound_effects.configure.call_args.kwargs["enabled"] is False
    handler._event_emitter.configure.assert_called_once_with(enabled=False)


def test_individual_toggles_are_passed_through(handler) -> None:
    handler._apply_house_config(
        {
            **_FULL_HOUSE,
            "light_countdown": False,
            "winner_scene": False,
            "sfx_wrong": False,
        }
    )
    lights_kw = handler._party_lights.configure.call_args.kwargs
    assert lights_kw["light_countdown"] is False
    assert lights_kw["winner_scene"] is False
    assert lights_kw["light_question"] is True  # untouched neighbours stay on

    sfx_kw = handler._sound_effects.configure.call_args.kwargs
    assert sfx_kw["sfx_wrong"] is False
    assert sfx_kw["sfx_correct"] is True


def test_entity_overrides_are_passed_through(handler) -> None:
    handler._apply_house_config(
        {
            **_FULL_HOUSE,
            "light_entities": ["light.wohnzimmer", "light.kueche"],
            "media_player": "media_player.sonos",
            "winner_scene_entity": "scene.party",
        }
    )
    lights_kw = handler._party_lights.configure.call_args.kwargs
    assert lights_kw["light_entities"] == ["light.wohnzimmer", "light.kueche"]
    assert lights_kw["winner_scene_entity"] == "scene.party"
    assert (
        handler._sound_effects.configure.call_args.kwargs["media_player"]
        == "media_player.sonos"
    )


def test_preset_key_is_ignored(handler) -> None:
    """Presets are frontend-only — the backend sees resolved booleans and must
    not choke on (or interpret) a stray ``preset`` key."""
    handler._apply_house_config({**_FULL_HOUSE, "preset": "full-party"})
    assert handler._party_lights.configure.call_args.kwargs["enabled"] is True


# ---------------------------------------------------------------------------
# Defensiveness — untyped client JSON
# ---------------------------------------------------------------------------


def test_empty_dict_defaults_master_off_children_on(handler) -> None:
    """Partial dict → master defaults OFF (like TTS), children default ON, so a
    half-populated payload degrades to "master decides" rather than to a
    silently half-dead panel."""
    handler._apply_house_config({})

    lights_kw = handler._party_lights.configure.call_args.kwargs
    assert lights_kw["enabled"] is False
    assert lights_kw["light_question"] is True
    assert lights_kw["winner_scene"] is True
    assert lights_kw["light_entities"] == []

    sfx_kw = handler._sound_effects.configure.call_args.kwargs
    assert sfx_kw["enabled"] is False
    assert sfx_kw["sfx_correct"] is True
    handler._event_emitter.configure.assert_called_once_with(enabled=False)


def test_partial_dict_keeps_present_keys(handler) -> None:
    handler._apply_house_config({"enabled": True, "sfx_winner": False})
    assert handler._sound_effects.configure.call_args.kwargs["enabled"] is True
    assert handler._sound_effects.configure.call_args.kwargs["sfx_winner"] is False
    assert handler._sound_effects.configure.call_args.kwargs["sfx_streak"] is True


def test_malformed_types_are_coerced_not_raised(handler) -> None:
    """Garbage from the wire must never raise out of the applier — a raise here
    would kill the admin's whole message frame."""
    handler._apply_house_config(
        {
            "enabled": "yes",  # truthy string, not a bool
            "light_question": 0,  # falsy int
            "light_entities": "light.not_a_list",  # str, not a list
            "media_player": None,
            "winner_scene_entity": 42,
        }
    )
    lights_kw = handler._party_lights.configure.call_args.kwargs
    assert lights_kw["enabled"] is True
    assert lights_kw["light_question"] is False
    # A non-list light_entities is dropped rather than smuggled into a light
    # service call (where a bare string would iterate into characters).
    assert lights_kw["light_entities"] == []
    assert lights_kw["winner_scene_entity"] == "42"
    assert handler._sound_effects.configure.call_args.kwargs["media_player"] == ""


def test_one_exploding_consumer_does_not_kill_the_others(handler) -> None:
    """A stale entity id (host removed the light) must at worst kill its own
    subsystem — never the other two, and never the frame."""
    handler._party_lights.configure.side_effect = RuntimeError("stale light entity")

    handler._apply_house_config(_FULL_HOUSE)  # must not raise

    # The other two were still configured despite the lights blowing up.
    assert handler._sound_effects.configure.call_args.kwargs["enabled"] is True
    handler._event_emitter.configure.assert_called_once_with(enabled=True)


def test_unwired_consumers_are_a_noop(handler) -> None:
    """Standalone dev server: nothing wired → applying is a silent no-op."""
    handler.set_party_lights(None)
    handler.set_sound_effects(None)
    handler.set_event_emitter(None)
    handler._apply_house_config(_FULL_HOUSE)  # must not raise


# ---------------------------------------------------------------------------
# configure_house message: admin gate + lobby-time path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_configure_house_is_admin_gated(handler, game) -> None:
    """A non-admin socket must never reconfigure the host's lights: the frame is
    rejected with "Admin only" and no consumer is touched."""
    admin_ws = _ws()
    handler._conn.add_connection(admin_ws, is_admin=True, is_dashboard=False)
    game.add_player("Host", admin_ws)
    game.get_player("Host").is_admin = True

    rogue_ws = _ws()
    handler._conn.add_connection(rogue_ws, is_admin=False, is_dashboard=False)
    game.add_player("Rogue", rogue_ws)

    await handler._handle_message(
        rogue_ws,
        {"type": MSG_CONFIGURE_HOUSE, **_FULL_HOUSE},
        is_admin=False,
    )

    handler._conn.send_error.assert_awaited()
    assert handler._conn.send_error.await_args.args[2] == "Admin only"
    handler._party_lights.configure.assert_not_called()
    handler._sound_effects.configure.assert_not_called()
    handler._event_emitter.configure.assert_not_called()


@pytest.mark.asyncio
async def test_configure_house_from_admin_applies(handler, game) -> None:
    admin_ws = _ws()
    handler._conn.add_connection(admin_ws, is_admin=True, is_dashboard=False)

    await handler._handle_message(
        admin_ws,
        {"type": MSG_CONFIGURE_HOUSE, **_FULL_HOUSE, "sfx_streak": False},
        is_admin=True,
    )

    assert handler._party_lights.configure.call_args.kwargs["enabled"] is True
    assert handler._sound_effects.configure.call_args.kwargs["sfx_streak"] is False
    handler._event_emitter.configure.assert_called_once_with(enabled=True)


@pytest.mark.asyncio
async def test_configure_house_works_in_lobby_before_any_game(handler, game) -> None:
    """The panel pushes this on ws.onopen, so it MUST work with no game started —
    the lobby cues (join glow, lobby SFX) fire before start_game ever lands."""
    assert game.phase == GamePhase.LOBBY
    assert game.game_id is None

    await handler._handle_configure_house(_ws(), dict(_FULL_HOUSE), game)

    assert handler._party_lights.configure.call_args.kwargs["enabled"] is True
    # The applier never touches game_state — the lobby stays untouched.
    assert game.phase == GamePhase.LOBBY
    assert game.game_id is None


@pytest.mark.asyncio
async def test_configure_house_tolerates_empty_payload(handler, game) -> None:
    await handler._handle_configure_house(_ws(), {}, game)
    assert handler._party_lights.configure.call_args.kwargs["enabled"] is False


# ---------------------------------------------------------------------------
# start_game payload path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_game_applies_house_block(handler, game) -> None:
    """A ``house`` key on the start_game payload is applied exactly like ``tts``."""
    admin = _ws()
    game.add_player("Markus", admin)
    game.get_player("Markus").is_admin = True

    await handler._handle_start_game(
        admin,
        {
            "category": "geographie",
            "num_rounds": 5,
            "language": "de",
            "timer_duration": 30,
            "house": {**_FULL_HOUSE, "light_reveal": False},
        },
        game,
    )

    assert game.phase == GamePhase.QUESTION_ACTIVE
    lights_kw = handler._party_lights.configure.call_args.kwargs
    assert lights_kw["enabled"] is True
    assert lights_kw["light_reveal"] is False
    handler._event_emitter.configure.assert_called_once_with(enabled=True)


@pytest.mark.asyncio
async def test_start_game_without_house_block_leaves_config_in_force(
    handler, game
) -> None:
    """No ``house`` block → the applier is SKIPPED, not fed an empty dict.

    Feeding ``{}`` would read the master as False and silently disarm whatever
    the host enabled in the config-entry options (CONF_HOUSE_EVENTS_ENABLED) or
    already pushed via the lobby-time configure_house. Deliberate asymmetry with
    the ``tts`` block, which does default to an empty dict.
    """
    admin = _ws()
    game.add_player("Markus", admin)
    game.get_player("Markus").is_admin = True

    await handler._handle_start_game(
        admin,
        {"category": "geographie", "num_rounds": 5, "language": "de"},
        game,
    )

    handler._party_lights.configure.assert_not_called()
    handler._sound_effects.configure.assert_not_called()
    handler._event_emitter.configure.assert_not_called()


@pytest.mark.asyncio
async def test_service_start_game_leaves_house_config_untouched(handler, game) -> None:
    """The HA-service entry point (#367, voice/Zigbee/dashboard button) has no
    panel and passes no overrides — it must not disarm the house features."""
    await handler.admin_action_start_game(game)

    assert game.phase == GamePhase.QUESTION_ACTIVE
    handler._party_lights.configure.assert_not_called()
    handler._event_emitter.configure.assert_not_called()


# ---------------------------------------------------------------------------
# Signature contract against the REAL consumers
# ---------------------------------------------------------------------------
# The tests above stub the consumers, which is right for the applier's own
# logic but blind to signature drift: the applier lives in server/websocket.py
# while the three ``configure()`` methods live in lights.py / sound_effects.py /
# game_events.py. If someone renames a kwarg on either side, every mock-based
# test above still passes and the panel silently stops working at runtime. This
# binds the applier's ACTUAL calls against the REAL signatures, so the two sides
# cannot drift apart unnoticed.


def _real_consumer_spy(cls, method: str = "configure"):  # noqa: ANN001, ANN202
    """A mock whose ``configure`` rejects any call the real class would reject."""
    import inspect

    sig = inspect.signature(getattr(cls, method))
    spy = MagicMock()

    def _checked(*args, **kwargs):  # noqa: ANN002, ANN003
        # ``self`` is not part of the bound-method call the applier makes.
        sig.bind(None, *args, **kwargs)

    spy.configure.side_effect = _checked
    return spy


def test_applier_calls_match_the_real_consumer_signatures(handler) -> None:
    from custom_components.quizify.game_events import QuizifyEventEmitter
    from custom_components.quizify.lights import QuizifyPartyLights
    from custom_components.quizify.sound_effects import QuizifySoundEffects

    handler.set_party_lights(_real_consumer_spy(QuizifyPartyLights))
    handler.set_sound_effects(_real_consumer_spy(QuizifySoundEffects))
    handler.set_event_emitter(_real_consumer_spy(QuizifyEventEmitter))

    # The applier guards each configure() in a try/except, so a TypeError from a
    # bad signature would be swallowed — assert the calls actually LANDED.
    handler._apply_house_config(_FULL_HOUSE)

    handler._party_lights.configure.assert_called_once()
    handler._sound_effects.configure.assert_called_once()
    handler._event_emitter.configure.assert_called_once()


# ---------------------------------------------------------------------------
# snapshot_house_entities (pure helper)
# ---------------------------------------------------------------------------


class _FakeState:
    def __init__(self, entity_id: str, friendly_name: str | None = None) -> None:
        self.entity_id = entity_id
        self.attributes = {}
        if friendly_name is not None:
            self.attributes["friendly_name"] = friendly_name


class _FakeStates:
    def __init__(self, by_domain: dict[str, list[_FakeState]]) -> None:
        self._by_domain = by_domain

    def async_all(self, domain: str) -> list[_FakeState]:
        return list(self._by_domain.get(domain, []))


class _FakeHass:
    def __init__(self, by_domain: dict[str, list[_FakeState]]) -> None:
        self.states = _FakeStates(by_domain)


def _house_hass() -> _FakeHass:
    return _FakeHass(
        {
            "light": [
                _FakeState("light.kueche", "Küche"),
                _FakeState("light.wohnzimmer", "Wohnzimmer"),
                _FakeState("light.bare"),  # no friendly_name
            ],
            "media_player": [_FakeState("media_player.sonos", "Sonos")],
            "scene": [
                _FakeState("scene.party", "Party"),
                _FakeState("scene.abend", "Abend"),
            ],
        }
    )


def test_snapshot_house_none_hass_returns_empty_lists() -> None:
    """Standalone dev server (no hass) → three empty lists, no crash."""
    assert snapshot_house_entities(None) == {
        "lights": [],
        "media_players": [],
        "scenes": [],
    }


def test_snapshot_house_returns_three_sorted_lists() -> None:
    out = snapshot_house_entities(_house_hass())
    assert set(out) == {"lights", "media_players", "scenes"}

    # Case-insensitive sort by friendly_name; a missing friendly_name falls back
    # to the entity_id (so "light.bare" sorts under "l").
    assert [e["friendly_name"] for e in out["lights"]] == [
        "Küche",
        "light.bare",
        "Wohnzimmer",
    ]
    assert out["media_players"] == [
        {"entity_id": "media_player.sonos", "friendly_name": "Sonos"}
    ]
    assert [e["entity_id"] for e in out["scenes"]] == ["scene.abend", "scene.party"]


def test_snapshot_house_missing_domain_is_empty_not_error() -> None:
    """A host with no scenes at all still gets a well-formed payload."""
    out = snapshot_house_entities(_FakeHass({"light": [_FakeState("light.a", "A")]}))
    assert out["scenes"] == []
    assert out["media_players"] == []
    assert len(out["lights"]) == 1


# ---------------------------------------------------------------------------
# Delivery: WS piggyback (primary) + HTTP route (parity)
# ---------------------------------------------------------------------------


def _make_handler(tmp_path: Path, hass=None) -> QuizifyWebSocketHandler:  # noqa: ANN001
    runtime = _FakeRuntime(tmp_path, hass=hass)
    game = QuizifyGameState(runtime=runtime, entry_id="test")
    h = QuizifyWebSocketHandler(runtime=runtime, game_state_provider=lambda: game)
    h._conn = ConnectionManager(runtime, lambda: game)
    return h


async def _make_client(handler: QuizifyWebSocketHandler) -> TestClient:
    app = web.Application()
    app.router.add_get(WS_PATH, handler.handle)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


async def _first_game_state(ws) -> dict:  # noqa: ANN001
    await ws.send_json({"type": "admin_connect"})
    for _ in range(100):
        msg = await asyncio.wait_for(ws.receive_json(), timeout=5)
        if msg.get("type") == "game_state":
            return msg
    raise AssertionError("no game_state frame received")


@pytest.mark.asyncio
async def test_admin_connect_frame_carries_house_entities(tmp_path: Path) -> None:
    """#502 lesson: the panel's pickers populate from the ALREADY-authenticated
    admin socket, so they never race the admin token against a token-gated fetch."""
    handler = _make_handler(tmp_path, hass=_house_hass())
    client = await _make_client(handler)
    try:
        ws = await client.ws_connect(WS_PATH + "?role=admin")
        msg = await _first_game_state(ws)

        assert "admin_session_token" in msg  # still authenticated as before
        house = msg["house_entities"]
        assert set(house) == {"lights", "media_players", "scenes"}
        assert [e["entity_id"] for e in house["scenes"]] == [
            "scene.abend",
            "scene.party",
        ]
        assert house["media_players"] == [
            {"entity_id": "media_player.sonos", "friendly_name": "Sonos"}
        ]
        await ws.close()
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_admin_connect_frame_house_entities_empty_without_hass(
    tmp_path: Path,
) -> None:
    """Standalone runtime → the frame still carries the key, with empty lists."""
    handler = _make_handler(tmp_path, hass=None)
    client = await _make_client(handler)
    try:
        ws = await client.ws_connect(WS_PATH + "?role=admin")
        msg = await _first_game_state(ws)
        assert msg["house_entities"] == {
            "lights": [],
            "media_players": [],
            "scenes": [],
        }
        await ws.close()
    finally:
        await client.close()


def test_house_entities_route_is_registered() -> None:
    """HTTP parity endpoint for the WS piggyback above."""
    assert ("GET", "/api/quizify/house-entities", views.house_entities_view) in (
        views.ROUTES
    )


class _GateReq:
    """Minimal aiohttp-request stand-in for the admin-token gate."""

    def __init__(self, ctx, *, token: str | None = None) -> None:  # noqa: ANN001
        self.app = {APP_CTX_KEY: ctx}
        self.query: dict[str, str] = {} if token is None else {"token": token}
        self.headers: dict[str, str] = {}


class _GateConn:
    def validate_admin_token(self, token: str) -> bool:
        return token == "valid-admin-token"


def _gate_ctx(hass=None):  # noqa: ANN001
    runtime = SimpleNamespace()
    if hass is not None:
        runtime.hass = hass
    return SimpleNamespace(
        runtime=runtime, ws_handler=SimpleNamespace(conn=_GateConn())
    )


def test_house_entities_view_without_token_is_401() -> None:
    """The endpoint enumerates the host's home (lights/scenes/media players), so
    it carries the same admin gate as its tts-entities sibling. The full
    parametrized gate matrix lives in tests/test_admin_token_gating_356.py; this
    pins the 401 so the gate can't be dropped from THIS view unnoticed."""
    resp = asyncio.run(views.house_entities_view(_GateReq(_gate_ctx())))  # type: ignore[arg-type]
    assert resp.status == 401
    assert json.loads(resp.body) == {"error": "unauthorized"}


def test_house_entities_view_with_token_returns_the_three_lists() -> None:
    req = _GateReq(_gate_ctx(hass=_house_hass()), token="valid-admin-token")
    resp = asyncio.run(views.house_entities_view(req))  # type: ignore[arg-type]
    assert resp.status == 200
    body = json.loads(resp.body)
    assert set(body) == {"lights", "media_players", "scenes"}
    assert [e["entity_id"] for e in body["scenes"]] == ["scene.abend", "scene.party"]


def test_house_entities_view_without_hass_returns_empty_lists() -> None:
    """Standalone dev server parity with the WS frame."""
    req = _GateReq(_gate_ctx(), token="valid-admin-token")
    resp = asyncio.run(views.house_entities_view(req))  # type: ignore[arg-type]
    assert json.loads(resp.body) == {"lights": [], "media_players": [], "scenes": []}

"""Tests for the game-control HA services (issue #367).

Exposes a safe subset of the admin game controls (``start_game``,
``next_round``, ``pause``, ``resume``, ``end_game``) as Home Assistant services
so hosts can drive the game via Assist voice, a Zigbee remote, a dashboard
button or an automation instead of the admin WebSocket UI.

Two surfaces are covered:

* Integration level (real ``hass`` fixture, like ``test_init_313``): the
  services are registered, they transition the game with the same phase
  preconditions as the admin handlers, they raise ``ServiceValidationError``
  (never a raw ``KeyError``) on a bad phase / when the integration isn't set
  up, and a successful call fans out a broadcast so entities + clients refresh.
* Unit level (WS handler + game state with a fake connection): the refactored
  admin ``_handle_*`` and the ``admin_action_*`` service core drive ONE shared
  implementation — the WS path and the service path reach the same result.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from custom_components.quizify.const import DOMAIN  # noqa: E402
from custom_components.quizify.game.state import (  # noqa: E402
    GamePhase,
    QuizifyGameState,
)

# ---------------------------------------------------------------------------
# Unit-level: shared-core equivalence (WS path == service path)
# ---------------------------------------------------------------------------
#
# These don't need Home Assistant — they drive the WS handler + game state with
# a fake connection, mirroring test_admin_pause_cleanup_362.

from custom_components.quizify.server.connection import (  # noqa: E402
    ConnectionManager,
)
from custom_components.quizify.server.websocket import (  # noqa: E402
    QuizifyWebSocketHandler,
)


class _Runtime:
    def __init__(self, tmp_path: Path) -> None:
        self.data_dir = tmp_path

    async def run_in_executor(self, func, *args):  # noqa: ANN001, ANN002
        return func(*args)

    def create_task(self, coro):  # noqa: ANN001
        import asyncio

        return asyncio.ensure_future(coro)


def _fake_ws() -> MagicMock:
    ws = MagicMock()
    ws.closed = False
    ws.send_json = AsyncMock()
    ws.close = AsyncMock()
    return ws


def _make_handler(
    game: QuizifyGameState, tmp_path: Path
) -> QuizifyWebSocketHandler:
    runtime = _Runtime(tmp_path)
    h = QuizifyWebSocketHandler(runtime=runtime, game_state_provider=lambda: game)
    h._conn = ConnectionManager(runtime, lambda: game)
    h._conn.broadcast = AsyncMock()
    # Keep grace/timer machinery out of the way for the deterministic asserts.
    h.START_REDIRECT_GRACE = 0.0
    return h


def _new_active_game(tmp_path: Path) -> QuizifyGameState:
    """A game driven to QUESTION_ACTIVE on round 1 (lightning disabled)."""
    gs = QuizifyGameState(runtime=_Runtime(tmp_path), entry_id="test")
    gs.add_player("A", _fake_ws())
    gs.start_game(num_rounds=10, lightning_enabled=False)
    gs.start_next_question()
    assert gs.phase == GamePhase.QUESTION_ACTIVE
    return gs


@pytest.mark.asyncio
async def test_pause_ws_and_service_paths_match(tmp_path: Path) -> None:
    """``_handle_pause_game`` (WS) and ``admin_action_pause`` (service core)
    reach the identical result: both pause the round and both broadcast."""
    gs_ws = _new_active_game(tmp_path)
    gs_svc = _new_active_game(tmp_path)
    handler = _make_handler(gs_ws, tmp_path)

    # WS admin path.
    await handler._handle_pause_game(_fake_ws(), gs_ws)
    # Service-core path.
    paused = await handler.admin_action_pause(gs_svc)

    assert paused is True
    assert gs_ws.phase == GamePhase.PAUSED
    assert gs_svc.phase == GamePhase.PAUSED
    # Both drove a broadcast through the same shared core.
    assert handler._conn.broadcast.await_count == 2


@pytest.mark.asyncio
async def test_pause_service_core_reports_noop_off_active_phase(
    tmp_path: Path,
) -> None:
    """The service core returns False when nothing is pausable (LOBBY), so the
    service layer can turn that into a ServiceValidationError."""
    gs = QuizifyGameState(runtime=_Runtime(tmp_path), entry_id="test")
    handler = _make_handler(gs, tmp_path)
    assert gs.phase == GamePhase.LOBBY
    assert await handler.admin_action_pause(gs) is False


@pytest.mark.asyncio
async def test_resume_service_core_roundtrips_paused(tmp_path: Path) -> None:
    """pause then resume via the service cores round-trips through PAUSED."""
    gs = _new_active_game(tmp_path)
    handler = _make_handler(gs, tmp_path)

    assert await handler.admin_action_pause(gs) is True
    assert gs.phase == GamePhase.PAUSED
    assert await handler.admin_action_resume(gs) is True
    assert gs.phase == GamePhase.QUESTION_ACTIVE
    handler._cancel_timer_tick()

    # Resuming a non-paused game is a no-op → False (service raises on it).
    assert await handler.admin_action_resume(gs) is False


@pytest.mark.asyncio
async def test_next_round_core_matches_ws_advance(tmp_path: Path) -> None:
    """``admin_action_next_round`` and ``_handle_next_question`` advance the
    same way from ANSWER_REVEAL (both call the shared ``_advance_round``)."""
    gs_ws = _new_active_game(tmp_path)
    gs_svc = _new_active_game(tmp_path)
    handler = _make_handler(gs_ws, tmp_path)
    # Both need to be at an advanceable phase.
    gs_ws.evaluate_round()
    gs_svc.evaluate_round()
    assert gs_ws.phase == GamePhase.ANSWER_REVEAL
    assert gs_svc.phase == GamePhase.ANSWER_REVEAL

    await handler._handle_next_question(_fake_ws(), gs_ws)
    await handler.admin_action_next_round(gs_svc)

    assert gs_ws.round == 2
    assert gs_svc.round == 2
    assert gs_ws.phase == GamePhase.QUESTION_ACTIVE
    assert gs_svc.phase == GamePhase.QUESTION_ACTIVE
    handler._cancel_timer_tick()


@pytest.mark.asyncio
async def test_next_round_core_raises_off_phase(tmp_path: Path) -> None:
    """From QUESTION_ACTIVE the advance is invalid → the core raises so the
    service surfaces a ServiceValidationError (WS path would send an error)."""
    gs = _new_active_game(tmp_path)
    handler = _make_handler(gs, tmp_path)
    with pytest.raises(ValueError):
        await handler.admin_action_next_round(gs)


@pytest.mark.asyncio
async def test_end_game_core_ends(tmp_path: Path) -> None:
    """``admin_action_end_game`` transitions to FINALE (shared by WS + svc)."""
    gs = _new_active_game(tmp_path)
    handler = _make_handler(gs, tmp_path)
    await handler.admin_action_end_game(gs)
    assert gs.phase == GamePhase.FINALE


@pytest.mark.asyncio
async def test_start_game_core_requires_lobby(tmp_path: Path) -> None:
    """The start service core only starts from LOBBY — it must NOT nuke a game
    already in progress (unlike the admin re-pick handler)."""
    gs = _new_active_game(tmp_path)
    handler = _make_handler(gs, tmp_path)
    with pytest.raises(ValueError):
        await handler.admin_action_start_game(gs)


# ---------------------------------------------------------------------------
# Integration-level: real hass fixture, real service registry
# ---------------------------------------------------------------------------

pytest.importorskip("homeassistant")
pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant.core import HomeAssistant  # noqa: E402
from homeassistant.exceptions import ServiceValidationError  # noqa: E402
from homeassistant.setup import async_setup_component  # noqa: E402
from pytest_homeassistant_custom_component.common import (  # noqa: E402
    MockConfigEntry,
)

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

_NEW_SERVICES = ("start_game", "next_round", "pause", "resume", "end_game")


@pytest.fixture(autouse=True)
def _stub_frontend_panel():
    """Stub the frontend panel helpers (the hass_frontend wheel is absent under
    the test harness). Mirrors test_init_313."""
    panels: dict = {}

    def _register_panel(_hass, *, frontend_url_path, **_kw):
        panels[frontend_url_path] = True

    def _remove_panel(_hass, path):
        if path not in panels:
            raise KeyError(path)
        del panels[path]

    with (
        patch(
            "homeassistant.components.frontend.async_register_built_in_panel",
            side_effect=_register_panel,
        ),
        patch(
            "homeassistant.components.frontend.async_remove_panel",
            side_effect=_remove_panel,
        ),
    ):
        yield panels


@pytest.fixture
async def http_hass(hass: HomeAssistant) -> HomeAssistant:
    """A hass with the HTTP component set up (setup mounts routes on it)."""
    assert await async_setup_component(hass, "http", {"http": {}})
    await hass.async_block_till_done()
    return hass


async def _setup(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id) is True
    await hass.async_block_till_done()
    # Drop the start-redirect grace so the start_game service test doesn't
    # sleep 2.5s before emitting round 1.
    hass.data[DOMAIN]["ws_handler"].START_REDIRECT_GRACE = 0.0
    return entry


async def test_all_game_control_services_registered(
    http_hass: HomeAssistant,
) -> None:
    """Setup registers every #367 game-control service on the domain."""
    hass = http_hass
    await _setup(hass)
    for name in _NEW_SERVICES:
        assert hass.services.has_service(DOMAIN, name), f"missing service {name}"


async def test_start_game_service_transitions_and_broadcasts(
    http_hass: HomeAssistant,
) -> None:
    """From LOBBY, quizify.start_game starts the game and fans out a broadcast;
    calling it again (now not in LOBBY) raises ServiceValidationError."""
    hass = http_hass
    await _setup(hass)
    ws_handler = hass.data[DOMAIN]["ws_handler"]
    game = hass.data[DOMAIN]["game"]
    assert game.phase == GamePhase.LOBBY

    spy = AsyncMock()
    ws_handler._conn.broadcast = spy

    await hass.services.async_call(DOMAIN, "start_game", {}, blocking=True)
    await hass.async_block_till_done()

    assert game.phase == GamePhase.QUESTION_ACTIVE
    assert game.round == 1
    assert spy.await_count >= 1  # entities + clients refreshed
    ws_handler._cancel_timer_tick()

    # A second start while a game is running is refused, not a silent wipe.
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, "start_game", {}, blocking=True)


async def test_pause_then_resume_roundtrip(http_hass: HomeAssistant) -> None:
    """quizify.pause then quizify.resume round-trips through PAUSED; resuming a
    non-paused game raises ServiceValidationError."""
    hass = http_hass
    await _setup(hass)
    game = hass.data[DOMAIN]["game"]
    # Drive to QUESTION_ACTIVE directly (bypass the WS grace/timer path).
    game.start_game(num_rounds=10, lightning_enabled=False)
    game.start_next_question()
    assert game.phase == GamePhase.QUESTION_ACTIVE

    await hass.services.async_call(DOMAIN, "pause", {}, blocking=True)
    assert game.phase == GamePhase.PAUSED

    await hass.services.async_call(DOMAIN, "resume", {}, blocking=True)
    await hass.async_block_till_done()
    assert game.phase == GamePhase.QUESTION_ACTIVE
    hass.data[DOMAIN]["ws_handler"]._cancel_timer_tick()

    # Resuming when not paused → validation error.
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, "resume", {}, blocking=True)


async def test_pause_off_active_phase_raises(http_hass: HomeAssistant) -> None:
    """Pausing with no active question (LOBBY) raises ServiceValidationError
    rather than silently no-opping."""
    hass = http_hass
    await _setup(hass)
    game = hass.data[DOMAIN]["game"]
    assert game.phase == GamePhase.LOBBY
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, "pause", {}, blocking=True)


async def test_next_round_advances_and_end_game_ends(
    http_hass: HomeAssistant,
) -> None:
    """quizify.next_round advances mid-game; quizify.end_game ends the game."""
    hass = http_hass
    await _setup(hass)
    game = hass.data[DOMAIN]["game"]
    ws_handler = hass.data[DOMAIN]["ws_handler"]
    game.start_game(num_rounds=10, lightning_enabled=False)
    game.start_next_question()
    game.evaluate_round()
    assert game.phase == GamePhase.ANSWER_REVEAL
    assert game.round == 1

    await hass.services.async_call(DOMAIN, "next_round", {}, blocking=True)
    await hass.async_block_till_done()
    assert game.round == 2
    assert game.phase == GamePhase.QUESTION_ACTIVE
    ws_handler._cancel_timer_tick()

    await hass.services.async_call(DOMAIN, "end_game", {}, blocking=True)
    assert game.phase == GamePhase.FINALE
    # end_game() schedules fire-and-forget analytics/question-stats save tasks;
    # drain them so they don't linger into the hass-fixture teardown (which the
    # harness flags as an unclosed-task error).
    await hass.async_block_till_done()


async def test_next_round_off_phase_raises(http_hass: HomeAssistant) -> None:
    """next_round from a fresh lobby (no game) raises ServiceValidationError."""
    hass = http_hass
    await _setup(hass)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, "next_round", {}, blocking=True)


async def test_end_game_off_phase_raises(http_hass: HomeAssistant) -> None:
    """end_game with no running game (LOBBY) raises ServiceValidationError."""
    hass = http_hass
    await _setup(hass)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, "end_game", {}, blocking=True)


async def test_service_without_setup_raises_validation_error(
    http_hass: HomeAssistant,
) -> None:
    """After the entry is unloaded (hass.data[DOMAIN] gone) a game-control
    service raises ServiceValidationError, not a raw KeyError."""
    hass = http_hass
    entry = await _setup(hass)
    assert await hass.config_entries.async_unload(entry.entry_id) is True
    await hass.async_block_till_done()
    assert DOMAIN not in hass.data

    for name in _NEW_SERVICES:
        with pytest.raises(ServiceValidationError):
            await hass.services.async_call(DOMAIN, name, {}, blocking=True)

"""Starting by voice or button must honour the host's settings (#744).

``quizify.start_game`` had ``fields: {}`` and ``admin_action_start_game`` called
the bare ``game_state.start_game()``: mixed packs, medium, 10 rounds, German,
Lightning and Hot Seat on — whatever the host had actually set up in the lobby.
It then ran ``_apply_tts_config({})``, and ``bool({}.get("enabled"))`` is
``False``, so the quizmaster the host had just switched on went silent for the
whole game. Saved presets were unreachable from every one of these paths.

Three seams are covered here:

* the settings layering (``resolve_start_settings``) — a pure function, so the
  precedence can be asserted without a socket or a Home Assistant;
* the narrator: a socket-less start must LEAVE the lobby-time TTS config alone,
  the way a missing ``house`` block has always been left alone;
* the service itself, through the real HA service registry, including a saved
  preset looked up by name.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import voluptuous as vol

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from custom_components.quizify.const import DOMAIN  # noqa: E402
from custom_components.quizify.game.state import (  # noqa: E402
    GamePhase,
    QuizifyGameState,
)
from custom_components.quizify.server.connection import (  # noqa: E402
    ConnectionManager,
)
from custom_components.quizify.server.websocket import (  # noqa: E402
    QuizifyWebSocketHandler,
)
from custom_components.quizify.tts import QuizifyTTSAnnouncer  # noqa: E402


class _Runtime:
    def __init__(self, tmp_path: Path) -> None:
        self.data_dir = tmp_path

    async def run_in_executor(self, func, *args):  # noqa: ANN001, ANN002
        return func(*args)

    def create_task(self, coro):  # noqa: ANN001
        import asyncio  # noqa: PLC0415

        return asyncio.ensure_future(coro)


def _fake_ws() -> MagicMock:
    ws = MagicMock()
    ws.closed = False
    ws.send_json = AsyncMock()
    ws.close = AsyncMock()
    return ws


def _make_handler(game: QuizifyGameState, tmp_path: Path) -> QuizifyWebSocketHandler:
    runtime = _Runtime(tmp_path)
    h = QuizifyWebSocketHandler(runtime=runtime, game_state_provider=lambda: game)
    h._conn = ConnectionManager(runtime, lambda: game)
    h._conn.broadcast = AsyncMock()
    h.START_REDIRECT_GRACE = 0.0
    return h


def _game(tmp_path: Path) -> QuizifyGameState:
    return QuizifyGameState(runtime=_Runtime(tmp_path), entry_id="test")


# ---------------------------------------------------------------------------
# The settings layering
# ---------------------------------------------------------------------------


def test_last_used_settings_are_the_fallback(tmp_path: Path) -> None:
    """THE #744 case: the host set the evening up, played it, and now says
    "Hey Nabu, start the quiz" from the couch."""
    gs = _game(tmp_path)
    gs.start_game(
        categories=["geographie"],
        difficulty="hard",
        num_rounds=15,
        language="de",
        lightning_enabled=False,
    )
    gs.reset_to_lobby()

    resolved = QuizifyWebSocketHandler.resolve_start_settings(gs)

    assert resolved["categories"] == ["geographie"]
    assert resolved["difficulty"] == "hard"
    assert resolved["num_rounds"] == 15
    assert resolved["lightning_enabled"] is False


def test_a_first_ever_start_falls_through_to_the_game_defaults(
    tmp_path: Path,
) -> None:
    """Nothing played yet → nothing to carry over, and start_game's own
    defaults keep owning every field."""
    assert QuizifyWebSocketHandler.resolve_start_settings(_game(tmp_path)) == {}


def test_the_preset_beats_the_last_game(tmp_path: Path) -> None:
    gs = _game(tmp_path)
    gs.start_game(categories=["geographie"], difficulty="hard", num_rounds=15)
    gs.reset_to_lobby()

    resolved = QuizifyWebSocketHandler.resolve_start_settings(
        gs,
        preset={
            "name": "With kids",
            "packs": ["tiere-natur"],
            "rounds": 8,
            "timer": 45,
            "difficulty": "easy",
            "lightning": False,
            "hot_seat": False,
            "powerups": False,
            "wager": False,
        },
    )

    assert resolved["categories"] == ["tiere-natur"]
    assert resolved["category"] is None
    assert resolved["difficulty"] == "easy"
    assert resolved["num_rounds"] == 8
    assert resolved["timer_duration"] == 45
    assert resolved["powerups_enabled"] is False
    assert resolved["wager_enabled"] is False


def test_the_explicit_fields_beat_the_preset(tmp_path: Path) -> None:
    resolved = QuizifyWebSocketHandler.resolve_start_settings(
        _game(tmp_path),
        preset={"packs": ["tiere-natur"], "rounds": 8, "difficulty": "easy"},
        overrides={"num_rounds": 20, "category": "geographie", "language": "es"},
    )

    assert resolved["num_rounds"] == 20
    assert resolved["category"] == "geographie"
    # The narrower single-pack pick fully replaces the preset's list — it does
    # not merge with it.
    assert resolved["categories"] is None
    assert resolved["language"] == "es"
    assert resolved["difficulty"] == "easy"  # untouched by the overrides


def test_mixed_clears_the_pack_and_difficulty_picks(tmp_path: Path) -> None:
    gs = _game(tmp_path)
    gs.start_game(categories=["geographie"], difficulty="hard")
    gs.reset_to_lobby()

    resolved = QuizifyWebSocketHandler.resolve_start_settings(
        gs, overrides={"category": "mixed", "difficulty": "mixed"}
    )

    assert resolved["category"] is None
    assert resolved["categories"] is None
    assert resolved["difficulty"] is None


def test_a_stale_last_settings_key_can_never_wedge_the_voice_command(
    tmp_path: Path,
) -> None:
    """``last_settings`` is written by ``start_game`` itself, so a future
    version can add a key this one does not accept. Filtering it out here keeps
    "start the quiz" working across an update instead of raising TypeError."""
    gs = _game(tmp_path)
    gs.start_game(num_rounds=12)
    gs.reset_to_lobby()
    gs._last_settings["some_future_field"] = "boom"

    resolved = QuizifyWebSocketHandler.resolve_start_settings(gs)

    assert "some_future_field" not in resolved
    gs.start_game(**resolved)  # would raise TypeError if it leaked through
    assert gs.total_rounds == 12


# ---------------------------------------------------------------------------
# The narrator
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_socketless_start_does_not_mute_the_narrator(
    tmp_path: Path,
) -> None:
    """THE #744 bug. The host switches narration on in the lobby (which reaches
    the announcer over ``configure_tts``, before any ``start_game``), then starts
    the game by voice. The start carries no ``tts`` block, and an absent block
    must mean "leave as configured" — exactly as an absent ``house`` block
    already did — not "narration off"."""
    gs = _game(tmp_path)
    handler = _make_handler(gs, tmp_path)
    announcer = QuizifyTTSAnnouncer(
        hass=None,
        game_state=gs,
        tts_entity_id="tts.cloud",
        media_player_entity_id="media_player.kitchen",
    )
    handler.set_tts_announcer(announcer)
    # The lobby-time push from admin.js.
    handler._apply_tts_config({"enabled": True, "announce_reveal": False})
    assert announcer._enabled is True

    await handler.admin_action_start_game(gs)

    assert gs.phase != GamePhase.LOBBY
    assert announcer._enabled is True
    assert announcer._announce_reveal is False


@pytest.mark.asyncio
async def test_an_admin_start_with_a_tts_block_still_applies_it(
    tmp_path: Path,
) -> None:
    """The counter-regression: the admin panel always sends the block, and it
    must still be able to switch narration OFF for a game."""
    gs = _game(tmp_path)
    handler = _make_handler(gs, tmp_path)
    announcer = QuizifyTTSAnnouncer(
        hass=None,
        game_state=gs,
        tts_entity_id="tts.cloud",
        media_player_entity_id="media_player.kitchen",
    )
    handler.set_tts_announcer(announcer)
    handler._apply_tts_config({"enabled": True})
    assert announcer._enabled is True

    await handler._after_start_game(gs, {"enabled": False})

    assert announcer._enabled is False


@pytest.mark.asyncio
async def test_the_service_core_starts_the_last_used_game(
    tmp_path: Path,
) -> None:
    """End to end on the socket-less core: the game that actually starts is the
    one the host last configured."""
    gs = _game(tmp_path)
    handler = _make_handler(gs, tmp_path)
    gs.start_game(categories=["geographie"], difficulty="hard", num_rounds=15)
    gs.reset_to_lobby()

    await handler.admin_action_start_game(gs)

    assert gs.categories == ["geographie"]
    assert gs.difficulty == "hard"
    assert gs.total_rounds == 15


# ---------------------------------------------------------------------------
# The Lovelace card
# ---------------------------------------------------------------------------

_CARD = (
    _REPO_ROOT
    / "custom_components"
    / "quizify"
    / "www"
    / "cards"
    / "quizify-host-card.js"
)


def test_the_card_starts_through_the_service_not_the_socket() -> None:
    """The card has no setup screen, so its ``start_game`` frame carried no
    settings — and the admin path cannot tell an absent field from an explicit
    null, so it could only answer with the factory game. The service is the
    surface that knows what "no settings given" means (#744)."""
    source = _CARD.read_text(encoding="utf-8")
    assert "callService('quizify', 'start_game'" in source
    # Every control routes through the one dispatcher, so a second start button
    # cannot quietly go back to the socket.
    assert "self._dispatch(primary.msg)" in source
    assert "self._dispatch(el.dataset.msg)" in source
    # …and the socket stays the fallback, so the button is never dead when the
    # card is rendered without a hass.
    assert "this._send('start_game')" in source


# ---------------------------------------------------------------------------
# The service, through the real HA registry
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


@pytest.fixture(autouse=True)
def _stub_frontend_panel():
    """Stub the frontend panel helpers (no hass_frontend wheel under test)."""
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
    assert await async_setup_component(hass, "http", {"http": {}})
    await hass.async_block_till_done()
    return hass


async def _setup(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id) is True
    await hass.async_block_till_done()
    # Drop the start-redirect grace so these don't sleep 2.5s each.
    hass.data[DOMAIN]["ws_handler"].START_REDIRECT_GRACE = 0.0
    return entry


async def test_the_service_accepts_the_hosts_settings(
    http_hass: HomeAssistant,
) -> None:
    """The fields #744 asked for actually reach the game."""
    hass = http_hass
    await _setup(hass)

    await hass.services.async_call(
        DOMAIN,
        "start_game",
        {
            "category": ["geographie"],
            "difficulty": "hard",
            "num_rounds": 15,
            "language": "de",
            "timer_duration": 45,
        },
        blocking=True,
    )
    await hass.async_block_till_done()

    game = hass.data[DOMAIN]["game"]
    assert game.categories == ["geographie"]
    assert game.difficulty == "hard"
    assert game.total_rounds == 15
    assert game.language == "de"


async def test_the_service_starts_a_saved_preset_by_name(
    http_hass: HomeAssistant,
) -> None:
    """Saved presets were unreachable from the service path (#744). They are
    matched on the host-visible NAME — the only part of a preset a voice
    sentence or a dashboard button can carry."""
    hass = http_hass
    await _setup(hass)

    from custom_components.quizify.server.views import (  # noqa: PLC0415
        get_preset_store,
    )

    await get_preset_store(hass.http.app).save(
        {
            "name": "With kids",
            "packs": ["tiere-natur"],
            "rounds": 8,
            "difficulty": "easy",
            "lightning": False,
            "powerups": False,
        }
    )

    await hass.services.async_call(
        DOMAIN, "start_game", {"preset": "with kids"}, blocking=True
    )
    await hass.async_block_till_done()

    game = hass.data[DOMAIN]["game"]
    assert game.categories == ["tiere-natur"]
    assert game.difficulty == "easy"
    assert game.total_rounds == 8


async def test_an_unknown_preset_names_the_ones_that_exist(
    http_hass: HomeAssistant,
) -> None:
    """A typo has to be self-correcting, not silently start the wrong game."""
    hass = http_hass
    await _setup(hass)

    from custom_components.quizify.server.views import (  # noqa: PLC0415
        get_preset_store,
    )

    await get_preset_store(hass.http.app).save({"name": "With kids", "rounds": 8})

    with pytest.raises(ServiceValidationError, match="With kids"):
        await hass.services.async_call(
            DOMAIN, "start_game", {"preset": "with kidz"}, blocking=True
        )
    assert hass.data[DOMAIN]["game"].phase == GamePhase.LOBBY


async def test_the_service_rejects_an_out_of_range_round_count(
    http_hass: HomeAssistant,
) -> None:
    """The schema is the guard: ``total_rounds`` drives the round comparison, so
    a 0 or a 500 must never reach the game state."""
    hass = http_hass
    await _setup(hass)

    with pytest.raises(vol.Invalid):
        await hass.services.async_call(
            DOMAIN, "start_game", {"num_rounds": 0}, blocking=True
        )
    assert hass.data[DOMAIN]["game"].phase == GamePhase.LOBBY

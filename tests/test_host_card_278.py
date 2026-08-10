"""Guard the Lovelace host card (#278).

The card is browser code and this repo has no JS test runner, so its contract
is asserted over the shipped file text — the same approach as
``test_admin_token_durable_storage.py`` and ``test_entity_picker_race_524.py``.

Two things are worth pinning, and they are the two that would fail silently:

1. **The phase → message map.** The card mirrors what ``admin.js`` sends for a
   given ``GamePhase``. Rename a phase in ``game/state.py`` and the card would
   simply stop offering a button — no error, just a dead card that looks fine
   in a screenshot.
2. **The no-token fallback.** #530 shipped a control surface that silently did
   nothing because a credential was missing. The card must always name that
   state and link out of it.

``cards.py`` is tested for the promise it makes to setup: never raise.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
COMPONENT = REPO / "custom_components" / "quizify"
CARD_JS = COMPONENT / "www" / "cards" / "quizify-host-card.js"
PHASES_PY = COMPONENT / "game" / "phase_controller.py"
CARDS_PY = COMPONENT / "cards.py"

# Phase -> the message the card must send. Keep in step with PHASE_ACTIONS in
# the card and with what admin.js does for the same phase.
EXPECTED_ACTIONS = {
    "LOBBY": "start_game",
    "QUESTION_ACTIVE": "admin_skip",
    "ANSWER_REVEAL": "next_question",
    "PAUSED": "resume_game",
    "FINALE": "play_again",
}


def _card_text() -> str:
    return CARD_JS.read_text(encoding="utf-8")


def test_card_file_ships() -> None:
    assert CARD_JS.is_file(), "the host card must ship inside www/ to be served"


def test_every_phase_maps_to_the_expected_message() -> None:
    text = _card_text()
    for phase, message in EXPECTED_ACTIONS.items():
        pattern = re.compile(
            re.escape(phase) + r"\s*:\s*\{[^}]*msg\s*:\s*'" + re.escape(message) + r"'"
        )
        assert pattern.search(text), (
            f"{phase} must trigger {message!r} in PHASE_ACTIONS — a phase whose "
            "mapping is missing or wrong leaves the host with a dead button."
        )


def test_card_phases_exist_in_the_game() -> None:
    """A phase the card knows must still exist in GamePhase."""
    game_phases = set(re.findall(r"^\s+([A-Z_]+) = \"", PHASES_PY.read_text(), re.M))
    assert game_phases, "could not read GamePhase from game/phase_controller.py"

    card_phases = set(re.findall(r"^\s+([A-Z_]+): \{ labelKey", _card_text(), re.M))
    assert card_phases, "could not read PHASE_ACTIONS from the card"

    unknown = card_phases - game_phases
    assert not unknown, (
        f"the card handles phases the game no longer has: {sorted(unknown)}"
    )


def test_auth_frame_goes_first_and_never_into_the_url() -> None:
    """#359: the admin token travels in a frame, not as ?token= in the URL."""
    text = _card_text()
    assert "'admin_auth'" in text, "the card must authenticate with an admin_auth frame"

    ws_path = re.search(r"var WS_PATH = '([^']+)'", text)
    assert ws_path, "the WebSocket path must be a single named constant"
    assert ws_path.group(1) == "/api/quizify/ws?role=admin", (
        "the admin token must never ride in the WebSocket URL — it leaks into "
        f"proxy logs and browser history (#359); got {ws_path.group(1)!r}"
    )
    assert "'?token='" not in text and '"?token="' not in text
    auth_at = text.index("'admin_auth'")
    connect_at = text.index("'admin_connect'")
    assert auth_at < connect_at, (
        "admin_auth must be sent before admin_connect, or the server answers the "
        "connect frame as a plain player"
    )


def test_missing_token_shows_a_way_out_rather_than_dead_controls() -> None:
    """The #530 lesson, pinned: never render an empty control surface."""
    text = _card_text()
    assert "no-token" in text, "the card needs an explicit no-credential state"
    assert "/quizify/admin" in text, (
        "the no-credential state must link to the admin page — that is the only "
        "way for the host to mint the token this card needs"
    )


def test_token_key_is_not_named_in_the_card() -> None:
    """utils.js stays the one place that knows the storage key."""
    text = _card_text()
    assert "quizify_admin_session_token" not in text
    assert "readAdminToken" in text, "the card must reuse the shared token helper"


def test_compact_footer_offers_end_game() -> None:
    """The compact card must be able to END a game, not only run one.

    The #278 spec puts a text-weight "End game" in the compact footer beside
    the join link. The first implementation shipped the footer without it, so
    a host on the compact card could start and advance a game but had to
    switch to the cockpit or the admin page to finish it — found by rendering
    every phase against the deployed 1.7.0-RC1 build.
    """
    text = _card_text()
    foot_at = text.index('<div class="foot"><a href="\' + esc(joinUrl)')
    footer = text[foot_at : foot_at + 700]
    assert "end_game" in footer, (
        "the compact footer must carry the end-game action; without it the "
        "compact card cannot finish a game at all"
    )
    assert "expanded" in footer, (
        "the end-game action belongs to the compact footer only — expanded "
        "already has it in the control row"
    )


def test_reset_is_confirmed() -> None:
    text = _card_text()
    reset_at = text.index("'reset_game'")
    window = text[max(0, reset_at - 400) : reset_at]
    assert "confirm" in window, "reset wipes a running game — it must ask first"


def test_config_rejects_an_unknown_mode() -> None:
    text = _card_text()
    assert "mode must be 'compact' or 'expanded'" in text, (
        "an unknown mode must raise in setConfig so Lovelace shows a config "
        "error instead of rendering an arbitrary density"
    )
    assert "mode: 'compact'" in text, "compact is the documented default"


def test_resource_registration_never_raises() -> None:
    """Setup must survive any Lovelace shape, including none at all."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("quizify_cards", CARDS_PY)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    class _Hass:
        def __init__(self, data):
            self.data = data

    class _Explosive:
        @property
        def resources(self):
            raise RuntimeError("core changed shape")

    for data in ({}, {"lovelace": None}, {"lovelace": _Explosive()}):
        result = asyncio.run(module.async_register_card_resource(_Hass(data), "1.6.1"))
        assert result is False, "an unavailable resource store means 'not registered'"


def test_resource_url_carries_the_cache_buster() -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location("quizify_cards", CARDS_PY)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module._resource_url("1.6.1").endswith("?v=1.6.1")
    assert module._resource_url(None) == module.CARD_URL
    assert module.CARD_URL.startswith("/quizify/static/"), (
        "the card is served by the existing static mount; a different prefix "
        "would need its own route"
    )

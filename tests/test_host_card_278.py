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
3. **Coverage of the whole phase enum (#801).** A phase missing from
   ``PHASE_ACTIONS`` used to fall through to a disabled 'Connecting…' button,
   so the host of a running game read a connection error for the entire wager
   window and the entire Hot Seat detour. Every ``GamePhase`` must now resolve
   to either an action or a deliberate status label, which is what stops the
   next phase added to the game from reintroducing that silence.

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

# Phase -> the message the card must send, or ``None`` where the host has no
# move and the button is a status label instead (#801). Keep in step with
# PHASE_ACTIONS in the card and with what admin.js does for the same phase.
#
# The ``None`` rows are not gaps: the server refuses ``admin_skip`` and
# ``next_question`` in those phases (``_handle_admin_skip`` special-cases only
# WAGER_ACTIVE and QUESTION_ACTIVE, and ``_advance_round`` accepts only LOBBY,
# ANSWER_REVEAL, LIGHTNING_RECAP and HOT_SEAT_REVEAL), so a button there would
# do nothing but raise ERR_INVALID_ACTION.
EXPECTED_ACTIONS: dict[str, str | None] = {
    "LOBBY": "start_game",
    "WAGER_ACTIVE": "admin_skip",
    "QUESTION_ACTIVE": "admin_skip",
    "ANSWER_REVEAL": "next_question",
    "PAUSED": "resume_game",
    "FINALE": "play_again",
    "LIGHTNING": None,
    "LIGHTNING_RECAP": "next_question",
    "HOT_SEAT_AUCTION": None,
    "HOT_SEAT": None,
    "HOT_SEAT_REVEAL": "next_question",
}

#: Phases in which the server actually acts on ``admin_skip``. The expanded
#: card's extra "Reveal" button sends it, so it may only be rendered here.
SKIPPABLE = {"QUESTION_ACTIVE", "WAGER_ACTIVE"}


def _phase_actions() -> dict[str, tuple[str, str | None]]:
    """Parse PHASE_ACTIONS out of the card: phase -> (labelKey, msg|None)."""
    text = _card_text()
    block = re.search(r"var PHASE_ACTIONS = \{(.*?)\n    \};", text, re.S)
    assert block, "could not find PHASE_ACTIONS in the card"
    entries = re.findall(
        r"^\s+([A-Z_]+): \{ labelKey: '(\w+)', msg: (?:'(\w+)'|(null)) \}",
        block.group(1),
        re.M,
    )
    assert entries, "PHASE_ACTIONS parsed as empty — the entry shape changed"
    return {phase: (label, msg or None) for phase, label, msg, _null in entries}


def _card_labels() -> dict[str, set[str]]:
    """Parse the card's inline LABELS: language code -> the keys it defines."""
    text = _card_text()
    block = re.search(r"var LABELS = \{(.*?)\n    \};", text, re.S)
    assert block, "could not find LABELS in the card"
    out: dict[str, set[str]] = {}
    for lang, body in re.findall(
        r"\n        (\w+): \{(.*?)\n        \}", block.group(1), re.S
    ):
        # Drop the quoted values first, so a colon or a word inside a
        # translated string cannot be mistaken for a key.
        stripped = re.sub(r"'[^']*'", "''", body)
        out[lang] = set(re.findall(r"(\w+):", stripped))
    assert out, "LABELS parsed as empty — the bundle shape changed"
    return out


def _game_phases() -> set[str]:
    phases = set(re.findall(r"^\s+([A-Z_]+) = \"", PHASES_PY.read_text(), re.M))
    assert phases, "could not read GamePhase from game/phase_controller.py"
    return phases


def _card_text() -> str:
    return CARD_JS.read_text(encoding="utf-8")


def test_card_file_ships() -> None:
    assert CARD_JS.is_file(), "the host card must ship inside www/ to be served"


def test_every_phase_maps_to_the_expected_message() -> None:
    actions = _phase_actions()
    for phase, message in EXPECTED_ACTIONS.items():
        assert phase in actions, (
            f"{phase} is missing from PHASE_ACTIONS — a phase whose mapping is "
            "absent leaves the host with a dead button."
        )
        assert actions[phase][1] == message, (
            f"{phase} must trigger {message!r} in PHASE_ACTIONS, not "
            f"{actions[phase][1]!r} — a wrong mapping is a button the server "
            "answers with ERR_INVALID_ACTION."
        )


def test_card_phases_exist_in_the_game() -> None:
    """A phase the card knows must still exist in GamePhase."""
    card_phases = set(_phase_actions())
    unknown = card_phases - _game_phases()
    assert not unknown, (
        f"the card handles phases the game no longer has: {sorted(unknown)}"
    )


def test_every_game_phase_is_covered_by_the_card() -> None:
    """#801: no phase may fall through to the disabled 'Connecting…' button.

    ``_primary()`` labels an unknown phase with ``connecting``, which reads as
    a broken connection. It shipped that way for WAGER_ACTIVE, HOT_SEAT_AUCTION,
    HOT_SEAT and HOT_SEAT_REVEAL, so a host running the evening from a wall
    tablet was told the card was still connecting through the whole betting
    window and the whole Hot Seat detour — and at HOT_SEAT_REVEAL, the one
    phase where the server does accept ``next_question``, the card offered
    nothing and the game could not be resumed from it at all.

    This test is the reason the same thing cannot happen to the next phase
    somebody adds to ``GamePhase``: coverage is asserted against the enum, not
    against a hand-kept list.
    """
    missing = _game_phases() - set(_phase_actions())
    assert not missing, (
        "every GamePhase needs a PHASE_ACTIONS entry — either an action or a "
        "deliberate status label with msg: null. Missing: "
        f"{sorted(missing)}. Without one the card tells the host it is "
        "'Connecting…' while the game is running fine."
    )


def test_phases_the_host_cannot_advance_carry_a_real_status_label() -> None:
    """A msg-less entry has to say something other than 'Connecting…'."""
    labels = _card_labels()
    for phase, (label_key, message) in _phase_actions().items():
        if message is not None:
            continue
        assert label_key != "connecting", (
            f"{phase} must name what the room is doing, not claim the card is "
            "still connecting"
        )
        for lang, keys in labels.items():
            assert label_key in keys, (
                f"the {phase} status label {label_key!r} is missing from the "
                f"{lang} bundle — the card would render `undefined`"
            )


def test_every_label_key_the_map_uses_exists_in_all_three_languages() -> None:
    labels = _card_labels()
    assert set(labels) == {"en", "de", "es"}, (
        f"the card must carry exactly en/de/es; got {sorted(labels)}"
    )
    for phase, (label_key, _msg) in _phase_actions().items():
        for lang, keys in labels.items():
            assert label_key in keys, (
                f"{phase} uses label {label_key!r}, absent from the {lang} bundle"
            )


def test_lightning_does_not_send_a_message_the_server_refuses() -> None:
    """LIGHTNING sent ``admin_skip``, which ERR_INVALID_ACTIONs (#801).

    ``_handle_admin_skip`` special-cases WAGER_ACTIVE and QUESTION_ACTIVE only;
    in LIGHTNING it falls through to ``_handle_next_question``, whose phase
    gate rejects it. The lightning loop is server-driven and has no host step,
    so the honest card offers none.
    """
    label_key, message = _phase_actions()["LIGHTNING"]
    assert message is None, (
        "the lightning loop has no host-driven step — offering one gives the "
        f"host a button that errors; got {message!r}"
    )
    assert label_key != "connecting"


def test_reveal_button_is_offered_only_where_admin_skip_works() -> None:
    """The expanded card's extra Reveal button is gated to two phases (#801)."""
    text = _card_text()
    gate = re.search(r"var SKIPPABLE_PHASES = \{([^}]*)\}", text)
    assert gate, (
        "the expanded Reveal button sends admin_skip, which the server refuses "
        "outside QUESTION_ACTIVE/WAGER_ACTIVE — it needs an explicit phase gate"
    )
    assert set(re.findall(r"([A-Z_]+):", gate.group(1))) == SKIPPABLE

    reveal_at = text.index("esc(t.reveal)")
    window = text[max(0, reveal_at - 400) : reveal_at]
    assert "SKIPPABLE_PHASES" in window, (
        "the Reveal button must be rendered behind the phase gate, not always"
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

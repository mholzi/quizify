"""An English game must hand every phone an English frame (#776).

The host picks the language in the admin setup, but that pick only ever
reached the game inside the ``start_game`` payload. For the whole life of the
lobby ``GameState.language`` therefore sat at its constructor default ``"de"``,
and the lobby snapshot said so. ``_syncServerLanguage`` in player-core.js
believes the snapshot — that is its job, a German game should show German
chrome even to an English phone — so every phone joining an English lobby was
stamped German on arrival and stayed German through the first question.

Measured in the v1.15.0-RC1 live test: a freshly opened ``/quizify/player`` is
``lang="en"`` before joining and ``lang="de"`` one second after, with
``localStorage['quizify-player-lang']`` null throughout. The phone never chose
German; the lobby handed it German.

The fix carries the host's pick into the lobby (``set_language``, pushed by
admin.js on connect and on every chip tap, the same contract ``configure_house``
uses) rather than papering over it on the client. Two things follow, and both
are the point: the joining phone's FIRST state frame already says English, and
a host who flips the chip mid-lobby re-renders the phones already sitting in it.

Second half of the same issue: ``renderAllTime`` wrote ``textContent`` and left
no ``data-i18n`` attribute, so the ``initPageTranslations()`` sweep that runs on
a language change walked straight past the line. Verified live: the DOM held
"All-time · 5 of 64 · 3 wins from 3 games" while ``t()`` already returned the
German. Same shape as #648. Fixed by carrying the interpolation params on the
element as JSON so the sweep can re-render them — and while in that string, the
English "1 wins from 2 games" got its singular.
"""

from __future__ import annotations

import asyncio
import json
import re
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
from custom_components.quizify.server import protocol  # noqa: E402
from custom_components.quizify.server.connection import ConnectionManager  # noqa: E402
from custom_components.quizify.server.serializers import (  # noqa: E402
    serialize_state_snapshot,
)
from custom_components.quizify.server.websocket import (  # noqa: E402
    MSG_SET_LANGUAGE,
    QuizifyWebSocketHandler,
)

_CC = _REPO_ROOT / "custom_components" / "quizify"
_WWW = _CC / "www"
_JS = _WWW / "js"
_I18N = _WWW / "i18n"

_LANGS = ("en", "de", "es")

# The five sentences renderAllTime chooses between. Only three of the four
# (wins × games) combinations are reachable once you have won something: two
# wins from one game is not a thing.
_ALL_TIME_KEYS = (
    "allTime",
    "allTimeOneWin",
    "allTimeOneWinOneGame",
    "allTimeFirstWin",
    "allTimeFirstWinOneGame",
)


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


class _FakeRuntime:
    def __init__(self, tmp_path: Path) -> None:
        self.data_dir = tmp_path

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
def handler(game, tmp_path: Path):
    h = QuizifyWebSocketHandler(
        runtime=_FakeRuntime(tmp_path), game_state_provider=lambda: game
    )
    h._conn = ConnectionManager(_FakeRuntime(tmp_path), lambda: game)
    h._conn.send_error = AsyncMock()
    h._broadcast_state_projected = AsyncMock()
    return h


def _without_comments(source: str) -> str:
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"^\s*//.*$", "", source, flags=re.M)


def _js_function(source: str, signature: str) -> str:
    """Return the body of a top-level-ish JS function declaration."""
    start = source.index(signature)
    depth = 0
    seen = False
    for i in range(start, len(source)):
        if source[i] == "{":
            depth += 1
            seen = True
        elif source[i] == "}":
            depth -= 1
            if seen and depth == 0:
                return source[start : i + 1]
    raise AssertionError(f"unbalanced braces after {signature!r}")


# ---------------------------------------------------------------------------
# The lobby carries the host's language
# ---------------------------------------------------------------------------


def test_a_fresh_game_still_defaults_to_german() -> None:
    """Pinned so the fix is read for what it is.

    The default is not the bug — a host who never touches the chip should get
    the same game they got before. The bug was that nothing could change the
    default until start_game, half an hour of lobby later.
    """
    assert QuizifyGameState.__init__ is not None
    source = (_CC / "game" / "state.py").read_text("utf-8")
    assert 'self.language: str = "de"' in source


@pytest.mark.asyncio
async def test_the_host_language_reaches_the_lobby_snapshot(handler, game) -> None:
    """This is the whole issue: before the fix the snapshot said "de" until
    start_game, so the joining phone's very first frame stamped it German."""
    assert game.phase == GamePhase.LOBBY
    assert serialize_state_snapshot(game)["language"] == "de"

    await handler._handle_set_language(_ws(), {"language": "en"}, game)

    assert game.language == "en"
    assert serialize_state_snapshot(game)["language"] == "en"


@pytest.mark.asyncio
async def test_the_change_is_broadcast_to_phones_already_in_the_lobby(
    handler, game
) -> None:
    """Storing it without re-broadcasting would only help phones that join
    AFTER the host flips the chip. The people already staring at a German
    lobby are exactly the ones who need the correction."""
    await handler._handle_set_language(_ws(), {"language": "en"}, game)

    handler._broadcast_state_projected.assert_awaited_once()
    assert handler._broadcast_state_projected.await_args.args[0] is game


@pytest.mark.asyncio
async def test_an_unchanged_language_costs_no_broadcast(handler, game) -> None:
    """admin.js pushes on every socket open, and the socket reopens on every
    HA restart / network blip. Re-broadcasting the full projected state for a
    value that did not move would be pure noise on every phone."""
    await handler._handle_set_language(_ws(), {"language": "de"}, game)

    assert game.language == "de"
    handler._broadcast_state_projected.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_lobby_language_can_be_flipped_more_than_once(handler, game) -> None:
    """Hosts browse. de → en → es must all land, not just the first one."""
    for code in ("en", "es", "de"):
        await handler._handle_set_language(_ws(), {"language": code}, game)
        assert game.language == code

    assert handler._broadcast_state_projected.await_count == 3


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "phase",
    [GamePhase.QUESTION_ACTIVE, GamePhase.ANSWER_REVEAL, GamePhase.FINALE],
)
async def test_it_is_refused_once_the_game_is_running(handler, game, phase) -> None:
    """Swapping the chrome mid-game without swapping the questions would leave
    a German frame around English questions — the very picture from the issue,
    just caused from the other side."""
    game.phase = phase

    await handler._handle_set_language(_ws(), {"language": "en"}, game)

    assert game.language == "de"
    handler._broadcast_state_projected.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload", [{}, {"language": None}, {"language": ""}, {"language": 7}]
)
async def test_a_junk_payload_leaves_the_language_alone(handler, game, payload) -> None:
    """The frame is untyped client JSON. A missing / null / non-string code
    must be ignored rather than written into the snapshot every phone reads."""
    await handler._handle_set_language(_ws(), payload, game)

    assert game.language == "de"
    handler._broadcast_state_projected.assert_not_awaited()


def test_the_message_is_admin_gated() -> None:
    """A player who can rename the room's language is a player who can ruin
    the room's game."""
    handler_fn, admin_required = QuizifyWebSocketHandler._DISPATCH[MSG_SET_LANGUAGE]
    assert admin_required is True
    assert handler_fn is not None


def test_the_wire_contract_declares_it() -> None:
    """tests/test_protocol.py pins CLIENT_MESSAGE_TYPES against the real
    dispatch table; this is the human-readable half of the same statement."""
    assert MSG_SET_LANGUAGE == "set_language"
    assert MSG_SET_LANGUAGE in protocol.CLIENT_MESSAGE_TYPES


# ---------------------------------------------------------------------------
# The admin pushes it — on connect and on every tap
# ---------------------------------------------------------------------------


def test_the_admin_pushes_the_language_on_connect() -> None:
    """The on-connect push is what makes the FIRST phone to join correct. A
    push only on chip-change would leave a host who never touches the chip
    (because HA is already English) serving a German lobby."""
    source = _without_comments((_JS / "admin.js").read_text("utf-8"))
    onopen = source.split("ws.onopen = function ()", 1)[1].split("ws.onmessage", 1)[0]

    assert "_pushLanguage()" in onopen
    assert "send('set_language'" in source


def test_the_admin_pushes_the_language_when_the_chip_changes() -> None:
    """Otherwise the correction still waits for start_game, which is the bug."""
    source = _without_comments((_JS / "admin.js").read_text("utf-8"))
    callback = source.split("setupChips(els.languageChips", 1)[1][:900]

    assert "selectedLanguage = v" in callback
    assert "_pushLanguage()" in callback
    assert callback.index("selectedLanguage = v") < callback.index("_pushLanguage()"), (
        "push the new value, not the old one"
    )


def test_start_game_still_carries_the_language() -> None:
    """The lobby push is an addition, not a replacement: a host who reloads the
    admin page mid-lobby, or a game started from the HA service, must still
    arrive with a language."""
    ws_source = (_CC / "server" / "websocket.py").read_text("utf-8")
    assert 'language = data.get("language", "de")' in ws_source
    assert "language: selectedLanguage," in (_JS / "admin.js").read_text("utf-8")


# ---------------------------------------------------------------------------
# The all-time line re-translates itself
# ---------------------------------------------------------------------------


def test_the_sweep_can_translate_a_line_that_needs_params() -> None:
    """Before #776 initPageTranslations() called t(key) with no params, so any
    line with a {placeholder} was untranslatable by the sweep and had to be
    written once, by hand, in whatever language happened to be loaded."""
    source = (_JS / "i18n.js").read_text("utf-8")
    body = _js_function(source, "function initPageTranslations(root)")

    assert "t(key, _readI18nParams(el))" in body
    reader = _js_function(source, "function _readI18nParams(el)")
    assert "data-i18n-params" in reader
    assert "JSON.parse" in reader
    # A hand-edited or truncated attribute must degrade to an untranslated
    # line, not throw and abort the whole sweep for every other element.
    assert "catch" in reader


def test_the_all_time_line_leaves_the_sweep_something_to_find() -> None:
    """The measured symptom: data-i18n was null on #pl-alltime, so the line
    stayed English on a German lobby screen."""
    source = _without_comments((_JS / "player-lobby.js").read_text("utf-8"))
    body = _js_function(source, "function renderAllTime(standing, elementId)")

    assert "el.setAttribute('data-i18n', key)" in body
    assert "el.setAttribute('data-i18n-params', JSON.stringify(params))" in body


def test_clearing_the_line_clears_its_translation_keys(  # noqa: D103
) -> None:
    """A first-timer's empty line must not be re-filled by the next sweep."""
    source = _without_comments((_JS / "player-lobby.js").read_text("utf-8"))
    body = _js_function(source, "function renderAllTime(standing, elementId)")
    empty_branch = body.split("if (!standing || !standing.total_players)", 1)[1].split(
        "}", 1
    )[0]

    assert "removeAttribute('data-i18n')" in empty_branch
    assert "removeAttribute('data-i18n-params')" in empty_branch


def test_the_end_screen_line_is_covered_too() -> None:
    """#624 gave renderAllTime an elementId so the same line renders on the end
    screen. One function, so one fix — pinned so a future refactor that splits
    them does not silently drop the end-screen half."""
    core = _without_comments((_JS / "player-core.js").read_text("utf-8"))

    assert "lobby.renderAllTime(msg.all_time, 'end-alltime')" in core


# ---------------------------------------------------------------------------
# … in the right number
# ---------------------------------------------------------------------------


def _bundle(lang: str) -> dict:
    return json.loads((_I18N / f"{lang}.json").read_text("utf-8"))


@pytest.mark.parametrize("lang", _LANGS)
def test_every_bundle_carries_every_all_time_sentence(lang: str) -> None:
    """A missing key falls back to English, which on a German lobby is the very
    mixed-language screen this issue is about."""
    lobby = _bundle(lang)["lobby"]
    for key in _ALL_TIME_KEYS:
        assert key in lobby, f"{lang}.json is missing lobby.{key}"


@pytest.mark.parametrize("lang", _LANGS)
def test_the_singular_sentences_spell_their_number_out(lang: str) -> None:
    """"1 wins from 2 games" was the reported wording. The count that is one is
    written into the sentence instead of interpolated, which is what lets each
    language inflect its own noun without a pluralisation engine."""
    lobby = _bundle(lang)["lobby"]

    assert "{wins}" not in lobby["allTimeOneWin"]
    assert "{games}" in lobby["allTimeOneWin"]

    assert "{wins}" not in lobby["allTimeOneWinOneGame"]
    assert "{games}" not in lobby["allTimeOneWinOneGame"]

    assert "{games}" not in lobby["allTimeFirstWinOneGame"]

    # The plural sentences keep interpolating, or they would say the same
    # number to everyone.
    assert "{wins}" in lobby["allTime"] and "{games}" in lobby["allTime"]
    assert "{games}" in lobby["allTimeFirstWin"]


@pytest.mark.parametrize("lang", _LANGS)
def test_every_all_time_sentence_still_names_the_rank(lang: str) -> None:
    lobby = _bundle(lang)["lobby"]
    for key in _ALL_TIME_KEYS:
        assert "{rank}" in lobby[key] and "{total}" in lobby[key], key


def test_the_key_is_picked_from_both_counts() -> None:
    """Reading the selection out of the source rather than re-implementing it:
    the point is that games_played, not just wins, decides the sentence — the
    first game you win is the case both a wins-only and a games-only rule get
    wrong."""
    source = _without_comments((_JS / "player-lobby.js").read_text("utf-8"))
    body = _js_function(source, "function _allTimeKey(standing)")

    assert "standing.games_played === 1" in body
    assert "standing.wins === 1" in body
    for key in _ALL_TIME_KEYS:
        assert f"lobby.{key}" in body, key

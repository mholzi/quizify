"""The host can switch off power-ups and the final-round wager (#742).

``start_game`` took only ``lightning_enabled`` and ``hot_seat_enabled``. A
power-up was dealt every round unconditionally, and ``_needs_wager_window``
fired on the last round of every non-team, non-estimate game — so a host who
picked *With kids*, a preset that already turns Lightning and Hot Seat off
because "losing points is the mechanic children like least", still got Steal
and Freeze dealt out and a final question staked on "no answer costs you the
stake".

The two new toggles are a deliberate copy of the two that already work: the
same ``_coerce_toggle`` on the wire, the same default-ON, the same seat in the
``start_game`` payload, the same seat in ``last_settings`` so the one-tap
rematch keeps them, the same field in the preset store, the same markup in the
setup card. The tests below therefore pin the *sameness* as much as the
behaviour — a copy that drifts in one of those six places is the failure mode.

Two things are asserted that the existing pair does not need:

* Turning power-ups off must not touch ``was_granted_this_game``. If a
  disabled game marked players as granted, switching power-ups back on for a
  rematch would find everyone already served and deal nobody anything.
* ``is_final_round`` on the question payload must agree with
  ``_needs_wager_window``. That flag is what makes the phone render the wager
  UI; with the window closed, a phone offered a bet it cannot place is worse
  than no bet at all.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from custom_components.quizify.game.questions import Answer, Question  # noqa: E402
from custom_components.quizify.game.state import QuizifyGameState  # noqa: E402
from custom_components.quizify.server.connection import ConnectionManager  # noqa: E402
from custom_components.quizify.server.preset_store import _validate  # noqa: E402
from custom_components.quizify.server.websocket import (  # noqa: E402
    QuizifyWebSocketHandler,
)

WWW = _REPO_ROOT / "custom_components" / "quizify" / "www"
ADMIN_HTML = WWW / "admin.html"
ADMIN_JS = WWW / "js" / "admin.js"


class _FakeRuntime:
    def __init__(self, tmp_path: Path) -> None:
        self.data_dir = tmp_path

    def create_task(self, coro):  # noqa: ANN001
        return asyncio.ensure_future(coro)

    async def run_in_executor(self, func, *args):  # noqa: ANN001, ANN002
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, func, *args)


def _ws() -> MagicMock:
    ws = MagicMock()
    ws.closed = False
    ws.send_json = AsyncMock()
    return ws


@pytest.fixture
def game(tmp_path: Path) -> QuizifyGameState:
    return QuizifyGameState(runtime=_FakeRuntime(tmp_path), entry_id="test")


@pytest.fixture
def handler(game: QuizifyGameState, monkeypatch) -> QuizifyWebSocketHandler:
    runtime = _FakeRuntime(game._runtime.data_dir)  # type: ignore[attr-defined]
    h = QuizifyWebSocketHandler(runtime=runtime, game_state_provider=lambda: game)
    h._conn = ConnectionManager(runtime, lambda: game)
    h._conn.broadcast = AsyncMock()
    h._conn.broadcast_to_admins_and_dashboards = AsyncMock()
    h._conn.send = AsyncMock()
    h._conn.send_error = AsyncMock()
    monkeypatch.setattr(
        "custom_components.quizify.server.websocket.asyncio.sleep", AsyncMock()
    )
    monkeypatch.setattr(h, "_start_timer_tick", lambda *a, **k: None)
    return h


def _mc_question() -> Question:
    return Question(
        id="q1",
        question="Capital of France?",
        answers=[
            Answer(text="Paris", correct=True),
            Answer(text="Berlin", correct=False),
            Answer(text="Rome", correct=False),
        ],
    )


def _players(game: QuizifyGameState, *names: str) -> None:
    for name in names:
        game.add_player(name, _ws())


# ---------------------------------------------------------------------------
# Power-ups
# ---------------------------------------------------------------------------


class TestPowerUpsToggle:
    def test_default_on_still_deals_a_powerup(self, game: QuizifyGameState) -> None:
        _players(game, "A", "B")
        game.start_game(num_rounds=3)
        game.start_next_question()

        granted = [p for p in ("A", "B") if game._powerup_manager.get_powerup(p)]
        assert granted, "a power-up is dealt every round by default"

    def test_off_deals_nothing_for_the_whole_game(
        self, game: QuizifyGameState
    ) -> None:
        _players(game, "A", "B")
        game.start_game(num_rounds=3, powerups_enabled=False)
        for _ in range(3):
            game.start_next_question()
            assert game._powerup_manager.get_powerup("A") is None
            assert game._powerup_manager.get_powerup("B") is None

    def test_off_does_not_burn_the_once_per_game_grant(
        self, game: QuizifyGameState
    ) -> None:
        """The trap this guard exists for (#340 + #742).

        ``was_granted_this_game`` is what limits a player to one power-up per
        game. If a disabled round marked players as granted, a rematch with
        power-ups switched back on would find everyone already served and deal
        nobody anything for the rest of the evening.
        """
        _players(game, "A", "B")
        game.start_game(num_rounds=3, powerups_enabled=False)
        game.start_next_question()

        assert game._powerup_manager.was_granted_this_game("A") is False
        assert game._powerup_manager.was_granted_this_game("B") is False


# ---------------------------------------------------------------------------
# The final-round wager
# ---------------------------------------------------------------------------


class TestWagerToggle:
    def test_default_on_opens_the_window_on_the_last_round(
        self, game: QuizifyGameState
    ) -> None:
        _players(game, "A")
        game.start_game(num_rounds=3)
        game.round = 3

        assert game.wager_enabled is True
        assert game._needs_wager_window(_mc_question()) is True

    def test_off_plays_the_last_question_straight(
        self, game: QuizifyGameState
    ) -> None:
        _players(game, "A")
        game.start_game(num_rounds=3, wager_enabled=False)
        game.round = 3

        assert game.wager_enabled is False
        assert game._needs_wager_window(_mc_question()) is False

    @pytest.mark.asyncio
    async def test_off_withholds_the_wager_ui_from_the_phone(
        self, handler: QuizifyWebSocketHandler, game: QuizifyGameState
    ) -> None:
        """``is_final_round`` is what makes the client render the slider.

        It has to agree with ``_needs_wager_window``: a phone shown a bet the
        server opens no window for would sit on a control nothing accepts.
        """
        admin = _ws()
        game.add_player("Markus", admin)
        game.get_player("Markus").is_admin = True
        await handler._handle_start_game(
            admin,
            {
                "category": "geographie",
                "num_rounds": 1,
                "language": "de",
                "wager_enabled": False,
            },
            game,
        )

        payloads = [
            call.args[1]
            for call in handler._conn.send.call_args_list
            if isinstance(call.args[1], dict)
            and call.args[1].get("type") == "question_started"
        ]
        assert payloads, "the first question was never sent"
        assert all(p["is_final_round"] is False for p in payloads)

    @pytest.mark.asyncio
    async def test_on_opens_the_window_instead_of_the_question(
        self, handler: QuizifyWebSocketHandler, game: QuizifyGameState
    ) -> None:
        """The other side of the same round, for contrast.

        With the toggle on, the last round is held back (#656): the phones get
        a ``wager_window`` and no question at all until the betting closes. The
        test above proves the toggle removes exactly that.
        """
        admin = _ws()
        game.add_player("Markus", admin)
        game.get_player("Markus").is_admin = True
        await handler._handle_start_game(
            admin,
            {"category": "geographie", "num_rounds": 1, "language": "de"},
            game,
        )

        types = {
            call.args[1].get("type")
            for call in handler._conn.send.call_args_list
            if isinstance(call.args[1], dict)
        }
        assert "wager_window" in types
        assert "question_started" not in types


# ---------------------------------------------------------------------------
# The wire — same coercion, same default as Lightning and Hot Seat
# ---------------------------------------------------------------------------


class TestWireToggles:
    @pytest.mark.asyncio
    async def test_missing_keys_default_both_on(
        self, handler: QuizifyWebSocketHandler, game: QuizifyGameState
    ) -> None:
        admin = _ws()
        game.add_player("Markus", admin)
        game.get_player("Markus").is_admin = True
        await handler._handle_start_game(
            admin,
            {"category": "geographie", "num_rounds": 10, "language": "de"},
            game,
        )
        assert game._powerups_enabled is True
        assert game.wager_enabled is True

    @pytest.mark.asyncio
    @pytest.mark.parametrize("falsey", [False, "false", "0", "off", "no", 0])
    async def test_falsey_forms_disable_both(
        self,
        handler: QuizifyWebSocketHandler,
        game: QuizifyGameState,
        falsey,
    ) -> None:
        """Same ``_coerce_toggle`` as #285/#616 — a "0" string counts as off."""
        admin = _ws()
        game.add_player("Markus", admin)
        game.get_player("Markus").is_admin = True
        await handler._handle_start_game(
            admin,
            {
                "category": "geographie",
                "num_rounds": 10,
                "language": "de",
                "powerups_enabled": falsey,
                "wager_enabled": falsey,
            },
            game,
        )
        assert game._powerups_enabled is False
        assert game.wager_enabled is False


# ---------------------------------------------------------------------------
# Persistence — the rematch and the preset store
# ---------------------------------------------------------------------------


class TestPersistence:
    def test_last_settings_carries_both(self, game: QuizifyGameState) -> None:
        """#670's lesson, applied to a third and fourth mechanic.

        Without this the one-tap rematch falls back to the defaults and hands
        a kids' game back the Steal and the betting window its preset had
        switched off.
        """
        _players(game, "A")
        game.start_game(num_rounds=3, powerups_enabled=False, wager_enabled=False)

        assert game.last_settings["powerups_enabled"] is False
        assert game.last_settings["wager_enabled"] is False

    def test_a_rematch_replays_the_same_settings(
        self, game: QuizifyGameState
    ) -> None:
        _players(game, "A")
        game.start_game(num_rounds=3, powerups_enabled=False, wager_enabled=False)
        settings = dict(game.last_settings)
        game.reset_to_lobby()
        game.start_game(**settings)

        assert game._powerups_enabled is False
        assert game.wager_enabled is False

    def test_preset_store_keeps_both(self) -> None:
        record = _validate(
            {"name": "Kids", "rounds": 5, "powerups": False, "wager": False}
        )
        assert record["powerups"] is False
        assert record["wager"] is False

    def test_preset_store_omits_what_was_not_given(self) -> None:
        record = _validate({"name": "Plain", "rounds": 5})
        assert "powerups" not in record
        assert "wager" not in record


# ---------------------------------------------------------------------------
# The setup card — the same place in the markup as the other two
# ---------------------------------------------------------------------------


class TestSetupCard:
    @pytest.mark.parametrize(
        "toggle_id", ["powerups-enabled-toggle", "wager-enabled-toggle"]
    )
    def test_toggle_exists_and_defaults_checked(self, toggle_id: str) -> None:
        src = ADMIN_HTML.read_text("utf-8")
        assert f'id="{toggle_id}" checked' in src

    @pytest.mark.parametrize("attr", ["data-powerups", "data-wager"])
    def test_every_bundle_card_carries_the_attribute(self, attr: str) -> None:
        """A card without it applies ``null`` and silently keeps the old value.

        Counted against ``data-hot-seat`` rather than against every
        ``.preset-card``: the *Eigene* card deliberately carries no bundle
        attributes at all, because it unfolds the manual controls instead of
        applying a bundle. (``data-lightning`` would be the obvious yardstick
        but is also named in a comment above the kids card.)
        """
        src = ADMIN_HTML.read_text("utf-8")
        bundle_cards = src.count("data-hot-seat=")
        assert bundle_cards > 0
        assert src.count(f"{attr}=") == bundle_cards

    def test_the_kids_preset_switches_both_off(self) -> None:
        """The host story in the issue: *With kids* means a gentle game."""
        src = ADMIN_HTML.read_text("utf-8")
        kids = src.split('data-preset="kinder"', 1)[1][:400]
        assert 'data-powerups="0"' in kids
        assert 'data-wager="0"' in kids

        js = ADMIN_JS.read_text("utf-8")
        kids_row = [ln for ln in js.splitlines() if "'kinder'" in ln][0]
        assert "powerups: false" in kids_row
        assert "wager: false" in kids_row


class TestAdminPayload:
    def test_both_ride_the_start_game_payload(self) -> None:
        js = ADMIN_JS.read_text("utf-8")
        assert "powerups_enabled: powerupsEnabled" in js
        assert "wager_enabled: wagerEnabled" in js

    def test_both_ride_a_saved_preset(self) -> None:
        js = ADMIN_JS.read_text("utf-8")
        assert "powerups: selectedPowerups" in js
        assert "wager: selectedWager" in js

    def test_both_join_the_bundle_match(self) -> None:
        """Flipping a toggle by hand must make the run read as *Eigene*.

        Same rule #513 established for Lightning and #616 for the Hot Seat.
        """
        js = ADMIN_JS.read_text("utf-8")
        bundle = js.split("function _sameBundle(", 1)[1][:900]
        assert "p.powerups === selectedPowerups" in bundle
        assert "p.wager === selectedWager" in bundle


class TestI18n:
    @pytest.mark.parametrize("lang", ["en", "de", "es"])
    @pytest.mark.parametrize(
        "key", ["q8", "powerupsHint", "q9", "wagerHint"]
    )
    def test_every_language_labels_the_new_toggles(
        self, lang: str, key: str
    ) -> None:
        import json

        data = json.loads((WWW / "i18n" / f"{lang}.json").read_text("utf-8"))
        value = data["setup"]["eigene"].get(key)
        assert value, f"{lang}.json is missing setup.eigene.{key}"

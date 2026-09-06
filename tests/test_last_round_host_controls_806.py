"""The last round leaves the admin-tab host a way forward (issue #806).

``handleRoundSummary`` hid ``#next-question-btn`` whenever the round summary
carried ``last_round``, on the reasoning that "Next Question there would
promise a round that does not exist". The round does not exist — but the
*advance* does: ``next_question`` from ANSWER_REVEAL on the last round runs
``_start_next_question`` → ``start_next_question()`` → ``end_game()`` and lands
on the finale (#255). That is the same step the phone's admin bar offers, where
it is labelled ``reveal.finalResults`` — "Final Results" (``player-reveal.js``).

So the host who started without joining (``doStartGameNoJoin``) and stayed on
``/quizify/admin`` reached the last reveal with a single red button, whose
confirmation modal read "All players will be disconnected." — which
``end_game`` does not do: it transitions to FINALE and the phones render the
end screen, everyone still connected. The one action the room was waiting for
was dressed as the destructive one. The disconnect wording belongs to reset,
where ``admin.resetGameWarning`` already carries it.

The JS assertions are structural (read the shipped source, assert on its
shape) because the suite has no JS runtime — the same pattern as
``test_admin_reveal_controls_618.py``, which pinned the behaviour this
replaces. The premise underneath them is not structural and is checked for
real: the last round's advance really does reach the finale.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import MagicMock

from custom_components.quizify.game.state import GamePhase, QuizifyGameState

_REPO_ROOT = Path(__file__).resolve().parent.parent
_WWW = _REPO_ROOT / "custom_components" / "quizify" / "www"
_ADMIN_JS = _WWW / "js" / "admin.js"
_ADMIN_HTML = _WWW / "admin.html"
_LANGS = ("de", "en", "es")


def _function_body(source: str, signature: str) -> str:
    """The text of one top-level function, brace-matched."""
    start = source.index(signature)
    depth = 0
    for i in range(start, len(source)):
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
            if depth == 0:
                return source[start : i + 1]
    raise AssertionError(f"unbalanced braces after {signature!r}")


def _reveal_body() -> str:
    return _function_body(_ADMIN_JS.read_text("utf-8"), "function handleRoundSummary(")


def _bundle(code: str) -> dict:
    return json.loads((_WWW / "i18n" / f"{code}.json").read_text("utf-8"))


# --------------------------------------------------------------------------
# The premise: the last round's advance really does reach the finale
# --------------------------------------------------------------------------


class _Runtime:
    def __init__(self, tmp_path: Path) -> None:
        self.data_dir = tmp_path

    async def run_in_executor(self, func, *args):  # noqa: ANN001, ANN002
        return func(*args)

    def create_task(self, coro):  # noqa: ANN001
        return asyncio.ensure_future(coro)


def _ws() -> MagicMock:
    ws = MagicMock()
    ws.closed = False
    return ws


def test_advancing_past_the_last_round_lands_on_the_finale(tmp_path: Path) -> None:
    """Why relabelling is honest and hiding was not. If this ever stops being
    true the button has to go back into hiding — and this test says so first."""
    game = QuizifyGameState(runtime=_Runtime(tmp_path), entry_id="t")
    for name in ("Anna", "Mira"):
        game.add_player(name, _ws())
    game.start_game(
        category="picture-round-en",
        difficulty="easy",
        num_rounds=1,
        language="en",
        lightning_enabled=False,
        hot_seat_enabled=False,
        # The final-wager window would sit between the reveal and the finale
        # and is not what this pins.
        wager_enabled=False,
    )
    assert game.start_next_question() is not None
    assert game.round == game.total_rounds, "not actually the last round"
    game.phase = GamePhase.ANSWER_REVEAL  # where the host's button lives

    # What that button sends.
    assert game.start_next_question() is None
    assert game.phase == GamePhase.FINALE

    # And nobody was thrown off the socket on the way there.
    assert len(game.players) == 2
    assert all(not p.ws.closed for p in game.players.values())


# --------------------------------------------------------------------------
# The fix: the button stays, relabelled
# --------------------------------------------------------------------------


def test_the_last_round_no_longer_hides_the_forward_button() -> None:
    """The defect: the only control left was the red one."""
    body = _reveal_body()

    assert "nextQuestionBtn.classList.remove('hidden')" in body
    hidden_under_condition = [
        line
        for line in body.splitlines()
        if "nextQuestionBtn" in line and "last_round" in line and "!" in line
    ]
    assert not hidden_under_condition, (
        "Next Question must not be gated away on the last round"
    )


def test_the_last_round_relabels_the_button_as_final_results() -> None:
    """Same word the phone's admin bar uses for the same step — a host holding
    both screens must not read two names for one button."""
    body = _reveal_body()

    assert "last_round" in body, "the label has to depend on last_round"
    assert "reveal.finalResults" in body
    assert "admin.nextQuestion" in body, "the other rounds keep their own label"
    assert "setAttribute('data-i18n'" in body, (
        "data-i18n must follow the label or a mid-game language switch "
        "re-translates the old key"
    )
    assert "textContent" in body


def test_every_bundle_carries_the_relabelled_string() -> None:
    """A missing key renders the raw key — on the host's screen, at the last
    reveal, in front of the room."""
    for code in _LANGS:
        assert _bundle(code)["reveal"]["finalResults"].strip()


# --------------------------------------------------------------------------
# The false claim in the confirmation
# --------------------------------------------------------------------------


def test_the_end_game_warning_no_longer_promises_a_disconnect() -> None:
    """``end_game`` transitions to FINALE and the phones render the end
    screen. Nothing disconnects — that is what reset does, and
    ``resetGameWarning`` already says so."""
    disconnect_words = {
        "de": ("getrennt", "verbindung"),
        "en": ("disconnect", "kicked"),
        "es": ("desconect",),
    }
    for code in _LANGS:
        warning = _bundle(code)["admin"]["endGameWarning"].lower()
        for word in disconnect_words[code]:
            assert word not in warning, (
                f"{code}: endGameWarning still talks about disconnecting players"
            )
        assert len(warning) > 20, f"{code}: endGameWarning must say what happens"

    # Reset is where that wording belongs, and it still carries it.
    assert "cleared" in _bundle("en")["admin"]["resetGameWarning"].lower()


def test_the_end_game_warning_says_what_end_game_does() -> None:
    """It reaches the final results. That is the whole content of the step."""
    expected = {
        "de": "endergebnis",
        "en": "final results",
        "es": "resultados finales",
    }
    for code in _LANGS:
        warning = _bundle(code)["admin"]["endGameWarning"].lower()
        assert expected[code] in warning, (
            f"{code}: endGameWarning must name the final results"
        )


def test_the_html_fallback_matches_the_english_bundle() -> None:
    """The inline text is what a host sees before i18n.js resolves; a stale
    copy would show the old claim for exactly as long as anyone looks."""
    html = _ADMIN_HTML.read_text("utf-8")
    marker = '<p data-i18n="admin.endGameWarning">'
    start = html.index(marker) + len(marker)
    inline = html[start : html.index("</p>", start)]
    assert inline == _bundle("en")["admin"]["endGameWarning"]

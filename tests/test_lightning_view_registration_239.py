"""Regression tests for issue #239 — lightning round renders no view on reconnect.

Root cause (client): a player view is shown by ADDING the ``active`` class to
its container (``.view.active { display:flex }`` in styles.css), and
``player-utils.js`` ``showView()`` only ever touches the IDs listed in its
``viewIds`` array. The three Lightning Round (#42) view containers —
``lightning-splash-view``, ``lightning-view`` and ``lightning-recap-view`` —
were **never registered** in that array. So ``showView('lightning-view')``
hid every *other* view but never un-hid the lightning one, leaving it as the
initial ``class="view hidden"``. On a reconnect into a live LIGHTNING round
(``/quizify/player?...&reconnect=1``) the LIGHTNING handler therefore ended
with BOTH ``#lightning-view`` and ``#lightning-splash-view`` hidden → a blank
screen (the RC8 watchdog then fell back to the join view).

This is the cousin of #221, which fixed the *data* path (the reconnect/refresh
``game_state`` now carries the ``lightning`` sub-state). #239 is the *view*
path: even with the data present, the view could not be revealed.

The fix registers the three lightning view IDs in ``viewIds``. These tests:

1. Statically assert the lightning view IDs are present in ``viewIds`` in both
   the source module and the built bundle (guards this exact regression).
2. Re-assert the server reconnect snapshot carries the full ``lightning``
   sub-state, so the now-revealable client actually has data to render.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from custom_components.quizify.game.state import (  # noqa: E402
    GamePhase,
    QuizifyGameState,
)

_WWW_JS = _REPO_ROOT / "custom_components" / "quizify" / "www" / "js"
_LIGHTNING_VIEW_IDS = (
    "lightning-splash-view",
    "lightning-view",
    "lightning-recap-view",
)


def _extract_view_ids(js_text: str) -> set[str]:
    """Pull the string literals out of the first ``var viewIds = [ ... ];``."""
    match = re.search(r"var\s+viewIds\s*=\s*\[(.*?)\];", js_text, re.DOTALL)
    assert match, "could not locate the viewIds array literal"
    # Strip // comments so apostrophes inside explanatory text (e.g.
    # showView('lightning-view') in a comment) aren't mistaken for entries.
    body = re.sub(r"//[^\n]*", "", match.group(1))
    return set(re.findall(r"'([^']+)'", body))


@pytest.mark.parametrize("filename", ["player-utils.js", "player.bundle.js"])
def test_lightning_views_registered_in_viewids(filename: str) -> None:
    """showView() can only reveal IDs present in viewIds — lightning ones must be."""
    js_text = (_WWW_JS / filename).read_text(encoding="utf-8")
    view_ids = _extract_view_ids(js_text)
    for vid in _LIGHTNING_VIEW_IDS:
        assert vid in view_ids, (
            f"{filename}: '{vid}' missing from viewIds — showView() cannot "
            "reveal it, so the lightning round renders blank (#239)."
        )


def test_lightning_view_containers_exist_in_html() -> None:
    """Sanity: the registered IDs actually correspond to real view containers."""
    html = (
        _REPO_ROOT
        / "custom_components"
        / "quizify"
        / "www"
        / "player.html"
    ).read_text(encoding="utf-8")
    for vid in _LIGHTNING_VIEW_IDS:
        assert f'id="{vid}"' in html, f"player.html missing #{vid}"


class _FakeRuntime:
    def __init__(self, tmp_path: Path) -> None:
        self.data_dir = tmp_path


def _fake_ws() -> MagicMock:
    ws = MagicMock()
    ws.closed = False
    return ws


def test_reconnect_snapshot_carries_lightning_substate(tmp_path: Path) -> None:
    """The reconnect snapshot must hand the (now revealable) client its data.

    Mirrors the #221 contract from the snapshot side: a player reconnecting
    into a live LIGHTNING round gets ``get_state_snapshot()``, which must
    include the ``lightning`` sub-state the client's LIGHTNING case reads to
    pick splash vs question — otherwise even a fixed showView() has nothing
    to render.
    """
    state = QuizifyGameState(runtime=_FakeRuntime(tmp_path), entry_id="test")
    state.add_player("A", _fake_ws())
    assert state.start_lightning_round() is True
    assert state.begin_lightning_questions() is True
    assert state.phase == GamePhase.LIGHTNING

    snap = state.get_state_snapshot()

    assert snap["phase"] == "LIGHTNING"
    assert "lightning" in snap
    ln = snap["lightning"]
    assert ln["splash_pending"] is False
    assert "question" in ln
    assert isinstance(ln["question"]["text"], str) and ln["question"]["text"]
    assert isinstance(ln["question"]["answers"], list) and ln["question"]["answers"]

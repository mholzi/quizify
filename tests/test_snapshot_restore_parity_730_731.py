"""The restore path must carry what the live path carries (#730, #731).

Two bugs, one shape. A phone that reloads mid-round never receives the one-shot
event that opened the round — those are not re-sent — so the client rebuilds the
same view from the ``game_state`` snapshot instead. Both restore paths did that
by re-listing, in an object literal, the fields worth forwarding. Which means
every field added to a live payload has to be remembered a second time, in a
different file, by whoever adds it. Twice it was not:

* **#730** — ``question`` was missing from the Hot Seat list. The snapshot has
  carried ``hot_seat.question.text`` since #664 and the live handler has
  rendered it since #698, but the restore never passed it. The seat holder
  whose phone locked came back to three answer buttons under a blank question
  (fresh page) or the previous round's (in-tab reconnect), clock running — and
  an unanswered Hot Seat question costs the entire stake (#653).
* **#731** — ``reveal_style`` was missing from the question list. A progressive
  reveal starts the picture blurred and sharpens it as the timer drains, which
  is only a mechanic if *no* phone can shortcut it. A reload handed that phone
  the sharp picture for the rest of the round: cheap, repeatable, silent.

#275 was the same bug a third time, caught earlier: estimate rounds rendered as
an A/B/C grid because ``question_type``/``estimate`` were not on the list yet.

So the fix is not "add the two fields". Both restore paths now forward the
frame — every key the server puts in the block rides along — and name only the
fields a restore must genuinely source elsewhere or recompute. The tests below
pin both halves:

1. the client forwards a field it has never heard of (run under node, on the
   real functions), so a hand-written list cannot come back; and
2. the server snapshot carries every field the live payload does, with the
   handful of exceptions written out by name — so the client's forwarding has
   something to forward.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from custom_components.quizify.game.state import (  # noqa: E402
    GamePhase,
    QuizifyGameState,
)
from custom_components.quizify.server.round_message_builder import (  # noqa: E402
    RoundMessageBuilder,
)
from custom_components.quizify.server.serializers import (
    # noqa: E402,
    serialize_question_for_player,
    serialize_state_snapshot,
)

JS = _REPO / "custom_components" / "quizify" / "www" / "js"
PLAYER_CORE = JS / "player-core.js"
PLAYER_GAME = JS / "player-game.js"
PLAYER_HOTSEAT = JS / "player-hotseat.js"
WEBSOCKET = _REPO / "custom_components" / "quizify" / "server" / "websocket.py"

needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not installed"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeRuntime:
    def __init__(self, tmp_path: Path) -> None:
        self.data_dir = tmp_path


def _ws() -> MagicMock:
    ws = MagicMock()
    ws.closed = False
    return ws


def _js_function(path: Path, name: str) -> str:
    """The full source of a top-level ``function <name>(...) { ... }``.

    Brace-matched rather than line-counted so the extraction survives the
    function moving or growing. A missing function is an assertion failure with
    a readable message, not an IndexError: this helper is how the tests below
    reach the code under test, and "the restore path went back to a hand-written
    literal" should read as exactly that.
    """
    source = path.read_text(encoding="utf-8")
    marker = f"function {name}("
    assert marker in source, f"{path.name} has no {marker[:-1]}"
    start = source.index(marker)
    depth, j = 0, source.index("{", start)
    while True:
        if source[j] == "{":
            depth += 1
        elif source[j] == "}":
            depth -= 1
            if depth == 0:
                return source[start : j + 1]
        j += 1


def _node(script: str) -> dict:
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, check=True
    ).stdout
    return json.loads(out)


# ---------------------------------------------------------------------------
# The client forwards the frame — the guard against a hand-written list
# ---------------------------------------------------------------------------

# A field name no version of this repo has ever seen. If the restore path is
# re-listing fields by hand it cannot possibly forward this; if it forwards the
# frame it cannot possibly drop it. That is the whole class of bug, in one key.
_FUTURE_FIELD = "a_field_added_next_year"

_SNAPSHOT_QUESTION = {
    "text": "Which city is this?",
    "answers": ["Lisbon", "Porto", "Faro"],
    "category": "Geography",
    "difficulty": "easy",
    "image_url": "/quizify/static/img/packs/city.jpg",
    "reveal_style": "progressive",
    "question_type": "multiple_choice",
    "time_limit": 30,
    "time_remaining": 12.5,
    _FUTURE_FIELD: "carried",
}


def _restored_question() -> dict:
    """Run the real restore builder over a snapshot, under node.

    ``_myScore`` reads the module's player state, which a snapshot on its own
    cannot provide, so it is stubbed; everything else is the shipped function.
    """
    snapshot = {
        "round": 3,
        "total_rounds": 10,
        "leaderboard": [],
        "question": dict(_SNAPSHOT_QUESTION),
    }
    call = f"questionStartedFromSnapshot({json.dumps(snapshot)})"
    return _node(
        _js_function(PLAYER_CORE, "questionStartedFromSnapshot")
        + "\nfunction _myScore() { return 42; }\n"
        + f"console.log(JSON.stringify({call}));"
    )


@needs_node
def test_the_question_restore_forwards_a_field_it_has_never_heard_of() -> None:
    """The #731 fix, stated as the class rather than the instance."""
    live = _restored_question()
    assert live[_FUTURE_FIELD] == "carried", (
        "the restore path is re-listing fields by hand again — the next field "
        "added to question_started will be dropped exactly like reveal_style was"
    )


@needs_node
def test_the_question_restore_carries_the_reveal_style() -> None:
    """#731 itself: the field whose absence handed one phone the sharp picture."""
    live = _restored_question()
    assert live["reveal_style"] == "progressive"
    assert live["image_url"] == _SNAPSHOT_QUESTION["image_url"]


@needs_node
def test_the_restored_clock_is_what_is_left_and_the_blur_is_the_whole_round() -> None:
    """The two numbers that must NOT be the same on a restore.

    The countdown gets the remaining seconds — a reconnect may not buy thinking
    time. The blur is drawn as ``remaining / duration``, so it needs the full
    round: given the remainder it computes 1.0 and restarts the reveal at
    maximum blur, then sharpens at double speed. Right field, wrong number, and
    the mechanic is broken in the other direction.
    """
    live = _restored_question()
    assert live["timer_duration"] == 12.5, "the countdown must resume, not restart"
    assert live["reveal_duration"] == 30, "the blur fraction is of the whole round"
    assert live["question_text"] == _SNAPSHOT_QUESTION["text"]
    assert live["round_num"] == 3 and live["total_rounds"] == 10


def test_the_banner_takes_the_reveal_duration_when_the_restore_supplies_one() -> None:
    """``reveal_duration`` is only carried if renderQuestion actually reads it."""
    source = PLAYER_GAME.read_text(encoding="utf-8")
    assert "data.reveal_duration || data.timer_duration" in source, (
        "renderQuestion still hands the banner timer_duration, so a restore "
        "restarts the progressive blur at maximum instead of resuming it (#731)"
    )


@needs_node
def test_the_hot_seat_restore_carries_the_question_and_an_unknown_field() -> None:
    """#730 and its class in one run.

    ``question`` is the field the hand-written list forgot; the unknown one is
    every field it would forget next.
    """
    hot_seat = {
        "stage": "question",
        "winner": "Anna",
        "own_bank": 40,
        "you_are_seated": True,
        "time_remaining": 11.5,
        "answer_seconds": 20,
        "question": {
            "text": "Which city is this?",
            "answers": ["Lisbon", "Porto", "Faro"],
            "category": "Geography",
            "difficulty": "easy",
            "image_url": "/quizify/static/img/packs/city.jpg",
            _FUTURE_FIELD: "carried",
        },
    }
    call = f"questionMessageFromSnapshot({json.dumps(hot_seat)})"
    msg = _node(
        _js_function(PLAYER_HOTSEAT, "questionMessageFromSnapshot")
        + f"\nconsole.log(JSON.stringify({call}));"
    )
    assert msg["question"] == "Which city is this?", (
        "the seat holder who reloads still gets the answer grid with no "
        "question, and a timeout costs the whole stake (#730/#653)"
    )
    assert msg[_FUTURE_FIELD] == "carried"
    assert msg["answers"] == ["Lisbon", "Porto", "Faro"]
    assert msg["you_are_seated"] is True
    assert msg["winner"] == "Anna"
    assert msg["score"] == 40
    # The room's remaining seconds, not a fresh window.
    assert msg["seconds"] == 11.5


# ---------------------------------------------------------------------------
# The server carries what the client forwards
# ---------------------------------------------------------------------------

# Every field of the live ``question_started`` that snapshot["question"] does
# NOT hold — each one named, with where the restore path gets it instead. This
# map is the whole point of the test: adding a field to the live payload without
# adding it to the snapshot fails here, and the only way to pass is to carry it
# or to write down, here, why a restore sources it elsewhere.
QUESTION_SOURCED_ELSEWHERE = {
    "type": "the message envelope — a snapshot is not a question_started",
    "question_text": 'renamed: snapshot["question"]["text"]',
    "timer_duration": "recomputed: the remaining seconds, never a fresh round",
    "round_num": 'snapshot["round"]',
    "total_rounds": 'snapshot["total_rounds"]',
    "player_score": 'snapshot["leaderboard"]',
    "is_final_round": "derived: round_num >= total_rounds (isFinalRound)",
}

# The same map for the Hot Seat question. Shorter, because the restore reads
# both the block and its nested question, so most fields are simply there.
HOT_SEAT_SOURCED_ELSEWHERE = {
    "type": "the message envelope",
    "question": 'renamed: hot_seat["question"]["text"]',
    "round_num": 'snapshot["round"]',
    "total_rounds": 'snapshot["total_rounds"]',
    "seconds": 'recomputed: hot_seat["time_remaining"]',
    "score": 'renamed: hot_seat["own_bank"]',
}


def _game_in_question_active(tmp_path: Path, category: str) -> QuizifyGameState:
    state = QuizifyGameState(runtime=_FakeRuntime(tmp_path), entry_id="test")
    state.add_player("Alice", _ws())
    state.add_player("Bob", _ws())
    state.start_game(category=category, language="de", num_rounds=3, difficulty="easy")
    assert state.start_next_question() is not None
    assert state.phase == GamePhase.QUESTION_ACTIVE
    return state


@pytest.mark.parametrize(
    "category",
    [
        # A multiple-choice pack and an estimate pack (#275): the estimate path
        # adds fields to both payloads, and the parity has to hold for both.
        "geographie",
        "schaetzfragen-de",
    ],
)
def test_every_live_question_field_reaches_the_snapshot(
    tmp_path: Path, category: str
) -> None:
    state = _game_in_question_active(tmp_path, category)
    question = state.get_current_question()
    assert question is not None

    live = serialize_question_for_player(
        question,
        [a.text for a in question.answers],
        round_num=1,
        total_rounds=3,
        timer_duration=30.0,
        player_score=0,
    )
    snapshot_question = serialize_state_snapshot(state)["question"]

    missing = set(live) - set(snapshot_question)
    assert missing == set(QUESTION_SOURCED_ELSEWHERE), (
        "question_started and the snapshot have drifted apart. Fields the live "
        f"path sends and the snapshot does not carry: "
        f"{sorted(missing - set(QUESTION_SOURCED_ELSEWHERE))}. Either add them "
        "to the snapshot in serialize_state_snapshot(), or add them to "
        "QUESTION_SOURCED_ELSEWHERE with the reason a restore sources them "
        "elsewhere. Fields listed as exceptions that the live path no longer "
        f"sends: {sorted(set(QUESTION_SOURCED_ELSEWHERE) - missing)}."
    )


def test_the_question_type_is_in_the_snapshot_for_every_question(
    tmp_path: Path,
) -> None:
    """It used to be added only for estimates.

    A field the snapshot carries *sometimes* is worse than one it never
    carries: the restore path cannot forward it without knowing which case it
    is in, which is how the hand-written list justified itself.
    """
    state = _game_in_question_active(tmp_path, "geographie")
    assert serialize_state_snapshot(state)["question"]["question_type"] == "multiple_choice"


def _hot_seat_in_question_stage(tmp_path: Path) -> QuizifyGameState:
    state = QuizifyGameState(runtime=_FakeRuntime(tmp_path), entry_id="test")
    for name in ("Anna", "Ben", "Mira"):
        state.add_player(name, _ws())
    state.start_game(
        category="picture-round-en", difficulty="easy", num_rounds=5, language="en"
    )
    for player in state.get_players():
        player.score = 40
    state.phase = GamePhase.ANSWER_REVEAL
    assert state.start_hot_seat_auction()
    state.hot_seat.record_bid("Anna", 50)
    assert state.close_hot_seat_auction() == "Anna"
    return state


def _live_hot_seat_question_keys() -> set[str]:
    """Keys of every ``hot_seat_question`` payload a *phone* receives.

    Read off the source rather than duplicated here, so a field added to the
    broadcast is picked up without anyone updating this test — the same reason
    #698 reads the payload literal out of websocket.py. The region stops at the
    admin/dashboard send: those carry ``correct_index``, which no phone gets and
    no snapshot may ever hold.
    """
    source = WEBSOCKET.read_text(encoding="utf-8")
    start = source.index('"type": "hot_seat_question"')
    end = source.index("if sends:", start)
    return set(re.findall(r'"([a-z_]+)":', source[start:end]))


def test_every_live_hot_seat_question_field_reaches_the_snapshot(
    tmp_path: Path,
) -> None:
    state = _hot_seat_in_question_stage(tmp_path)
    projected = RoundMessageBuilder().project_snapshot_for_player(
        state, snapshot=serialize_state_snapshot(state), player=state.get_player("Anna")
    )
    block = projected["hot_seat"]
    assert block["stage"] == "question"

    # The block's own "question" key is the nested container, not the live
    # payload's question *text* — dropping it keeps the two from cancelling out
    # and hiding exactly the field #730 was about.
    available = (set(block) - {"question"}) | set(block["question"])
    missing = _live_hot_seat_question_keys() - available
    assert missing == set(HOT_SEAT_SOURCED_ELSEWHERE), (
        "hot_seat_question and the snapshot's hot_seat block have drifted "
        f"apart. Sent live, absent from the snapshot: "
        f"{sorted(missing - set(HOT_SEAT_SOURCED_ELSEWHERE))}. Carry them in "
        "serialize_state_snapshot(), or name them in HOT_SEAT_SOURCED_ELSEWHERE. "
        f"Listed but no longer sent: "
        f"{sorted(set(HOT_SEAT_SOURCED_ELSEWHERE) - missing)}."
    )


def test_the_auction_snapshot_still_withholds_the_question(tmp_path: Path) -> None:
    """The one thing parity must not be allowed to leak.

    Bidding is a bet on yourself, not on a question you have already read, so
    the auction stage carries no question block at all — and "forward the whole
    frame" must never turn into "send the whole round early".
    """
    state = QuizifyGameState(runtime=_FakeRuntime(tmp_path), entry_id="test")
    for name in ("Anna", "Ben", "Mira"):
        state.add_player(name, _ws())
    state.start_game(
        category="picture-round-en", difficulty="easy", num_rounds=5, language="en"
    )
    for player in state.get_players():
        player.score = 40
    state.phase = GamePhase.ANSWER_REVEAL
    assert state.start_hot_seat_auction()

    block = serialize_state_snapshot(state)["hot_seat"]
    assert block["stage"] == "auction"
    assert "question" not in block

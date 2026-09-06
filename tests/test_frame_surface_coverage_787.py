"""Every server frame is accounted for on every surface (issue #787).

The television (``dashboard.html``), the host page (``js/admin.js``) and the
phone (``js/player-core.js``) each own a ``switch (msg.type)``. Nothing checked
that the three agreed, so they drifted — and the drift is the mechanism behind
a family of shipped bugs, not a tidiness complaint:

* **#664** the TV froze in Hot Seat — frames arrived, no case, silence.
* **#698** the TV read "Question undefined" — the auction frame it did not
  handle carried the round number.
* **#699** the host page has no case for any Hot Seat frame at all.
* **#741** the TV never showed power-ups; the host page dropped the same three.
* **#733/#734** i18n gaps that existed only on the television.

Every one of those was found by a person looking at a screen, weeks after the
server-side feature was "done". A frame is done when the phone renders it, and
the other two surfaces are discovered later by QA.

**What this file makes impossible.** ``SERVER_FRAMES`` in
``server/protocol.py`` is the declared wire contract, and
``tests/test_protocol.py`` already pins it against the real dict literals in
the server. This test takes that list and demands a decision for every frame on
every surface: it either *renders* it, or it is declared *ignored* with the
reason, or it is a recorded *gap*. A new frame with no entry fails here — you
cannot add one to the server and find out in December which screen forgot it.

The three-way split is the whole design, and each third is checked against the
real source in **both** directions:

* ``renders`` — the surface must actually have the case. Delete the case, red.
* ``ignored`` — the surface must actually NOT have the case. A reason that has
  quietly become false is as bad as no reason: either the frame is now handled
  (move it to ``renders``) or the line is a lie.
* ``gaps``   — drift that exists today, spelled out. The surface must still not
  handle it; the day somebody wires it up, this test says so and the entry
  comes out. The list can only shrink by accident, never grow by accident.

What this does NOT check: that a handler is *correct*, or that it renders
anything at all. A ``case 'x': break;`` passes. It checks that somebody decided.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_WWW = _REPO / "custom_components" / "quizify" / "www"
_PROTOCOL = _REPO / "custom_components" / "quizify" / "server" / "protocol.py"

#: The three message routers, by the name this file calls each surface.
SURFACE_SOURCES = {
    "tv": _WWW / "dashboard.html",
    "host": _WWW / "js" / "admin.js",
    "phone": _WWW / "js" / "player-core.js",
}
SURFACES = tuple(SURFACE_SOURCES)


# ---------------------------------------------------------------------------
# Reading the two sides
# ---------------------------------------------------------------------------


def declared_server_frames() -> list[str]:
    """The frame types the server declares it sends.

    Read out of ``protocol.py`` with ``ast`` rather than imported, for the same
    reason ``protocol.py`` itself is dependency-free: this test must not need a
    working Home Assistant to say which frames exist.
    """
    tree = ast.parse(_PROTOCOL.read_text("utf-8"))
    for node in ast.walk(tree):
        target = None
        if isinstance(node, ast.AnnAssign):
            target = node.target
        elif isinstance(node, ast.Assign) and node.targets:
            target = node.targets[0]
        if getattr(target, "id", None) == "SERVER_FRAMES":
            assert isinstance(node.value, ast.Dict)
            return [key.value for key in node.value.keys]
    raise AssertionError("SERVER_FRAMES not found in server/protocol.py")


_CASE_RE = re.compile(r"case\s+'([a-z_0-9]+)'\s*:")


def handled_types(surface: str) -> set[str]:
    """The frame types one surface's message router has a case for.

    All three routers are the same shape — ``function handleMessage(msg) {
    switch (msg.type || msg.event) { … } }`` — so the body is taken by brace
    balance from that one anchor and the ``case`` labels read out of it. Only
    lower-case labels count, which is what keeps the phase switches
    (``LOBBY``, ``QUESTION_ACTIVE``, …) out of the result without needing to
    know where they are.

    Anchoring on the function rather than grepping the whole file matters: a
    frame type named in a comment, or compared with ``===`` somewhere far from
    the router, would otherwise read as "handled".
    """
    source = SURFACE_SOURCES[surface].read_text("utf-8")
    anchor = "function handleMessage(msg)"
    assert anchor in source, (
        f"{surface} no longer has a `{anchor}` router — this guard reads the "
        "case labels out of it, so it has to be taught the new shape rather "
        "than silently finding nothing"
    )
    start = source.index(anchor)
    depth = 0
    end = None
    for i in range(source.index("{", start), len(source)):
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
            if depth == 0:
                end = i
                break
    assert end is not None, f"{surface}: unbalanced handleMessage"
    return set(_CASE_RE.findall(source[start:end]))


# ---------------------------------------------------------------------------
# The declaration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Coverage:
    """One frame's decision, for all three surfaces.

    ``audience`` is documentation, and it is the fact that decides most rows:
    ``broadcast`` reaches every socket, ``host_and_tv`` is
    ``broadcast_to_admins_and_dashboards``, ``unicast`` is a ``send(ws, …)`` to
    one player. A broadcast that only one surface handles is the suspicious
    shape; a unicast the other two ignore is simply correct.
    """

    audience: str
    renders: frozenset[str]
    ignored: dict[str, str] = field(default_factory=dict)
    gaps: dict[str, str] = field(default_factory=dict)


def _c(
    audience: str,
    *renders: str,
    ignored: dict[str, str] | None = None,
    gaps: dict[str, str] | None = None,
) -> Coverage:
    return Coverage(
        audience=audience,
        renders=frozenset(renders),
        ignored=dict(ignored or {}),
        gaps=dict(gaps or {}),
    )


# The reasons that repeat, written once so a row does not have to argue the
# same point forty times.
_NOT_A_PLAYER_TV = (
    "the television is not a player; it connects with role=dashboard and never joins"
)
_NOT_A_PLAYER_HOST = (
    "unicast to one player's socket; the host page's socket is role=admin"
)
_PHONE_HAS_NO = "sent only to the host page and the television"

COVERAGE: dict[str, Coverage] = {
    # --- join / reconnect -------------------------------------------------
    "joined": _c(
        "unicast",
        "host",
        "phone",
        ignored={"tv": _NOT_A_PLAYER_TV},
    ),
    "reconnected": _c(
        "unicast",
        "phone",
        ignored={
            "tv": _NOT_A_PLAYER_TV,
            "host": (
                "the host's own socket resumes with admin_auth, not a"
                " player session token"
            ),
        },
    ),
    "reconnect_failed": _c(
        "unicast",
        "phone",
        ignored={
            "tv": _NOT_A_PLAYER_TV,
            "host": (
                "no player session to fail; the admin socket re-authenticates instead"
            ),
        },
    ),
    "kicked": _c(
        "unicast",
        "phone",
        ignored={
            "tv": _NOT_A_PLAYER_TV,
            "host": (
                "the host does the kicking; there is nobody to remove them"
            ),
        },
    ),
    # --- lobby roster -----------------------------------------------------
    "player_joined": _c("broadcast", "tv", "host", "phone"),
    "player_left": _c("broadcast", "tv", "host", "phone"),
    "game_reset": _c("broadcast", "tv", "host", "phone"),
    "teams_update": _c("broadcast", "tv", "host", "phone"),
    "team_joined": _c(
        "unicast",
        "phone",
        ignored={
            "tv": (
                "the roster broadcast already carries the teams;"
                " this is one phone's ack"
            ),
            "host": "one phone's ack; the host learns teams from teams_update",
        },
    ),
    "team_left": _c(
        "unicast",
        "phone",
        ignored={
            "tv": "one phone's ack; the board follows teams_update",
            "host": "one phone's ack; the host follows teams_update",
        },
    ),
    "team_answer": _c(
        "unicast",
        "phone",
        ignored={
            "tv": (
                "each member is told what their team's answer stands at, in their"
                " own shuffle order — meaningless off the phone that holds it"
            ),
            "host": "per-member, in that member's own answer order",
        },
    ),
    # --- the round --------------------------------------------------------
    "question_started": _c("broadcast", "tv", "host", "phone"),
    "timer_tick": _c("broadcast", "tv", "host", "phone"),
    "game_state": _c("broadcast", "tv", "host", "phone"),
    "answer_result": _c(
        "unicast",
        "phone",
        ignored={
            "tv": (
                "right/wrong for one player; the board shows the room, not a person"
            ),
            "host": "right/wrong for one player",
        },
    ),
    "guess_accepted": _c(
        "unicast",
        "phone",
        ignored={
            "tv": "an ack that locks one phone's input",
            "host": "an ack that locks one phone's input",
        },
    ),
    "answer_progress": _c(
        "broadcast",
        "tv",
        "phone",
        gaps={
            "host": (
                "#619 wired the submission tracker to the two screens the room "
                "looks at and left the host page out. The host is the one who "
                "decides to move on early, and has no view of who it is "
                "waiting for."
            )
        },
    ),
    "round_summary": _c("broadcast", "tv", "host", "phone"),
    "finale": _c("broadcast", "tv", "host", "phone"),
    "evening_tally": _c(
        "host_and_tv",
        "tv",
        ignored={"phone": _PHONE_HAS_NO},
        gaps={
            "host": (
                "#612 sends the sitting's tally to admins AND dashboards, and "
                "only the television renders it. Either the host page grows a "
                "place for it or the server should stop sending it there."
            )
        },
    ),
    "head_to_head": _c(
        "host_and_tv",
        "tv",
        ignored={"phone": _PHONE_HAS_NO},
        gaps={
            "host": (
                "#613, same shape as evening_tally: broadcast to admins and "
                "dashboards, rendered on one of them."
            )
        },
    ),
    "all_time_update": _c(
        "unicast",
        "phone",
        ignored={
            "tv": (
                "one player's own season standing (#371); the board would be"
                " showing a stranger's history"
            ),
            "host": "one player's own season standing",
        },
    ),
    # --- wagering ---------------------------------------------------------
    "wager_window": _c(
        "unicast",
        "phone",
        ignored={
            "tv": "each phone gets its own bank; the board follows wager_progress",
            "host": "each phone gets its own bank; the host follows wager_progress",
        },
    ),
    "wager_progress": _c(
        "host_and_tv",
        "tv",
        "host",
        ignored={
            "phone": _PHONE_HAS_NO + " — how many have bet, never how big (#656)"
        },
    ),
    "wager_accepted": _c(
        "unicast",
        "phone",
        ignored={
            "tv": "one phone's ack for its own stake",
            "host": "one phone's ack for its own stake",
        },
    ),
    # --- power-ups and reactions -----------------------------------------
    "powerup_assigned": _c(
        "unicast",
        "phone",
        ignored={
            "tv": (
                "what one player was dealt, before they use it — announcing it"
                " would give the table away"
            ),
            "host": "what one player was dealt, before they use it",
        },
    ),
    "powerup_applied": _c("broadcast", "tv", "host", "phone"),
    "reaction": _c("broadcast", "tv", "host", "phone"),
    "reaction_bonus": _c("broadcast", "tv", "host", "phone"),
    # --- lightning --------------------------------------------------------
    "lightning_splash": _c("broadcast", "tv", "host", "phone"),
    "lightning_question": _c("broadcast", "tv", "host", "phone"),
    "lightning_tick": _c("broadcast", "tv", "host", "phone"),
    "lightning_recap": _c("broadcast", "tv", "host", "phone"),
    "lightning_answer_result": _c(
        "unicast",
        "phone",
        ignored={
            "tv": "locks one phone's buttons and shows it right/wrong",
            "host": "locks one phone's buttons and shows it right/wrong",
        },
    ),
    "lightning_team_answer": _c(
        "unicast",
        "phone",
        ignored={
            "tv": "per-member, in that member's own answer order",
            "host": "per-member, in that member's own answer order",
        },
    ),
    # --- hot seat ---------------------------------------------------------
    #
    # This block used to be #699 in one column: seven broadcasts, and the host
    # page had a case for none of them — which is also #664 (the TV froze)
    # read from the other side, the mode having been built phone-first and the
    # other surfaces discovered separately, months apart. #832 closed the
    # column: the detour announces its phase changes with these frames and
    # sends one snapshot at the start, so a host page that read the snapshot
    # alone was right for four seconds and stale for the rest of the round.
    "hot_seat_auction": _c(
        "broadcast",
        "tv",
        "host",
        ignored={
            "phone": (
                "the phone is served the unicast hot_seat_auction_you, which"
                " carries its own bank"
            )
        },
    ),
    "hot_seat_auction_you": _c(
        "unicast",
        "phone",
        ignored={
            "tv": "one player's own bank and bidding controls",
            "host": "one player's own bank and bidding controls",
        },
    ),
    "hot_seat_bid_accepted": _c(
        "unicast",
        "phone",
        ignored={
            "tv": (
                "a blind auction — the room learns how many have bid"
                " (hot_seat_bid_count), never how much"
            ),
            "host": "a blind auction; the count is the public half",
        },
    ),
    "hot_seat_bid_count": _c("broadcast", "tv", "phone", "host"),
    "hot_seat_bet_accepted": _c(
        "unicast",
        "phone",
        ignored={
            "tv": "one spectator's own stake on the seat holder (#616)",
            "host": "one spectator's own stake on the seat holder",
        },
    ),
    "hot_seat_no_bids": _c("broadcast", "tv", "phone", "host"),
    "hot_seat_awarded": _c("broadcast", "tv", "phone", "host"),
    "hot_seat_question": _c("broadcast", "tv", "phone", "host"),
    "hot_seat_answer_accepted": _c(
        "unicast",
        "phone",
        ignored={
            "tv": "an ack for the one person in the chair",
            "host": "an ack for the one person in the chair",
        },
    ),
    "hot_seat_tick": _c("broadcast", "tv", "phone", "host"),
    "hot_seat_result": _c("broadcast", "tv", "phone", "host"),
    # --- errors -----------------------------------------------------------
    "error": _c(
        "unicast",
        "host",
        "phone",
        ignored={
            "tv": (
                "the board's only outbound message is get_state, and an error "
                "for it would be a server bug the room cannot act on — there "
                "is no input on a television to correct"
            )
        },
    ),
}


#: Types a surface handles that the server does not send. Each one is a
#: decision to keep dead weight, so each one carries the reason.
LEGACY_ALIASES: dict[str, str] = {
    "round_evaluated": (
        "an internal state-machine event name (server/broadcast_dispatcher.py), "
        "never a wire frame. Both boards alias it onto round_summary; harmless, "
        "and cheap insurance for a client cached from a build that did send it."
    ),
    "game_ended": (
        "the other internal state event, aliased onto finale for the same reason."
    ),
    "leaderboard_update": (
        "#619 found zero senders and the case was kept deliberately: a live TV "
        "cached from an older build may still be listening, and the handler "
        "costs nothing."
    ),
}


# ---------------------------------------------------------------------------
# The guard
# ---------------------------------------------------------------------------


def test_every_declared_frame_has_a_decision() -> None:
    """A new server frame cannot be added without saying what each screen does.

    This is the assertion the whole file exists for. Without it, "the phone
    renders it" keeps being the definition of done.
    """
    declared = set(declared_server_frames())
    missing = sorted(declared - set(COVERAGE))
    assert not missing, (
        f"new server frames with no surface decision: {missing}. Add each to "
        "COVERAGE in this file and say, for all three surfaces, whether it "
        "renders, is rightly ignored, or is a gap. Deciding here is cheaper "
        "than a guest finding out in a party."
    )


def test_the_table_describes_no_frame_the_server_stopped_sending() -> None:
    """A row for a dead frame is a rule nobody can break — and misleads."""
    declared = set(declared_server_frames())
    stale = sorted(set(COVERAGE) - declared)
    assert not stale, (
        f"COVERAGE describes frames the server no longer declares: {stale}. "
        "Drop the rows (and any case labels they were protecting)."
    )


@pytest.mark.parametrize("frame", sorted(COVERAGE))
def test_each_frame_accounts_for_all_three_surfaces(frame: str) -> None:
    """Exactly once each — no surface silently left out, none decided twice."""
    entry = COVERAGE[frame]
    named = list(entry.renders) + list(entry.ignored) + list(entry.gaps)
    assert sorted(named) == sorted(SURFACES), (
        f"{frame}: surfaces accounted for = {sorted(named)}, expected each of "
        f"{list(SURFACES)} exactly once"
    )
    assert entry.audience in {"broadcast", "host_and_tv", "unicast"}, (
        f"{frame}: unknown audience {entry.audience!r}"
    )


@pytest.mark.parametrize("surface", SURFACES)
def test_every_frame_declared_rendered_is_actually_handled(surface: str) -> None:
    """Delete a case and this goes red, which is the point of #741 and #698."""
    handled = handled_types(surface)
    missing = sorted(
        frame
        for frame, entry in COVERAGE.items()
        if surface in entry.renders and frame not in handled
    )
    assert not missing, (
        f"{surface} is declared to render {missing} but its message router has "
        "no case for them. Either restore the handler or move the frame to "
        "`ignored` / `gaps` with the reason."
    )


@pytest.mark.parametrize("surface", SURFACES)
def test_every_reason_for_ignoring_a_frame_is_still_true(surface: str) -> None:
    """A reason that has quietly become false is worse than no reason at all."""
    handled = handled_types(surface)
    now_handled = sorted(
        frame
        for frame, entry in COVERAGE.items()
        if surface in entry.ignored and frame in handled
    )
    assert not now_handled, (
        f"{surface} now handles {now_handled}, which this file says it is right "
        "to ignore. Move them to `renders` — the stated reason is no longer the "
        "truth about the code."
    )


@pytest.mark.parametrize("surface", SURFACES)
def test_a_recorded_gap_that_got_fixed_is_removed(surface: str) -> None:
    """The gap list can only shrink, and only on purpose."""
    handled = handled_types(surface)
    closed = sorted(
        frame
        for frame, entry in COVERAGE.items()
        if surface in entry.gaps and frame in handled
    )
    assert not closed, (
        f"{surface} handles {closed} now — the gap is closed. Move them to "
        "`renders` and delete the note, so the list keeps meaning what it says."
    )


@pytest.mark.parametrize("surface", SURFACES)
def test_no_surface_handles_a_frame_nobody_sends(surface: str) -> None:
    """Dead cases are how a switch grows fiction.

    ``leaderboard_update`` is the live example: the television handled it for a
    long time and the server had never sent it. Keeping one is allowed — it is
    written down in LEGACY_ALIASES with the reason — inventing a new one is not.
    """
    declared = set(declared_server_frames())
    unknown = sorted(handled_types(surface) - declared - set(LEGACY_ALIASES))
    assert not unknown, (
        f"{surface} has cases for {unknown}, which server/protocol.py does not "
        "declare. Either the frame is real and belongs in SERVER_FRAMES, or the "
        "case is dead and should go — or, if it is deliberate dead weight, say "
        "so in LEGACY_ALIASES."
    )


def test_every_legacy_alias_is_still_referenced_somewhere() -> None:
    """An alias no surface handles any more is just a paragraph."""
    everything: set[str] = set()
    for surface in SURFACES:
        everything |= handled_types(surface)
    orphans = sorted(set(LEGACY_ALIASES) - everything)
    assert not orphans, (
        f"LEGACY_ALIASES explains {orphans}, which no surface handles any "
        "more. Delete the entries."
    )


def test_the_gap_list_is_what_the_issue_said_it_was() -> None:
    """A floor under the record, so nobody quietly empties it to go green.

    #787 counted the drift before any of this was written, and #830 wrote out
    what was left of it for the host page: seven ``hot_seat_*`` broadcasts plus
    ``answer_progress``, ``evening_tally`` and ``head_to_head``. The seven are
    gone — #832 wired the host page to the detour it had only ever seen in a
    snapshot, and each entry came out with the handler that closed it, which is
    the only way this list is allowed to shrink.

    The other three are named rather than counted, because a count says nothing
    about which one somebody deleted. When the last of them is wired, this test
    goes with it rather than being weakened.
    """
    gaps = {
        (frame, surface)
        for frame, entry in COVERAGE.items()
        for surface in entry.gaps
    }
    host = {frame for frame, surface in gaps if surface == "host"}
    assert host == {"answer_progress", "evening_tally", "head_to_head"}, (
        "#830 records three frames the host page still drops; this file now "
        f"records {sorted(host)}"
    )

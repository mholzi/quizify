"""A team-level Hot Seat auction and final wager (#804).

#668 and #669 switched both features off in team mode and called the team-level
versions "a feature, not a fix". The setup screen went on offering both, ticked,
with their normal descriptions — and since teams are formed by the guests in
the lobby, i.e. after the settings, a host had no way to learn that the promise
had lapsed. This is the other half of that issue: the mechanics themselves.

The shape is the one #552 used to bring Lightning into team mode. There is an
**entrant**: a team, or a player who joined none. Entrants bid, entrants bet,
entrants stake, and settlement is written to the entrant — which is the same
object ``get_ranked_participants`` hands the leaderboard, the podium and the
awards, so a detour's points land where the room can see them.

Two decisions inside that shape are worth naming, because both had a plausible
alternative:

* **One bid per team, and the bidder takes the chair.** That is the carrier
  rule team answers already use, pointed forwards instead of backwards: rather
  than asking afterwards who owns the response, the tap that spends the team's
  points is the tap that volunteers to answer for them. It keeps the mode's
  premise — one player answers alone — while the stake and the payout belong
  to the team. A bid stays sealed and un-re-decidable, unlike an answer: there
  is no clock to change it under and nothing to show the team it changed from.

* **The seat holder's whole team is refused a spectator bet.** The seat holder
  is refused because backing their own failure and then supplying it turns a
  question they cannot answer into a profit. Teammates share the purse, so the
  same hedge is available to any of them.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from custom_components.quizify.game.hot_seat import HotSeatRound, stake_of
from custom_components.quizify.game.phase_controller import GamePhase
from custom_components.quizify.game.state import QuizifyGameState


def _ws() -> MagicMock:
    ws = MagicMock()
    ws.closed = False
    return ws


class _Runtime:
    def __init__(self, tmp_path: Path) -> None:
        self.data_dir = tmp_path


def _game(tmp_path: Path, *, rounds: int = 8) -> QuizifyGameState:
    """Two teams of two plus one guest who joined none — three entrants."""
    st = QuizifyGameState(runtime=_Runtime(tmp_path), entry_id="test")
    for name in ("Anna", "Jan", "Mira", "Tom", "Eva"):
        st.add_player(name, _ws())
    st.create_team("Sofa", "Anna")
    st.join_team(st.get_team_of("Anna")["team_id"], "Jan")
    st.create_team("Küche", "Mira")
    st.join_team(st.get_team_of("Mira")["team_id"], "Tom")
    st.start_game(
        num_rounds=rounds,
        language="en",
        hot_seat_seed=7,
        lightning_seed=7,
    )
    return st


def _team(st: QuizifyGameState, name: str):  # noqa: ANN202
    return next(t for t in st.team_registry.all_teams() if t.name == name)


def _open_auction(st: QuizifyGameState) -> HotSeatRound:
    st.phase = GamePhase.ANSWER_REVEAL
    assert st.start_hot_seat_auction() is True, "the auction refused to open"
    hs = st.hot_seat
    assert hs is not None
    return hs


def _seat_answer_index(hs: HotSeatRound, *, correct: bool) -> int:
    """A button index in the seat holder's own shuffle."""
    want = hs.question.answers[hs.correct_index].text
    texts = hs.shuffled_answers()
    if correct:
        return texts.index(want)
    return next(i for i, t in enumerate(texts) if t != want)


# ===========================================================================
# The auction
# ===========================================================================


def test_the_banks_are_the_scores_the_room_can_see(tmp_path: Path) -> None:
    """#669's core complaint: bids were shares of an invisible number.

    ``player.score`` in team mode is the carrier's by-product. The auction now
    snapshots the participants the leaderboard is built from, so a bid costs
    what the television says it costs.
    """
    st = _game(tmp_path)
    _team(st, "Sofa").score = 200
    _team(st, "Küche").score = 120
    st.get_player("Eva").score = 60

    hs = _open_auction(st)

    assert hs.scores == {
        _team(st, "Sofa").team_id: 200,
        _team(st, "Küche").team_id: 120,
        "Eva": 60,
    }


def test_a_member_bids_a_share_of_the_team_score(tmp_path: Path) -> None:
    st = _game(tmp_path)
    _team(st, "Sofa").score = 200
    hs = _open_auction(st)

    assert hs.record_bid("Anna", 25) is True
    bid = hs.bids[_team(st, "Sofa").team_id]
    assert bid.pct == 25
    assert bid.by == "Anna"
    assert stake_of(hs.scores[bid.name], bid.pct) == 50


def test_one_bid_per_team(tmp_path: Path) -> None:
    """The second member is refused, not merged: the window is sealed."""
    st = _game(tmp_path)
    _team(st, "Sofa").score = 200
    hs = _open_auction(st)

    assert hs.record_bid("Anna", 25) is True
    assert hs.record_bid("Jan", 90) is False
    assert len(hs.bids) == 1
    assert hs.bids[_team(st, "Sofa").team_id].pct == 25


def test_the_member_who_bid_takes_the_chair(tmp_path: Path) -> None:
    """The carrier rule, pointed forwards: the tap that pays is the tap that sits."""
    st = _game(tmp_path)
    _team(st, "Sofa").score = 200
    _team(st, "Küche").score = 200
    hs = _open_auction(st)

    hs.record_bid("Jan", 60)
    hs.record_bid("Mira", 10)

    assert hs.resolve_auction() == _team(st, "Sofa").team_id
    assert hs.seat_holder == "Jan"
    assert hs.winner_name == "Sofa"


def test_only_the_seat_holder_may_answer(tmp_path: Path) -> None:
    """One player answers alone — that is the mode. A teammate is not them."""
    st = _game(tmp_path)
    _team(st, "Sofa").score = 200
    _team(st, "Küche").score = 200
    hs = _open_auction(st)
    hs.record_bid("Jan", 60)
    hs.record_bid("Mira", 10)
    hs.resolve_auction()

    assert hs.record_answer("Anna", _seat_answer_index(hs, correct=True)) is None
    assert hs.record_answer("Jan", _seat_answer_index(hs, correct=True)) is True


def test_the_seat_holders_team_may_not_bet(tmp_path: Path) -> None:
    """Teammates share the purse, so they share the hedge the seat holder has."""
    st = _game(tmp_path)
    _team(st, "Sofa").score = 200
    _team(st, "Küche").score = 200
    hs = _open_auction(st)
    hs.record_bid("Jan", 60)
    hs.record_bid("Mira", 10)
    hs.resolve_auction()

    assert hs.is_on_seat_team("Anna") is True
    assert hs.record_bet("Anna", "wont", 100) is False
    assert hs.record_bet("Jan", "wont", 100) is False
    assert hs.record_bet("Mira", "will", 50) is True


def test_one_bet_per_team(tmp_path: Path) -> None:
    st = _game(tmp_path)
    for team in st.team_registry.all_teams():
        team.score = 200
    hs = _open_auction(st)
    hs.record_bid("Anna", 60)
    hs.resolve_auction()

    assert hs.record_bet("Mira", "will", 50) is True
    assert hs.record_bet("Tom", "wont", 50) is False
    assert len(hs.bets) == 1


def test_a_won_chair_pays_the_team(tmp_path: Path) -> None:
    """#669's other half: the settlement used to land on ``player.score``.

    The leaderboard, podium and awards all read the participants, so a payout
    written to a member is a payout nobody in the room ever sees.
    """
    st = _game(tmp_path)
    sofa, kueche = _team(st, "Sofa"), _team(st, "Küche")
    sofa.score = 200
    kueche.score = 200
    hs = _open_auction(st)
    hs.record_bid("Anna", 50)
    hs.resolve_auction()
    hs.record_bet("Mira", "wont", 50)
    hs.record_answer("Anna", _seat_answer_index(hs, correct=True))

    st.finish_hot_seat()

    assert sofa.score == 300  # +50 % of 200
    assert kueche.score == 100  # backed the wrong side, -50 % of 200
    assert st.get_player("Anna").score == 0  # no shadow payout


def test_a_lost_chair_costs_the_team(tmp_path: Path) -> None:
    st = _game(tmp_path)
    sofa = _team(st, "Sofa")
    sofa.score = 200
    hs = _open_auction(st)
    hs.record_bid("Anna", 50)
    hs.resolve_auction()
    hs.record_answer("Anna", _seat_answer_index(hs, correct=False))

    st.finish_hot_seat()
    assert sofa.score == 100


def test_an_unanswered_chair_costs_the_team_too(tmp_path: Path) -> None:
    """#653's strict rule, unchanged: the chair was bought either way."""
    st = _game(tmp_path)
    sofa = _team(st, "Sofa")
    sofa.score = 200
    hs = _open_auction(st)
    hs.record_bid("Anna", 50)
    hs.resolve_auction()

    st.finish_hot_seat()
    assert sofa.score == 100


def test_a_solo_guest_still_settles_on_themselves(tmp_path: Path) -> None:
    """A player who joined no team is an entrant of their own (#365)."""
    st = _game(tmp_path)
    for team in st.team_registry.all_teams():
        team.score = 50
    eva = st.get_player("Eva")
    eva.score = 300
    hs = _open_auction(st)
    hs.record_bid("Eva", 40)
    hs.resolve_auction()
    hs.record_answer("Eva", _seat_answer_index(hs, correct=True))

    st.finish_hot_seat()
    assert eva.score == 420
    assert hs.seat_holder == "Eva"


def test_a_team_member_never_collects_the_teams_payout(tmp_path: Path) -> None:
    """The entrant key of a teamed player names nobody payable.

    Written as its own test because the bug it guards against is silent: a
    settlement resolved by name would credit the member's shadow column and
    the team's row would simply never move.
    """
    st = _game(tmp_path)
    assert st._participant_by_entrant("Anna") is None
    assert st._participant_by_entrant("Eva") is st.get_player("Eva")
    assert st._participant_by_entrant(_team(st, "Sofa").team_id) is _team(st, "Sofa")


def test_two_teams_called_the_same_thing_stay_two_bidders(tmp_path: Path) -> None:
    """#728's lesson, ported: keyed by name they collapse into one entrant."""
    st = QuizifyGameState(runtime=_Runtime(tmp_path), entry_id="test")
    for name in ("Anna", "Mira", "Eva"):
        st.add_player(name, _ws())
    st.create_team("Sofa", "Anna")
    st.create_team("Sofa", "Mira")
    st.start_game(
        num_rounds=8, language="en", hot_seat_seed=7, lightning_seed=7,
    )
    for team in st.team_registry.all_teams():
        team.score = 100
    st.get_player("Eva").score = 100

    hs = _open_auction(st)
    assert len(hs.scores) == 3
    assert hs.record_bid("Anna", 30) is True
    assert hs.record_bid("Mira", 40) is True
    assert len(hs.bids) == 2


def test_the_reveal_prints_names_and_not_ids(tmp_path: Path) -> None:
    st = _game(tmp_path)
    for team in st.team_registry.all_teams():
        team.score = 100
    hs = _open_auction(st)
    hs.record_bid("Anna", 30)
    hs.record_bid("Mira", 10)

    rows = hs.reveal()
    assert [r["name"] for r in rows] == ["Sofa", "Küche"]
    assert rows[0]["by"] == "Anna"
    assert rows[0]["entrant_id"] == _team(st, "Sofa").team_id


def test_the_summary_names_the_person_and_the_payer(tmp_path: Path) -> None:
    """``winner`` is who sits, ``entrant`` is who pays — identical solo."""
    st = _game(tmp_path)
    _team(st, "Sofa").score = 100
    hs = _open_auction(st)
    hs.record_bid("Jan", 30)
    hs.resolve_auction()

    summary = hs.summary()
    assert summary["winner"] == "Jan"
    assert summary["entrant"] == "Sofa"
    assert summary["winner_delta"] == -30


# ===========================================================================
# The final wager
# ===========================================================================


def _final_round(tmp_path: Path) -> QuizifyGameState:
    st = _game(tmp_path, rounds=1)
    st.start_next_question()
    return st


def test_the_final_round_parks_the_team_game_in_the_betting_window(
    tmp_path: Path,
) -> None:
    st = _final_round(tmp_path)
    assert st.phase == GamePhase.WAGER_ACTIVE


def test_any_member_may_place_the_teams_bet_and_the_last_one_counts(
    tmp_path: Path,
) -> None:
    """The rule the standing answer already follows (#365)."""
    st = _final_round(tmp_path)
    assert st.record_wager("Anna", 40) is True
    assert _team(st, "Sofa").wager == 40
    assert st.record_wager("Jan", 70) is True
    assert _team(st, "Sofa").wager == 70
    assert st.get_player("Anna").wager is None


def test_the_room_waits_on_teams_not_on_members(tmp_path: Path) -> None:
    """One bet per team, so a tally over people never reaches its own total."""
    st = _final_round(tmp_path)
    st.record_wager("Anna", 40)
    assert st.players_missing_wager() == ["Küche", "Eva"]
    st.record_wager("Tom", 10)
    st.record_wager("Eva", 0)
    assert st.players_missing_wager() == []


def test_a_disconnected_team_does_not_hold_the_window_open(
    tmp_path: Path,
) -> None:
    st = _final_round(tmp_path)
    for name in ("Mira", "Tom"):
        st.get_player(name).connected = False
    st.record_wager("Anna", 40)
    st.record_wager("Eva", 0)
    assert st.players_missing_wager() == []


def test_a_won_bet_pays_the_team_against_the_team_score(tmp_path: Path) -> None:
    """#668's complaint: the bet was staked against the carrier's shadow bank."""
    st = _final_round(tmp_path)
    sofa = _team(st, "Sofa")
    sofa.score = 120
    st.get_player("Jan").score = 999  # the shadow bank must not be read
    st.record_wager("Anna", 50)
    st.arm_round_timers()

    question = st.get_current_question()
    correct = next(i for i, a in enumerate(question.answers) if a.correct)
    st.submit_answer("Jan", correct)
    st.evaluate_round()

    assert sofa.score == 180  # 120 + 50 % of 120


def test_a_lost_bet_costs_the_team_and_stops_at_zero(tmp_path: Path) -> None:
    st = _final_round(tmp_path)
    sofa = _team(st, "Sofa")
    sofa.score = 120
    st.record_wager("Jan", 100)
    st.arm_round_timers()

    question = st.get_current_question()
    wrong = next(i for i, a in enumerate(question.answers) if not a.correct)
    st.submit_answer("Anna", wrong)
    st.evaluate_round()

    assert sofa.score == 0


def test_a_team_that_never_answers_still_pays_its_bet(tmp_path: Path) -> None:
    """#653's rule, applied to the team: a silent clock costs the stake."""
    st = _final_round(tmp_path)
    sofa = _team(st, "Sofa")
    sofa.score = 200
    st.record_wager("Anna", 25)
    st.arm_round_timers()

    st.evaluate_round()

    assert sofa.score == 150
    assert sofa.round_history[-1] == "timeout"
    assert sofa.round_scores[-1] == -50


def test_a_team_without_a_bet_loses_nothing_by_sitting_out(
    tmp_path: Path,
) -> None:
    """Nobody is punished for not betting (#308)."""
    st = _final_round(tmp_path)
    sofa = _team(st, "Sofa")
    sofa.score = 200
    st.arm_round_timers()
    st.evaluate_round()

    assert sofa.score == 200
    assert sofa.round_scores[-1] == 0


def test_the_solo_guest_keeps_their_own_bet(tmp_path: Path) -> None:
    """A player in no team bets for themselves, in the same team game."""
    st = _final_round(tmp_path)
    eva = st.get_player("Eva")
    eva.score = 80
    assert st.record_wager("Eva", 50) is True
    assert eva.wager == 50
    st.arm_round_timers()

    question = st.get_current_question()
    correct = next(i for i, a in enumerate(question.answers) if a.correct)
    st.submit_answer("Eva", correct)
    st.evaluate_round()

    assert eva.score == 120


def test_the_bet_is_locked_once_the_team_has_an_answer(tmp_path: Path) -> None:
    """#255: a wager stakes points on an answer, so it closes when that does.

    No member is ever marked ``submitted`` in team mode (#365), so a check on
    that flag would leave the team's bet editable for the whole question.
    """
    st = _final_round(tmp_path)
    assert st.wager_is_locked("Anna") is False
    st.arm_round_timers()
    question = st.get_current_question()
    st.submit_answer("Jan", next(i for i, a in enumerate(question.answers) if a.correct))
    assert st.wager_is_locked("Anna") is True
    assert st.wager_is_locked("Mira") is False


def test_the_teams_bet_is_cleared_between_rounds(tmp_path: Path) -> None:
    """A wager is per-round, exactly as ``PlayerSession.reset_round`` has it."""
    st = _final_round(tmp_path)
    st.record_wager("Anna", 40)
    _team(st, "Sofa").reset_round()
    assert _team(st, "Sofa").wager is None


@pytest.mark.parametrize("bank", (0, 5, 400))
def test_the_window_shows_the_team_bank_not_the_members(
    tmp_path: Path, bank: int
) -> None:
    """The slider prices the bet; priced against a shadow score it lies."""
    from custom_components.quizify.server.round_message_builder import (
        RoundMessageBuilder,
    )

    st = _final_round(tmp_path)
    _team(st, "Sofa").score = bank
    st.get_player("Anna").score = 7  # the number that must NOT be shown

    msg = RoundMessageBuilder().build_wager_window(
        st,
        question=st.get_current_question(),
        player=st.get_player("Anna"),
        window_duration=20.0,
    )
    assert msg["player_score"] == bank


# ===========================================================================
# What reaches the phones and the television
# ===========================================================================


def _project(st: QuizifyGameState, name: str) -> dict:
    from custom_components.quizify.server.round_message_builder import (
        RoundMessageBuilder,
    )

    return RoundMessageBuilder().project_snapshot_for_player(
        st,
        snapshot=st.get_state_snapshot(),
        player=st.get_player(name),
    )["hot_seat"]


def test_a_phone_is_told_its_teams_bank_and_not_its_own(tmp_path: Path) -> None:
    """``own_bank`` prices the bid slider; read by name it was 0 for everyone."""
    st = _game(tmp_path)
    _team(st, "Sofa").score = 240
    st.get_player("Anna").score = 3  # the shadow number that must not be shown
    _open_auction(st)

    assert _project(st, "Anna")["own_bank"] == 240


def test_a_phone_sees_the_bid_its_team_already_placed(tmp_path: Path) -> None:
    """A reload must not offer a second bid the server will refuse."""
    st = _game(tmp_path)
    _team(st, "Sofa").score = 240
    hs = _open_auction(st)
    hs.record_bid("Anna", 35)

    assert _project(st, "Jan")["you_bid"] == 35
    assert _project(st, "Mira")["you_bid"] is None


def test_the_snapshot_seats_the_person_and_flags_their_team(
    tmp_path: Path,
) -> None:
    """Compared against the entrant, ``you_are_seated`` matched nobody."""
    st = _game(tmp_path)
    for team in st.team_registry.all_teams():
        team.score = 200
    hs = _open_auction(st)
    hs.record_bid("Jan", 60)
    hs.record_bid("Mira", 10)
    hs.resolve_auction()

    seated = _project(st, "Jan")
    mate = _project(st, "Anna")
    rival = _project(st, "Mira")

    assert seated["you_are_seated"] is True
    assert seated["you_are_seat_team"] is False
    assert mate["you_are_seated"] is False
    assert mate["you_are_seat_team"] is True
    assert rival["you_are_seat_team"] is False
    assert seated["winner"] == "Jan"
    assert seated["entrant"] == "Sofa"


def test_the_wager_tally_counts_entrants(tmp_path: Path) -> None:
    """"1 of 5 in" for a room where every team has decided never closes."""
    from custom_components.quizify.server.round_message_builder import (
        RoundMessageBuilder,
    )

    st = _final_round(tmp_path)
    st.record_wager("Anna", 40)
    msg = RoundMessageBuilder().build_wager_progress(st)

    assert msg["player_count"] == 3
    assert msg["locked_in"] == 1
    assert msg["waiting_on"] == ["Küche", "Eva"]


def test_the_television_reads_the_payer_and_its_own_delta() -> None:
    """``deltas`` is keyed by entrant — a team id no screen can construct."""
    dashboard = (
        Path(__file__).resolve().parent.parent
        / "custom_components" / "quizify" / "www" / "dashboard.html"
    ).read_text(encoding="utf-8")
    assert "msg.entrant || msg.winner" in dashboard
    assert "msg.winner_delta" in dashboard


def test_the_phone_hides_the_bet_slider_from_the_seat_holders_team() -> None:
    """A control the server refuses is worse than no control."""
    js = (
        Path(__file__).resolve().parent.parent
        / "custom_components" / "quizify" / "www" / "js" / "player-hotseat.js"
    ).read_text(encoding="utf-8")
    assert "you_are_seat_team" in js
    assert "hotSeat.teamSeated" in js


@pytest.mark.parametrize("language", ("de", "en", "es"))
def test_the_team_seated_line_is_translated(language: str) -> None:
    import json

    bundle = json.loads(
        (
            Path(__file__).resolve().parent.parent
            / "custom_components" / "quizify" / "www" / "i18n" / f"{language}.json"
        ).read_text(encoding="utf-8")
    )
    assert bundle["hotSeat"]["teamSeated"].strip()


def test_the_bundle_carries_the_phone_change() -> None:
    """player.html loads the bundle, not the modules — #625's second trap."""
    bundle = (
        Path(__file__).resolve().parent.parent
        / "custom_components" / "quizify" / "www" / "js" / "player.bundle.js"
    ).read_text(encoding="utf-8")
    assert "you_are_seat_team" in bundle

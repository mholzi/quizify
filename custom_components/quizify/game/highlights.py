"""End-of-game superlative awards for Quizify."""

from __future__ import annotations

from dataclasses import dataclass

from .player import PlayerSession


@dataclass
class Superlative:
    """A single end-of-game award."""

    award: str  # e.g. "Fastest Finger"
    icon: str  # emoji
    winner: str  # player name
    detail: str  # e.g. "avg 4.2s per correct answer"

    def to_dict(self) -> dict[str, str]:
        return {
            "award": self.award,
            "icon": self.icon,
            "winner": self.winner,
            "detail": self.detail,
        }


MIN_PLAYERS = 2
MIN_ROUNDS = 3


def compute_superlatives(players: list[PlayerSession]) -> list[Superlative]:
    """Compute end-of-game superlative awards.

    Each award goes to exactly one player. A player can only win one award.
    Returns empty list if fewer than 2 players or fewer than 3 rounds played.
    """
    if len(players) < MIN_PLAYERS:
        return []

    max_rounds = max((len(p.round_history) for p in players), default=0)
    if max_rounds < MIN_ROUNDS:
        return []

    awarded: set[str] = set()
    results: list[Superlative] = []

    def _try_award(award: str, icon: str, detail: str, winner: str | None) -> None:
        if winner is None or winner in awarded:
            return
        awarded.add(winner)
        results.append(Superlative(award=award, icon=icon, winner=winner, detail=detail))

    # --- Fastest Finger: lowest average answer time (correct answers only) ---
    best_avg_time: float | None = None
    fastest_player: str | None = None
    for p in players:
        if p.name in awarded:
            continue
        if len(p.answer_times) >= 2:
            avg = sum(p.answer_times) / len(p.answer_times)
            if best_avg_time is None or avg < best_avg_time:
                best_avg_time = avg
                fastest_player = p.name

    if fastest_player and best_avg_time is not None:
        _try_award(
            "Fastest Finger",
            "⚡",
            f"avg {best_avg_time:.1f}s per correct answer",
            fastest_player,
        )

    # --- Comeback King: biggest score improvement second half vs first half ---
    best_comeback: int | None = None
    comeback_player: str | None = None
    for p in players:
        if p.name in awarded:
            continue
        scores = p.round_scores
        if len(scores) < 4:
            continue
        mid = len(scores) // 2
        first_half = sum(scores[:mid])
        second_half = sum(scores[mid:])
        improvement = second_half - first_half
        if improvement > 0 and (best_comeback is None or improvement > best_comeback):
            best_comeback = improvement
            comeback_player = p.name

    if comeback_player and best_comeback is not None:
        _try_award(
            "Comeback King",
            "🚀",
            f"+{best_comeback} pts in the second half",
            comeback_player,
        )

    # --- Hot Streak: longest streak achieved during the game ---
    best_streak = 0
    streak_player: str | None = None
    for p in players:
        if p.name in awarded:
            continue
        if p.max_streak > best_streak:
            best_streak = p.max_streak
            streak_player = p.name

    if streak_player and best_streak >= 2:
        _try_award(
            "Hot Streak",
            "🔥",
            f"{best_streak} correct in a row",
            streak_player,
        )

    # --- Perfect Round: highest correct-answer ratio (or most correct) ---
    best_ratio = 0.0
    best_correct = 0
    perfect_player: str | None = None
    perfect_player_obj: PlayerSession | None = None
    for p in players:
        if p.name in awarded:
            continue
        total = len(p.round_history)
        if total == 0:
            continue
        correct = p.round_history.count("correct")
        ratio = correct / total
        if ratio > best_ratio or (ratio == best_ratio and correct > best_correct):
            best_ratio = ratio
            best_correct = correct
            perfect_player = p.name
            perfect_player_obj = p

    if perfect_player and perfect_player_obj and best_correct >= 2:
        pct = int(best_ratio * 100)
        _try_award(
            "Perfect Round",
            "💯",
            f"{best_correct}/{len(perfect_player_obj.round_history)} correct ({pct}%)",
            perfect_player,
        )

    # --- Buzzkill: most freeze power-ups used against others ---
    most_freezes = 0
    buzzkill_player: str | None = None
    for p in players:
        if p.name in awarded:
            continue
        if p.freezes_used > most_freezes:
            most_freezes = p.freezes_used
            buzzkill_player = p.name

    if buzzkill_player and most_freezes >= 1:
        _try_award(
            "Buzzkill",
            "🧊",
            f"{most_freezes} freeze{'s' if most_freezes != 1 else ''} used",
            buzzkill_player,
        )

    # --- Knowledge Expert: highest score on hard questions only ---
    best_hard = 0
    expert_player: str | None = None
    for p in players:
        if p.name in awarded:
            continue
        if p.hard_score > best_hard:
            best_hard = p.hard_score
            expert_player = p.name

    if expert_player and best_hard > 0:
        _try_award(
            "Knowledge Expert",
            "🧠",
            f"{best_hard} pts on hard questions",
            expert_player,
        )

    return results

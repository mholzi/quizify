"""Per-question stats persistence for Quizify.

Tracks, for each question id, how often it has been shown, how often it
was answered correctly, and the average time-to-answer for correct
submissions. Used by pack maintainers to spot questions that are too
hard, too easy, or ambiguous.

Lives in its own JSON file (``question_stats.json``) rather than in
``analytics.json`` so the dashboard endpoint can stay slim — question-
level stats grow with the pack catalogue, not with games played.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import TYPE_CHECKING, Any, TypedDict

if TYPE_CHECKING:
    from .runtime import Runtime

_LOGGER = logging.getLogger(__name__)


class QuestionStat(TypedDict):
    """Per-question aggregate stats."""

    question_id: str
    shown_count: int
    correct_count: int
    # Sum of correct answer elapsed times — divide by correct_count for avg.
    # Stored as sum so a single round update is a single add.
    total_time_correct: float
    last_shown: int  # unix seconds


class QuestionStatsData(TypedDict):
    version: int
    questions: dict[str, QuestionStat]


class QuestionStatsService:
    """Async JSON store for per-question aggregate stats."""

    def __init__(self, runtime: Runtime) -> None:
        self._runtime = runtime
        self._path = runtime.data_dir / "question_stats.json"
        self._data: QuestionStatsData = {"version": 1, "questions": {}}
        self._save_lock = asyncio.Lock()
        self._dirty = False

    async def load(self) -> None:
        try:
            if self._path.exists():
                content = await self._runtime.run_in_executor(self._path.read_text)
                raw = json.loads(content)
                # Lenient migration: tolerate missing keys.
                self._data = {
                    "version": int(raw.get("version", 1)),
                    "questions": raw.get("questions") or {},
                }
                _LOGGER.debug(
                    "Loaded question stats: %d questions tracked",
                    len(self._data["questions"]),
                )
        except (json.JSONDecodeError, KeyError, TypeError) as err:
            _LOGGER.warning("Question stats file corrupted, recreating: %s", err)
            self._data = {"version": 1, "questions": {}}

    def record_round(
        self,
        question_id: str,
        results: list[tuple[bool, float]],
    ) -> None:
        """Record one round's results.

        ``results`` is a list of (correct, elapsed_seconds) tuples — one
        per participant who submitted. Players who timed out should NOT
        be included; counting them as wrong would dilute the difficulty
        signal (a question can't be blamed for someone not answering).

        ``shown_count`` counts SUBMISSIONS, not rounds, so a question
        seen by 5 players in one round counts as 5. That way
        ``correct_count / shown_count`` is a true "what fraction of
        submitters got this right" — the difficulty signal.
        """
        if not question_id:
            return
        now = int(time.time())
        q = self._data["questions"].get(question_id)
        if q is None:
            q = {
                "question_id": question_id,
                "shown_count": 0,
                "correct_count": 0,
                "total_time_correct": 0.0,
                "last_shown": 0,
            }
            self._data["questions"][question_id] = q
        q["last_shown"] = now
        for correct, elapsed in results:
            q["shown_count"] += 1
            if correct:
                q["correct_count"] += 1
                q["total_time_correct"] += float(elapsed)
        self._dirty = True

    def get_hardest(self, limit: int = 25, min_shown: int = 3) -> list[dict[str, Any]]:
        """Questions with the LOWEST correct rate, gated by min_shown so a
        one-time miss doesn't dominate the list."""
        items = []
        for q in self._data["questions"].values():
            if q["shown_count"] < min_shown:
                continue
            rate = q["correct_count"] / q["shown_count"] if q["shown_count"] else 0.0
            avg = (
                q["total_time_correct"] / q["correct_count"]
                if q["correct_count"] else None
            )
            items.append({
                "question_id": q["question_id"],
                "shown_count": q["shown_count"],
                "correct_count": q["correct_count"],
                "correct_rate": round(rate, 3),
                "avg_time_correct": round(avg, 2) if avg is not None else None,
                "last_shown": q["last_shown"],
            })
        items.sort(key=lambda x: x["correct_rate"])
        return items[:limit]

    def get_easiest(self, limit: int = 25, min_shown: int = 3) -> list[dict[str, Any]]:
        """Inverse of get_hardest — highest correct rate."""
        hardest = self.get_hardest(limit=len(self._data["questions"]), min_shown=min_shown)
        hardest.sort(key=lambda x: x["correct_rate"], reverse=True)
        return hardest[:limit]

    async def save_if_dirty(self) -> None:
        """Persist if any record_round happened since the last save.

        Cheap to call after every game end; no-op if nothing changed.
        """
        if not self._dirty:
            return
        async with self._save_lock:
            try:
                def _mkdir() -> None:
                    self._path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
                await self._runtime.run_in_executor(_mkdir)
                temp_path = self._path.with_suffix(".tmp")
                content = json.dumps(self._data, indent=2)
                def _write() -> None:
                    temp_path.write_text(content)
                    os.replace(temp_path, self._path)
                await self._runtime.run_in_executor(_write)
                self._dirty = False
            except OSError as err:
                _LOGGER.error("Failed to save question stats: %s", err)

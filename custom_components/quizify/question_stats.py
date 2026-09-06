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
import logging
import time
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any, TypedDict

from .storage import JsonFile

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

    #: Seconds between the last recorded round and the debounced write. Long
    #: enough that a normal round cadence coalesces into one write, short
    #: enough that walking away mid-game costs at most this much (#588).
    SAVE_DEBOUNCE_SECONDS = 5.0

    def __init__(self, runtime: Runtime) -> None:
        self._runtime = runtime
        self._path = runtime.data_dir / "question_stats.json"
        self._file = JsonFile(runtime, self._path, label="Question stats file")
        self._data: QuestionStatsData = {"version": 1, "questions": {}}
        self._save_lock = asyncio.Lock()
        self._dirty = False
        self._save_timer: asyncio.Future[None] | None = None

    async def load(self) -> None:
        """Read the stats file, degrading a broken one to an empty record.

        Parse failures are handled by the shared store (#790, #700); what is
        left here is the lenient *migration* — a file of the right syntax but
        the wrong shape (a bare list, a missing ``questions`` key) must not
        take setup down either.
        """
        raw = await self._file.load(None)
        if raw is None:
            return
        try:
            if not isinstance(raw, dict):
                raise TypeError(f"expected an object, got {type(raw).__name__}")
            questions = raw.get("questions") or {}
            if not isinstance(questions, dict):
                raise TypeError("'questions' is not an object")
            self._data = {
                "version": int(raw.get("version", 1)),
                "questions": questions,
            }
        except (KeyError, TypeError, ValueError) as err:
            _LOGGER.warning("Question stats file corrupted, recreating: %s", err)
            self._data = {"version": 1, "questions": {}}
            return
        _LOGGER.debug(
            "Loaded question stats: %d questions tracked",
            len(self._data["questions"]),
        )

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
        self._schedule_save()

    def aggregate_for_questions(self, ids: Iterable[str]) -> tuple[int, int]:
        """Sum (shown_count, correct_count) over the given question *ids*.

        Public read accessor used by the featured-pack difficulty ranking so
        callers don't reach into the internal ``_data`` store. Questions with
        no recorded stats contribute zero. Returns ``(total_shown,
        total_correct)``.
        """
        questions = self._data["questions"]
        shown = 0
        correct = 0
        for qid in ids:
            s = questions.get(qid)
            if s and s.get("shown_count", 0) >= 1:
                shown += s["shown_count"]
                correct += s.get("correct_count", 0)
        return shown, correct

    def get_hardest(self, limit: int = 25, min_shown: int = 3) -> list[dict[str, Any]]:
        """Questions with the LOWEST correct rate, gated by min_shown so a
        one-time miss doesn't dominate the list."""
        items: list[dict[str, Any]] = []
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
        hardest = self.get_hardest(
            limit=len(self._data["questions"]), min_shown=min_shown
        )
        hardest.sort(key=lambda x: x["correct_rate"], reverse=True)
        return hardest[:limit]

    async def save_if_dirty(self) -> None:
        """Persist if any record_round happened since the last save.

        Cheap to call after every game end; no-op if nothing changed.
        """
        if not self._dirty:
            return
        async with self._save_lock:
            # The serialize runs inside the executor (#304) so the (often
            # large) ``json.dumps`` is off the event loop, not just the write.
            if await self._file.save(self._data):
                self._dirty = False

    def _schedule_save(self) -> None:
        """(Re-)arm the debounced write after a recorded round (#588).

        ``end_game()`` used to be the only caller of :meth:`save_if_dirty`, so
        a game that was abandoned — tab closed, HA restarted, integration
        reloaded — threw away every round it had accumulated. Rearming a short
        timer here means an interrupted evening still leaves its rounds on
        disk, while a game played to the end still writes exactly once at the
        end (the timer coalesces the rounds in between).

        No-op without a running event loop: ``record_round`` is a plain
        synchronous call and is exercised that way in tests, where scheduling a
        task would raise. Those callers keep the previous behaviour and persist
        through :meth:`save_if_dirty` / :meth:`async_flush`.
        """
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return
        timer = self._save_timer
        if timer is not None and not timer.done():
            timer.cancel()
        self._save_timer = self._runtime.create_task(self._debounced_save())

    async def _debounced_save(self) -> None:
        """Wait out the quiet period, then persist."""
        try:
            await asyncio.sleep(self.SAVE_DEBOUNCE_SECONDS)
        except asyncio.CancelledError:
            # Superseded by a later round, or cancelled by async_flush() —
            # which persists itself. Nothing to do.
            return
        await self.save_if_dirty()

    async def async_flush(self) -> None:
        """Persist right now, cancelling any armed debounce (#588).

        Called on config-entry unload and on ``EVENT_HOMEASSISTANT_STOP`` so a
        restart, a reload or an integration update cannot drop the rounds that
        are still only in memory. Safe to call when nothing is pending:
        :meth:`save_if_dirty` is a no-op then.
        """
        timer = self._save_timer
        self._save_timer = None
        if timer is not None and not timer.done():
            timer.cancel()
        await self.save_if_dirty()

"""Question bank and data models for Quizify."""

from __future__ import annotations

import json
import logging
import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from ..const import ANSWERS_PER_QUESTION
from .seasons import parse_season

if TYPE_CHECKING:
    from ..runtime import Runtime

_LOGGER = logging.getLogger(__name__)

QUESTIONS_DIR = Path(__file__).resolve().parent.parent / "questions"

# Community packs live in a dedicated subfolder so user-contributed JSON is
# kept apart from the built-in, reviewed packs. The non-recursive ``*.json``
# glob used for built-in discovery never reaches into this subfolder, so the
# two namespaces stay cleanly separated.
COMMUNITY_SUBDIR = "community"

# Slugs of community packs are prefixed so they can never collide with (or
# silently shadow) a built-in pack slug.
COMMUNITY_SLUG_PREFIX = "community-"

# Defensive caps applied to user-contributed packs only. Built-in packs are
# reviewed by hand, but community JSON is untrusted input: a single pack must
# not be able to exhaust memory or flood the category picker. These ceilings
# are generous (the largest built-in pack is ~150 questions) but finite.
MAX_COMMUNITY_PACK_BYTES = 1_048_576  # 1 MiB per pack file
MAX_COMMUNITY_QUESTIONS = 500  # questions kept per community pack


@dataclass
class Answer:
    """A single answer option."""

    text: str
    correct: bool


@dataclass
class Question:
    """A quiz question with multiple choice answers."""

    id: str
    question: str
    answers: list[Answer] = field(default_factory=list)
    difficulty: str = "medium"
    fun_fact: str = ""
    category: str = ""
    language: str = "de"
    # Optional image rendered above the question text on the dashboard /
    # as a thumbnail on player screens. Empty string means "no image"
    # (issue #25). Only http(s) URLs are accepted (see _sanitize_image_url);
    # anything else is dropped at parse time so a malformed pack can't
    # inject markup or point at a local path.
    image_url: str = ""


def _sanitize_image_url(raw: object, question_id: str) -> str:
    """Return a safe http(s) image URL, or "" if absent/invalid.

    Only absolute http/https URLs are allowed. A relative path, a
    ``javascript:``/``data:`` scheme, or a non-string is dropped (and
    logged) so a malformed or hostile pack can't break the dashboard
    render or smuggle markup through the field.
    """
    if not raw:
        return ""
    if not isinstance(raw, str):
        _LOGGER.warning(
            "Question '%s': ignoring non-string image_url (%s)",
            question_id,
            type(raw).__name__,
        )
        return ""
    url = raw.strip()
    if url.lower().startswith(("http://", "https://")):
        return url
    _LOGGER.warning(
        "Question '%s': ignoring image_url with unsupported scheme: %r",
        question_id,
        url[:60],
    )
    return ""


def _normalize_season(raw: object) -> dict | None:
    """Validate a pack's ``season`` field, returning a normalised dict or None.

    Delegates parsing/validation to :func:`.seasons.parse_season` (which logs
    and tolerates anything malformed), then re-emits the *normalised* MM-DD
    window as a plain JSON-serialisable dict so it can be stored on the pack
    metadata and shipped over the ``/api/quizify/packs`` endpoint unchanged.
    Returns ``None`` when there is no (valid) season, keeping packs without the
    field fully back-compatible.
    """
    season = parse_season(raw)
    if season is None:
        return None
    return {
        "start": f"{season.start[0]:02d}-{season.start[1]:02d}",
        "end": f"{season.end[0]:02d}-{season.end[1]:02d}",
        "label": season.label,
    }


def _parse_question(data: dict, category_name: str) -> Question | None:
    """Parse a single question dict into a Question, or None on invalid data."""
    required = ("id", "question", "answers")
    for key in required:
        if key not in data:
            _LOGGER.warning(
                "Skipping question in '%s': missing field '%s'",
                category_name,
                key,
            )
            return None

    answers_raw = data["answers"]
    if not isinstance(answers_raw, list) or len(answers_raw) != ANSWERS_PER_QUESTION:
        _LOGGER.warning(
            "Skipping question '%s': expected %d answers, got %s",
            data["id"],
            ANSWERS_PER_QUESTION,
            len(answers_raw) if isinstance(answers_raw, list) else type(answers_raw),
        )
        return None

    correct_count = sum(1 for a in answers_raw if a.get("correct"))
    if correct_count != 1:
        _LOGGER.warning(
            "Skipping question '%s': expected 1 correct answer, got %d",
            data["id"],
            correct_count,
        )
        return None

    answers = [
        Answer(text=a["text"], correct=bool(a.get("correct", False)))
        for a in answers_raw
    ]

    return Question(
        id=data["id"],
        question=data["question"],
        answers=answers,
        difficulty=data.get("difficulty", "medium"),
        fun_fact=data.get("fun_fact", ""),
        category=data.get("category", category_name),
        image_url=_sanitize_image_url(data.get("image_url"), data["id"]),
    )


class QuestionBank:
    """Manages loading and serving questions from JSON files."""

    def __init__(self, questions_dir: Path | None = None) -> None:
        """Initialize the question bank."""
        self._questions_dir = questions_dir or QUESTIONS_DIR
        self._categories: dict[str, list[Question]] = {}
        self._pack_versions: dict[str, dict] = {}
        self._queue: list[Question] = []
        self._queue_index: int = 0
        self._loaded: bool = False
        # Question history: maps question_id -> unix timestamp last shown
        # (or 0 if never)
        self._history: dict[str, float] = {}
        self._history_path: Path | None = None
        # Questions shown in the current game (to record at end)
        self._shown_this_game: list[str] = []

    @property
    def categories(self) -> dict[str, list[Question]]:
        """Loaded categories mapping slug -> questions.

        A shallow copy so callers can read the per-category question lists
        (e.g. featured-pack difficulty aggregation) without mutating the
        bank's internal store. Use :meth:`get_categories` for just the slugs.
        """
        return dict(self._categories)

    @property
    def questions_dir(self) -> Path:
        """Directory the question-pack JSON files are loaded from."""
        return self._questions_dir

    def load_category(self, category: str) -> list[Question]:
        """Load questions for a single category from its JSON file."""
        file_path = self._questions_dir / f"{category}.json"
        if not file_path.is_file():
            _LOGGER.warning("Category file not found: %s", file_path)
            return []

        try:
            raw = json.loads(file_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            _LOGGER.error("Invalid JSON in '%s': %s", file_path, exc)
            return []

        questions_data = raw.get("questions", [])
        category_name = raw.get("name", category)
        pack_language = raw.get("language", "de")
        pack_version = raw.get("version", "1.0")
        questions: list[Question] = []

        for entry in questions_data:
            q = _parse_question(entry, category_name)
            if q is not None:
                q.language = pack_language
                questions.append(q)

        self._categories[category] = questions
        meta: dict = {
            "version": pack_version,
            "name": category_name,
            "language": pack_language,
            "question_count": len(questions),
            # Store the pack's theme at load time (#309) so the featured-pack
            # view can map it to a spotlight icon without re-reading + re-parsing
            # the pack JSON per request — and, crucially, so it resolves for
            # community packs too (their on-disk path differs from the slug, so
            # the old per-request re-read always missed them → 🎲 default icon).
            "theme": raw.get("theme", "") if isinstance(raw.get("theme"), str) else "",
        }
        # Optional recurring seasonal window (#276). Parsed + validated here so
        # a malformed window is dropped at load time; the normalised MM-DD form
        # is stored on the pack metadata (date-agnostic) and the date check
        # happens later in the featured-pack view against the current clock.
        season_meta = _normalize_season(raw.get("season"))
        if season_meta is not None:
            meta["season"] = season_meta
        self._pack_versions[category] = meta
        _LOGGER.debug(
            "Loaded %d questions for category '%s' (v%s)",
            len(questions),
            category,
            pack_version,
        )
        return questions

    def load_all_categories(self) -> dict[str, list[Question]]:
        """Discover and load all category JSON files.

        Subsequent calls return the cached result without re-reading from disk.
        Call reload_categories() to force a fresh load.
        """
        if self._loaded:
            return dict(self._categories)

        if not self._questions_dir.is_dir():
            _LOGGER.warning("Questions directory not found: %s", self._questions_dir)
            return {}

        # Skip non-pack metadata files. `versions.json` is the pack
        # manifest, not a question pack — loading it produced an empty
        # "versions" category that tripped up tests and showed up as a
        # 0-question option to the host.
        _META_FILES = {"versions"}

        for file_path in sorted(self._questions_dir.glob("*.json")):
            category_slug = file_path.stem
            if category_slug in _META_FILES:
                continue
            self.load_category(category_slug)

        # User-contributed packs are loaded after the built-in packs so the
        # collision check can see every built-in slug. Failures inside this
        # call are self-contained (each bad pack is skipped, not raised).
        self.load_community_packs()

        self._loaded = True
        return dict(self._categories)

    def reload_categories(self) -> dict[str, list[Question]]:
        """Clear the cache and reload all categories from disk."""
        self._loaded = False
        self._categories = {}
        return self.load_all_categories()

    def load_community_packs(self) -> dict[str, list[Question]]:
        """Discover and load user-contributed packs from the community subfolder.

        Community packs are untrusted input, so loading is deliberately
        defensive: every failure mode (missing folder, unreadable file,
        oversized file, malformed JSON, wrong top-level shape, missing name,
        empty/oversized question list, slug collision with a built-in pack) is
        logged and skipped rather than raised. A single bad pack can never
        prevent the others — or the built-in packs — from loading.

        Each community pack is registered under a prefixed slug
        (``community-<filename>``) so it can never shadow a built-in category.
        Returns the mapping of the community packs that were loaded.
        """
        community_dir = self._questions_dir / COMMUNITY_SUBDIR
        loaded: dict[str, list[Question]] = {}

        if not community_dir.is_dir():
            _LOGGER.debug("No community pack directory at %s", community_dir)
            return loaded

        for file_path in sorted(community_dir.glob("*.json")):
            slug = f"{COMMUNITY_SLUG_PREFIX}{file_path.stem}"

            if slug in self._categories:
                _LOGGER.warning(
                    "Skipping community pack '%s': slug '%s' already loaded",
                    file_path.name,
                    slug,
                )
                continue

            try:
                size = file_path.stat().st_size
            except OSError as exc:
                _LOGGER.warning("Cannot stat community pack '%s': %s", file_path, exc)
                continue

            if size > MAX_COMMUNITY_PACK_BYTES:
                _LOGGER.warning(
                    "Skipping community pack '%s': %d bytes exceeds limit of %d",
                    file_path.name,
                    size,
                    MAX_COMMUNITY_PACK_BYTES,
                )
                continue

            try:
                raw = json.loads(file_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
                _LOGGER.warning("Invalid community pack '%s': %s", file_path.name, exc)
                continue

            if not isinstance(raw, dict):
                _LOGGER.warning(
                    "Skipping community pack '%s': top-level JSON must be an object",
                    file_path.name,
                )
                continue

            questions_data = raw.get("questions")
            if not isinstance(questions_data, list) or not questions_data:
                _LOGGER.warning(
                    "Skipping community pack '%s': 'questions' must be a "
                    "non-empty list",
                    file_path.name,
                )
                continue

            if len(questions_data) > MAX_COMMUNITY_QUESTIONS:
                _LOGGER.warning(
                    "Community pack '%s': truncating %d questions to limit of %d",
                    file_path.name,
                    len(questions_data),
                    MAX_COMMUNITY_QUESTIONS,
                )
                questions_data = questions_data[:MAX_COMMUNITY_QUESTIONS]

            category_name = raw.get("name")
            if not isinstance(category_name, str) or not category_name.strip():
                _LOGGER.warning(
                    "Skipping community pack '%s': missing or empty 'name'",
                    file_path.name,
                )
                continue

            pack_language = raw.get("language", "de")
            if not isinstance(pack_language, str):
                pack_language = "de"
            pack_version = raw.get("version", "1.0")
            if not isinstance(pack_version, (str, int, float)):
                pack_version = "1.0"

            questions: list[Question] = []
            seen_ids: set[str] = set()
            for entry in questions_data:
                if not isinstance(entry, dict):
                    continue
                q = _parse_question(entry, category_name)
                if q is None:
                    continue
                if q.id in seen_ids:
                    _LOGGER.warning(
                        "Community pack '%s': dropping duplicate question id '%s'",
                        file_path.name,
                        q.id,
                    )
                    continue
                seen_ids.add(q.id)
                q.language = pack_language
                questions.append(q)

            if not questions:
                _LOGGER.warning(
                    "Skipping community pack '%s': no valid questions after validation",
                    file_path.name,
                )
                continue

            self._categories[slug] = questions
            community_meta: dict = {
                "version": str(pack_version),
                "name": category_name,
                "language": pack_language,
                "question_count": len(questions),
                "community": True,
                # Theme stored at load time (#309) — community packs live at
                # questions/community/<stem>.json under a ``community-`` slug, so
                # the old views.py path read (questions_dir / f"{slug}.json")
                # never found them and they always fell back to the 🎲 icon.
                "theme": (
                    raw.get("theme", "")
                    if isinstance(raw.get("theme"), str)
                    else ""
                ),
            }
            community_season = _normalize_season(raw.get("season"))
            if community_season is not None:
                community_meta["season"] = community_season
            self._pack_versions[slug] = community_meta
            loaded[slug] = questions
            _LOGGER.info(
                "Loaded community pack '%s' (%d questions) as '%s'",
                file_path.name,
                len(questions),
                slug,
            )

        return loaded

    def get_categories(self) -> list[str]:
        """Return list of loaded category slugs."""
        return list(self._categories.keys())

    def get_pack_versions(self) -> dict[str, dict]:
        """Return metadata (version, name, language, question_count) per pack."""
        return dict(self._pack_versions)

    def get_next_question(
        self, category: str | None = None, difficulty: str | None = None
    ) -> Question | None:
        """Get the next question from the queue, advancing the index."""
        if not self._queue:
            self._build_queue(category, difficulty)

        if self._queue_index >= len(self._queue):
            return None

        question = self._queue[self._queue_index]
        self._queue_index += 1
        return question

    def get_next_question_at_difficulty(
        self, target_difficulty: str
    ) -> Question | None:
        """Serve the next unconsumed queued question, preferring ``target_difficulty``.

        Used by the group-level adaptive ("auto") difficulty mode (#40): the
        queue is built across *all* difficulties (no per-difficulty filter), and
        each round we pull the next question matching the calibrated target.

        Selection (all over not-yet-consumed queue entries, preserving the
        history-aware ordering established by ``_build_queue``):

        1. Prefer the first question whose ``difficulty`` equals the target.
        2. If none remain at the exact target, fall back to the nearest
           available difficulty on the easy<->medium<->hard ladder.
        3. If the queue is exhausted, return None.

        The chosen question is removed from the remaining-queue window (so it is
        not served twice) and the queue index advances past it.
        """
        remaining = self._queue[self._queue_index:]
        if not remaining:
            return None

        ladder = ["easy", "medium", "hard"]
        try:
            target_idx = ladder.index(target_difficulty)
        except ValueError:
            target_idx = 1  # treat unknown target as medium

        # Build a search order over difficulties: exact target first, then
        # the closest rungs outward (so a missing "hard" prefers "medium"
        # over "easy", etc.). Stable, deterministic.
        order = sorted(
            range(len(ladder)),
            key=lambda i: (abs(i - target_idx), i),
        )

        for diff_idx in order:
            diff = ladder[diff_idx]
            for offset, q in enumerate(remaining):
                if q.difficulty == diff:
                    # Consume this question: pull it out of the queue so the
                    # window shrinks and the next call sees one fewer item.
                    abs_index = self._queue_index + offset
                    self._queue.pop(abs_index)
                    return q

        return None

    def shuffle_questions(self, questions: list[Question]) -> list[Question]:
        """Return a shuffled copy of the given question list."""
        shuffled = list(questions)
        random.shuffle(shuffled)
        return shuffled

    def reset(
        self,
        category: str | None = None,
        categories: list[str] | None = None,
        difficulty: str | None = None,
        language: str | None = None,
    ) -> None:
        """Reset and rebuild the question queue for a new game session.

        Pass ``categories`` (list of slugs) to restrict to a specific subset.
        Pass ``category`` (single slug) for single-category mode.
        Pass neither for mixed (all categories).
        """
        self._queue_index = 0
        self._build_queue(category, difficulty, language, categories)

    def get_question_count(
        self, category: str, difficulty: str | None = None
    ) -> int:
        """Return the question count for a category, optionally by difficulty."""
        questions = self._categories.get(category, [])
        if difficulty is not None:
            return sum(1 for q in questions if q.difficulty == difficulty)
        return len(questions)

    def validate_answer(self, question: Question, answer_index: int) -> bool:
        """Check whether the given answer index is correct."""
        if not 0 <= answer_index < len(question.answers):
            return False
        return question.answers[answer_index].correct

    def get_correct_answer(self, question: Question) -> Answer:
        """Return the correct Answer for a question.

        Raises ValueError if no answer is marked correct. The validator
        (`_parse_question`) requires exactly one correct answer per
        question, so reaching this fallback means the question pack
        is malformed at runtime. Better to fail loudly than silently
        return `answers[0]` (which is almost certainly wrong) and
        display the wrong answer to players. (#146.)
        """
        for answer in question.answers:
            if answer.correct:
                return answer
        raise ValueError(
            f"Question {question.id!r} has no correct answer marked"
        )

    # ------------------------------------------------------------------
    # Question history
    # ------------------------------------------------------------------

    def set_history_path(self, history_path: Path) -> None:
        """Bind the history file path without touching disk (non-blocking)."""
        self._history_path = history_path

    def _read_history(self) -> None:
        """Blocking read of the history file. MUST run off the event loop."""
        history_path = self._history_path
        if history_path is None:
            return
        if history_path.exists():
            try:
                raw = json.loads(history_path.read_text(encoding="utf-8"))
                self._history = {k: float(v) for k, v in raw.items()}
                _LOGGER.debug("Loaded question history: %d entries", len(self._history))
            except (json.JSONDecodeError, ValueError, OSError) as exc:
                _LOGGER.warning("Failed to load question history: %s", exc)
                self._history = {}
        else:
            self._history = {}

    def load_history(self, history_path: Path) -> None:
        """Load question history from a JSON file (synchronous).

        Retained for the standalone dev server and tests. On the HA event
        loop use :meth:`load_history_async` so the ``read_text`` is offloaded
        (issue #222 read-path).
        """
        self.set_history_path(history_path)
        self._read_history()

    async def load_history_async(self, history_path: Path, runtime: Runtime) -> None:
        """Bind the path and load history via the runtime's executor thread."""
        self.set_history_path(history_path)
        await runtime.run_in_executor(self._read_history)

    def _write_history(self) -> None:
        """Blocking write of the history file. MUST run off the event loop.

        Snapshot a copy of ``self._history`` *before* dispatching this to an
        executor thread so the serialized payload can't tear if the loop
        thread mutates the dict (via ``record_shown``) while the write runs.
        """
        if self._history_path is None:
            return
        snapshot = dict(self._history)
        try:
            self._history_path.parent.mkdir(parents=True, exist_ok=True)
            self._history_path.write_text(
                json.dumps(snapshot, indent=2), encoding="utf-8"
            )
        except OSError as exc:
            _LOGGER.warning("Failed to save question history: %s", exc)

    def save_history(self) -> None:
        """Persist question history to disk (synchronous).

        Retained for the standalone dev server and tests. In Home Assistant
        this blocks the event loop — callers on the loop thread MUST use
        :meth:`save_history_async` / :meth:`flush_shown_history_async`
        instead (issue #222).
        """
        self._write_history()

    async def save_history_async(self, runtime: Runtime) -> None:
        """Persist question history via the runtime's executor thread.

        Mirrors the offload pattern used by ``QuestionStats`` and the
        asset-fingerprint walk (#213): the blocking ``write_text`` never
        runs on the event loop. ``runtime`` is a
        :class:`~custom_components.quizify.runtime.Runtime`.
        """
        if self._history_path is None:
            return
        await runtime.run_in_executor(self._write_history)

    def record_shown(self, question_id: str) -> None:
        """Mark a question as shown now."""
        self._history[question_id] = time.time()
        self._shown_this_game.append(question_id)

    def flush_shown_history(self) -> None:
        """Save history at end of game and reset the shown-this-game list.

        Synchronous variant — only safe off the event loop (standalone
        server / tests). On the HA event loop use
        :meth:`flush_shown_history_async`.
        """
        self._shown_this_game = []
        self.save_history()

    async def flush_shown_history_async(self, runtime: Runtime) -> None:
        """Async flush: reset shown-this-game and persist via the executor.

        The shown-this-game list is cleared synchronously (cheap, in-memory)
        and only the file write is offloaded to a thread (issue #222).
        """
        self._shown_this_game = []
        await self.save_history_async(runtime)

    def _build_queue(
        self,
        category: str | None = None,
        difficulty: str | None = None,
        language: str | None = None,
        categories: list[str] | None = None,
    ) -> None:
        """Build and shuffle the internal question queue.

        Priority: ``categories`` list > single ``category`` > all (mixed).
        """
        if categories:
            pool = [q for slug in categories for q in self._categories.get(slug, [])]
        elif category is not None:
            pool = list(self._categories.get(category, []))
        else:
            pool = [q for qs in self._categories.values() for q in qs]

        if difficulty is not None:
            pool = [q for q in pool if q.difficulty == difficulty]

        if language is not None:
            pool = [q for q in pool if q.language == language]

        # Sort by history: never-shown questions first, then oldest-shown first.
        # Within each group, randomise to avoid predictable ordering.
        if self._history:
            import random as _random
            never_shown = [q for q in pool if q.id not in self._history]
            previously_shown = [q for q in pool if q.id in self._history]
            _random.shuffle(never_shown)
            previously_shown.sort(key=lambda q: self._history.get(q.id, 0))
            self._queue = never_shown + previously_shown
        else:
            self._queue = self.shuffle_questions(pool)
        self._queue_index = 0

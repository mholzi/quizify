"""Question bank and data models for Quizify."""

from __future__ import annotations

import json
import logging
import math
import random
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from ..const import ANSWERS_PER_QUESTION, DEFAULT_AVOID_RECENT_REPEATS
from .seasons import parse_season

if TYPE_CHECKING:
    from ..runtime import Runtime

_LOGGER = logging.getLogger(__name__)

QUESTIONS_DIR = Path(__file__).resolve().parent.parent / "questions"

# Community packs live in a dedicated subfolder so user-contributed JSON is
# kept apart from the built-in, reviewed packs. The non-recursive ``*.json``
# glob used for built-in discovery never reaches into this subfolder, so the
# two namespaces stay cleanly separated.
#
# NOTE (#743): this folder is *inside* the integration directory, which HACS
# replaces wholesale on every update. It is where the shipped example pack
# lives and where hosts used to drop their own packs — and where those packs
# were silently deleted by the next update. The host-owned drop-in location is
# now ``<config>/quizify/packs`` (see ``COMMUNITY_PACKS_DIRNAME``); this folder
# remains a read-only scan root for the shipped example and for any pack a
# migration could not move.
COMMUNITY_SUBDIR = "community"

# Host-owned drop-in folder, relative to the runtime data dir
# (``<config>/quizify`` under Home Assistant). Outside ``custom_components``,
# so a HACS update cannot touch it (#743).
COMMUNITY_PACKS_DIRNAME = "packs"

# Files shipped inside ``questions/community`` by the integration itself. They
# are part of the release artefact, not host data, so the #743 migration leaves
# them where they are — moving them would copy repo content into the host's
# config directory and re-create it on every update.
SHIPPED_COMMUNITY_PACKS = frozenset({"example-pack.json"})

# Slugs of community packs are prefixed so they can never collide with (or
# silently shadow) a built-in pack slug.
COMMUNITY_SLUG_PREFIX = "community-"

# Defensive caps applied to user-contributed packs only. Built-in packs are
# reviewed by hand, but community JSON is untrusted input: a single pack must
# not be able to exhaust memory or flood the category picker. These ceilings
# are generous (the largest built-in pack is ~150 questions) but finite.
MAX_COMMUNITY_PACK_BYTES = 1_048_576  # 1 MiB per pack file
MAX_COMMUNITY_QUESTIONS = 500  # questions kept per community pack

# ---------------------------------------------------------------------------
# Freshness engine (#436) — avoid repeating questions across recent sessions
# ---------------------------------------------------------------------------
# The bank already tracks, per question, the unix timestamp it was last shown
# (``self._history``) and orders "never-shown first, then oldest-shown first".
# The freshness engine refines that plain oldest-first sort into a
# decay-weighted ordering and, when enabled, hard-excludes very recently shown
# questions — with a pool-size guard so a game can always be filled.

# Exponential-decay half-life: the age (seconds since a question was last shown)
# at which it has recovered *half* of its freshness. A question shown long ago
# scores close to a never-shown one; one shown moments ago scores near zero.
# 7 days keeps a question de-prioritised for the week around a session but lets
# it rotate back within a fortnight — a good fit for a party quiz played every
# so often rather than continuously.
FRESHNESS_HALFLIFE_SECONDS = 7 * 24 * 60 * 60  # 7 days
# Decay time-constant τ derived from the half-life: weight = 1 - exp(-age / τ),
# and at age == half-life the weight is exactly 0.5.
FRESHNESS_DECAY_TAU_SECONDS = FRESHNESS_HALFLIFE_SECONDS / math.log(2)

# Hard-exclude window: a question shown within this many seconds is dropped from
# the pool entirely when "avoid recent repeats" is ON (subject to the guard
# below). 6 hours spans a single party evening / multi-round sitting so the same
# questions don't come back the same night, without permanently retiring them.
RECENT_REPEAT_WINDOW_SECONDS = 6 * 60 * 60  # 6 hours

# Pool-size guard: only hard-exclude recent questions while at least this many
# fresh questions remain — otherwise fall back to the soft-penalised
# (decay-ordered) full pool. Home Assistant caps a game at 50 rounds
# (server/websocket.py), so keeping ≥ 50 fresh questions guarantees *any* game
# can be filled regardless of the requested round count; below it, excluding
# would risk an empty / too-short pool, so we degrade gracefully instead.
MIN_FRESH_POOL_SIZE = 50


@dataclass
class Answer:
    """A single answer option."""

    text: str
    correct: bool


# Question types (#275). The default is multiple-choice so every existing
# pack — which carries no ``type`` field — keeps parsing exactly as before.
QUESTION_TYPE_MULTIPLE_CHOICE = "multiple_choice"
QUESTION_TYPE_ESTIMATE = "estimate"
VALID_QUESTION_TYPES = (QUESTION_TYPE_MULTIPLE_CHOICE, QUESTION_TYPE_ESTIMATE)

#: Image reveal styles (#434). ``""`` shows the picture outright, the way
#: every image question behaved before. ``"progressive"`` starts it heavily
#: blurred and sharpens it as the round timer runs down, so guessing early is
#: worth more than guessing late.
REVEAL_STYLE_NONE = ""
REVEAL_STYLE_PROGRESSIVE = "progressive"
VALID_REVEAL_STYLES = (REVEAL_STYLE_NONE, REVEAL_STYLE_PROGRESSIVE)


@dataclass
class Question:
    """A quiz question.

    Multiple-choice questions carry an ``answers`` list with exactly one
    correct option. Estimate questions (#275, ``type == "estimate"``) carry a
    numeric ``estimate_answer`` plus a slider ``estimate_min``/``estimate_max``
    range (and optional ``estimate_unit``/``estimate_step``) instead, with an
    empty ``answers`` list.
    """

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
    # How the image is revealed (#434). Empty means "show it outright", which
    # is what every pack did before this field existed. See
    # ``_sanitize_reveal_style`` — a style without an image is dropped, since
    # unblurring nothing is an effect with no subject.
    reveal_style: str = REVEAL_STYLE_NONE
    # Question type (#275). Defaults to multiple_choice so every pack without
    # a ``type`` field behaves exactly as before.
    type: str = QUESTION_TYPE_MULTIPLE_CHOICE
    # Estimate-question fields (#275). Only meaningful when
    # ``type == "estimate"``; None for multiple-choice questions.
    estimate_answer: float | None = None
    estimate_min: float | None = None
    estimate_max: float | None = None
    estimate_unit: str = ""
    # Slider step. Defaults to 1 (integer guesses) when omitted.
    estimate_step: float = 1.0

    @property
    def is_estimate(self) -> bool:
        """True for an estimate / closest-guess question (#275)."""
        return self.type == QUESTION_TYPE_ESTIMATE


#: The one local location a pack may point at: the integration's own static
#: mount (#536). Anything shipped with the integration lives under here, and
#: it is served read-only, so a pack can reference its own images without
#: depending on a third-party host that may vanish and break every install at
#: once. This is a PREFIX ALLOWLIST, not "relative paths are fine" — see
#: ``_sanitize_image_url``.
LOCAL_IMAGE_PREFIX = "/quizify/static/"

#: Traversal markers rejected inside an otherwise-allowed local path. The
#: percent-encoded form is listed because the browser, not this code, is what
#: finally resolves the path.
_TRAVERSAL_MARKERS = ("..", "%2e%2e")


def _sanitize_image_url(raw: object, question_id: str) -> str:
    """Return a safe image URL, or "" if absent/invalid.

    Two forms are accepted:

    * an absolute ``http``/``https`` URL, and
    * a path under :data:`LOCAL_IMAGE_PREFIX`, i.e. an image shipped inside
      the integration (#536).

    Everything else — any other relative path, ``javascript:``/``data:``, a
    non-string — is dropped (and logged) so a malformed or hostile pack can't
    break the dashboard render or smuggle markup through the field. The local
    form additionally refuses traversal, so a pack cannot climb out of the
    static mount and point at, say, a config file.
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
    if url.startswith(LOCAL_IMAGE_PREFIX):
        lowered = url.lower()
        if any(marker in lowered for marker in _TRAVERSAL_MARKERS):
            _LOGGER.warning(
                "Question '%s': ignoring image_url with path traversal: %r",
                question_id,
                url[:60],
            )
            return ""
        return url
    _LOGGER.warning(
        "Question '%s': ignoring image_url with unsupported scheme: %r",
        question_id,
        url[:60],
    )
    return ""


def _sanitize_reveal_style(
    raw: object, image_url: str, question_id: str
) -> str:
    """Return a valid reveal style, or "" if absent/invalid/pointless (#434).

    Two things are refused, both quietly falling back to "show the image
    outright" rather than to an error:

    * an unknown style — a typo'd ``"progresive"`` must not leave the picture
      permanently blurred, which is what an unrecognised value keyed straight
      into a CSS class would do; and
    * a style on a question with **no image**, because a reveal with nothing
      to reveal is the exact trap this issue was blocked on for months.
    """
    if not raw:
        return REVEAL_STYLE_NONE
    if not isinstance(raw, str):
        _LOGGER.warning(
            "Question '%s': ignoring non-string reveal_style (%s)",
            question_id,
            type(raw).__name__,
        )
        return REVEAL_STYLE_NONE
    style = raw.strip().lower()
    if style not in VALID_REVEAL_STYLES:
        _LOGGER.warning(
            "Question '%s': ignoring unknown reveal_style %r", question_id, style[:40]
        )
        return REVEAL_STYLE_NONE
    if not image_url:
        _LOGGER.warning(
            "Question '%s': reveal_style %r has no image to reveal, ignoring",
            question_id,
            style,
        )
        return REVEAL_STYLE_NONE
    return style


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


def _parse_estimate_question(data: dict, category_name: str) -> Question | None:
    """Parse an ``type: "estimate"`` question (#275), or None if malformed.

    An estimate question replaces the MC ``answers`` array with a numeric
    ``answer`` and a slider ``min``/``max`` range (and optional ``unit`` /
    ``step``). Validated defensively, matching the loader's tolerant style: a
    non-numeric answer, a missing/invalid range, an inverted range
    (``min >= max``), or an answer outside the range is logged and skipped
    rather than raised.
    """
    answer_raw = data.get("answer")
    min_raw = data.get("min")
    max_raw = data.get("max")

    if answer_raw is None or min_raw is None or max_raw is None:
        _LOGGER.warning(
            "Skipping estimate question '%s': needs numeric 'answer', "
            "'min' and 'max'",
            data["id"],
        )
        return None

    try:
        answer_val = float(answer_raw)
        min_val = float(min_raw)
        max_val = float(max_raw)
    except (TypeError, ValueError):
        _LOGGER.warning(
            "Skipping estimate question '%s': 'answer'/'min'/'max' must be "
            "numeric",
            data["id"],
        )
        return None

    if min_val >= max_val:
        _LOGGER.warning(
            "Skipping estimate question '%s': min (%s) must be < max (%s)",
            data["id"],
            min_val,
            max_val,
        )
        return None

    if not min_val <= answer_val <= max_val:
        _LOGGER.warning(
            "Skipping estimate question '%s': answer (%s) outside range "
            "[%s, %s]",
            data["id"],
            answer_val,
            min_val,
            max_val,
        )
        return None

    step_raw = data.get("step", 1)
    try:
        step_val = float(step_raw)
    except (TypeError, ValueError):
        step_val = 1.0
    if step_val <= 0:
        step_val = 1.0

    unit_raw = data.get("unit", "")
    unit_val = unit_raw if isinstance(unit_raw, str) else ""

    image_url = _sanitize_image_url(data.get("image_url"), data["id"])

    return Question(
        id=data["id"],
        question=data["question"],
        answers=[],
        difficulty=data.get("difficulty", "medium"),
        fun_fact=data.get("fun_fact", ""),
        category=data.get("category", category_name),
        image_url=image_url,
        reveal_style=_sanitize_reveal_style(
            data.get("reveal_style"), image_url, data["id"]
        ),
        type=QUESTION_TYPE_ESTIMATE,
        estimate_answer=answer_val,
        estimate_min=min_val,
        estimate_max=max_val,
        estimate_unit=unit_val,
        estimate_step=step_val,
    )


def _parse_question(data: dict, category_name: str) -> Question | None:
    """Parse a single question dict into a Question, or None on invalid data.

    Branches on the optional ``type`` field (#275): ``"estimate"`` routes to
    the numeric estimate parser; anything else (including the absent default)
    is treated as a multiple-choice question and keeps the original rules
    (exactly ``ANSWERS_PER_QUESTION`` answers, exactly one correct).
    """
    # ``id`` and ``question`` are required for every type; ``answers`` is only
    # required for multiple-choice (estimate questions carry no answers array).
    for key in ("id", "question"):
        if key not in data:
            _LOGGER.warning(
                "Skipping question in '%s': missing field '%s'",
                category_name,
                key,
            )
            return None

    q_type = data.get("type", QUESTION_TYPE_MULTIPLE_CHOICE)
    if q_type not in VALID_QUESTION_TYPES:
        _LOGGER.warning(
            "Skipping question '%s': unknown type '%s'",
            data["id"],
            q_type,
        )
        return None

    if q_type == QUESTION_TYPE_ESTIMATE:
        return _parse_estimate_question(data, category_name)

    if "answers" not in data:
        _LOGGER.warning(
            "Skipping question '%s': missing field 'answers'",
            data["id"],
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

    # Every answer must be an object with non-empty ``text`` (#700). Community
    # packs come off disk unvalidated, so a bare string ("A") or a text-less
    # object ({"correct": true}) used to raise out of the loader and take
    # async_setup_entry down with it. Mirrors pack_submission's rules.
    for a in answers_raw:
        if not isinstance(a, dict):
            _LOGGER.warning(
                "Skipping question '%s': every answer must be an object, got %s",
                data["id"],
                type(a).__name__,
            )
            return None
        if not isinstance(a.get("text"), str) or not a["text"].strip():
            _LOGGER.warning(
                "Skipping question '%s': every answer needs a non-empty 'text'",
                data["id"],
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

    image_url = _sanitize_image_url(data.get("image_url"), data["id"])

    return Question(
        id=data["id"],
        question=data["question"],
        answers=answers,
        difficulty=data.get("difficulty", "medium"),
        fun_fact=data.get("fun_fact", ""),
        category=data.get("category", category_name),
        image_url=image_url,
        reveal_style=_sanitize_reveal_style(
            data.get("reveal_style"), image_url, data["id"]
        ),
    )


class QuestionBank:
    """Manages loading and serving questions from JSON files."""

    def __init__(
        self,
        questions_dir: Path | None = None,
        community_dir: Path | None = None,
    ) -> None:
        """Initialize the question bank.

        *questions_dir* holds the built-in packs shipped with the integration.
        *community_dir* is the host-owned drop-in folder outside the
        integration directory (``<config>/quizify/packs`` under Home
        Assistant, #743). When it is ``None`` — bare unit tests, or any caller
        without a runtime — only the in-integration community folder is
        scanned and no migration is attempted.
        """
        self._questions_dir = questions_dir or QUESTIONS_DIR
        self._community_dir = (
            Path(community_dir) if community_dir is not None else None
        )
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
        # Freshness engine toggle (#436). Defaults to the options-flow default so
        # a bank that is never told otherwise behaves like the user-visible
        # default. Threaded in from the config entry via set_avoid_recent_repeats.
        self._avoid_recent_repeats: bool = DEFAULT_AVOID_RECENT_REPEATS

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

    @property
    def community_dir(self) -> Path | None:
        """Host-owned drop-in folder for community packs, if one is bound.

        Lives outside the integration directory so a HACS update cannot delete
        what the host put there (#743). ``None`` when the bank was created
        without a runtime.
        """
        return self._community_dir

    @property
    def legacy_community_dir(self) -> Path:
        """The pre-#743 community folder inside the integration directory.

        Still scanned (it carries the shipped example pack), but no longer the
        place hosts are told to drop their own files.
        """
        return self._questions_dir / COMMUNITY_SUBDIR

    @property
    def is_loaded(self) -> bool:
        """Whether :meth:`load_all_categories` has already populated the bank.

        Lets callers skip a redundant (executor-offloaded) reload when the
        packs are already cached in memory.
        """
        return self._loaded

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

    def load_all_categories(self, force: bool = False) -> dict[str, list[Question]]:
        """Discover and load all category JSON files.

        Subsequent calls return the cached result without re-reading from disk.
        Pass ``force=True`` (or call :meth:`reload_categories`) to re-read from
        disk regardless of the cache. The ``force`` flag exists so the reload
        path cannot be defeated by the ``_loaded`` short-circuit — the flag is
        checked here, not by the caller, so a concurrent load that re-sets
        ``_loaded`` in between cannot turn a requested reload into a no-op.
        """
        if self._loaded and not force:
            return dict(self._categories)

        if force:
            self._reset_loaded_state()

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

    def _reset_loaded_state(self) -> None:
        """Drop every cached artefact of a previous load.

        ``_pack_versions`` matters as much as ``_categories``: the admin
        category chips and the ``/api/quizify/packs`` endpoint are rendered
        from it, so a reload that only cleared the questions would keep
        showing a chip for a pack the host had just deleted. The queue is
        dropped too — it holds ``Question`` objects from the previous load.
        """
        self._loaded = False
        self._categories = {}
        self._pack_versions = {}
        self._queue = []
        self._queue_index = 0

    def reload_categories(self) -> dict[str, list[Question]]:
        """Clear the cache and reload all categories from disk.

        Blocking disk I/O — call it from an executor, never on the event loop.
        Exposed to hosts as the ``quizify.reload_packs`` service (#743) so a
        newly dropped pack can be picked up without restarting Home Assistant.
        """
        return self.load_all_categories(force=True)

    def _community_scan_roots(self) -> list[Path]:
        """Directories scanned for community packs, in precedence order.

        The host-owned folder outside the integration comes first so that when
        the same filename exists in both places the host's copy wins — the
        in-integration copy is then skipped by the slug-collision check.
        """
        roots: list[Path] = []
        if self._community_dir is not None:
            roots.append(self._community_dir)
        legacy = self.legacy_community_dir
        if legacy not in roots:
            roots.append(legacy)
        return roots

    def _ensure_community_dir(self) -> None:
        """Create the host-owned drop-in folder so it is there to be found.

        The README tells hosts to drop packs into ``<config>/quizify/packs``;
        a folder that only appears once somebody guesses the path right is a
        worse instruction than one that is simply there. Best-effort: a
        read-only config dir logs and moves on, and every scan root is
        ``is_dir()``-guarded anyway.
        """
        if self._community_dir is None:
            return
        try:
            self._community_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            _LOGGER.debug(
                "Could not create community pack directory %s: %s",
                self._community_dir,
                exc,
            )

    def _migrate_legacy_community_packs(self) -> None:
        """Move host-dropped packs out of the doomed in-integration folder.

        Before #743 the only documented drop-in location was
        ``custom_components/quizify/questions/community`` — inside the
        directory HACS replaces wholesale on every update, so each update
        deleted the host's own packs without a word. On every load we move any
        leftover ``*.json`` there into the host-owned folder, so a host who
        already had packs in the old place keeps them instead of losing them
        at the next update.

        Deliberately conservative: files shipped by the integration itself
        (:data:`SHIPPED_COMMUNITY_PACKS`) are left alone, an existing file of
        the same name in the target is never overwritten, and any OS error is
        logged and swallowed — a pack that could not be moved is still loaded
        from the legacy folder by :meth:`load_community_packs`, so a failed
        migration degrades to the old behaviour rather than losing a pack.
        """
        if self._community_dir is None:
            return

        legacy = self.legacy_community_dir
        if not legacy.is_dir() or legacy == self._community_dir:
            return

        for file_path in sorted(legacy.glob("*.json")):
            if file_path.name in SHIPPED_COMMUNITY_PACKS:
                continue

            target = self._community_dir / file_path.name
            if target.exists():
                _LOGGER.warning(
                    "Community pack '%s' also exists in %s — leaving the old "
                    "copy in place; the file in %s is the one that loads",
                    file_path.name,
                    self._community_dir,
                    self._community_dir,
                )
                continue

            try:
                self._community_dir.mkdir(parents=True, exist_ok=True)
                shutil.move(str(file_path), str(target))
            except OSError as exc:
                _LOGGER.warning(
                    "Could not move community pack '%s' to %s: %s. It is still "
                    "loaded from %s, but a HACS update will delete it — move "
                    "it by hand.",
                    file_path.name,
                    self._community_dir,
                    exc,
                    legacy,
                )
                continue

            _LOGGER.warning(
                "Moved community pack '%s' from %s to %s so a HACS update "
                "cannot delete it (#743)",
                file_path.name,
                legacy,
                self._community_dir,
            )

    def load_community_packs(self) -> dict[str, list[Question]]:
        """Discover and load user-contributed packs from every scan root.

        Two roots are scanned (#743): the host-owned drop-in folder outside
        the integration directory first, then the in-integration
        ``questions/community`` folder that ships the example pack. Leftover
        host packs in the second are migrated into the first before scanning.

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
        self._ensure_community_dir()
        self._migrate_legacy_community_packs()

        loaded: dict[str, list[Question]] = {}
        for root in self._community_scan_roots():
            loaded.update(self._load_community_dir(root))
        return loaded

    def _load_community_dir(self, community_dir: Path) -> dict[str, list[Question]]:
        """Load every community pack found in a single scan root."""
        loaded: dict[str, list[Question]] = {}

        if not community_dir.is_dir():
            _LOGGER.debug("No community pack directory at %s", community_dir)
            return loaded

        for file_path in sorted(community_dir.glob("*.json")):
            slug = f"{COMMUNITY_SLUG_PREFIX}{file_path.stem}"

            if slug in self._categories:
                _LOGGER.warning(
                    "Skipping community pack '%s' in %s: slug '%s' already "
                    "loaded",
                    file_path.name,
                    community_dir,
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
                # Second line of defence (#700): the parser is meant to return
                # None on bad input, but a future field assumption must not be
                # able to abort setup over a guest's JSON file.
                try:
                    q = _parse_question(entry, category_name)
                except Exception:  # noqa: BLE001 - untrusted file, never fatal
                    _LOGGER.warning(
                        "Community pack '%s': dropping a question that could "
                        "not be parsed",
                        file_path.name,
                        exc_info=True,
                    )
                    continue
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
                # Theme stored at load time (#309) — community packs live in a
                # separate folder under a ``community-`` slug, so the old
                # views.py path read (questions_dir / f"{slug}.json") never
                # found them and they always fell back to the 🎲 icon. Since
                # #743 they may live outside the integration entirely, which
                # makes reconstructing a path from the slug wronger still.
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

    def peek_next_question(self) -> Question | None:
        """The question ``get_next_question`` will serve next, without taking it.

        Read-only twin of the method above: same entry, no ``_queue_index``
        advance and no lazy ``_build_queue`` (an empty queue means the game has
        not started, and building one here would hand the *real* draw a
        different order). Used by the image preload hint (#736), which needs to
        know the next picture one round early.

        NB: this is only the truthful answer for the plain draw. The adaptive
        ("auto") mode serves through ``get_next_question_at_difficulty``, which
        picks a *matching* entry out of the remaining window rather than the
        head — the caller is responsible for not asking there.
        """
        if self._queue_index >= len(self._queue):
            return None
        return self._queue[self._queue_index]

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

    def set_avoid_recent_repeats(self, enabled: bool) -> None:
        """Enable/disable the #436 freshness engine's hard-exclude behaviour.

        Threaded in from the config entry's ``avoid_recent_repeats`` option at
        both initial setup and options reload. When OFF, :meth:`build_pool`
        keeps the original never-shown-first / oldest-shown-first ordering; when
        ON it applies freshness-decay ordering plus a guarded hard-exclude of
        recently shown questions (see :meth:`build_pool`).
        """
        self._avoid_recent_repeats = bool(enabled)

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
            # Compact (no indent): the history map is machine-read only and
            # grows one entry per seen question, so pretty-printing just
            # inflates the file + write cost. Matches the compact writes in
            # analytics.py / question_stats (#457).
            self._history_path.write_text(
                json.dumps(snapshot), encoding="utf-8"
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

    def build_pool(
        self,
        category: str | None = None,
        difficulty: str | None = None,
        language: str | None = None,
        categories: list[str] | None = None,
        exclude_ids: set[str] | None = None,
    ) -> list[Question]:
        """Return a history-ordered question pool **without** mutating state.

        Same filtering + never-shown-first ordering as the internal game
        queue, but returns the list instead of assigning it to ``_queue``.
        Used by the lightning round (#350) to build its own private queue so
        it never disturbs the main game's ``_queue``/``_queue_index``.

        Priority: ``categories`` list > single ``category`` > all (mixed).
        ``exclude_ids`` drops those question ids from the pool (e.g. questions
        the main game already showed this session).

        When the freshness engine is ON (#436, ``set_avoid_recent_repeats``),
        the ordering is refined by exponential freshness-decay and questions
        shown within :data:`RECENT_REPEAT_WINDOW_SECONDS` are hard-excluded —
        but only while ≥ :data:`MIN_FRESH_POOL_SIZE` fresh questions remain, so
        a game can always be filled (see :meth:`_order_pool_freshness_aware`).
        When OFF, the original never-shown-first / oldest-shown-first ordering
        below runs unchanged (fully back-compatible).
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

        if exclude_ids:
            pool = [q for q in pool if q.id not in exclude_ids]

        if self._avoid_recent_repeats:
            return self._order_pool_freshness_aware(pool)

        # Sort by history: never-shown questions first, then oldest-shown first.
        # Within each group, randomise to avoid predictable ordering.
        if self._history:
            never_shown = [q for q in pool if q.id not in self._history]
            previously_shown = [q for q in pool if q.id in self._history]
            random.shuffle(never_shown)
            previously_shown.sort(key=lambda q: self._history.get(q.id, 0))
            return never_shown + previously_shown
        return self.shuffle_questions(pool)

    def _freshness_weight(self, question_id: str, now: float) -> float:
        """Freshness score in ``[0.0, 1.0]`` — higher = fresher.

        Never-shown questions score ``1.0``. A previously shown question scores
        ``1 - exp(-age / τ)`` where ``age`` is the seconds since it was last
        shown: near ``0.0`` right after it was shown, rising back toward ``1.0``
        as it ages (crossing ``0.5`` at :data:`FRESHNESS_HALFLIFE_SECONDS`).
        """
        last_shown = self._history.get(question_id)
        if last_shown is None:
            return 1.0
        age = max(0.0, now - last_shown)
        return 1.0 - math.exp(-age / FRESHNESS_DECAY_TAU_SECONDS)

    def _order_pool_freshness_aware(self, pool: list[Question]) -> list[Question]:
        """Freshness-decay ordering + guarded hard-exclude of recent questions.

        Ordering: never-shown questions first (shuffled), then previously shown
        ordered freshest-first by :meth:`_freshness_weight` (i.e. oldest last-
        shown first — the decay-weighted refinement of the plain oldest-first
        sort).

        Hard-exclude: questions shown within
        :data:`RECENT_REPEAT_WINDOW_SECONDS` are dropped — but *only* while the
        surviving pool still holds ≥ :data:`MIN_FRESH_POOL_SIZE` questions.
        Otherwise (small / single-pack selections) we fall back to the full
        decay-ordered pool, so the pool is never emptied or shortened below what
        the selection can actually offer. This graceful degradation is the key
        correctness property: a small pack always fills a game, at the cost of
        allowing recent repeats when there is genuinely nothing fresher to show.
        """
        now = time.time()
        never_shown = [q for q in pool if q.id not in self._history]
        previously_shown = [q for q in pool if q.id in self._history]
        random.shuffle(never_shown)
        previously_shown.sort(
            key=lambda q: self._freshness_weight(q.id, now), reverse=True
        )

        recent_cutoff = now - RECENT_REPEAT_WINDOW_SECONDS
        non_recent = [
            q for q in previously_shown
            if self._history.get(q.id, 0.0) <= recent_cutoff
        ]
        # ``non_recent`` preserves ``previously_shown``'s freshest-first order.
        kept = never_shown + non_recent
        if len(kept) >= MIN_FRESH_POOL_SIZE:
            return kept
        # Not enough fresh questions to safely fill a game → soft-penalise:
        # keep everything, just decay-ordered (recent questions sink to the end).
        return never_shown + previously_shown

    def _build_queue(
        self,
        category: str | None = None,
        difficulty: str | None = None,
        language: str | None = None,
        categories: list[str] | None = None,
    ) -> None:
        """Build and shuffle the internal question queue."""
        self._queue = self.build_pool(category, difficulty, language, categories)
        self._queue_index = 0

    def remaining_queue_ids(self) -> set[str]:
        """Ids of questions still queued but not yet served this game.

        Lets a detour (e.g. the lightning round, #350) see what the main game
        is about to show without mutating the shared queue.
        """
        return {q.id for q in self._queue[self._queue_index:]}

    def shown_this_game_ids(self) -> set[str]:
        """Ids of questions already shown in the current game session."""
        return set(self._shown_this_game)

    def drop_from_queue(self, ids: set[str]) -> None:
        """Remove not-yet-served questions from the pending queue (#350).

        Lets the lightning round claim questions from the shared pool without
        the main game re-showing them in its remaining rounds. The already
        served prefix (and thus ``_queue_index``) is left untouched.
        """
        if not ids:
            return
        served = self._queue[: self._queue_index]
        remaining = [q for q in self._queue[self._queue_index :] if q.id not in ids]
        self._queue = served + remaining

"""Tests for the freshness engine (issue #436).

The :class:`QuestionBank` already tracks, per question, the unix timestamp it
was last shown (``self._history``) and orders "never-shown first, then
oldest-shown first". Issue #436 layers a freshness engine on top:

* an exponential freshness-decay ordering (refines the plain oldest-first sort),
* a *guarded* hard-exclude of questions shown within a recency window — dropped
  only while enough fresh questions remain to fill a game, otherwise the build
  degrades gracefully to the decay-ordered full pool (never empty / too short),
* an ``avoid_recent_repeats`` options toggle threaded onto the bank; when OFF,
  ``build_pool`` behaves *exactly* as before this change (back-compat).

Timestamps are fully injected (``bank._history`` set directly) and ``time.time``
is monkeypatched to a fixed ``NOW`` so the decay math is deterministic — no
reliance on wall-clock time in any assertion.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from custom_components.quizify.game import questions as questions_mod  # noqa: E402
from custom_components.quizify.game.questions import (  # noqa: E402
    MIN_FRESH_POOL_SIZE,
    RECENT_REPEAT_WINDOW_SECONDS,
    Answer,
    Question,
    QuestionBank,
)

# Fixed "now" for deterministic decay math.
NOW = 1_700_000_000.0
HOUR = 3600.0
DAY = 24 * HOUR


def _make_question(qid: str) -> Question:
    """Minimal valid multiple-choice question with a stable id."""
    return Question(
        id=qid,
        question=f"Q {qid}?",
        answers=[
            Answer(text="right", correct=True),
            Answer(text="wrong1", correct=False),
            Answer(text="wrong2", correct=False),
        ],
        difficulty="medium",
        language="de",
    )


def _bank_with(n: int, *, slug: str = "cat") -> QuestionBank:
    """Return a bank pre-loaded with ``n`` questions in one category."""
    bank = QuestionBank()
    qs = [_make_question(f"q{i:03d}") for i in range(n)]
    bank._categories = {slug: qs}
    return bank


@pytest.fixture(autouse=True)
def _frozen_time(monkeypatch: pytest.MonkeyPatch) -> None:
    """Freeze ``questions.time.time`` at NOW for every test in this module."""
    monkeypatch.setattr(questions_mod.time, "time", lambda: NOW)


# ---------------------------------------------------------------------------
# 1. Toggle ON + large pool → recent questions are hard-excluded
# ---------------------------------------------------------------------------
def test_recent_questions_hard_excluded_when_pool_large() -> None:
    # A comfortably-large pool so the pool-size guard passes even after we mark
    # a chunk as "shown minutes ago".
    total = MIN_FRESH_POOL_SIZE + 20
    bank = _bank_with(total)
    bank.set_avoid_recent_repeats(True)

    # Mark the first 10 questions as shown 5 minutes ago (well inside the
    # recency window). The remaining 60+ are never-shown → plenty fresh.
    recent_ids = {f"q{i:03d}" for i in range(10)}
    bank._history = dict.fromkeys(recent_ids, NOW - 5 * 60)

    pool = bank.build_pool(category="cat")
    pool_ids = {q.id for q in pool}

    # Guard passes (fresh remaining = total-10 >= MIN_FRESH_POOL_SIZE) → recents
    # are dropped entirely.
    assert recent_ids.isdisjoint(pool_ids)
    assert len(pool) == total - len(recent_ids)


# ---------------------------------------------------------------------------
# 2. Pool-size guard → graceful degradation for a small pool
# ---------------------------------------------------------------------------
def test_small_pool_degrades_to_soft_penalise() -> None:
    # A small single-pack selection: 12 questions, 10 of them shown minutes ago.
    # Hard-excluding them would leave only 2 → far below MIN_FRESH_POOL_SIZE, so
    # the build MUST fall back to the decay-ordered full pool (never empty /
    # never shorter than what the pack can offer).
    total = 12
    bank = _bank_with(total)
    bank.set_avoid_recent_repeats(True)

    recent_ids = [f"q{i:03d}" for i in range(10)]
    # Stagger their timestamps so the decay ordering is observable.
    bank._history = {
        qid: NOW - (5 * 60) - (idx * 60) for idx, qid in enumerate(recent_ids)
    }

    pool = bank.build_pool(category="cat")

    # Graceful degradation: nothing is dropped — the whole pack survives.
    assert len(pool) == total
    assert {q.id for q in pool} == {f"q{i:03d}" for i in range(total)}

    # And a recently-shown question is still present despite being inside the
    # recency window (soft-penalise, not hard-exclude).
    assert any(q.id in recent_ids for q in pool)

    # Never-shown lead; the recently shown sink to the tail, ordered
    # freshest-first (oldest last_shown first). q009 has the largest age
    # (NOW-840 → freshest) so it leads the tail; q000 was shown most recently
    # (NOW-300 → stalest) so it comes last.
    tail_ids = [q.id for q in pool if q.id in recent_ids]
    assert tail_ids[0] == "q009"
    assert tail_ids[-1] == "q000"


def test_all_recent_pool_never_empty() -> None:
    # Extreme case: a tiny pack where EVERY question was just shown. The build
    # must still return them all rather than an empty pool.
    bank = _bank_with(6)
    bank.set_avoid_recent_repeats(True)
    bank._history = {f"q{i:03d}": NOW - 60 for i in range(6)}

    pool = bank.build_pool(category="cat")

    assert len(pool) == 6


# ---------------------------------------------------------------------------
# 3. Decay ordering: long-ago before recent; never-shown lead
# ---------------------------------------------------------------------------
def test_decay_ordering_prefers_older_and_never_shown() -> None:
    bank = _bank_with(3)
    bank.set_avoid_recent_repeats(True)

    # q000 never shown; q001 shown long ago (30 days); q002 shown recently but
    # OUTSIDE the hard-exclude window so it stays in the pool (soft-penalised).
    long_ago = NOW - 30 * DAY
    recent_but_outside = NOW - (RECENT_REPEAT_WINDOW_SECONDS + DAY)
    bank._history = {"q001": long_ago, "q002": recent_but_outside}

    # Small pool (< MIN_FRESH_POOL_SIZE) → soft-penalise path keeps all three,
    # so we can observe the full ordering.
    pool_ids = [q.id for q in bank.build_pool(category="cat")]

    # Never-shown leads, then freshest-first: the 30-days-ago question outranks
    # the more-recently-shown one.
    assert pool_ids == ["q000", "q001", "q002"]

    # Freshness weight is monotonic: never-shown == 1.0, older > newer.
    assert bank._freshness_weight("q000", NOW) == 1.0
    assert (
        bank._freshness_weight("q001", NOW)
        > bank._freshness_weight("q002", NOW)
    )


def test_freshness_weight_bounds() -> None:
    bank = _bank_with(1)
    bank._history = {"q000": NOW}
    # Just shown → weight ~0 (very stale), clamped non-negative even if a clock
    # skew made last_shown slightly in the future.
    assert bank._freshness_weight("q000", NOW) == pytest.approx(0.0, abs=1e-9)
    bank._history = {"q000": NOW + 999}  # future timestamp (skew)
    assert bank._freshness_weight("q000", NOW) == pytest.approx(0.0, abs=1e-9)
    # Unknown id → never shown → maximally fresh.
    assert bank._freshness_weight("nope", NOW) == 1.0


# ---------------------------------------------------------------------------
# 4. Toggle OFF ⇒ identical to the pre-change never-shown/oldest-first behaviour
# ---------------------------------------------------------------------------
def _legacy_build_pool(bank: QuestionBank, pool: list[Question]) -> list[Question]:
    """Reproduce the exact pre-#436 ordering for a back-compat oracle."""
    import random

    if bank._history:
        never_shown = [q for q in pool if q.id not in bank._history]
        previously_shown = [q for q in pool if q.id in bank._history]
        random.shuffle(never_shown)
        previously_shown.sort(key=lambda q: bank._history.get(q.id, 0))
        return never_shown + previously_shown
    return bank.shuffle_questions(pool)


def test_toggle_off_matches_legacy_ordering() -> None:
    import random

    total = MIN_FRESH_POOL_SIZE + 20  # large enough that ON would hard-exclude
    bank = _bank_with(total)
    bank.set_avoid_recent_repeats(False)

    # Mix of never-shown, recent, and old — the case where ON would diverge.
    bank._history = {
        "q000": NOW - 60,          # recent
        "q001": NOW - 2 * HOUR,    # recent
        "q005": NOW - 40 * DAY,    # old
        "q006": NOW - 10 * DAY,    # old
    }

    pool = list(bank._categories["cat"])

    random.seed(1234)
    got = bank.build_pool(category="cat")
    random.seed(1234)
    want = _legacy_build_pool(bank, list(pool))

    assert [q.id for q in got] == [q.id for q in want]
    # Sanity: no question was dropped (OFF never hard-excludes).
    assert len(got) == total


def test_default_bank_matches_options_default() -> None:
    # A fresh bank defaults to the options-flow default (avoid on) so behaviour
    # matches the user-visible default when nothing threads the toggle in.
    from custom_components.quizify.const import DEFAULT_AVOID_RECENT_REPEATS

    bank = QuestionBank()
    assert bank._avoid_recent_repeats is DEFAULT_AVOID_RECENT_REPEATS


# ---------------------------------------------------------------------------
# 5. Options flow accepts + round-trips avoid_recent_repeats
# ---------------------------------------------------------------------------
pytest.importorskip("homeassistant")
pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant import data_entry_flow  # noqa: E402
from homeassistant.core import HomeAssistant  # noqa: E402
from pytest_homeassistant_custom_component.common import (  # noqa: E402
    MockConfigEntry,
)

from custom_components.quizify.const import (  # noqa: E402
    CONF_AVOID_RECENT_REPEATS,
    CONF_COMMUNITY_SUBMIT_SECRET,
    CONF_COMMUNITY_SUBMIT_URL,
    CONF_FINALE_SCENE,
    CONF_LOBBY_MUSIC_URL,
    CONF_MEDIA_PLAYER_ENTITY,
    CONF_PARTY_LIGHT_ENTITIES,
    CONF_TTS_ENTITY,
    DEFAULT_AVOID_RECENT_REPEATS,
    DOMAIN,
)

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


async def test_options_flow_lists_avoid_recent_repeats(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN)
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    schema_keys = {str(marker) for marker in result["data_schema"].schema}
    assert CONF_AVOID_RECENT_REPEATS in schema_keys


async def test_options_flow_default_is_on(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN)
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    for marker in result["data_schema"].schema:
        if str(marker) == CONF_AVOID_RECENT_REPEATS:
            assert marker.default() is DEFAULT_AVOID_RECENT_REPEATS
            break
    else:  # pragma: no cover
        pytest.fail("avoid_recent_repeats marker not found in options schema")


async def test_options_flow_round_trips_avoid_recent_repeats(
    hass: HomeAssistant,
) -> None:
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN)
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    user_input = {
        CONF_PARTY_LIGHT_ENTITIES: [],
        CONF_TTS_ENTITY: "tts.test",
        CONF_MEDIA_PLAYER_ENTITY: "media_player.test",
        CONF_FINALE_SCENE: "scene.test",
        CONF_LOBBY_MUSIC_URL: "",
        CONF_AVOID_RECENT_REPEATS: False,
        CONF_COMMUNITY_SUBMIT_URL: "",
        CONF_COMMUNITY_SUBMIT_SECRET: "",
    }
    result2 = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input=user_input
    )
    assert result2["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result2["data"][CONF_AVOID_RECENT_REPEATS] is False
    assert entry.options[CONF_AVOID_RECENT_REPEATS] is False

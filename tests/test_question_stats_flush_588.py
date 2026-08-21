"""Per-question stats survive an unfinished game (issue #588).

Before this, ``save_if_dirty()`` had exactly one caller — ``end_game()`` in
``game/state.py`` — so ``record_round()`` only ever mutated an in-memory dict.
Every game that did not reach the end (host closed the tab, HA restarted, the
integration was reloaded or updated mid-game) threw its rounds away without a
trace. On the live instance that showed up as a stats file whose newest entry
was nine days old while games were being played.

Two paths close the hole and both are pinned here:

* a **debounced write** armed by ``record_round()``, so an abandoned game still
  leaves its rounds on disk;
* an explicit **flush** on config-entry unload and on ``EVENT_HOMEASSISTANT_
  STOP``, so a restart or reload cannot drop what is still only in memory.

The service-level tests run anywhere. The lifecycle tests need the real
``homeassistant`` package plus the ``pytest-homeassistant-custom-component``
harness and skip (not error) without them.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from custom_components.quizify.question_stats import (  # noqa: E402
    QuestionStatsService,
)
from custom_components.quizify.runtime import StandaloneRuntime  # noqa: E402


def _service(tmp_path: Path, debounce: float = 0.02) -> QuestionStatsService:
    """A stats service on a tmp data dir with a test-length debounce."""
    svc = QuestionStatsService(StandaloneRuntime(tmp_path))
    # Instance attribute shadows the class constant — the production value
    # (5 s) would make these tests sleep for no benefit.
    svc.SAVE_DEBOUNCE_SECONDS = debounce
    return svc


def _stored(tmp_path: Path) -> dict:
    return json.loads((tmp_path / "question_stats.json").read_text())


async def test_abandoned_game_still_persists_its_rounds(tmp_path: Path) -> None:
    """The regression itself: rounds reach disk without any end_game()."""
    svc = _service(tmp_path)

    svc.record_round("geo_en_001", [(True, 4.0), (False, 9.0)])

    # Nothing is written synchronously — the point is that it lands *later*
    # without anyone ending the game.
    assert not (tmp_path / "question_stats.json").exists()

    await asyncio.sleep(0.1)

    q = _stored(tmp_path)["questions"]["geo_en_001"]
    assert q["shown_count"] == 2
    assert q["correct_count"] == 1


async def test_rounds_in_quick_succession_write_once(tmp_path: Path) -> None:
    """The debounce coalesces a normal round cadence into a single write.

    A save per round would rewrite the whole file (which grows with the pack
    catalogue) several times a minute during a game.
    """
    svc = _service(tmp_path, debounce=0.08)
    writes = 0
    original = svc.save_if_dirty

    async def counting_save() -> None:
        nonlocal writes
        writes += 1
        await original()

    svc.save_if_dirty = counting_save  # type: ignore[method-assign]

    for i in range(4):
        svc.record_round(f"geo_en_{i:03d}", [(True, 3.0)])
        await asyncio.sleep(0.01)
    await asyncio.sleep(0.2)

    assert writes == 1, f"expected the rounds to coalesce, saw {writes} writes"
    assert len(_stored(tmp_path)["questions"]) == 4


async def test_flush_writes_immediately_and_disarms_the_timer(
    tmp_path: Path,
) -> None:
    """async_flush() persists now, and the pending debounce does not fire
    a second, redundant write afterwards."""
    svc = _service(tmp_path, debounce=5.0)
    writes = 0
    original = svc.save_if_dirty

    async def counting_save() -> None:
        nonlocal writes
        writes += 1
        await original()

    svc.save_if_dirty = counting_save  # type: ignore[method-assign]

    svc.record_round("nat_en_007", [(True, 2.5)])
    await svc.async_flush()

    assert _stored(tmp_path)["questions"]["nat_en_007"]["shown_count"] == 1
    assert writes == 1

    # Give the (cancelled) debounce more than its due; it must stay quiet.
    await asyncio.sleep(0.05)
    assert writes == 1


async def test_flush_without_pending_changes_is_a_noop(tmp_path: Path) -> None:
    """Called on every unload, so it has to be cheap and safe when idle."""
    svc = _service(tmp_path)
    await svc.async_flush()
    assert not (tmp_path / "question_stats.json").exists()


def test_record_round_outside_an_event_loop_does_not_raise(
    tmp_path: Path,
) -> None:
    """``record_round`` is a plain synchronous call and is used that way in
    tests and in the standalone server's sync paths. Arming a task there would
    raise ``RuntimeError: no running event loop``; the scheduler skips instead
    and the data still persists through an explicit save."""
    svc = _service(tmp_path)

    svc.record_round("pop_010", [(True, 1.0)])

    assert svc._save_timer is None
    asyncio.run(svc.save_if_dirty())
    assert _stored(tmp_path)["questions"]["pop_010"]["shown_count"] == 1


async def test_cancelled_analytics_save_is_not_reported_as_an_error(
    tmp_path: Path,
) -> None:
    """Fallout from the flush, fixed alongside it.

    ``QuizifyAnalytics.schedule_save`` attaches a done-callback that reads
    ``task.exception()``. On a cancelled task that call re-raises the
    ``CancelledError`` inside the callback, where nobody handles it. The flush
    added here awaits during ``EVENT_HOMEASSISTANT_STOP``, which is exactly the
    window in which HA cancels a save in flight — so a latent crash became a
    reproducible one, and the callback now checks for cancellation first.
    """
    from custom_components.quizify.analytics import QuizifyAnalytics

    analytics = QuizifyAnalytics(StandaloneRuntime(tmp_path))

    async def _never() -> None:
        await asyncio.sleep(3600)

    task = asyncio.ensure_future(_never())
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # Must not raise — this runs as a done-callback with no waiter.
    analytics._handle_save_error(task)


# --------------------------------------------------------------------------
# Entry lifecycle — needs the real HA harness.
# --------------------------------------------------------------------------

pytest.importorskip("homeassistant")
pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant.const import EVENT_HOMEASSISTANT_STOP  # noqa: E402
from homeassistant.core import HomeAssistant  # noqa: E402
from homeassistant.setup import async_setup_component  # noqa: E402
from pytest_homeassistant_custom_component.common import (  # noqa: E402
    MockConfigEntry,
)

from custom_components.quizify.const import DOMAIN  # noqa: E402

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


@pytest.fixture(autouse=True)
def _stub_frontend_panel():
    """Same stubs as ``test_init_313`` — the prebuilt ``hass_frontend`` wheel
    isn't installed under the harness, so the two panel helpers the
    integration imports inside setup/unload are replaced."""
    from unittest.mock import patch

    with (
        patch(
            "homeassistant.components.frontend.async_register_built_in_panel"
        ),
        patch("homeassistant.components.frontend.async_remove_panel"),
    ):
        yield


@pytest.fixture
async def http_hass(hass: HomeAssistant) -> HomeAssistant:
    """A hass with the HTTP component set up (setup mounts routes on it)."""
    assert await async_setup_component(hass, "http", {"http": {}})
    await hass.async_block_till_done()
    return hass


async def _setup(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id) is True
    await hass.async_block_till_done()
    return entry


def _stats_file(hass: HomeAssistant) -> Path:
    return Path(hass.config.path("quizify")) / "question_stats.json"


def _stored_questions(hass: HomeAssistant) -> dict:
    """Questions currently on disk, ``{}`` when the file isn't there yet."""
    path = _stats_file(hass)
    if not path.exists():
        return {}
    return json.loads(path.read_text()).get("questions", {})


@pytest.fixture
def clean_stats_file(hass: HomeAssistant):
    """Remove the stats file around the test.

    The harness config dir lives inside site-packages and survives between
    runs, so a file left by an earlier test would be loaded at setup and
    written back out — which would make an "is it on disk yet" assertion
    meaningless.
    """
    path = _stats_file(hass)
    path.unlink(missing_ok=True)
    yield
    path.unlink(missing_ok=True)


async def test_unload_flushes_recorded_rounds(
    http_hass: HomeAssistant, clean_stats_file: None
) -> None:
    """A reload or an integration update goes through unload — the rounds
    collected so far must be on disk when it returns."""
    hass = http_hass
    entry = await _setup(hass)

    stats = hass.data[DOMAIN]["ctx"].question_stats
    stats.SAVE_DEBOUNCE_SECONDS = 3600.0  # only the flush can save here
    stats.record_round("hist_en_042", [(True, 5.0), (True, 6.5)])
    assert "hist_en_042" not in _stored_questions(hass)

    assert await hass.config_entries.async_unload(entry.entry_id) is True
    await hass.async_block_till_done()

    q = _stored_questions(hass)["hist_en_042"]
    assert q["shown_count"] == 2
    assert q["correct_count"] == 2


async def test_ha_stop_flushes_recorded_rounds(
    http_hass: HomeAssistant, clean_stats_file: None
) -> None:
    """Shutting HA down mid-game keeps the rounds."""
    hass = http_hass
    await _setup(hass)

    stats = hass.data[DOMAIN]["ctx"].question_stats
    stats.SAVE_DEBOUNCE_SECONDS = 3600.0
    stats.record_round("sci_en_013", [(False, 12.0)])
    assert "sci_en_013" not in _stored_questions(hass)

    hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
    await hass.async_block_till_done()

    q = _stored_questions(hass)["sci_en_013"]
    assert q["shown_count"] == 1
    assert q["correct_count"] == 0

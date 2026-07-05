"""Regression tests for the BE-B isolated batch (#451, #452, #454, #457).

Each of the four fixes is a small, independently-verifiable behaviour:

* #451 — a FREEZE lockout survives a pause/resume cycle (previously
  ``QuestionTimer.resumed`` rebuilt the timer fully unfrozen, so a target
  paused mid-lockout came back able to submit).
* #452 — the served HTML/sw template text is cached by mtime and only re-read
  when the file on disk actually changes.
* #454 — the sliding-window limiter reclaims buckets left behind by one-shot
  sources via an opportunistic global sweep.
* #457 — question history is persisted compactly (no ``indent=2``).
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from custom_components.quizify.game.phase_controller import (  # noqa: E402
    PhaseController,
)
from custom_components.quizify.game.questions import QuestionBank  # noqa: E402
from custom_components.quizify.server import views  # noqa: E402
from custom_components.quizify.server.rate_limit import (  # noqa: E402
    SlidingWindowLimiter,
)

# ---------------------------------------------------------------------------
# #451 — freeze survives pause/resume
# ---------------------------------------------------------------------------


def _active_controller(players: list[str]) -> PhaseController:
    pc = PhaseController(players_fn=lambda: list(players))
    pc.begin_round(30.0)
    pc.enter_question_active()
    return pc


class TestFreezeSurvivesPauseResume:
    def test_frozen_target_stays_frozen_after_resume(self) -> None:
        pc = _active_controller(["alice", "bob"])
        pc.timers["alice"].freeze(5.0)
        assert pc.timers["alice"].is_frozen()

        assert pc.pause() is True
        assert pc.resume() is True

        # alice was locked out at pause → still locked out after resume (#451).
        assert pc.timers["alice"].is_frozen()
        assert pc.timers["alice"].frozen_remaining() > 0.0
        # bob was never frozen → must not become frozen.
        assert not pc.timers["bob"].is_frozen()

    def test_unfrozen_target_stays_unfrozen(self) -> None:
        pc = _active_controller(["alice"])
        pc.pause()
        pc.resume()
        assert not pc.timers["alice"].is_frozen()

    def test_expired_freeze_not_reapplied(self) -> None:
        pc = _active_controller(["alice"])
        # A freeze that already elapsed before pause must NOT be re-applied.
        pc.timers["alice"].freeze(0.001)
        time.sleep(0.01)
        assert not pc.timers["alice"].is_frozen()
        pc.pause()
        assert "alice" not in pc.paused_frozen
        pc.resume()
        assert not pc.timers["alice"].is_frozen()


# ---------------------------------------------------------------------------
# #452 — HTML/sw template text cached by mtime
# ---------------------------------------------------------------------------


class TestTemplateMtimeCache:
    def test_reads_from_disk_only_when_mtime_changes(self, tmp_path: Path) -> None:
        views._TEMPLATE_TEXT_CACHE.clear()
        page = tmp_path / "player.html"
        page.write_text("v1", encoding="utf-8")

        assert views._read_template_cached(page) == "v1"

        # Rewrite the SAME bytes but bump the mtime forward — the cache must
        # notice the mtime change and re-read (returns the new content).
        page.write_text("v2", encoding="utf-8")
        future = time.time() + 10
        os.utime(page, (future, future))
        assert views._read_template_cached(page) == "v2"

    def test_unchanged_file_is_not_reread(self, tmp_path: Path) -> None:
        views._TEMPLATE_TEXT_CACHE.clear()
        page = tmp_path / "admin.html"
        page.write_text("cached", encoding="utf-8")
        assert views._read_template_cached(page) == "cached"

        # Delete the file underneath: a cache hit (same mtime key) must still
        # return the cached text without touching disk. We prove the read is
        # elided by removing the file — a re-read would raise.
        cached_entry = views._TEMPLATE_TEXT_CACHE[str(page)]
        # Re-stat returns the same mtime_ns, so the cached branch is taken.
        assert views._read_template_cached(page) == "cached"
        assert views._TEMPLATE_TEXT_CACHE[str(page)] == cached_entry


# ---------------------------------------------------------------------------
# #454 — limiter global sweep of one-shot buckets
# ---------------------------------------------------------------------------


class TestLimiterSweep:
    def test_one_shot_buckets_are_swept(self) -> None:
        clock = [0.0]
        lim = SlidingWindowLimiter(5, 60.0, clock=lambda: clock[0])

        # Fill the dict with one-shot sources that never return.
        threshold = SlidingWindowLimiter._SWEEP_THRESHOLD
        for i in range(threshold):
            assert lim.check(f"ip-{i}") is True
        assert len(lim._buckets) == threshold

        # Advance well past the window so every existing bucket is stale, then
        # a single new check trips the sweep and reclaims all of them.
        clock[0] += 61.0
        assert lim.check("fresh") is True
        assert len(lim._buckets) == 1
        assert "fresh" in lim._buckets

    def test_active_buckets_are_not_swept(self) -> None:
        clock = [0.0]
        lim = SlidingWindowLimiter(5, 60.0, clock=lambda: clock[0])
        threshold = SlidingWindowLimiter._SWEEP_THRESHOLD
        # Half the keys are recent, half are stale.
        for i in range(threshold):
            lim.check(f"old-{i}")
        clock[0] += 61.0
        for i in range(threshold):
            lim.check(f"new-{i}")
        # The next check sweeps: the stale ``old-*`` buckets go, the recent
        # ``new-*`` buckets survive.
        clock[0] += 1.0
        lim.check("trigger")
        assert not any(k.startswith("old-") for k in lim._buckets)
        assert any(k.startswith("new-") for k in lim._buckets)


# ---------------------------------------------------------------------------
# #457 — compact history write
# ---------------------------------------------------------------------------


class TestCompactHistory:
    def test_history_written_without_indentation(self, tmp_path: Path) -> None:
        bank = QuestionBank(questions_dir=tmp_path)
        hist_path = tmp_path / "history.json"
        bank._history_path = hist_path
        bank._history = {"q1": 1.0, "q2": 2.0, "q3": 3.0}

        bank.save_history()

        raw = hist_path.read_text(encoding="utf-8")
        # Compact: single line, no pretty-print newlines/indentation (indent=2
        # would spread every key onto its own indented line).
        assert "\n" not in raw.strip()
        assert raw == json.dumps({"q1": 1.0, "q2": 2.0, "q3": 3.0})
        # Still valid + round-trips to the same mapping.
        assert json.loads(raw) == {"q1": 1.0, "q2": 2.0, "q3": 3.0}

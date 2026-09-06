"""Every store now degrades a broken file the same way (#790).

The point of `storage.py` is not that one more module exists — it is that the
corrupt-file policy stops being a per-store opinion. Before the port each of
these cases was handled by *some* of the seven copies and not the others:

  * question stats caught ``JSONDecodeError`` but not the ``AttributeError``
    a JSON array produces on ``raw.get(...)``,
  * analytics caught ``JSONDecodeError`` but let an ``OSError`` from the read
    escape ``load()`` and take setup down with it — the #700 shape,
  * the question history caught ``OSError`` on the read but wrote straight
    over the live file, so a crash mid-write produced the corrupt file the
    *next* start would choke on,
  * the token store returned whatever JSON it found, array or string included.

Each test below drives the store it names, not the storage module, so it
fails if that store is ever reverted to its own implementation.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from custom_components.quizify.analytics import QuizifyAnalytics  # noqa: E402
from custom_components.quizify.game.questions import QuestionBank  # noqa: E402
from custom_components.quizify.question_stats import (  # noqa: E402
    QuestionStatsService,
)
from custom_components.quizify.server.flag_store import FlagStore  # noqa: E402
from custom_components.quizify.server.pack_news import PackNewsStore  # noqa: E402
from custom_components.quizify.server.preset_store import PresetStore  # noqa: E402
from custom_components.quizify.server.token_store import TokenStore  # noqa: E402


class _Runtime:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir

    def create_task(self, coro):  # noqa: ANN001
        return asyncio.ensure_future(coro)

    async def run_in_executor(self, func, *args):  # noqa: ANN001, ANN002
        return func(*args)


#: Files that are syntactically fine but the wrong *shape*, plus one that is
#: not JSON at all. A store must survive all of them.
BROKEN = ("[]", '"a string"', "null", "{", "")


class TestQuestionStats:
    @pytest.mark.parametrize("content", BROKEN)
    @pytest.mark.asyncio
    async def test_a_broken_file_leaves_an_empty_record(
        self, tmp_path: Path, content: str
    ) -> None:
        (tmp_path / "question_stats.json").write_text(content, encoding="utf-8")
        svc = QuestionStatsService(_Runtime(tmp_path))

        await svc.load()

        assert svc.get_hardest() == []
        # Still usable afterwards — this is the #588 path.
        svc.record_round("q1", [(True, 2.0)])
        await svc.save_if_dirty()
        on_disk = json.loads((tmp_path / "question_stats.json").read_text("utf-8"))
        assert on_disk["questions"]["q1"]["shown_count"] == 1

    @pytest.mark.asyncio
    async def test_an_unreadable_file_does_not_raise(self, tmp_path: Path) -> None:
        (tmp_path / "question_stats.json").mkdir()

        await QuestionStatsService(_Runtime(tmp_path)).load()


class TestAnalytics:
    @pytest.mark.parametrize("content", BROKEN)
    @pytest.mark.asyncio
    async def test_a_broken_file_leaves_an_empty_record(
        self, tmp_path: Path, content: str
    ) -> None:
        (tmp_path / "analytics.json").write_text(content, encoding="utf-8")
        analytics = QuizifyAnalytics(_Runtime(tmp_path))

        await analytics.load()

        assert analytics.get_games() == []

    @pytest.mark.asyncio
    async def test_a_broken_file_is_rewritten_so_the_next_start_is_clean(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "analytics.json"
        path.write_text("{ truncated", encoding="utf-8")

        await QuizifyAnalytics(_Runtime(tmp_path)).load()

        assert json.loads(path.read_text("utf-8"))["games"] == []

    @pytest.mark.asyncio
    async def test_an_unreadable_file_does_not_raise(self, tmp_path: Path) -> None:
        """The #700 shape: ``load()`` used to let the OSError escape."""
        (tmp_path / "analytics.json").mkdir()

        await QuizifyAnalytics(_Runtime(tmp_path)).load()


class TestQuestionHistory:
    @pytest.mark.parametrize("content", BROKEN)
    def test_a_broken_history_starts_empty(
        self, tmp_path: Path, content: str
    ) -> None:
        path = tmp_path / "question_history.json"
        path.write_text(content, encoding="utf-8")
        bank = QuestionBank()

        bank.load_history(path)

        bank.record_shown("q1")
        bank.save_history()
        assert list(json.loads(path.read_text("utf-8"))) == ["q1"]

    def test_the_history_write_is_atomic(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """It was the one store writing straight over the live file."""
        path = tmp_path / "question_history.json"
        bank = QuestionBank()
        bank.load_history(path)
        bank.record_shown("q1")
        bank.save_history()

        def _boom(src, dst):  # noqa: ANN001
            raise OSError("no space")

        monkeypatch.setattr(os, "replace", _boom)
        bank.record_shown("q2")
        bank.save_history()

        # The failed write left the previous, complete history in place.
        assert list(json.loads(path.read_text("utf-8"))) == ["q1"]


class TestTokenStore:
    @pytest.mark.parametrize("content", BROKEN)
    @pytest.mark.asyncio
    async def test_a_broken_token_file_reads_as_no_token(
        self, tmp_path: Path, content: str
    ) -> None:
        """A non-object payload must re-bootstrap, not be handed to the caller
        as if it were a token record."""
        (tmp_path / "admin_token.json").write_text(content, encoding="utf-8")

        assert await TokenStore(_Runtime(tmp_path)).load() is None


class TestPresetStore:
    @pytest.mark.parametrize("content", BROKEN + ('{"presets": "nope"}',))
    @pytest.mark.asyncio
    async def test_a_broken_file_degrades_to_no_presets(
        self, tmp_path: Path, content: str
    ) -> None:
        (tmp_path / "presets.json").write_text(content, encoding="utf-8")
        store = PresetStore(_Runtime(tmp_path))

        assert await store.list() == []
        # And saving over it repairs the file.
        await store.save({"name": "Family night"})
        assert [p["name"] for p in await store.list()] == ["Family night"]


class TestPackNews:
    @pytest.mark.parametrize("content", BROKEN)
    @pytest.mark.asyncio
    async def test_a_broken_file_is_treated_as_a_first_run(
        self, tmp_path: Path, content: str
    ) -> None:
        (tmp_path / "pack_news.json").write_text(content, encoding="utf-8")
        store = PackNewsStore(_Runtime(tmp_path))

        assert await store.sync({"geography", "history"}) == []
        assert await store.sync({"geography", "history", "world-cup"}) == [
            "world-cup"
        ]


class TestFlagStore:
    """The flag log had no name at all until #790 — it was two inline blocks
    in ``views.py``, so neither half could be tested without an HTTP request."""

    @pytest.mark.asyncio
    async def test_add_then_list(self, tmp_path: Path) -> None:
        store = FlagStore(_Runtime(tmp_path))

        await store.add("geo_037", reason="ambiguous", player_name="Ann")

        assert await store.list() == [
            {
                "ts": pytest.approx(await _now(), abs=5),
                "question_id": "geo_037",
                "reason": "ambiguous",
                "player_name": "Ann",
            }
        ]

    @pytest.mark.asyncio
    async def test_the_client_ip_is_stored_but_never_returned(
        self, tmp_path: Path
    ) -> None:
        store = FlagStore(_Runtime(tmp_path))

        await store.add("geo_037", remote="203.0.113.42")

        assert "203.0.113.42" in (tmp_path / "flagged.jsonl").read_text("utf-8")
        assert "remote" not in (await store.list())[0]

    @pytest.mark.asyncio
    async def test_the_fields_are_bounded_by_the_store(
        self, tmp_path: Path
    ) -> None:
        """#357 lived in the view; a second caller would have missed it."""
        store = FlagStore(_Runtime(tmp_path))

        entry = await store.add("x" * 500, reason="y" * 500, player_name="z" * 500)

        assert len(entry["question_id"]) == 64
        assert len(entry["reason"]) == 200
        assert len(entry["player_name"]) == 50

    @pytest.mark.asyncio
    async def test_a_torn_line_costs_one_entry(self, tmp_path: Path) -> None:
        path = tmp_path / "flagged.jsonl"
        path.write_text(
            '{"question_id": "q1"}\n{"question_id": "q2"\n"a string"\n',
            encoding="utf-8",
        )

        assert await FlagStore(_Runtime(tmp_path)).list() == [{"question_id": "q1"}]


async def _now() -> int:
    import time

    return int(time.time())

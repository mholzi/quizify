"""Tests for the shared JSON persistence layer (#790).

Seven stores used to hand-write the same read-in-executor / atomic-write
routine, each with its own idea of what a corrupt file means. Two shipped bugs
came out of that spread — #700 (a malformed file took setup down) and #588
(a save policy that existed in one store and not the others).

These tests pin the two properties the layer exists to guarantee *for every
store at once*, which is why they are written against `storage.py` and then
re-asserted through each ported store:

  * **A broken file degrades to the caller's default, with a warning.** A
    truncated JSON file, a file of the wrong shape, an unreadable one — none
    of them may raise at the caller.
  * **A write is all-or-nothing.** Content goes to a sibling ``.tmp`` and is
    moved into place with ``os.replace``; a failure part-way leaves the
    previous version of the file intact and no stray ``.tmp`` behind.

Both fail without `custom_components/quizify/storage.py`: the module simply
does not import.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from custom_components.quizify.storage import (  # noqa: E402
    JsonFile,
    JsonlFile,
    append_jsonl,
    read_json,
    read_jsonl,
    write_json,
)


class _Runtime:
    """Minimal Runtime double — runs the blocking work inline."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir

    def create_task(self, coro):  # noqa: ANN001
        return asyncio.ensure_future(coro)

    async def run_in_executor(self, func, *args):  # noqa: ANN001, ANN002
        return func(*args)


# ---------------------------------------------------------------------------
# Corrupt-file policy (#700)
# ---------------------------------------------------------------------------


class TestCorruptFileDegradesToDefault:
    def test_a_truncated_file_yields_the_default(self, tmp_path: Path) -> None:
        """The #700 shape: the process died mid-write, the file is half JSON."""
        path = tmp_path / "half.json"
        path.write_text('{"packs": ["geo", "his', encoding="utf-8")

        assert read_json(path, {"packs": []}) == {"packs": []}

    def test_it_warns_and_names_the_file(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Silent degradation would hide a real disk problem for months."""
        path = tmp_path / "presets.json"
        path.write_text("not json at all", encoding="utf-8")

        with caplog.at_level(logging.WARNING):
            read_json(path, [], label="Preset store")

        assert "Preset store" in caplog.text

    def test_a_missing_file_is_not_corrupt(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A first run is the normal case and must not log a warning."""
        with caplog.at_level(logging.WARNING):
            assert read_json(tmp_path / "absent.json", {"a": 1}) == {"a": 1}
        assert caplog.text == ""

    def test_an_unreadable_file_also_degrades(self, tmp_path: Path) -> None:
        """A directory where a file was expected: OSError, not JSONDecodeError.

        Three of the seven original copies caught only ``JSONDecodeError`` on
        the read path, so this case propagated out of ``load()`` and up into
        setup.
        """
        path = tmp_path / "state.json"
        path.mkdir()

        assert read_json(path, {"ok": True}) == {"ok": True}

    def test_raise_is_available_for_callers_that_want_it(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "broken.json"
        path.write_text("{", encoding="utf-8")

        with pytest.raises(json.JSONDecodeError):
            read_json(path, {}, on_corrupt="raise")

    @pytest.mark.asyncio
    async def test_json_file_load_applies_the_same_policy(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "async.json"
        path.write_text('{"a":', encoding="utf-8")

        assert await JsonFile(_Runtime(tmp_path), path).load({"a": 0}) == {"a": 0}


# ---------------------------------------------------------------------------
# Atomic writes
# ---------------------------------------------------------------------------


class TestWritesAreAtomic:
    def test_the_content_goes_through_a_tmp_sibling(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A reader must never observe a partially written target file.

        Asserted by capturing what ``os.replace`` was handed: if the payload
        were written straight to the target there would be no replace at all.
        """
        path = tmp_path / "analytics.json"
        seen: list[tuple[str, str]] = []
        real_replace = os.replace

        def _spy(src, dst):  # noqa: ANN001
            seen.append((Path(src).name, Path(dst).name))
            # The target must not exist yet — the payload lives in the tmp.
            assert Path(src).read_text(encoding="utf-8") == '{"games": [1, 2]}'
            return real_replace(src, dst)

        monkeypatch.setattr(os, "replace", _spy)
        assert write_json(path, {"games": [1, 2]}) is True

        assert seen == [("analytics.tmp", "analytics.json")]
        assert json.loads(path.read_text(encoding="utf-8")) == {"games": [1, 2]}

    def test_a_failed_write_leaves_the_previous_version_intact(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Disk full half-way through: yesterday's file must still be readable."""
        path = tmp_path / "question_stats.json"
        path.write_text('{"version": 1, "questions": {"q1": 3}}', encoding="utf-8")

        def _boom(src, dst):  # noqa: ANN001
            raise OSError(28, "No space left on device")

        monkeypatch.setattr(os, "replace", _boom)
        assert write_json(path, {"version": 1, "questions": {}}) is False

        assert json.loads(path.read_text(encoding="utf-8")) == {
            "version": 1,
            "questions": {"q1": 3},
        }

    def test_a_failed_write_leaves_no_stray_tmp(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / "presets.json"

        def _boom(src, dst):  # noqa: ANN001
            raise OSError("nope")

        monkeypatch.setattr(os, "replace", _boom)
        write_json(path, {"presets": []})

        assert list(tmp_path.iterdir()) == []

    def test_the_parent_directory_is_created(self, tmp_path: Path) -> None:
        path = tmp_path / "fresh" / "install" / "pack_news.json"

        assert write_json(path, {"known": []}) is True
        assert json.loads(path.read_text(encoding="utf-8")) == {"known": []}

    def test_an_unserialisable_payload_does_not_touch_the_file(
        self, tmp_path: Path
    ) -> None:
        """The old copies serialised inside the write closure, so a bad payload
        aborted after the target had already been opened."""
        path = tmp_path / "state.json"
        path.write_text('{"good": true}', encoding="utf-8")

        assert write_json(path, {"when": object()}) is False
        assert json.loads(path.read_text(encoding="utf-8")) == {"good": True}

    @pytest.mark.asyncio
    async def test_json_file_round_trips(self, tmp_path: Path) -> None:
        store = JsonFile(_Runtime(tmp_path), tmp_path / "sub" / "round.json")

        assert await store.save({"n": 1}) is True
        assert await store.load(None) == {"n": 1}
        assert await store.exists() is True
        assert await store.remove() is True
        assert await store.load({"n": 0}) == {"n": 0}
        # Removing an absent file is not an error.
        assert await store.remove() is True


# ---------------------------------------------------------------------------
# JSONL
# ---------------------------------------------------------------------------


class TestJsonlLog:
    def test_append_then_read_back(self, tmp_path: Path) -> None:
        path = tmp_path / "flagged.jsonl"

        append_jsonl(path, {"question_id": "q1"})
        append_jsonl(path, {"question_id": "q2"})

        assert read_jsonl(path) == [{"question_id": "q1"}, {"question_id": "q2"}]

    def test_a_torn_line_costs_one_entry_not_the_file(self, tmp_path: Path) -> None:
        """A process killed mid-append leaves half a line behind."""
        path = tmp_path / "flagged.jsonl"
        path.write_text(
            '{"question_id": "q1"}\n{"question_id": "q2"\n{"question_id": "q3"}\n',
            encoding="utf-8",
        )

        assert read_jsonl(path) == [{"question_id": "q1"}, {"question_id": "q3"}]

    def test_the_log_is_capped_by_dropping_the_oldest_half(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "flagged.jsonl"
        for i in range(40):
            append_jsonl(path, {"i": i})
        size = path.stat().st_size

        append_jsonl(path, {"i": 40}, max_bytes=size)

        entries = read_jsonl(path)
        assert entries[0] == {"i": 20}
        assert entries[-1] == {"i": 40}

    def test_the_trim_is_atomic_too(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A failed trim must not truncate the log — the append still happens."""
        path = tmp_path / "flagged.jsonl"
        for i in range(10):
            append_jsonl(path, {"i": i})

        def _boom(src, dst):  # noqa: ANN001
            raise OSError("nope")

        monkeypatch.setattr(os, "replace", _boom)
        append_jsonl(path, {"i": 10}, max_bytes=1)

        entries = read_jsonl(path)
        assert entries[0] == {"i": 0}
        assert entries[-1] == {"i": 10}

    def test_a_missing_log_reads_as_empty(self, tmp_path: Path) -> None:
        assert read_jsonl(tmp_path / "never-written.jsonl") == []

    @pytest.mark.asyncio
    async def test_jsonl_file_offloads(self, tmp_path: Path) -> None:
        log = JsonlFile(_Runtime(tmp_path), tmp_path / "deep" / "flagged.jsonl")

        assert await log.append({"a": 1}) is True
        assert await log.read_all() == [{"a": 1}]

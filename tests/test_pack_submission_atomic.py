"""Pack submission records are written atomically (#1067).

An interrupted write used to truncate pack_submissions.json, and the next
reconcile then wrote the empty default back, so every submission was lost. The
store now saves through the same sibling-.tmp-and-replace helper as the rest of
the integration.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from custom_components.quizify.server import pack_submission  # noqa: E402
from custom_components.quizify.server.pack_submission import (  # noqa: E402
    PackSubmissionStore,
)


class _FakeRuntime:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir


class _FakeCtx:
    def __init__(self, data_dir: Path) -> None:
        self.runtime = _FakeRuntime(data_dir)
        self.community_submit_url = None


def _records(name: str) -> dict[str, Any]:
    return {
        "submissions": [{"id": name, "name": name, "status": "pending"}],
        "last_poll": None,
    }


def test_save_round_trips(tmp_path: Path) -> None:
    store = PackSubmissionStore(_FakeCtx(tmp_path))
    assert store._save(_records("first")) is True
    assert json.loads((tmp_path / pack_submission.SUBMIT_RECORDS_FILE).read_text()) == _records("first")


def test_an_interrupted_save_keeps_the_previous_records(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = PackSubmissionStore(_FakeCtx(tmp_path))
    path = tmp_path / pack_submission.SUBMIT_RECORDS_FILE
    assert store._save(_records("kept")) is True

    def interrupted_replace(src, dst):
        raise OSError("power lost before the rename")

    monkeypatch.setattr("custom_components.quizify.storage.os.replace", interrupted_replace)
    assert store._save(_records("lost")) is False

    assert json.loads(path.read_text()) == _records("kept")
    assert not path.with_suffix(".tmp").exists()

"""Tests for the community-pack submission feature (#180).

Covers:
  1. The server-side validator (mirror of the #179 schema) — accept a good
     pack, reject each malformed shape with a specific error.
  2. The GitHub issue-state → submission-status mapping.
  3. The throttled reconcile: it stamps last_poll, only updates pending items,
     and persists — driven without an implicit event loop (asyncio.run).
  4. The config endpoint reporting enabled true/false off the AppContext.

CI runs Python 3.13; none of this uses asyncio.get_event_loop(). Async paths
run via asyncio.run() / pytest-asyncio, and the fake runtime's executor uses
the running loop.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from custom_components.quizify.const import (  # noqa: E402
    SUBMIT_STATUS_ACCEPTED,
    SUBMIT_STATUS_DECLINED,
    SUBMIT_STATUS_PENDING,
)
from custom_components.quizify.server.pack_submission import (  # noqa: E402
    PackSubmissionStore,
    _issue_to_status,
    validate_pack,
)


def _good_question(qid: str = "q1") -> dict:
    return {
        "id": qid,
        "question": "Which planet is the Red Planet?",
        "answers": [
            {"text": "Mars", "correct": True},
            {"text": "Venus", "correct": False},
            {"text": "Jupiter", "correct": False},
        ],
        "difficulty": "easy",
    }


def _good_pack(n: int = 2) -> dict:
    return {
        "name": "Test Pack",
        "language": "en",
        "version": "1.0",
        "questions": [_good_question(f"q{i}") for i in range(n)],
    }


# ---------------------------------------------------------------------------
# 1. Validator
# ---------------------------------------------------------------------------


class TestValidatePack:
    def test_good_pack_passes(self) -> None:
        ok, errors = validate_pack(_good_pack())
        assert ok is True
        assert errors == []

    def test_non_object_rejected(self) -> None:
        ok, errors = validate_pack(["not", "a", "dict"])
        assert ok is False
        assert any("object" in e.lower() for e in errors)

    def test_missing_name_rejected(self) -> None:
        pack = _good_pack()
        del pack["name"]
        ok, errors = validate_pack(pack)
        assert ok is False
        assert any("name" in e.lower() for e in errors)

    def test_empty_questions_rejected(self) -> None:
        pack = _good_pack()
        pack["questions"] = []
        ok, errors = validate_pack(pack)
        assert ok is False
        assert any("questions" in e.lower() for e in errors)

    def test_wrong_answer_count_rejected(self) -> None:
        pack = _good_pack()
        pack["questions"][0]["answers"] = pack["questions"][0]["answers"][:2]
        ok, errors = validate_pack(pack)
        assert ok is False
        assert any("answers" in e.lower() for e in errors)

    def test_no_correct_answer_rejected(self) -> None:
        pack = _good_pack()
        for a in pack["questions"][0]["answers"]:
            a["correct"] = False
        ok, errors = validate_pack(pack)
        assert ok is False
        assert any("correct" in e.lower() for e in errors)

    def test_two_correct_answers_rejected(self) -> None:
        pack = _good_pack()
        pack["questions"][0]["answers"][1]["correct"] = True
        ok, errors = validate_pack(pack)
        assert ok is False
        assert any("correct" in e.lower() for e in errors)

    def test_duplicate_ids_rejected(self) -> None:
        pack = _good_pack()
        pack["questions"][1]["id"] = pack["questions"][0]["id"]
        ok, errors = validate_pack(pack)
        assert ok is False
        assert any("dup" in e.lower() for e in errors)

    def test_missing_question_text_rejected(self) -> None:
        pack = _good_pack()
        pack["questions"][0]["question"] = "  "
        ok, errors = validate_pack(pack)
        assert ok is False
        assert any("question" in e.lower() for e in errors)


# ---------------------------------------------------------------------------
# 2. Issue-state mapping
# ---------------------------------------------------------------------------


class TestIssueToStatus:
    def test_open_is_pending(self) -> None:
        assert _issue_to_status("open", "") == SUBMIT_STATUS_PENDING

    def test_closed_completed_is_accepted(self) -> None:
        assert _issue_to_status("closed", "completed") == SUBMIT_STATUS_ACCEPTED

    def test_closed_no_reason_is_accepted(self) -> None:
        assert _issue_to_status("closed", "") == SUBMIT_STATUS_ACCEPTED

    def test_closed_not_planned_is_declined(self) -> None:
        assert _issue_to_status("closed", "not_planned") == SUBMIT_STATUS_DECLINED


class TestIssueNumberFromUrl:
    def test_html_url(self) -> None:
        assert (
            PackSubmissionStore.issue_number_from_url(
                "https://github.com/mholzi/quizify/issues/207"
            )
            == 207
        )

    def test_api_url(self) -> None:
        assert (
            PackSubmissionStore.issue_number_from_url(
                "https://api.github.com/repos/mholzi/quizify/issues/42"
            )
            == 42
        )

    def test_garbage(self) -> None:
        assert PackSubmissionStore.issue_number_from_url("not a url") is None
        assert PackSubmissionStore.issue_number_from_url(None) is None


# ---------------------------------------------------------------------------
# Fakes (no implicit event loop — executor uses the running loop)
# ---------------------------------------------------------------------------


class _FakeRuntime:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir

    async def run_in_executor(self, func, *args) -> Any:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, func, *args)

    def create_task(self, coro):  # pragma: no cover — unused here
        return asyncio.ensure_future(coro)

    def get_client_session(self):  # pragma: no cover — _fetch_issue is stubbed
        # Reconcile now pulls the shared session from the runtime (#456); the
        # tests stub _fetch_issue so the returned value is never used.
        return None


class _FakeCtx:
    def __init__(self, data_dir: Path, submit_url: str | None = None) -> None:
        self.runtime = _FakeRuntime(data_dir)
        self.community_submit_url = submit_url


# ---------------------------------------------------------------------------
# 3. Reconcile
# ---------------------------------------------------------------------------


class TestReconcile:
    def test_reconcile_stamps_last_poll_and_skips_non_pending(
        self, tmp_path: Path
    ) -> None:
        """Reconcile must always stamp last_poll and never re-fetch a
        submission that already has a terminal status. We assert that by
        making _fetch_issue blow up if called — with only an accepted item
        present, it must not be called."""

        async def _run() -> dict:
            ctx = _FakeCtx(tmp_path)
            store = PackSubmissionStore(ctx)

            async def _boom(*_a, **_k):
                raise AssertionError("should not poll a terminal submission")

            store._fetch_issue = _boom  # type: ignore[assignment]

            data = {
                "submissions": [
                    {
                        "id": "1",
                        "issue_number": 5,
                        "status": SUBMIT_STATUS_ACCEPTED,
                    }
                ],
                "last_poll": None,
            }
            return await store.reconcile(data)

        result = asyncio.run(_run())
        assert result["last_poll"] is not None
        assert result["submissions"][0]["status"] == SUBMIT_STATUS_ACCEPTED

    def test_reconcile_updates_pending_from_github(self, tmp_path: Path) -> None:
        """A pending submission whose issue is now closed-as-not_planned must
        flip to declined, with last_checked stamped."""

        async def _run() -> dict:
            ctx = _FakeCtx(tmp_path)
            store = PackSubmissionStore(ctx)

            async def _fake_fetch(_session, issue_number, *_a):
                assert issue_number == 9
                return "closed", "not_planned"

            store._fetch_issue = _fake_fetch  # type: ignore[assignment]

            data = {
                "submissions": [
                    {"id": "1", "issue_number": 9, "status": SUBMIT_STATUS_PENDING}
                ],
                "last_poll": None,
            }
            return await store.reconcile(data)

        result = asyncio.run(_run())
        sub = result["submissions"][0]
        assert sub["status"] == SUBMIT_STATUS_DECLINED
        assert sub["last_checked"] is not None

    def test_add_then_get_persists(self, tmp_path: Path) -> None:
        """add() writes a record; get_with_reconcile() reads it back. With a
        fresh last_poll set, the reconcile is skipped so no network is hit."""
        import datetime as _dt

        async def _run() -> dict:
            ctx = _FakeCtx(tmp_path)
            store = PackSubmissionStore(ctx)
            await store.add(
                {
                    "id": "1",
                    "name": "Test Pack",
                    "issue_number": 1,
                    "status": SUBMIT_STATUS_PENDING,
                }
            )
            # Pre-stamp last_poll to now so get_with_reconcile won't poll.
            raw = json.loads((tmp_path / "pack_submissions.json").read_text())
            raw["last_poll"] = _dt.datetime.now(_dt.timezone.utc).isoformat()
            (tmp_path / "pack_submissions.json").write_text(json.dumps(raw))
            return await store.get_with_reconcile()

        result = asyncio.run(_run())
        assert len(result["submissions"]) == 1
        assert result["submissions"][0]["name"] == "Test Pack"

    def test_poll_due_logic(self, tmp_path: Path) -> None:
        store = PackSubmissionStore(_FakeCtx(tmp_path))
        assert store._poll_due({"last_poll": None}) is True
        assert store._poll_due({"last_poll": "garbage"}) is True
        import datetime as _dt

        recent = _dt.datetime.now(_dt.timezone.utc).isoformat()
        assert store._poll_due({"last_poll": recent}) is False


# ---------------------------------------------------------------------------
# 4. Config view (enabled flag)
# ---------------------------------------------------------------------------


class TestSubmitConfigView:
    def test_disabled_when_no_url(self, tmp_path: Path) -> None:
        from custom_components.quizify.server import pack_submission as ps

        class _Req:
            app = {ps.APP_CTX_KEY: _FakeCtx(tmp_path, submit_url=None)}

        resp = asyncio.run(ps.submit_config_view(_Req()))
        body = json.loads(resp.text)
        assert body["enabled"] is False
        assert "limits" in body

    def test_enabled_when_url_set(self, tmp_path: Path) -> None:
        from custom_components.quizify.server import pack_submission as ps

        class _Req:
            app = {
                ps.APP_CTX_KEY: _FakeCtx(
                    tmp_path, submit_url="https://worker.example/quizify"
                )
            }

        resp = asyncio.run(ps.submit_config_view(_Req()))
        body = json.loads(resp.text)
        assert body["enabled"] is True


@pytest.mark.parametrize(
    "url,expected",
    [(" ", False), ("", False), ("https://x", True)],
)
def test_enabled_trims_whitespace(tmp_path: Path, url: str, expected: bool) -> None:
    from custom_components.quizify.server import pack_submission as ps

    class _Req:
        app = {ps.APP_CTX_KEY: _FakeCtx(tmp_path, submit_url=url)}

    resp = asyncio.run(ps.submit_config_view(_Req()))
    assert json.loads(resp.text)["enabled"] is expected

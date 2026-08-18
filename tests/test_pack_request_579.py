"""Tests for pack requests (#579) — asking for a pack instead of authoring one.

Covers:
  1. ``validate_request`` against the SHARED case catalog
     (tests/fixtures/pack_request_cases.json), which the worker's
     ``validateRequest`` reads too — that file is the anti-drift contract.
  2. The view: feature gate, validation errors, and the persisted record shape
     (``kind: "request"``, theme in ``name`` so the existing timeline renders it).
  3. What actually travels to the worker: exactly three normalized fields, so an
     extra key in the request body cannot reach the issue.
  4. The rate limit is SHARED with submissions — same worker, same PAT, one budget.
  5. Reconcile handles a request record with no special casing, which is the
     whole reason requests live in the submission store.

No HA, no network: the worker POST is intercepted with a fake session.
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
    RECORD_KIND_REQUEST,
    RECORD_KIND_SUBMISSION,
    REQUEST_MAX_NOTES_CHARS,
    REQUEST_MAX_THEME_CHARS,
    SUBMIT_STATUS_ACCEPTED,
    SUBMIT_STATUS_PENDING,
)
from custom_components.quizify.server import pack_submission as ps  # noqa: E402
from custom_components.quizify.server.pack_submission import (  # noqa: E402
    PackSubmissionStore,
    request_pack_view,
    submit_pack_view,
    validate_request,
)

_CASES = json.loads(
    (_REPO_ROOT / "tests" / "fixtures" / "pack_request_cases.json").read_text(
        encoding="utf-8"
    )
)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, status: int = 200) -> None:
        self.status = status

    async def json(self, content_type: Any = None) -> dict:
        return {
            "issue_number": 579,
            "issue_url": "https://github.com/mholzi/quizify/issues/579",
        }

    async def __aenter__(self) -> "_FakeResponse":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None


class _CapturingSession:
    """Captures the JSON body and headers handed to .post()."""

    captured_json: Any = "<unset>"
    captured_headers: Any = "<unset>"

    async def __aenter__(self) -> "_CapturingSession":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    def post(self, url, json=None, headers=None, timeout=None):  # noqa: A002
        type(self).captured_json = json
        type(self).captured_headers = headers
        return _FakeResponse()


class _FakeRuntime:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir

    async def run_in_executor(self, func, *args) -> Any:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, func, *args)

    def get_client_session(self) -> _CapturingSession:
        return _CapturingSession()


class _FakeCtx:
    def __init__(
        self,
        data_dir: Path,
        submit_url: str | None = "https://worker.example/submit",
        submit_secret: str | None = None,
    ) -> None:
        self.runtime = _FakeRuntime(data_dir)
        self.community_submit_url = submit_url
        self.community_submit_secret = submit_secret


class _FakeRequest:
    """Just enough of aiohttp.web.Request for the two POST views."""

    def __init__(self, ctx: _FakeCtx, body: Any, remote: str = "1.2.3.4") -> None:
        self.app = {ps.APP_CTX_KEY: ctx}
        self.remote = remote
        self._body = body

    async def json(self) -> Any:
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


def _reset_limiter() -> None:
    ps._rate_limiter._buckets.clear()


def _good_pack() -> dict:
    return {
        "name": "Filler Pack",
        "language": "en",
        "questions": [
            {
                "id": "q1",
                "question": "Which planet is the Red Planet?",
                "answers": [
                    {"text": "Mars", "correct": True},
                    {"text": "Venus", "correct": False},
                    {"text": "Jupiter", "correct": False},
                ],
            }
        ],
    }


def _post_request(ctx: _FakeCtx, body: Any, remote: str = "1.2.3.4") -> Any:
    """Drive request_pack_view once and return the response."""
    request = _FakeRequest(ctx, body, remote)

    async def _go() -> Any:
        return await request_pack_view(request)  # type: ignore[arg-type]

    return asyncio.run(_go())


def _payload_of(resp: Any) -> dict:
    return json.loads(resp.body.decode())


# ---------------------------------------------------------------------------
# 1. Validator — lockstep with the worker via the shared catalog
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", _CASES["valid"], ids=lambda c: c["id"])
def test_shared_valid_cases_pass(case: dict) -> None:
    ok, errors = validate_request(case["request"])
    assert ok, f"{case['id']} should validate ({case['reason']}): {errors}"
    assert errors == []


@pytest.mark.parametrize("case", _CASES["malformations"], ids=lambda c: c["id"])
def test_shared_malformations_rejected(case: dict) -> None:
    ok, errors = validate_request(case["request"])
    assert not ok, f"{case['id']} should be rejected ({case['reason']})"
    assert errors, "a rejection must carry at least one message"


def test_cap_boundaries_are_off_by_one_safe() -> None:
    """The caps are inclusive: exactly at the limit passes, one over fails.

    Pinned separately from the fixture so a cap change in const.py has to be a
    decision, not a silent widening of what lands in a GitHub issue.
    """
    ok, _ = validate_request({"theme": "x" * REQUEST_MAX_THEME_CHARS})
    assert ok
    ok, _ = validate_request({"theme": "x" * (REQUEST_MAX_THEME_CHARS + 1)})
    assert not ok
    ok, _ = validate_request({"theme": "ok", "notes": "y" * REQUEST_MAX_NOTES_CHARS})
    assert ok
    ok, _ = validate_request(
        {"theme": "ok", "notes": "y" * (REQUEST_MAX_NOTES_CHARS + 1)}
    )
    assert not ok


def test_error_names_the_offending_field() -> None:
    ok, errors = validate_request({"language": "de"})
    assert not ok
    assert any("theme" in e for e in errors)


# ---------------------------------------------------------------------------
# 2. View — gate, validation, record shape
# ---------------------------------------------------------------------------


def test_disabled_when_no_worker_url(tmp_path: Path) -> None:
    _reset_limiter()
    ctx = _FakeCtx(tmp_path, submit_url=None)
    resp = _post_request(ctx, {"request": {"theme": "Cheese"}})
    assert resp.status == 403
    assert _payload_of(resp)["code"] == "SUBMIT_DISABLED"


def test_invalid_request_is_400_with_errors(tmp_path: Path) -> None:
    _reset_limiter()
    ctx = _FakeCtx(tmp_path)
    resp = _post_request(ctx, {"request": {"theme": ""}})
    assert resp.status == 400
    body = _payload_of(resp)
    assert body["code"] == "INVALID_FORMAT"
    assert body["errors"]


def test_non_json_body_is_400(tmp_path: Path) -> None:
    _reset_limiter()
    ctx = _FakeCtx(tmp_path)
    resp = _post_request(ctx, ValueError("not json"))
    assert resp.status == 400
    assert _payload_of(resp)["code"] == "INVALID_FORMAT"


def test_missing_request_key_is_400(tmp_path: Path) -> None:
    """A body without a `request` key must not be read as an empty request."""
    _reset_limiter()
    ctx = _FakeCtx(tmp_path)
    resp = _post_request(ctx, {"pack": _good_pack()})
    assert resp.status == 400


def test_success_persists_request_record(tmp_path: Path) -> None:
    _reset_limiter()
    ctx = _FakeCtx(tmp_path)
    resp = _post_request(
        ctx,
        {
            "request": {
                "theme": "  80s German TV shows  ",
                "language": "de",
                "notes": "  skew nostalgic  ",
            }
        },
    )
    assert resp.status == 200
    assert _payload_of(resp) == {
        "ok": True,
        "issue_number": 579,
        "issue_url": "https://github.com/mholzi/quizify/issues/579",
    }

    records = json.loads((tmp_path / "pack_submissions.json").read_text())
    assert len(records["submissions"]) == 1
    rec = records["submissions"][0]
    assert rec["kind"] == RECORD_KIND_REQUEST
    assert rec["theme"] == "80s German TV shows"  # stripped
    assert rec["name"] == rec["theme"]  # timeline renders `name` unchanged
    assert rec["notes"] == "skew nostalgic"
    assert rec["question_count"] == 0
    assert rec["status"] == SUBMIT_STATUS_PENDING
    assert rec["issue_number"] == 579


def test_submission_record_is_tagged_too(tmp_path: Path) -> None:
    """The submission path now stamps its kind as well, so a reader never has to
    guess which of two shapes a record is."""
    _reset_limiter()
    ctx = _FakeCtx(tmp_path)
    request = _FakeRequest(ctx, {"pack": _good_pack()})

    async def _go() -> Any:
        return await submit_pack_view(request)  # type: ignore[arg-type]

    resp = asyncio.run(_go())
    assert resp.status == 200
    records = json.loads((tmp_path / "pack_submissions.json").read_text())
    assert records["submissions"][0]["kind"] == RECORD_KIND_SUBMISSION


# ---------------------------------------------------------------------------
# 3. What travels to the worker
# ---------------------------------------------------------------------------


def test_worker_gets_exactly_three_normalized_fields(tmp_path: Path) -> None:
    """An extra key in the body must not reach the worker — and therefore not
    the issue. The view sends its own dict, not the caller's."""
    _reset_limiter()
    _CapturingSession.captured_json = "<unset>"
    ctx = _FakeCtx(tmp_path)
    _post_request(
        ctx,
        {
            "request": {
                "theme": "Belgian beer",
                "language": "en",
                "notes": "",
                "labels": ["urgent"],
                "assignee": "mholzi",
            }
        },
    )
    sent = _CapturingSession.captured_json
    assert set(sent) == {"request"}
    assert sent["request"] == {
        "theme": "Belgian beer",
        "language": "en",
        "notes": "",
    }


def test_language_defaults_to_de(tmp_path: Path) -> None:
    _reset_limiter()
    _CapturingSession.captured_json = "<unset>"
    ctx = _FakeCtx(tmp_path)
    _post_request(ctx, {"request": {"theme": "Formel 1"}})
    assert _CapturingSession.captured_json["request"]["language"] == "de"


def test_secret_header_travels_on_the_request_path(tmp_path: Path) -> None:
    """The request path must not be an unauthenticated back door into the worker
    while the submission path is gated."""
    _reset_limiter()
    _CapturingSession.captured_headers = "<unset>"
    ctx = _FakeCtx(tmp_path, submit_secret="s3cr3t-token")
    _post_request(ctx, {"request": {"theme": "Cheese"}})
    assert _CapturingSession.captured_headers == {"X-Quizify-Secret": "s3cr3t-token"}


# ---------------------------------------------------------------------------
# 4. One rate-limit budget for both paths
# ---------------------------------------------------------------------------


def test_rate_limit_is_shared_with_submissions(tmp_path: Path) -> None:
    """Both paths file issues with the same PAT in the same repo, so they share
    one bucket. Splitting the budget would hand a caller twice the rate."""
    _reset_limiter()
    ctx = _FakeCtx(tmp_path)

    async def _go() -> Any:
        # Exhaust the window with submissions from one IP...
        for _ in range(ps._RATE_LIMIT_REQUESTS):
            resp = await submit_pack_view(
                _FakeRequest(ctx, {"pack": _good_pack()})  # type: ignore[arg-type]
            )
            assert resp.status == 200
        # ...then a request from the same IP must be refused.
        return await request_pack_view(
            _FakeRequest(ctx, {"request": {"theme": "Cheese"}})  # type: ignore[arg-type]
        )

    resp = asyncio.run(_go())
    assert resp.status == 429
    assert _payload_of(resp)["code"] == "RATE_LIMITED"


def test_other_ip_still_allowed(tmp_path: Path) -> None:
    """The bucket is per IP, not global — one busy tablet must not lock out the
    host's phone."""
    _reset_limiter()
    ctx = _FakeCtx(tmp_path)

    async def _go() -> Any:
        for _ in range(ps._RATE_LIMIT_REQUESTS):
            await request_pack_view(
                _FakeRequest(ctx, {"request": {"theme": "A"}}, remote="10.0.0.1")  # type: ignore[arg-type]
            )
        return await request_pack_view(
            _FakeRequest(ctx, {"request": {"theme": "B"}}, remote="10.0.0.2")  # type: ignore[arg-type]
        )

    assert asyncio.run(_go()).status == 200


# ---------------------------------------------------------------------------
# 5. Reconcile needs no special case for requests
# ---------------------------------------------------------------------------


def test_reconcile_resolves_a_request_record(tmp_path: Path) -> None:
    """A request issue closes exactly like a submission issue. This is the claim
    that justifies one store and one reconcile for both kinds — if it ever stops
    holding, this test is where it shows."""

    async def _run() -> dict:
        ctx = _FakeCtx(tmp_path)
        store = PackSubmissionStore(ctx)

        async def _fake_fetch(_session, issue_number, *_a):
            assert issue_number == 579
            return "closed", "completed"

        store._fetch_issue = _fake_fetch  # type: ignore[assignment]
        data = {
            "submissions": [
                {
                    "id": "1",
                    "kind": RECORD_KIND_REQUEST,
                    "issue_number": 579,
                    "status": SUBMIT_STATUS_PENDING,
                }
            ],
            "last_poll": None,
        }
        return await store.reconcile(data)

    result = asyncio.run(_run())
    assert result["submissions"][0]["status"] == SUBMIT_STATUS_ACCEPTED

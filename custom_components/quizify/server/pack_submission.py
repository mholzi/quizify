"""In-app community-pack submission (#180).

A user composes/pastes a community question pack in the admin UI. The browser
validates it against the same schema the on-disk loader enforces (#179), then
POSTs the validated pack here. This view proxies the pack to a configurable
**worker** endpoint (``community_submit_url``) that holds a GitHub token and
creates a labelled issue in ``mholzi/quizify`` — the token never lives in the
browser or this integration. A submission record is persisted in the
integration data dir.

A separate GET reconciles each persisted submission against the GitHub **issue
state** (closed-as-completed = accepted, closed-as-not_planned = declined),
throttled to ~hourly so a busy admin tab can't hammer GitHub's
unauthenticated, per-IP rate limit. This mirrors Beatify's
``PlaylistRequestsView`` (the #970 lesson: trust the issue *state*, not labels).

The whole feature stays inert until ``community_submit_url`` is set: the config
endpoint reports ``enabled: false`` (so the UI hides the section) and POSTs are
refused with ``SUBMIT_DISABLED``.

No HA coupling: handlers read everything they need from the
:class:`~..server.context.AppContext` (``ctx.runtime`` for the data dir and
executor, ``ctx.community_submit_url`` for the endpoint), so the standalone dev
server runs them unchanged.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

import aiohttp
from aiohttp import web

from ..const import (
    ERR_SUBMIT_DISABLED,
    ERR_SUBMIT_GITHUB_ERROR,
    ERR_SUBMIT_INVALID_FORMAT,
    ERR_SUBMIT_RATE_LIMITED,
    SUBMIT_ANSWERS_PER_QUESTION,
    SUBMIT_MAX_PACK_BYTES,
    SUBMIT_MAX_QUESTIONS,
    SUBMIT_MAX_RECORDS,
    SUBMIT_MIN_QUESTIONS,
    SUBMIT_POLL_INTERVAL_SECONDS,
    SUBMIT_POLL_TIMEOUT_SECONDS,
    SUBMIT_RECORDS_FILE,
    SUBMIT_STATUS_ACCEPTED,
    SUBMIT_STATUS_DECLINED,
    SUBMIT_STATUS_PENDING,
)
from .context import APP_CTX_KEY
from .rate_limit import SlidingWindowLimiter

if TYPE_CHECKING:
    from .context import AppContext

_LOGGER = logging.getLogger(__name__)

# GitHub issues API for the reconcile poll. Read-only, unauthenticated — the
# write side (creating the issue) is the worker's job, not ours.
_GITHUB_ISSUES_API = "https://api.github.com/repos/mholzi/quizify/issues"

# Per-IP submit rate limit. Generous enough for a legitimate compose-and-retry
# loop, tight enough that an inert anonymous endpoint can't be hammered. Shares
# the SlidingWindowLimiter helper with the WS flood guard (#260); empty buckets
# are evicted by the limiter so the dict doesn't grow one entry per IP forever
# (#258).
_RATE_LIMIT_REQUESTS = 5
_RATE_LIMIT_WINDOW = 60  # seconds
_rate_limiter = SlidingWindowLimiter(_RATE_LIMIT_REQUESTS, _RATE_LIMIT_WINDOW)

# Per-records-file lock so the load-modify-save in get_with_reconcile() / add()
# can't interleave (#256): a submit landing mid-reconcile would otherwise read a
# stale snapshot and clobber the other writer's record. PackSubmissionStore is
# instantiated per-request, so the lock can't live on the instance — it's keyed
# on the records-file path here, mirroring TokenStore._lock. asyncio.Lock is the
# right primitive (these are coroutines on one event loop; the actual file I/O
# runs in the executor but the read-decide-write critical section is async).
_store_locks: dict[str, asyncio.Lock] = {}


def _store_lock(path: object) -> asyncio.Lock:
    """Return the shared lock for a given records-file path."""
    key = str(path)
    lock = _store_locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _store_locks[key] = lock
    return lock


def _check_rate_limit(client_ip: str) -> bool:
    """Sliding-window rate limit keyed on client IP. True = allowed."""
    return _rate_limiter.check(client_ip)


# ---------------------------------------------------------------------------
# Validation — server-side mirror of the #179 community-pack schema
# ---------------------------------------------------------------------------
#
# The browser runs the same checks (pack-submit.js) to give per-field ✓/✗
# feedback before submit; this is the defensive guard so a hand-crafted POST
# that skips the UI can't push a malformed pack to the worker. Returns
# (ok, errors): errors is a list of human-readable strings (first 10 surfaced).


def validate_pack(pack: object) -> tuple[bool, list[str]]:
    """Validate a community pack against the #179 schema. Returns (ok, errors)."""
    errors: list[str] = []

    if not isinstance(pack, dict):
        return False, ["Top-level JSON must be an object."]

    name = pack.get("name")
    if not isinstance(name, str) or not name.strip():
        errors.append("Field 'name' is required and must be a non-empty string.")

    language = pack.get("language", "de")
    if not isinstance(language, str) or not language.strip():
        errors.append("Field 'language' must be a non-empty string.")

    questions = pack.get("questions")
    if not isinstance(questions, list) or not questions:
        errors.append("Field 'questions' must be a non-empty list.")
        return False, errors[:10]

    if len(questions) < SUBMIT_MIN_QUESTIONS:
        errors.append(f"At least {SUBMIT_MIN_QUESTIONS} question is required.")
    if len(questions) > SUBMIT_MAX_QUESTIONS:
        errors.append(f"Too many questions (max {SUBMIT_MAX_QUESTIONS}).")

    seen_ids: set[str] = set()
    for idx, q in enumerate(questions):
        prefix = f"Question {idx + 1}"
        if not isinstance(q, dict):
            errors.append(f"{prefix}: must be an object.")
            continue
        qid = q.get("id")
        if not isinstance(qid, str) or not qid.strip():
            errors.append(f"{prefix}: 'id' is required and must be a non-empty string.")
        elif qid in seen_ids:
            errors.append(f"{prefix}: duplicate id '{qid}'.")
        else:
            seen_ids.add(qid)
        if not isinstance(q.get("question"), str) or not q.get("question", "").strip():
            errors.append(f"{prefix}: 'question' is required and must be a non-empty string.")
        answers = q.get("answers")
        if not isinstance(answers, list) or len(answers) != SUBMIT_ANSWERS_PER_QUESTION:
            errors.append(
                f"{prefix}: exactly {SUBMIT_ANSWERS_PER_QUESTION} answers are required."
            )
            continue
        correct_count = 0
        for a in answers:
            if not isinstance(a, dict):
                errors.append(f"{prefix}: each answer must be an object.")
                continue
            if not isinstance(a.get("text"), str) or not a.get("text", "").strip():
                errors.append(f"{prefix}: every answer needs non-empty 'text'.")
            if a.get("correct") is True:
                correct_count += 1
        if correct_count != 1:
            errors.append(
                f"{prefix}: exactly 1 answer must be marked correct (got {correct_count})."
            )

    return (not errors), errors[:10]


# ---------------------------------------------------------------------------
# Persistence + GitHub status reconcile
# ---------------------------------------------------------------------------


def _issue_to_status(state: str, state_reason: str) -> str:
    """Map a GitHub issue's state + close reason to a submission status.

    The issue STATE is the source of truth (Beatify #970): any closed issue is
    a decision. ``not_planned`` close reason = declined; anything else closed
    (e.g. ``completed``) = accepted. Open = still pending.
    """
    if state != "closed":
        return SUBMIT_STATUS_PENDING
    if state_reason == "not_planned":
        return SUBMIT_STATUS_DECLINED
    return SUBMIT_STATUS_ACCEPTED


class PackSubmissionStore:
    """Persisted submission records + throttled GitHub status reconcile.

    Records live in ``<data_dir>/pack_submissions.json``:
    ``{"submissions": [...], "last_poll": "<iso8601>"}``. Each submission is
    ``{id, name, language, question_count, issue_number, issue_url, status,
    created, last_checked}``.
    """

    _ISSUE_NUMBER_RE = re.compile(r"/issues/(\d+)(?:[#?].*)?$")

    def __init__(self, ctx: "AppContext") -> None:
        self._ctx = ctx
        self._path = ctx.runtime.data_dir / SUBMIT_RECORDS_FILE
        # Shared across all stores pointing at the same file (this class is
        # instantiated per-request) so concurrent load-modify-save can't race.
        self._lock = _store_lock(self._path)

    # --- blocking helpers (run in executor) ---------------------------------

    def _load(self) -> dict:
        if self._path.exists():
            try:
                data = json.loads(self._path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
            except (OSError, ValueError) as err:
                _LOGGER.warning("Could not read pack submissions: %s", err)
        return {"submissions": [], "last_poll": None}

    def _save(self, data: dict) -> bool:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            return True
        except OSError as err:
            _LOGGER.error("Could not write pack submissions: %s", err)
            return False

    # --- reconcile ----------------------------------------------------------

    def _poll_due(self, data: dict) -> bool:
        last_poll = data.get("last_poll")
        if not last_poll:
            return True
        try:
            last = datetime.fromisoformat(str(last_poll).replace("Z", "+00:00"))
        except ValueError:
            return True
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        elapsed = (datetime.now(timezone.utc) - last).total_seconds()
        return elapsed >= SUBMIT_POLL_INTERVAL_SECONDS

    @staticmethod
    def issue_number_from_url(url: object) -> int | None:
        """Extract the issue number from a GitHub issue HTML/API URL."""
        if not isinstance(url, str):
            return None
        match = PackSubmissionStore._ISSUE_NUMBER_RE.search(url)
        if match:
            try:
                return int(match.group(1))
            except ValueError:
                return None
        return None

    async def _fetch_issue(
        self, session: aiohttp.ClientSession, issue_number: int
    ) -> tuple[str, str] | None:
        """Fetch a GitHub issue's (state, state_reason), or None on failure."""
        try:
            async with session.get(
                f"{_GITHUB_ISSUES_API}/{issue_number}",
                headers={"Accept": "application/vnd.github+json"},
            ) as resp:
                if resp.status != 200:
                    _LOGGER.debug(
                        "Pack-submission poll: issue %s returned HTTP %s",
                        issue_number,
                        resp.status,
                    )
                    return None
                issue = await resp.json()
            return issue.get("state", ""), issue.get("state_reason") or ""
        except (aiohttp.ClientError, ValueError) as err:
            _LOGGER.debug("Pack-submission poll: issue %s failed: %s", issue_number, err)
            return None

    async def reconcile(self, data: dict) -> dict:
        """Reconcile pending submissions against GitHub issue state.

        Stamps ``last_poll`` whenever a poll is attempted — even with nothing
        pending or GitHub unreachable — so a failure backs off for the full
        interval instead of retrying on every admin-tab open.
        """
        now = datetime.now(timezone.utc).isoformat()
        data["last_poll"] = now

        submissions = data.get("submissions", [])
        pending = [
            s
            for s in submissions
            if isinstance(s, dict)
            and s.get("issue_number")
            and s.get("status") in (None, "", SUBMIT_STATUS_PENDING)
        ]
        if not pending:
            return data

        timeout = aiohttp.ClientTimeout(total=SUBMIT_POLL_TIMEOUT_SECONDS)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                for sub in pending:
                    result = await self._fetch_issue(session, sub["issue_number"])
                    if result is None:
                        continue
                    state, state_reason = result
                    sub["status"] = _issue_to_status(state, state_reason)
                    sub["last_checked"] = now
        except (aiohttp.ClientError, ValueError) as err:
            _LOGGER.debug("Pack-submission reconcile aborted: %s", err)
        return data

    # --- public async API ---------------------------------------------------

    async def get_with_reconcile(self) -> dict:
        """Load records, reconcile against GitHub if the poll is due, persist.

        The whole load-modify-save runs under the per-file lock (#256) so a
        concurrent ``add()`` can't slip a fresh record in between this load and
        save — which would otherwise be silently dropped on persist.
        """
        async with self._lock:
            data = await self._ctx.runtime.run_in_executor(self._load)
            if self._poll_due(data):
                data = await self.reconcile(data)
                await self._ctx.runtime.run_in_executor(self._save, data)
            return data

    async def add(self, record: dict) -> None:
        """Append a submission record (newest first), capped, then persist.

        Held under the per-file lock (#256) so it can't interleave with a
        reconcile's load-modify-save and lose the new record.
        """
        async with self._lock:
            data = await self._ctx.runtime.run_in_executor(self._load)
            submissions = data.get("submissions")
            if not isinstance(submissions, list):
                submissions = []
            submissions.insert(0, record)
            data["submissions"] = submissions[:SUBMIT_MAX_RECORDS]
            await self._ctx.runtime.run_in_executor(self._save, data)


# ---------------------------------------------------------------------------
# HTTP views
# ---------------------------------------------------------------------------


def _get_ctx(request: web.Request) -> "AppContext":
    return request.app[APP_CTX_KEY]


async def submit_config_view(request: web.Request) -> web.Response:
    """Report whether the submit feature is enabled (drives the UI's visibility).

    Never leaks the worker URL to the browser — the browser POSTs to *our*
    endpoint, which proxies to the worker server-side. Returns the validation
    caps so the client validator stays in sync without hardcoding them.
    """
    ctx = _get_ctx(request)
    enabled = bool((ctx.community_submit_url or "").strip())
    return web.json_response(
        {
            "enabled": enabled,
            "limits": {
                "max_questions": SUBMIT_MAX_QUESTIONS,
                "min_questions": SUBMIT_MIN_QUESTIONS,
                "answers_per_question": SUBMIT_ANSWERS_PER_QUESTION,
                "max_bytes": SUBMIT_MAX_PACK_BYTES,
            },
        }
    )


async def submissions_list_view(request: web.Request) -> web.Response:
    """Return persisted submissions, reconciling status against GitHub if due."""
    ctx = _get_ctx(request)
    store = PackSubmissionStore(ctx)
    data = await store.get_with_reconcile()
    return web.json_response({"submissions": data.get("submissions", [])})


async def submit_pack_view(request: web.Request) -> web.Response:
    """Validate a composed pack and proxy it to the configured worker.

    Body: ``{"pack": {...}}``. On success records the submission (so its status
    can later be reconciled) and returns ``{ok, issue_number, issue_url}``.
    Error responses carry a localizable ``code`` (INVALID_FORMAT / RATE_LIMITED
    / GITHUB_ERROR / SUBMIT_DISABLED) plus a raw ``message`` fallback.
    """
    ctx = _get_ctx(request)
    submit_url = (ctx.community_submit_url or "").strip()
    if not submit_url:
        return web.json_response(
            {"code": ERR_SUBMIT_DISABLED, "message": "Pack submission is not configured."},
            status=403,
        )

    # Rate-limit bucket key. We use ``request.remote`` rather than parsing the
    # ``X-Forwarded-For`` header ourselves: behind a reverse proxy, a raw
    # forwarded header is attacker-controlled, so trusting it would let any
    # client spoof an arbitrary IP and either evade the limit or poison another
    # client's bucket (#259). Home Assistant already resolves the real client IP
    # for us when it is configured with ``http.use_x_forwarded_for`` +
    # ``trusted_proxies`` — its ``XForwardedRelaxed`` middleware rewrites
    # ``request.remote`` to the left-most untrusted hop *before* the request
    # reaches this handler. So ``request.remote`` is the correct, proxy-aware
    # key when HA is configured for a proxy, and the safe direct-peer IP when it
    # is not. Without that config behind a proxy the limit collapses to the
    # proxy's single IP — an accepted LAN trade-off (see DESIGN.md security
    # note); the limiter is a courtesy throttle on an already-shared-secret
    # endpoint, not an auth boundary.
    client_ip = request.remote or "unknown"
    if not _check_rate_limit(client_ip):
        return web.json_response(
            {"code": ERR_SUBMIT_RATE_LIMITED, "message": "Too many submissions, slow down."},
            status=429,
        )

    try:
        body = await request.json()
    except (ValueError, TypeError):
        return web.json_response(
            {"code": ERR_SUBMIT_INVALID_FORMAT, "message": "Body is not valid JSON."},
            status=400,
        )

    pack = (body or {}).get("pack") if isinstance(body, dict) else None
    # Size guard before validation so a giant blob can't be walked field by field.
    try:
        encoded = json.dumps(pack).encode("utf-8")
    except (TypeError, ValueError):
        return web.json_response(
            {"code": ERR_SUBMIT_INVALID_FORMAT, "message": "Pack is not serializable JSON."},
            status=400,
        )
    if len(encoded) > SUBMIT_MAX_PACK_BYTES:
        return web.json_response(
            {
                "code": ERR_SUBMIT_INVALID_FORMAT,
                "message": f"Pack exceeds {SUBMIT_MAX_PACK_BYTES} bytes.",
            },
            status=400,
        )

    ok, errors = validate_pack(pack)
    if not ok:
        return web.json_response(
            {
                "code": ERR_SUBMIT_INVALID_FORMAT,
                "message": "; ".join(errors) or "Pack failed validation.",
                "errors": errors,
            },
            status=400,
        )

    # Proxy to the worker, which holds the GitHub token and creates the issue.
    # When a shared secret is configured, send it as the X-Quizify-Secret header
    # so the worker can reject unauthenticated requests, closing the open-proxy
    # hole (#256). No secret → no header, and the worker (which only enforces
    # when its SHARED_SECRET is set) behaves exactly as before — back-compatible.
    submit_secret = (ctx.community_submit_secret or "").strip()
    submit_headers: dict[str, str] | None = (
        {"X-Quizify-Secret": submit_secret} if submit_secret else None
    )
    timeout = aiohttp.ClientTimeout(total=SUBMIT_POLL_TIMEOUT_SECONDS)
    worker_payload: dict[str, Any]
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                submit_url, json={"pack": pack}, headers=submit_headers
            ) as resp:
                try:
                    worker_payload = await resp.json(content_type=None)
                except (ValueError, aiohttp.ClientError):
                    worker_payload = {}
                if resp.status >= 400:
                    code = worker_payload.get("code") if isinstance(worker_payload, dict) else None
                    message = (
                        worker_payload.get("message")
                        if isinstance(worker_payload, dict)
                        else None
                    )
                    return web.json_response(
                        {
                            "code": code or ERR_SUBMIT_GITHUB_ERROR,
                            "message": message or f"Worker returned HTTP {resp.status}.",
                        },
                        status=resp.status if resp.status in (400, 429) else 502,
                    )
    except aiohttp.ClientError as err:
        _LOGGER.warning("Pack submission proxy failed: %s", err)
        return web.json_response(
            {"code": ERR_SUBMIT_GITHUB_ERROR, "message": f"Could not reach the worker: {err}"},
            status=502,
        )

    if not isinstance(worker_payload, dict):
        worker_payload = {}
    issue_url = worker_payload.get("issue_url") or worker_payload.get("html_url")
    issue_number = worker_payload.get("issue_number")
    if issue_number is None:
        issue_number = PackSubmissionStore.issue_number_from_url(issue_url)

    record = {
        "id": f"{int(time.time() * 1000)}",
        "name": str(pack.get("name", "")) if isinstance(pack, dict) else "",
        "language": str(pack.get("language", "")) if isinstance(pack, dict) else "",
        "question_count": len(pack.get("questions", [])) if isinstance(pack, dict) else 0,
        "issue_number": issue_number,
        "issue_url": issue_url,
        "status": SUBMIT_STATUS_PENDING,
        "created": datetime.now(timezone.utc).isoformat(),
        "last_checked": None,
    }
    await PackSubmissionStore(ctx).add(record)

    return web.json_response(
        {"ok": True, "issue_number": issue_number, "issue_url": issue_url}
    )

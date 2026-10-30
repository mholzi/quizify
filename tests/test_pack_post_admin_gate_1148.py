"""Regression tests for issue #1148 (security).

The pack-submit and pack-request POSTs only had the origin gate and a per-IP
limiter, so any phone that scanned the join QR could file a GitHub issue from
the host's Home Assistant and burn the worker's daily budget. Both now need the
admin token, and the admin page sends it.

The server half goes through the real route table and the real
``ConnectionManager``, so a wrong token is actually refused. The client half
runs the real ``pack-submit.js`` in node and reads the headers it sends.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from custom_components.quizify.game.state import QuizifyGameState  # noqa: E402
from custom_components.quizify.server import pack_submission as ps  # noqa: E402
from custom_components.quizify.server import views  # noqa: E402
from custom_components.quizify.server.connection import ConnectionManager  # noqa: E402
from custom_components.quizify.server.context import APP_CTX_KEY  # noqa: E402

_JS = _REPO_ROOT / "custom_components" / "quizify" / "www" / "js"
_I18N = _REPO_ROOT / "custom_components" / "quizify" / "www" / "i18n"
_STUB = Path(__file__).resolve().parent / "fixtures" / "dom_stub.js"
_SUBMIT = "/api/quizify/pack-submit"
_REQUEST = "/api/quizify/pack-submit/request"

_PACK = {
    "name": "Test Pack",
    "language": "en",
    "version": "1.0",
    "questions": [
        {
            "id": "q1",
            "question": "Which planet is the Red Planet?",
            "answers": [
                {"text": "Mars", "correct": True},
                {"text": "Venus", "correct": False},
                {"text": "Jupiter", "correct": False},
            ],
            "difficulty": "easy",
        }
    ],
}
_WISH = {"theme": "Volcanoes", "language": "en", "notes": ""}
_ROUTES = [(_SUBMIT, {"pack": _PACK}), (_REQUEST, {"request": _WISH})]


class _WorkerResponse:
    def __init__(self, status: int) -> None:
        self.status = status

    async def json(self, content_type: Any = None) -> dict:
        if self.status >= 400:
            return {"message": "worker refused"}
        return {
            "issue_number": 1148,
            "issue_url": "https://github.com/mholzi/quizify/issues/1148",
        }

    async def __aenter__(self) -> _WorkerResponse:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None


class _Worker:
    """Stands in for the client session; records posts and can refuse them."""

    def __init__(self, status: int = 200) -> None:
        self.status = status
        self.posts: list[Any] = []

    def post(self, url, json=None, headers=None, timeout=None):  # noqa: ANN001, ANN202, A002
        self.posts.append(json)
        return _WorkerResponse(self.status)


class _Runtime:
    def __init__(self, data_dir: Path, worker: _Worker) -> None:
        self.data_dir = data_dir
        self._worker = worker

    def create_task(self, coro):  # noqa: ANN001, ANN202
        return asyncio.ensure_future(coro)

    async def run_in_executor(self, func, *args):  # noqa: ANN001, ANN002, ANN202
        return func(*args)

    def get_client_session(self) -> _Worker:
        return self._worker


@pytest.fixture(autouse=True)
def _fresh_limiter() -> None:
    ps._rate_limiter._buckets.clear()


async def _with_client(tmp_path: Path, worker: _Worker, body) -> Any:  # noqa: ANN001
    """Serve the real route table; run ``body(client, token)``."""
    runtime = _Runtime(tmp_path, worker)
    game = QuizifyGameState(runtime=runtime, entry_id="test")
    conn = ConnectionManager(runtime, lambda: game)
    token = conn.get_or_create_admin_token()

    app = web.Application()
    app[APP_CTX_KEY] = SimpleNamespace(
        runtime=runtime,
        community_submit_url="https://worker.example/submit",
        community_submit_secret=None,
        ws_handler=SimpleNamespace(conn=conn),
    )
    views.register_routes(app.router)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        return await body(client, token)
    finally:
        await client.close()


def _records(tmp_path: Path) -> list[dict]:
    path = tmp_path / ps.SUBMIT_RECORDS_FILE
    if not path.exists():
        return []
    return json.loads(path.read_text()).get("submissions", [])


@pytest.mark.parametrize(("path", "payload"), _ROUTES)
@pytest.mark.parametrize(
    "headers",
    [{}, {"X-Quizify-Token": "not-the-token"}],
    ids=["no-token", "wrong-token"],
)
def test_a_guest_is_refused_and_nothing_is_filed(
    tmp_path: Path, path: str, payload: dict, headers: dict
) -> None:
    worker = _Worker()

    async def body(client: TestClient, _token: str) -> int:
        resp = await client.post(path, json=payload, headers=headers)
        return resp.status

    assert asyncio.run(_with_client(tmp_path, worker, body)) == 401
    assert worker.posts == [], "a guest reached the worker and filed an issue"
    assert _records(tmp_path) == []


@pytest.mark.parametrize(("path", "payload"), _ROUTES)
def test_the_host_token_still_files(tmp_path: Path, path: str, payload: dict) -> None:
    worker = _Worker()

    async def body(client: TestClient, token: str) -> tuple[int, dict]:
        resp = await client.post(
            path, json=payload, headers={"X-Quizify-Token": token}
        )
        return resp.status, await resp.json()

    status, data = asyncio.run(_with_client(tmp_path, worker, body))
    assert status == 200
    assert data["issue_number"] == 1148
    assert len(worker.posts) == 1
    assert len(_records(tmp_path)) == 1


@pytest.mark.parametrize(("path", "payload"), _ROUTES)
def test_a_worker_refusal_reaches_the_host_and_records_nothing(
    tmp_path: Path, path: str, payload: dict
) -> None:
    worker = _Worker(status=500)

    async def body(client: TestClient, token: str) -> int:
        resp = await client.post(
            path, json=payload, headers={"X-Quizify-Token": token}
        )
        return resp.status

    assert asyncio.run(_with_client(tmp_path, worker, body)) == 502
    assert _records(tmp_path) == []


@pytest.mark.parametrize(("path", "payload"), _ROUTES)
def test_the_origin_gate_still_applies_with_a_valid_token(
    tmp_path: Path, path: str, payload: dict
) -> None:
    worker = _Worker()

    async def body(client: TestClient, token: str) -> int:
        resp = await client.post(
            path,
            json=payload,
            headers={"X-Quizify-Token": token, "Origin": "http://evil.example"},
        )
        return resp.status

    assert asyncio.run(_with_client(tmp_path, worker, body)) == 403
    assert worker.posts == []


def test_the_limiter_still_applies_to_the_host(tmp_path: Path) -> None:
    worker = _Worker()

    async def body(client: TestClient, token: str) -> list[int]:
        statuses = []
        for _ in range(ps._RATE_LIMIT_REQUESTS + 1):
            resp = await client.post(
                _REQUEST,
                json={"request": _WISH},
                headers={"X-Quizify-Token": token},
            )
            statuses.append(resp.status)
        return statuses

    statuses = asyncio.run(_with_client(tmp_path, worker, body))
    assert statuses[-1] == 429
    assert statuses[:-1] == [200] * ps._RATE_LIMIT_REQUESTS


def test_refused_guests_do_not_spend_the_hosts_budget(tmp_path: Path) -> None:
    worker = _Worker()

    async def body(client: TestClient, token: str) -> int:
        for _ in range(ps._RATE_LIMIT_REQUESTS + 1):
            await client.post(_REQUEST, json={"request": _WISH})
        resp = await client.post(
            _REQUEST, json={"request": _WISH}, headers={"X-Quizify-Token": token}
        )
        return resp.status

    assert asyncio.run(_with_client(tmp_path, worker, body)) == 200


_PAGE_SCRIPT = """
require({stub});
QZ.serveI18n({i18n});
var i18nFetch = global.fetch;
var posts = [];
global.fetch = async function (url, opts) {{
    if (opts && opts.method === 'POST') {{
        posts.push({{ url: String(url), headers: opts.headers }});
        return {{ ok: true, status: 200, json: async function () {{
            return {{ ok: true, issue_number: 1, issue_url: 'https://x/1' }};
        }} }};
    }}
    if (String(url).indexOf('/api/quizify/pack-submit/config') !== -1) {{
        return {{ ok: true, status: 200, json: async function () {{
            return {{ enabled: true, limits: {{}} }};
        }} }};
    }}
    if (String(url).indexOf('/api/quizify/pack-submit/submissions') !== -1) {{
        return {{ ok: false, status: 401, json: async function () {{ return {{}}; }} }};
    }}
    return i18nFetch(url, opts);
}};
var localStorage = QZ.window.localStorage;
var sessionStorage = QZ.window.localStorage;
QZ.load({utilsjs});
var QuizifyUtils = QZ.window.QuizifyUtils;
QZ.load({i18njs});
var TOKEN = {token};
if (TOKEN) {{ QuizifyUtils.writeAdminToken(TOKEN); }}
QZ.els(['pack-submit-section', 'pack-submit-pane', 'pack-request-pane',
        'pack-mode-submit-btn', 'pack-mode-request-btn', 'pack-request-theme',
        'pack-request-language', 'pack-request-btn', 'pack-submit-input',
        'pack-submit-check-btn', 'pack-submit-continue-btn', 'pack-submit-btn',
        'pack-submit-result', 'pack-submit-status', 'pack-request-status']);
(async function () {{
    await window.QuizifyI18n.init('en');
    QZ.load({packsubmit});
    await new Promise(function (r) {{ setTimeout(r, 20); }});

    QZ.el('pack-submit-input').value = {pack};
    QZ.el('pack-submit-check-btn').click();
    QZ.el('pack-submit-btn').disabled = false;
    QZ.el('pack-submit-btn').click();
    await new Promise(function (r) {{ setTimeout(r, 20); }});

    QZ.el('pack-request-theme').value = 'Volcanoes';
    QZ.el('pack-request-btn').disabled = false;
    QZ.el('pack-request-btn').click();
    await new Promise(function (r) {{ setTimeout(r, 20); }});

    console.log(JSON.stringify(posts));
}})();
"""


def _run_page(token: str | None) -> dict[str, dict]:
    if shutil.which("node") is None:
        if os.environ.get("QUIZIFY_REQUIRE_NODE") == "1":
            pytest.fail("node not available (QUIZIFY_REQUIRE_NODE=1 requires it)")
        pytest.skip("node not available")
    script = _PAGE_SCRIPT.format(
        stub=json.dumps(str(_STUB)),
        i18n=json.dumps(str(_I18N)),
        utilsjs=json.dumps(str(_JS / "utils.js")),
        i18njs=json.dumps(str(_JS / "i18n.js")),
        packsubmit=json.dumps(str(_JS / "pack-submit.js")),
        token=json.dumps(token),
        pack=json.dumps(json.dumps(_PACK)),
    )
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, check=True
    )
    posts = json.loads(out.stdout.strip().splitlines()[-1])
    return {post["url"]: post["headers"] for post in posts}


def test_the_admin_page_sends_its_token_on_both_posts() -> None:
    posts = _run_page("host-token")
    assert set(posts) == {_SUBMIT, _REQUEST}
    for url, headers in posts.items():
        assert headers.get("X-Quizify-Token") == "host-token", url
        assert headers.get("Content-Type") == "application/json", url
        assert "token=" not in url, "#608: the token must not ride in the URL"


def test_without_a_token_the_page_sends_no_header() -> None:
    posts = _run_page(None)
    assert set(posts) == {_SUBMIT, _REQUEST}
    for url, headers in posts.items():
        assert "X-Quizify-Token" not in headers, url

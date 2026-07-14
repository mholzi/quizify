"""Regression tests for issue #356 (P1 security).

The host-only data endpoints (flags, all-time leaderboard, analytics data,
tts-entities, question-stats, pack submissions) used to be readable by any
client that could reach port 8123 — they carry player names + free-text flag
reasons, per-person play history and HA entity ids. They are now gated on the
admin session token (``?token=`` or the ``X-Quizify-Token`` header); a request
without a valid token gets 401.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from custom_components.quizify.server import views  # noqa: E402
from custom_components.quizify.server.context import APP_CTX_KEY  # noqa: E402

_TOKEN = "valid-admin-token"


class _Conn:
    def validate_admin_token(self, token: str) -> bool:
        return token == _TOKEN


class _Runtime:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir

    async def run_in_executor(self, func, *args):  # noqa: ANN001, ANN002
        return func(*args)


class _Req:
    def __init__(
        self,
        ctx,  # noqa: ANN001
        *,
        query_token: str | None = None,
        header_token: str | None = None,
    ) -> None:
        self.app = {APP_CTX_KEY: ctx}
        self.query: dict[str, str] = {}
        if query_token is not None:
            self.query["token"] = query_token
        self.headers: dict[str, str] = {}
        if header_token is not None:
            self.headers["X-Quizify-Token"] = header_token


def _ctx(tmp_path: Path):
    return SimpleNamespace(
        runtime=_Runtime(tmp_path),
        analytics=None,
        question_stats=None,
        ws_handler=SimpleNamespace(conn=_Conn()),
    )


# The in-file gated handlers that gracefully handle empty deps, so a passing
# gate lands on a normal (non-401) response.
_GATED = [
    views.flag_list_view,
    views.all_time_leaderboard_view,
    views.analytics_data_view,
    views.tts_entities_view,
    # "House Plays Along" entity pickers (#494 Phase 4) — same gate as its
    # tts-entities sibling, since it enumerates the host's light/media/scene
    # entities.
    views.house_entities_view,
    views.question_stats_view,
]


@pytest.mark.parametrize("handler", _GATED)
def test_no_token_is_401(handler, tmp_path: Path) -> None:
    resp = asyncio.run(handler(_Req(_ctx(tmp_path))))
    assert resp.status == 401
    assert json.loads(resp.body) == {"error": "unauthorized"}


@pytest.mark.parametrize("handler", _GATED)
def test_wrong_token_is_401(handler, tmp_path: Path) -> None:
    resp = asyncio.run(handler(_Req(_ctx(tmp_path), query_token="nope")))
    assert resp.status == 401


@pytest.mark.parametrize("handler", _GATED)
def test_valid_query_token_passes(handler, tmp_path: Path) -> None:
    resp = asyncio.run(handler(_Req(_ctx(tmp_path), query_token=_TOKEN)))
    assert resp.status != 401


@pytest.mark.parametrize("handler", _GATED)
def test_valid_header_token_passes(handler, tmp_path: Path) -> None:
    resp = asyncio.run(handler(_Req(_ctx(tmp_path), header_token=_TOKEN)))
    assert resp.status != 401


def test_submissions_wrapper_gates_without_token(tmp_path: Path) -> None:
    # The wrapper must 401 before delegating to the imported handler.
    resp = asyncio.run(views._gated_submissions_list_view(_Req(_ctx(tmp_path))))
    assert resp.status == 401


def test_missing_ws_handler_denies(tmp_path: Path) -> None:
    # Defensive: a ctx without a wired ws_handler must fail closed, not open.
    ctx = SimpleNamespace(runtime=_Runtime(tmp_path), analytics=None)
    resp = asyncio.run(views.flag_list_view(_Req(ctx, query_token=_TOKEN)))
    assert resp.status == 401

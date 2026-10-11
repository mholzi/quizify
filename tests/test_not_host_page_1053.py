"""An admin page that is not the host says so (#1053).

``/quizify/admin`` can be opened by anyone on the network, but only one browser
holds the host token. The server never rejects the others' socket (#359, fail
soft): it withholds the admin role and answers their ``admin_connect`` with
``INVALID_ACTION`` / ``"Admin only"``. The page swallowed that answer as
handshake noise and stayed on its default setup view, so a guest, or the host
on a second device, was looking at a setup and a lobby wired to nothing.

The page now shows a "This device isn't the host" view instead. These tests
hold four things:

* an unrecognised page (no token, or a stale one) lands on that view;
* a recognised host sees what it saw before, frame for frame;
* the very first page on a fresh install is still made host
  (``try_bootstrap_admin``), so "no token in the browser" alone is never read
  as "not the host";
* the view carries no control that changes the game, and the page sends
  nothing but the handshake while it is showing.

The first three run both halves for real: a live aiohttp socket against the
real handler produces the frames, and the real ``admin.js`` routing consumes
exactly those frames under node (``tests/fixtures/dom_stub.js``). Nothing here
asserts on a frame the server was not actually seen to send.
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import subprocess
import sys
from html.parser import HTMLParser
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from custom_components.quizify.game.state import QuizifyGameState  # noqa: E402
from custom_components.quizify.server import WS_PATH  # noqa: E402
from custom_components.quizify.server import websocket as ws_mod  # noqa: E402
from custom_components.quizify.server.connection import ConnectionManager  # noqa: E402
from custom_components.quizify.server.websocket import (  # noqa: E402
    QuizifyWebSocketHandler,
)

_WWW = _REPO / "custom_components" / "quizify" / "www"
_ADMIN_JS = _WWW / "js" / "admin.js"
_ICONS_JS = _WWW / "js" / "icons.js"
_ADMIN_HTML = _WWW / "admin.html"
_I18N = _WWW / "i18n"
_STUB = Path(__file__).resolve().parent / "fixtures" / "dom_stub.js"

_NEEDS_NODE = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not installed"
)

VIEW_IDS = {
    "setup": "setup-screen",
    "lobby": "lobby-screen",
    "game": "game-view",
    "finale": "admin-finale-view",
    "lightning": "admin-lightning-view",
    "lightningRecap": "admin-lightning-recap-view",
    "notHost": "not-host-screen",
}

#: The on-screen text of the chosen design (issue #1053, option 1), verbatim.
DESIGN_EN = {
    "titleLine1": "This device",
    "titleLine2": "isn't the host.",
    "body": "The game is run from another device. You can still play along.",
    "join": "Join as a player",
    "tv": "Open the TV screen",
    "hostTitle": "Are you the host?",
    "hostBefore": (
        "Open this page on the device you started on. "
        "To host from here instead, run"
    ),
    "hostAfter": "in Home Assistant, then reload.",
}


# ---------------------------------------------------------------------------
# The server half: what a page is actually told
# ---------------------------------------------------------------------------


class _FakeRuntime:
    def __init__(self, tmp_path: Path) -> None:
        self.data_dir = tmp_path

    def create_task(self, coro):  # noqa: ANN001, ANN202
        return asyncio.ensure_future(coro)

    async def run_in_executor(self, func, *args):  # noqa: ANN001, ANN002
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, func, *args)


class _Room:
    """One game, one real handler, one live server."""

    def __init__(self, tmp_path: Path) -> None:
        runtime = _FakeRuntime(tmp_path)
        self.game = QuizifyGameState(runtime=runtime, entry_id="test")
        game = self.game
        self.handler = QuizifyWebSocketHandler(
            runtime=runtime, game_state_provider=lambda: game
        )
        self.handler._conn = ConnectionManager(runtime, lambda: game)
        self.client: TestClient | None = None

    async def __aenter__(self) -> _Room:
        app = web.Application()
        app.router.add_get(WS_PATH, self.handler.handle)
        self.client = TestClient(TestServer(app))
        await self.client.start_server()
        return self

    async def __aexit__(self, *exc: object) -> None:
        assert self.client is not None
        await self.client.close()

    async def open_admin_page(self, saved_token: str | None, *, keep: bool = False):
        """Do what ``connect()`` in admin.js does when the socket opens.

        Same frames, same order: ``admin_auth`` when the browser has a token,
        ``admin_connect``, then the four settings the page pushes up front.
        Returns every frame the server answered with, and the socket when
        ``keep`` is set (a host that stays in the room).
        """
        assert self.client is not None
        ws = await self.client.ws_connect(WS_PATH + "?role=admin")
        if saved_token:
            await ws.send_json({"type": "admin_auth", "token": saved_token})
        await ws.send_json({"type": "admin_connect"})
        await ws.send_json({"type": "configure_tts", "enabled": False})
        await ws.send_json({"type": "configure_house", "enabled": False})
        await ws.send_json({"type": "set_language", "language": "es"})
        await ws.send_json({"type": "set_difficulty", "difficulty": "hard"})
        # A marker the server answers in order, so we know we have read
        # everything the frames above produced.
        await ws.send_json({"type": "__end_of_test_script__"})
        frames: list[dict] = []
        for _ in range(200):
            frame = await asyncio.wait_for(ws.receive_json(), timeout=5)
            if frame.get("message") == "Unknown message type":
                break
            frames.append(frame)
        else:  # pragma: no cover - a hang guard, not a path
            raise AssertionError("the server never answered the end marker")
        if keep:
            return frames, ws
        await ws.close()
        return frames, None


def _token_in(frames: list[dict]) -> str | None:
    for frame in frames:
        if frame.get("type") == "game_state" and frame.get("admin_session_token"):
            return frame["admin_session_token"]
    return None


# ---------------------------------------------------------------------------
# The page half: the real admin.js routing, fed those frames
# ---------------------------------------------------------------------------


def _block(source: str, signature: str) -> str:
    """One brace-balanced declaration, or '' when it is not there, so the
    harness still runs against a page without the fix and the tests fail on
    what the visitor sees, not on the fixture."""
    if signature not in source:
        return ""
    start = source.index(signature)
    depth = 0
    seen = False
    for i in range(start, len(source)):
        if source[i] == "{":
            depth += 1
            seen = True
        elif source[i] == "}":
            depth -= 1
            if seen and depth == 0:
                end = i + 1
                if source[end : end + 1] == ";":
                    end += 1
                return source[start:end]
    raise AssertionError(f"unbalanced braces after {signature!r}")


def _shipped_classes() -> dict[str, list[str] | None]:
    """The class list each view ships with in admin.html; None when absent."""
    html = _ADMIN_HTML.read_text("utf-8")
    out: dict[str, list[str] | None] = {}
    for element_id in VIEW_IDS.values():
        m = re.search(rf'<[^>]*\bid="{element_id}"[^>]*>', html)
        if not m:
            out[element_id] = None
            continue
        cls = re.search(r'class="([^"]*)"', m.group(0))
        out[element_id] = cls.group(1).split() if cls else []
    return out


_SCRIPT = r"""
require(%(stub)s);
global.setTimeout = function () {};
global.WebSocket = { OPEN: 1 };
console.warn = function () {};

var CLASSES = %(classes)s;
var VIEW_IDS = %(view_ids)s;
var views = {};
Object.keys(VIEW_IDS).forEach(function (name) {
    var id = VIEW_IDS[name];
    if (CLASSES[id] === null) return;          // not on the page at all
    var el = QZ.el(id);
    CLASSES[id].forEach(function (c) { el.classList.add(c); });
    views[name] = el;
});
// Reset is shown on purpose: the page must HIDE it, not merely leave it be.
var els = { resetGameBtn: QZ.el('reset-game-btn'), endGameBtn: null };

var _redirecting = false;
var WIRE = [];
var ws = { readyState: 1,
           send: function (raw) { WIRE.push(JSON.parse(raw).type); } };
var TOASTS = [];
function showErrorToast(message) { TOASTS.push(message); }
function _LOG_UNDELIVERED() {}
function _t(key) { return key; }
window.QuizifyI18n = { t: function (key) { return key; } };
var HANDLED = [];
function handleGameState(msg) { HANDLED.push(msg); }

%(admin)s

// The socket hands every frame to the same entry point the page wires up.
var deliver = (typeof routeMessage === 'function') ? routeMessage : handleMessage;

function activeViews() {
    return Object.keys(views).filter(function (name) {
        return views[name].classList.contains('active');
    });
}
function snap() {
    return { views: activeViews(),
             resetHidden: els.resetGameBtn.classList.contains('hidden'),
             toasts: TOASTS.slice(),
             handled: HANDLED.length };
}

var OUT = { steps: [] };
%(steps)s
console.log(JSON.stringify(OUT));
"""


def _run_page(steps: list[dict]) -> list[dict]:
    """Feed frame batches / send attempts to the real page code.

    Each step is ``{"frames": [...]}`` or ``{"send": [types...]}``; the
    snapshot after every step comes back in order.
    """
    source = _ADMIN_JS.read_text("utf-8")
    flag = re.search(r"^\s*var _notHost = false;$", source, re.M)
    parts = [
        flag.group(0) if flag else "",
        _block(source, "var NOT_HOST_SENDABLE = {"),
        _block(source, "function isHostRefusal(msg) {"),
        _block(source, "function enterNotHost() {"),
        _block(source, "function leaveNotHost() {"),
        _block(source, "function routeMessage(msg) {"),
        _block(source, "function showView(name) {"),
        _block(source, "function send(type, payload) {"),
        _block(source, "function handleMessage(msg) {"),
    ]
    js_steps = []
    for step in steps:
        if "frames" in step:
            js_steps.append(
                f"{json.dumps(step['frames'])}.forEach(deliver);\n"
                "OUT.steps.push(snap());"
            )
        else:
            js_steps.append(
                "WIRE = [];\n"
                f"{json.dumps(step['send'])}.forEach("
                "function (t) { send(t, {}); });\n"
                "OUT.steps.push({ wire: WIRE.slice(), toasts: TOASTS.slice() });"
            )
    script = _SCRIPT % {
        "stub": json.dumps(str(_STUB)),
        "classes": json.dumps(_shipped_classes()),
        "view_ids": json.dumps(VIEW_IDS),
        "admin": "\n\n".join(p for p in parts if p),
        "steps": "\n".join(js_steps),
    }
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, check=False
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)["steps"]


#: Everything a host page can send that the server treats as a command: the
#: admin-gated half of the dispatch table plus the two that live outside it.
GAME_COMMANDS = sorted(
    {name for name, (_h, admin_only) in QuizifyWebSocketHandler._DISPATCH.items()
     if admin_only}
    | {ws_mod.MSG_RESET_GAME, ws_mod.MSG_START_GAME, "join"}
)


# ---------------------------------------------------------------------------
# 1. An unrecognised page shows the new view
# ---------------------------------------------------------------------------


@_NEEDS_NODE
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "saved_token",
    [None, "11111111-2222-3333-4444-555555555555"],
    ids=["no-token-in-the-browser", "stale-token-in-the-browser"],
)
async def test_a_page_the_server_refuses_shows_not_the_host(
    tmp_path: Path, saved_token: str | None
) -> None:
    async with _Room(tmp_path) as room:
        # The real host arrives first and stays.
        host_frames, host_ws = await room.open_admin_page(None, keep=True)
        host_token = _token_in(host_frames)
        assert host_token, "precondition: the first page is the host"
        language, difficulty = room.game.language, room.game.difficulty

        # Then somebody else opens /quizify/admin.
        frames, _ = await room.open_admin_page(saved_token)

        # What the server told them: the refusal, and no state.
        assert {"type": "error", "code": "INVALID_ACTION"}.items() <= frames[0].items()
        assert frames[0]["message"] == "Admin only"
        assert not [f for f in frames if f.get("type") == "game_state"]
        # ... and not the host's token, in any frame.
        assert host_token not in json.dumps(frames)
        # ... and nothing they sent took effect.
        assert (room.game.language, room.game.difficulty) == (language, difficulty)

        await host_ws.close()

    (after,) = _run_page([{"frames": frames}])

    assert after["views"] == ["notHost"], (
        "an admin page the server refused must show the not-the-host view "
        f"instead of setup or lobby; it shows {after['views']}"
    )
    assert after["resetHidden"] is True, "Reset must not be offered to a non-host"
    assert after["toasts"] == [], (
        "the refusals of what the page sent before it knew are not the "
        "visitor's errors and must not pop up as toasts"
    )
    assert after["handled"] == 0


# ---------------------------------------------------------------------------
# 2. A recognised host sees what it saw before
# ---------------------------------------------------------------------------


@_NEEDS_NODE
@pytest.mark.asyncio
async def test_the_first_page_on_a_fresh_install_is_still_made_host(
    tmp_path: Path,
) -> None:
    """No token anywhere: the bootstrap path. The browser is exactly as empty
    as a guest's, which is why the page may not decide from its own storage."""
    async with _Room(tmp_path) as room:
        frames, _ = await room.open_admin_page(None)
        assert room.handler._conn.validate_admin_token(_token_in(frames) or "")

    assert _token_in(frames), "the bootstrap page must be handed the host token"
    (after,) = _run_page([{"frames": frames}])

    assert after["views"] == ["setup"], "the host's page starts on setup, as before"
    assert after["handled"] == len(
        [f for f in frames if f.get("type") == "game_state"]
    ), "every state frame must reach handleGameState untouched"


@_NEEDS_NODE
@pytest.mark.asyncio
async def test_the_host_coming_back_with_its_token_is_recognised(
    tmp_path: Path,
) -> None:
    async with _Room(tmp_path) as room:
        first, _ = await room.open_admin_page(None)
        token = _token_in(first)
        assert token
        # A reload: the token is no longer anybody's bootstrap, it has to be
        # presented in the admin_auth frame.
        frames, _ = await room.open_admin_page(token)
        # The settings the host page pushes on connect did land.
        assert (room.game.language, room.game.difficulty) == ("es", "hard")

    assert _token_in(frames) == token
    steps = _run_page([{"frames": frames}, {"send": GAME_COMMANDS}])

    assert steps[0]["views"] == ["setup"]
    assert steps[0]["handled"] >= 1
    assert steps[1]["wire"] == GAME_COMMANDS, (
        "a recognised host's commands must all go out, exactly as before"
    )


@_NEEDS_NODE
def test_frames_pass_through_to_the_old_router_unchanged_for_a_host() -> None:
    """Every frame type the router knows, in one go: nothing is dropped,
    reordered or shown differently while the page is the host."""
    frames = [
        {"type": "game_state", "phase": "LOBBY", "admin_session_token": "t"},
        {"type": "game_state", "phase": "QUESTION_ACTIVE"},
        {"type": "error", "code": "ADMIN_REQUIRED", "message": "x"},
        {"type": "error", "code": "INVALID_ACTION", "message": "Malformed message"},
        {"type": "game_state", "phase": "FINALE"},
    ]
    (after,) = _run_page([{"frames": frames}])

    assert after["views"] == ["setup"]
    assert after["handled"] == 3
    assert after["toasts"] == ["x", "Malformed message"], (
        "a refused command must stay as visible to the host as #586 made it"
    )


# ---------------------------------------------------------------------------
# 3. The new state changes nothing in the game
# ---------------------------------------------------------------------------

_REFUSAL = {"type": "error", "code": "INVALID_ACTION", "message": "Admin only"}


@_NEEDS_NODE
def test_a_refused_page_sends_the_handshake_and_nothing_else() -> None:
    steps = _run_page(
        [
            {"frames": [_REFUSAL]},
            {"send": GAME_COMMANDS},
            {"send": ["admin_auth", "admin_connect"]},
        ]
    )

    assert steps[0]["views"] == ["notHost"]
    assert steps[1]["wire"] == [], (
        f"a page that is not the host put {steps[1]['wire']} on the wire"
    )
    assert steps[1]["toasts"] == [], "and it must not complain about not sending"
    assert steps[2]["wire"] == ["admin_auth", "admin_connect"], (
        "the handshake is how the page finds its way back; it must still go out"
    )


@_NEEDS_NODE
def test_only_the_hosts_own_connect_frame_ends_the_state() -> None:
    steps = _run_page(
        [
            {"frames": [_REFUSAL]},
            # A broadcast, a refusal and a second "Admin only" do not.
            {
                "frames": [
                    {"type": "game_state", "phase": "FINALE"},
                    {"type": "error", "code": "ADMIN_REQUIRED", "message": "x"},
                    {"type": "player_joined", "name": "Guest"},
                    _REFUSAL,
                ]
            },
            # The frame only a recognised host is sent does (e.g. after
            # quizify.reset_admin_session and a reconnect).
            {
                "frames": [
                    {"type": "game_state", "phase": "LOBBY", "admin_session_token": "t"}
                ]
            },
            {"send": ["reset_game"]},
        ]
    )

    assert steps[1]["views"] == ["notHost"]
    assert steps[1]["handled"] == 0
    assert steps[1]["toasts"] == []
    assert steps[2]["views"] == ["setup"]
    assert steps[2]["handled"] == 1
    assert steps[3]["wire"] == ["reset_game"]


class _Subtree(HTMLParser):
    """Everything inside the element with one id, as (tag, attrs) and text."""

    _VOID = {"br", "img", "input", "hr", "meta", "link"}

    def __init__(self, element_id: str) -> None:
        super().__init__(convert_charrefs=True)
        self._id = element_id
        self._depth = 0
        self.tags: list[tuple[str, dict[str, str | None]]] = []
        self.text: list[str] = []

    def handle_starttag(self, tag, attrs):  # noqa: ANN001, ANN201
        attributes = dict(attrs)
        if self._depth == 0:
            if attributes.get("id") != self._id:
                return
            self._depth = 1
            return
        self.tags.append((tag, attributes))
        if tag not in self._VOID:
            self._depth += 1

    def handle_endtag(self, tag):  # noqa: ANN001, ANN201
        if self._depth and tag not in self._VOID:
            self._depth -= 1

    def handle_data(self, data):  # noqa: ANN001, ANN201
        if self._depth and data.strip():
            self.text.append(" ".join(data.split()))


def _not_host_view() -> _Subtree:
    parser = _Subtree(VIEW_IDS["notHost"])
    parser.feed(_ADMIN_HTML.read_text("utf-8"))
    return parser


def test_the_view_holds_no_control_only_two_plain_links() -> None:
    view = _not_host_view()
    assert view.tags, "admin.html has no #not-host-screen"

    controls = {"button", "input", "select", "textarea", "form", "option"}
    used = {tag for tag, _ in view.tags}
    assert not used & controls, f"the not-the-host view contains {used & controls}"
    for tag, attrs in view.tags:
        handlers = [name for name in attrs if name.startswith("on")]
        assert not handlers, f"<{tag}> carries an inline handler: {handlers}"
        assert attrs.get("role") != "button"

    links = [attrs for tag, attrs in view.tags if tag == "a"]
    assert [a.get("href") for a in links] == ["/quizify/player", "/quizify/dashboard"]

    # And nothing in the page script reaches into the view to give those
    # links, or anything else in it, a behaviour.
    source = _ADMIN_JS.read_text("utf-8")
    for _tag, attrs in view.tags:
        if attrs.get("id"):
            assert attrs["id"] not in source, f"admin.js wires #{attrs['id']}"


def test_the_view_shows_the_host_token_nowhere() -> None:
    """The only way back it names is the Home Assistant service."""
    view = _not_host_view()
    assert "quizify.reset_admin_session" in view.text
    markup = " ".join(view.text).lower()
    assert "token" not in markup
    source = _ADMIN_JS.read_text("utf-8")
    body = _block(source, "function enterNotHost() {")
    assert body and "Token" not in body and "token" not in body


def test_the_view_starts_hidden() -> None:
    """A host must never see it flash: setup is the page's default view."""
    classes = _shipped_classes()
    assert classes[VIEW_IDS["notHost"]] == ["view"]
    assert "active" in (classes[VIEW_IDS["setup"]] or [])


# ---------------------------------------------------------------------------
# 4. The words
# ---------------------------------------------------------------------------


def test_english_is_the_chosen_design_word_for_word() -> None:
    bundle = json.loads((_I18N / "en.json").read_text("utf-8"))
    assert bundle.get("notHost") == DESIGN_EN


@pytest.mark.parametrize("code", ["de", "es"])
def test_the_other_languages_carry_every_string(code: str) -> None:
    bundle = json.loads((_I18N / f"{code}.json").read_text("utf-8"))
    strings = bundle.get("notHost") or {}
    assert set(strings) == set(DESIGN_EN)
    for key, text in strings.items():
        assert text.strip(), key
        assert text != DESIGN_EN[key], f"{code}.notHost.{key} is still English"
        # Similar length, so the layout proven in English holds (the phone
        # column is 390px wide).
        assert len(text) <= len(DESIGN_EN[key]) * 1.6 + 8, (code, key, text)


def test_every_string_in_the_view_goes_through_i18n() -> None:
    view = _not_host_view()
    keys = [a["data-i18n"] for _t, a in view.tags if a.get("data-i18n")]
    assert sorted(keys) == sorted(f"notHost.{k}" for k in DESIGN_EN)
    # The markup defaults are the English bundle, so a page whose bundle has
    # not arrived yet already reads correctly.
    for text in DESIGN_EN.values():
        assert text in view.text
    # The service name is an identifier, not prose: it is the one literal.
    literal = [t for t in view.text if t not in DESIGN_EN.values()]
    assert literal == ["quizify.reset_admin_session"]


def test_the_lock_icon_is_in_the_shared_icon_set() -> None:
    view = _not_host_view()
    icons = [a.get("data-ui-icon") for _t, a in view.tags if a.get("data-ui-icon")]
    assert icons == ["lock"]
    assert re.search(r"^\s*lock:\s*'<", _ICONS_JS.read_text("utf-8"), re.M)

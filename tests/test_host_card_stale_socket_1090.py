"""A detached socket must not kill the host card's live one (#1090).

Lovelace detaches and re-attaches cards on view switches and reflows (a wall
tablet rotating is enough). ``disconnectedCallback`` closed the old socket and
``connectedCallback`` opened a new one at once, but the old socket's close
event fired later and its handler ran ``self._ws = null`` without checking
which socket it belonged to. The new socket kept rendering ``game_state``, so
the buttons looked live, while ``_send`` returned early on ``!this._ws`` — Next
and Skip did nothing until a page reload.

The card is loaded into node with a minimal DOM/WebSocket stub and driven
through detach -> re-attach -> late close of the old socket.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
CARD_JS = (
    REPO / "custom_components" / "quizify" / "www" / "cards" / "quizify-host-card.js"
)

_NEEDS_NODE = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not installed"
)

_SCRIPT = r"""
const fs = require('fs');
const sockets = [];
class FakeWS {
    constructor(url) {
        this.url = url; this.readyState = 0; this.sent = []; sockets.push(this);
    }
    send(d) { this.sent.push(JSON.parse(d)); }
    close() { this.readyState = 3; this.closeCalled = true; }
    open() { this.readyState = 1; if (this.onopen) this.onopen(); }
    fireClose() { this.readyState = 3; if (this.onclose) this.onclose(); }
}
class HTMLElement {
    attachShadow() {
        this.shadowRoot = {
            innerHTML: '',
            querySelector() { return null; },
            querySelectorAll() { return []; },
            getElementById() { return null; },
        };
        return this.shadowRoot;
    }
}
const registry = {};
global.HTMLElement = HTMLElement;
global.WebSocket = FakeWS;
global.location = { protocol: 'http:', host: 'ha.local' };
global.document = { createElement() { return {}; }, head: { appendChild() {} } };
global.window = global;
window.customElements = {
    get(t) { return registry[t]; },
    define(t, c) { registry[t] = c; },
};
window.QuizifyUtils = { readAdminToken() { return 'tok'; }, writeAdminToken() {} };
window.customCards = [];
eval(fs.readFileSync(PATH, 'utf8'));

const tick = () => new Promise(r => setTimeout(r, 0));
(async () => {
    const Card = registry['quizify-host-card'];
    const card = new Card();
    card.connectedCallback();
    await tick();
    const first = sockets[0];
    first.open();

    card.disconnectedCallback();   // Lovelace detaches ...
    card.connectedCallback();      // ... and re-attaches right away
    await tick();
    const second = sockets[1];
    second.open();

    first.fireClose();             // the old socket's close lands late
    const state = { type: 'game_state', phase: 'ANSWER_REVEAL' };
    second.onmessage({ data: JSON.stringify(state) });
    card._send('next_question');

    console.log(JSON.stringify({
        sockets: sockets.length,
        firstClosed: !!first.closeCalled,
        liveIsSecond: card._ws === second,
        status: card._status,
        sentOnSecond: second.sent.map(m => m.type),
    }));
})().catch(e => { console.error(e); process.exit(1); });
"""


def _run() -> dict:
    script = _SCRIPT.replace("PATH", json.dumps(str(CARD_JS)))
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, check=True
    )
    return json.loads(out.stdout.strip().splitlines()[-1])


@pytest.fixture(scope="module")
def result() -> dict:
    return _run()


@_NEEDS_NODE
def test_reattach_opens_a_new_socket_and_closes_the_old(result: dict) -> None:
    assert result["sockets"] == 2
    assert result["firstClosed"] is True


@_NEEDS_NODE
def test_late_close_of_the_old_socket_leaves_the_live_one(result: dict) -> None:
    assert result["liveIsSecond"] is True
    assert result["status"] == "online"


@_NEEDS_NODE
def test_buttons_still_send_after_reattach(result: dict) -> None:
    assert result["sentOnSecond"] == ["admin_auth", "admin_connect", "next_question"]

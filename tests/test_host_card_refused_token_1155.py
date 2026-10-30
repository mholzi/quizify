"""A refused admin token must show the link note, not Connecting (#1155).

With a stale stored token the server answers ``admin_connect`` with
INVALID_ACTION / "Admin only" and refuses commands with ADMIN_REQUIRED. The
card dropped those frames: it stayed on a disabled "Connecting..." and, once a
``game_state`` broadcast arrived, showed live buttons that fail silently. It
must render the existing "Open Quizify once" note with the admin link, and a
granted token (``admin_session_token``) must bring the live card back.
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
const GS = { type: 'game_state', phase: 'LOBBY', players: [{ name: 'A' }] };
async function run(frames) {
    sockets.length = 0;
    const Card = registry['quizify-host-card'];
    const card = new Card();
    card.connectedCallback();
    await tick();
    const ws = sockets[0];
    ws.open();
    for (const f of frames) ws.onmessage({ data: JSON.stringify(f) });
    const html = card.shadowRoot.innerHTML;
    return { note: html.includes('<p class="note">'), link: html.includes('href='),
             buttons: html.includes('<button') };
}
(async () => {
    const ERR = { type: 'error', code: 'INVALID_ACTION', message: 'Admin only' };
    console.log(JSON.stringify({
        handshake: await run([ERR]),
        handshakeThenState: await run([ERR, GS]),
        commandRefused: await run([GS, { type: 'error', code: 'ADMIN_REQUIRED' }]),
        otherError: await run([
            { type: 'error', code: 'INVALID_ACTION', message: 'x' }, GS]),
        granted: await run([ERR, { type: 'ok', admin_session_token: 't2' }, GS]),
        allowed: await run([GS]),
    }));
})().catch(e => { console.error(e); process.exit(1); });
"""


@pytest.fixture(scope="module")
def result() -> dict:
    script = _SCRIPT.replace("PATH", json.dumps(str(CARD_JS)))
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, check=True
    )
    return json.loads(out.stdout.strip().splitlines()[-1])


@_NEEDS_NODE
@pytest.mark.parametrize("case", ["handshake", "handshakeThenState", "commandRefused"])
def test_refusal_renders_link_note_without_buttons(result: dict, case: str) -> None:
    assert result[case]["note"] is True
    assert result[case]["link"] is True
    assert result[case]["buttons"] is False


@_NEEDS_NODE
@pytest.mark.parametrize("case", ["otherError", "granted", "allowed"])
def test_allowed_or_unrelated_frames_keep_the_live_card(
    result: dict, case: str
) -> None:
    assert result[case]["note"] is False
    assert result[case]["buttons"] is True

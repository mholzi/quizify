"""The Lovelace host card tells the truth about the room (#1182, L1/L2).

L1: the card took players from ``game_state`` alone. A lobby join produces
only the coalesced ``player_joined`` frame (#453), so after two joins the card
still read "0 players · Waiting for players" with Start locked while the
television listed both guests — measured live in the states review.

L2: with the socket gone the card kept the old round, roster and scores as if
current, End and Reset stayed clickable while ``_send()`` returned silently,
it stopped retrying after ten attempts with no way back, and a rejected
``quizify.start_game`` service call went unheard because the ``try`` only
caught a synchronous throw.

These run the real card under node with the same small fakes as
``test_host_card_refused_token_1155.py``.
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

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not installed"
)

_SCRIPT = r"""
const fs = require('fs');
const sockets = [];
const timers = [];
class FakeWS {
    constructor(url) { this.url = url; this.readyState = 0; this.sent = []; sockets.push(this); }
    send(d) { this.sent.push(JSON.parse(d)); }
    close() { this.readyState = 3; }
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
global.location = { protocol: 'http:', host: 'ha.local', origin: 'http://ha.local' };
global.document = {
    hidden: false,
    createElement() { return { dataset: {}, addEventListener() {} }; },
    head: { appendChild() {} },
    querySelector() { return null; },
};
global.window = global;
window.customElements = { get(t) { return registry[t]; }, define(t, c) { registry[t] = c; } };
window.QuizifyUtils = { readAdminToken() { return 'tok'; }, writeAdminToken() {} };
window.customCards = [];
const realSetTimeout = setTimeout;
global.setTimeout = (fn, ms) => { timers.push(ms); return timers.length; };
global.clearTimeout = () => {};
eval(fs.readFileSync(PATH, 'utf8'));

const tick = () => new Promise(r => realSetTimeout(r, 0));
const LOBBY = { type: 'game_state', phase: 'LOBBY', round: 0, total_rounds: 10, players: [] };
const GAME = { type: 'game_state', phase: 'ANSWER_REVEAL', round: 4, total_rounds: 10,
               players: [{ name: 'Lena', connected: true, score: 2300 }],
               leaderboard: [{ name: 'Lena', score: 2300 }] };

async function card(mode) {
    sockets.length = 0;
    const Card = registry['quizify-host-card'];
    const c = new Card();
    c.setConfig({ mode: mode || 'expanded' });
    c.connectedCallback();
    await tick();
    sockets[0].open();
    return c;
}
const send = (c, f) => sockets[sockets.length - 1].onmessage({ data: JSON.stringify(f) });
const html = c => c.shadowRoot.innerHTML;

(async () => {
    const out = {};

    // L1: roster frames reach the card.
    let c = await card();
    send(c, LOBBY);
    out.beforeJoin = { zero: html(c).includes('0 players'), startDisabled: html(c).includes('id="primary" disabled') };
    send(c, { type: 'player_joined', players: [{ name: 'Lena', connected: true }, { name: 'Ben', connected: true }], teams: [] });
    out.afterJoin = { lena: html(c).includes('Lena'), ben: html(c).includes('Ben'),
                      two: html(c).includes('· 2 players'),
                      startDisabled: html(c).includes('id="primary" disabled') };
    send(c, { type: 'player_left', players: [{ name: 'Ben', connected: true }], teams: [] });
    out.afterLeave = { lena: html(c).includes('>Lena<'), one: html(c).includes('· 1 player<') };

    // L2: offline locks the controls and says so.
    c = await card();
    send(c, GAME);
    out.online = { endEnabled: html(c).includes('data-ask="end">'), line: html(c).includes('No connection to Quizify') };
    sockets[0].fireClose();
    const h = html(c);
    out.offline = {
        line: h.includes('No connection to Quizify. We keep trying.'),
        locked: h.includes('End and Reset are locked'),
        endDisabled: h.includes('data-ask="end" disabled'),
        resetDisabled: h.includes('data-ask="reset" disabled'),
        primaryDisabled: h.includes('id="primary" disabled'),
        stamp: /As of \d\d:\d\d/.test(h),
        connectNowEarly: h.includes('id="reconnect"'),
        roundMetaGone: !h.includes('Question 4 of 10'),
    };
    c._retryDelay = 30000; c._render();
    out.offline.connectNowLate = html(c).includes('id="reconnect"');
    const sentBefore = sockets[0].sent.length;
    c._send('end_game');
    out.offline.sendIsNoop = sockets[0].sent.length === sentBefore;

    // L11 on the card: no attempt cap.
    c = await card();
    timers.length = 0;
    for (let i = 0; i < 20; i++) { c._retry = null; c._ws = null; c._scheduleRetry(); }
    out.retries = { count: timers.length, last: timers[timers.length - 1], max: Math.max.apply(null, timers) };

    // callService rejections are caught.
    c = await card('compact');
    send(c, Object.assign({}, LOBBY, { players: [{ name: 'Lena', connected: true }] }));
    c.hass = { language: 'en', callService() { return Promise.reject({ message: 'Quizify is not ready yet.' }); } };
    c._startGame();
    await tick(); await tick();
    out.rejectedWithText = { shown: html(c).includes('Quizify is not ready yet.'),
                             socketStart: sockets[0].sent.some(m => m.type === 'start_game') };
    c.hass = { language: 'en', callService() { return Promise.reject(new Error('')); } };
    c._startGame();
    await tick(); await tick();
    out.rejectedBare = { socketStart: sockets[0].sent.some(m => m.type === 'start_game') };

    console.log(JSON.stringify(out));
})().catch(e => { console.error(e); process.exit(1); });
"""


@pytest.fixture(scope="module")
def result() -> dict:
    script = _SCRIPT.replace("PATH", json.dumps(str(CARD_JS)))
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, check=True
    )
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_a_lobby_join_reaches_the_card(result: dict) -> None:
    assert result["beforeJoin"] == {"zero": True, "startDisabled": True}
    assert result["afterJoin"] == {
        "lena": True,
        "ben": True,
        "two": True,
        "startDisabled": False,
    }


def test_a_lobby_leave_reaches_the_card(result: dict) -> None:
    assert result["afterLeave"] == {"lena": False, "one": True}


def test_offline_locks_end_and_reset_and_says_so(result: dict) -> None:
    # #1183: end and reset open the confirm sheet (data-ask) instead of sending.
    assert result["online"] == {"endEnabled": True, "line": False}
    off = result["offline"]
    assert off["line"] and off["locked"]
    assert off["endDisabled"] and off["resetDisabled"] and off["primaryDisabled"]
    assert off["sendIsNoop"]


def test_offline_marks_old_numbers_with_their_age(result: dict) -> None:
    assert result["offline"]["stamp"] is True
    assert result["offline"]["roundMetaGone"] is True


def test_connect_now_appears_from_the_thirty_second_wait(result: dict) -> None:
    assert result["offline"]["connectNowEarly"] is False
    assert result["offline"]["connectNowLate"] is True


def test_the_card_never_stops_retrying(result: dict) -> None:
    assert result["retries"]["count"] == 20
    assert result["retries"]["max"] == 30000
    assert result["retries"]["last"] == 30000


def test_a_rejected_start_is_shown_not_swallowed(result: dict) -> None:
    assert result["rejectedWithText"] == {"shown": True, "socketStart": False}


def test_a_bare_rejection_falls_back_to_the_socket(result: dict) -> None:
    assert result["rejectedBare"] == {"socketStart": True}

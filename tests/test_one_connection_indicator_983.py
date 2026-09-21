"""One connection dot, painted in one place (#983).

The indicator existed twice — `admin.js` and `player-utils.js` — with the same
`#conn-status` id, the same inline `cssText` and the same three hex values the
stylesheet already had names for. The copies drifted in opposite directions:
the phone received the accessibility work from #424 (a shape next to the hue,
and the polite `#conn-status-announce` live region), the host page received the
manual retry from #290. Neither received the other's, so a host could not hear
a dropped connection and a guest could not force a reconnect — and nobody had
decided either way.

It now lives in `client-core.js`, which both pages already load through
`common.bundle.js` (#787), parameterised by the one thing that is genuinely
per-surface: what "retry" *does*. The tests below run the real module against
the real i18n bundles and press the real dot.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
WWW = REPO / "custom_components" / "quizify" / "www"
JS = WWW / "js"
CSS = WWW / "css"
STUB = Path(__file__).resolve().parent / "fixtures" / "dom_stub.js"

_NEEDS_NODE = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not installed"
)


def _core_indicator_block() -> str:
    src = (JS / "client-core.js").read_text("utf-8")
    return src[src.index("// Connection indicator") :]


# ---------------------------------------------------------------------------
# One owner
# ---------------------------------------------------------------------------


def test_neither_surface_paints_the_dot_itself() -> None:
    """A second `getElementById('conn-status')` is how the drift started."""
    for name in ("admin.js", "player-utils.js"):
        src = (JS / name).read_text("utf-8")
        assert "getElementById('conn-status')" not in src, (
            f"{name} still builds its own connection indicator (#983)"
        )
        assert "QuizifyClientCore.updateConnectionIndicator" in src, (
            f"{name} must call the shared indicator (#983)"
        )


def test_the_palette_comes_from_the_stylesheet() -> None:
    """The three hues and the halo are declared once, in the tokens."""
    block = _core_indicator_block()
    for hex_value in ("#7FA897", "#E8C47F", "#D66A6A", "#6E6A5C"):
        assert hex_value not in block, (
            f"{hex_value} is hard-coded in the indicator; "
            "www/css/src/00-tokens.css already names it (#983)"
        )

    tokens = (CSS / "src" / "00-tokens.css").read_text("utf-8")
    sheet = (CSS / "styles.css").read_text("utf-8")
    for var in (
        "--color-success",
        "--color-warning",
        "--color-error",
        "--color-text-muted",
        "--color-success-glow",
        "--color-warning-glow",
        "--color-error-glow",
        "--color-text-muted-glow",
    ):
        assert f"var({var})" in block, f"the indicator should read {var}"
        assert f"{var}:" in tokens, f"{var} is not declared in the tokens"
        assert f"{var}:" in sheet, f"{var} is missing from the built styles.css"


def test_the_host_page_carries_the_live_region() -> None:
    """The half of #424 the host never got."""
    html = (WWW / "admin.html").read_text("utf-8")
    idx = html.index('id="conn-status-announce"')
    frag = html[idx - 160 : idx + 160]
    assert 'aria-live="polite"' in frag
    assert 'role="status"' in frag
    assert "sr-only" in frag


def test_the_phone_registers_a_retry_action() -> None:
    """The half of #290 the phone never got — and it is the same function the
    connection-lost view's button already calls, so the two cannot drift."""
    core = (JS / "player-core.js").read_text("utf-8")
    assert "pu.setConnectionRetry(retryConnection)" in core
    assert "retryBtn.addEventListener('click', retryConnection)" in core
    # Being removed by the host is not a connection problem (#750).
    assert "{ noRetry: true }" in core


# ---------------------------------------------------------------------------
# What the merged dot does
# ---------------------------------------------------------------------------


_SCRIPT = """
require({stub});
QZ.serveI18n({i18n});
QZ.load({i18njs});
QZ.els(['conn-status', 'conn-status-announce']);
QZ.load({core});

var dot = document.getElementById('conn-status');
var announce = document.getElementById('conn-status-announce');

function snap() {{
    return {{
        text: dot.textContent,
        html: dot.innerHTML,
        role: dot.getAttribute('role'),
        cursor: dot.style.cursor,
        announced: announce.textContent,
        key: announce.getAttribute('data-i18n')
    }};
}}

(async function () {{
    await window.QuizifyI18n.init('en');
    var core = window.QuizifyClientCore;
    var taps = 0;
    var retry = function () {{ taps++; }};

    core.updateConnectionIndicator('connected', {{ retryHandler: retry }});
    var connected = snap();

    core.updateConnectionIndicator('reconnecting', {{ retryHandler: retry }});
    var reconnecting = snap();

    core.updateConnectionIndicator('disconnected', {{ retryHandler: retry }});
    var offered = snap();
    dot.click();
    var tapsAfterOffer = taps;

    core.updateConnectionIndicator('connected', {{ retryHandler: retry }});
    var recovered = snap();
    dot.click();
    var tapsAfterRecovery = taps;

    core.updateConnectionIndicator('disconnected', {{}});
    var readOnly = snap();
    dot.click();
    var tapsAfterReadOnly = taps;

    console.log(JSON.stringify({{
        connected: connected,
        reconnecting: reconnecting,
        offered: offered,
        tapsAfterOffer: tapsAfterOffer,
        recovered: recovered,
        tapsAfterRecovery: tapsAfterRecovery,
        readOnly: readOnly,
        tapsAfterReadOnly: tapsAfterReadOnly
    }}));
}})();
"""


def _run() -> dict:
    script = _SCRIPT.format(
        stub=json.dumps(str(STUB)),
        i18n=json.dumps(str(WWW / "i18n")),
        i18njs=json.dumps(str(JS / "i18n.js")),
        core=json.dumps(str(JS / "client-core.js")),
    )
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, check=True
    )
    return json.loads(out.stdout)


@_NEEDS_NODE
def test_connected_is_a_bare_dot() -> None:
    """Nothing to say and nothing to press while the socket is up."""
    result = _run()

    assert result["connected"]["text"] == ""
    assert result["connected"]["role"] is None
    assert result["connected"]["announced"] == "Connected"
    assert result["connected"]["key"] == "connection.connected"


@_NEEDS_NODE
def test_reconnecting_says_so_in_three_ways() -> None:
    """Hue for the glance, a glyph for the colour-blind (#424), a word for the
    host who wants to know whether the tablet is still trying (#290)."""
    result = _run()

    assert "…" in result["reconnecting"]["text"]
    assert "Reconnecting" in result["reconnecting"]["text"]
    assert result["reconnecting"]["announced"] == "Reconnecting..."
    assert result["reconnecting"]["key"] == "connection.reconnecting"
    # The label is written from JS, so it carries the key a late language
    # switch re-renders from (#783).
    assert 'data-i18n="connection.reconnecting"' in result["reconnecting"]["html"]


@_NEEDS_NODE
def test_a_surface_with_a_retry_gets_a_button() -> None:
    result = _run()

    assert "Retry connection" in result["offered"]["text"]
    assert "⊘" in result["offered"]["text"]
    assert result["offered"]["role"] == "button"
    assert result["offered"]["cursor"] == "pointer"
    assert result["tapsAfterOffer"] == 1
    # The visible label is the affordance; the announcement stays the state.
    assert result["offered"]["announced"] == "Disconnected"
    assert result["offered"]["key"] == "connection.disconnected"


@_NEEDS_NODE
def test_the_button_does_not_outlive_the_outage() -> None:
    """The host's copy set role="button" and never took it off again: a dot
    that claims to be a control after the socket is back is a control that
    does nothing."""
    result = _run()

    assert result["recovered"]["role"] is None
    assert result["recovered"]["cursor"] == ""
    assert result["tapsAfterRecovery"] == 1


@_NEEDS_NODE
def test_without_a_handler_the_dot_states_rather_than_offers() -> None:
    """A phone the host has removed has nothing to retry — it reads the state
    instead, and a tap does nothing."""
    result = _run()

    assert "Disconnected" in result["readOnly"]["text"]
    assert "Retry" not in result["readOnly"]["text"]
    assert result["readOnly"]["role"] is None
    assert result["tapsAfterReadOnly"] == 1

"""A failed fetch is an error, never "nothing there" (#1182, L5/L6/L7).

L5 analytics: only 401 was checked, so a 500 with a JSON body ran on as data
and every tile said "no data"; a later failure wrote into a hidden element;
the reported-questions section hid itself on an error exactly as if nothing
had been reported.

L6 speaker/light lists: ``_populateEntitySelect(sel, null)`` treated a failed
fetch like an empty list — "none found" on a host with 72 lights.

L7 pack submission: a failed "my submissions" request hid the card ("you never
submitted anything"), and an unreachable config hid the whole section as if
the feature were switched off.

The analytics page runs for real under node (its inline script, the shared DOM
stub, a fake ``fetch``). The admin and pack-submit parts are checked on the
function bodies that decide it.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_WWW = _REPO / "custom_components" / "quizify" / "www"
_STUB = Path(__file__).resolve().parent / "fixtures" / "dom_stub.js"

_NEEDS_NODE = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not installed"
)


def _analytics_script() -> str:
    html = (_WWW / "analytics.html").read_text("utf-8")
    m = re.search(r"<script>\s*(\(function\(\) \{.*?\}\)\(\);)\s*</script>", html, re.S)
    assert m, "analytics.html inline script not found"
    return m.group(1)


_SCRIPT = r"""
require(STUB);
QZ.els(['loading', 'content', 'empty', 'load-error', 'load-error-detail', 'load-retry',
        'stale-note', 'flags-section', 'flags-error', 'flags-retry', 'flags-card',
        'flagged-questions', 'total-games', 'avg-players', 'total-rounds', 'chart',
        'top-players', 'category-stats', 'recent-games']);
['load-error', 'stale-note', 'flags-error'].forEach(function (id) {
    document.getElementById(id).hidden = true;
});
global.setTimeout = window.setTimeout = function (fn) { fn(); return 1; };
window.QuizifyUtils = { readAdminToken: function () { return 'tok'; } };
window.QuizifyI18n = { t: function (k, p) { return p ? k + JSON.stringify(p) : k; } };
var calls = [];
var plan = PLAN;
global.fetch = window.fetch = function (url) {
    var key = url.indexOf('/flags') !== -1 ? 'flags' : 'data';
    calls.push(key);
    var r = plan[key].length > 1 ? plan[key].shift() : plan[key][0];
    return Promise.resolve({
        status: r.status, ok: r.status >= 200 && r.status < 300,
        json: function () { return Promise.resolve(r.body); }
    });
};
PAGE
setImmediate(function () { setImmediate(function () { setImmediate(function () {
    var g = function (id) { return document.getElementById(id); };
    console.log(JSON.stringify({
        errorShown: !g('load-error').hidden,
        detail: g('load-error-detail').textContent,
        emptyShown: g('empty').style.display === 'block',
        contentShown: g('content').style.display === 'block',
        dataCalls: calls.filter(function (c) { return c === 'data'; }).length,
        flagsError: !g('flags-error').hidden,
        flagsSection: g('flags-section').style.display
    }));
}); }); });
"""


def _run(plan: dict) -> dict:
    script = (
        _SCRIPT.replace("STUB", json.dumps(str(_STUB)))
        .replace("PLAN", json.dumps(plan))
        .replace("PAGE", _analytics_script())
    )
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, check=True
    )
    return json.loads(out.stdout.strip().splitlines()[-1])


_OK = {
    "status": 200,
    "body": {
        "total_games": 3,
        "avg_players_per_game": 2,
        "total_rounds": 30,
        "top_players": [],
        "category_stats": [],
        "recent_games": [],
        "chart_data": {"labels": [], "values": []},
    },
}
_EMPTY = {"status": 200, "body": dict(_OK["body"], total_games=0)}
_FLAGS_NONE = {"status": 200, "body": {"flags": []}}


@_NEEDS_NODE
def test_a_500_with_a_json_body_is_an_error_not_empty() -> None:
    r = _run({"data": [{"status": 500, "body": {"error": "boom"}}], "flags": [_FLAGS_NONE]})
    assert r["errorShown"] is True
    assert r["emptyShown"] is False
    assert "500" in r["detail"]
    assert r["dataCalls"] == 2, "one quiet retry after 2 s, then the error"


@_NEEDS_NODE
def test_a_json_error_body_with_200_is_an_error_too() -> None:
    r = _run({"data": [{"status": 200, "body": {"error": "nope"}}], "flags": [_FLAGS_NONE]})
    assert r["errorShown"] is True
    assert r["emptyShown"] is False


@_NEEDS_NODE
def test_the_quiet_retry_recovers_without_showing_an_error() -> None:
    r = _run({"data": [{"status": 503, "body": {}}, _OK], "flags": [_FLAGS_NONE]})
    assert r["errorShown"] is False
    assert r["contentShown"] is True


@_NEEDS_NODE
def test_a_real_empty_answer_is_still_the_friendly_empty_state() -> None:
    r = _run({"data": [_EMPTY], "flags": [_FLAGS_NONE]})
    assert r["emptyShown"] is True
    assert r["errorShown"] is False


@_NEEDS_NODE
def test_a_failed_flags_request_shows_its_error() -> None:
    r = _run({"data": [_OK], "flags": [{"status": 401, "body": {"error": "unauthorized"}}]})
    assert r["flagsError"] is True
    assert r["flagsSection"] == "block"


@_NEEDS_NODE
def test_no_flags_still_hides_the_section() -> None:
    r = _run({"data": [_OK], "flags": [_FLAGS_NONE]})
    assert r["flagsError"] is False
    assert r["flagsSection"] == "none"


# --- L6 / L7 ----------------------------------------------------------------


def _body(path: Path, signature: str) -> str:
    src = path.read_text("utf-8")
    start = src.index(signature)
    depth = 0
    for i in range(src.index("{", start), len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start : i + 1]
    raise AssertionError(signature)


def test_a_failed_entity_list_is_not_none_found() -> None:
    admin = _WWW / "js" / "admin.js"
    populate = _body(admin, "function _populateEntitySelect(")
    assert "entities === null" in populate and "states.listError" in populate
    lights = _body(admin, "function _renderHouseLightList(")
    assert "lights === null" in lights and "states.listError" in lights
    for loader in ("function _loadTtsEntities(", "function _loadHouseEntities("):
        body = _body(admin, loader)
        assert "resp.status === 401" in body, f"{loader}: a 401 is 'not yet'"


def test_my_submissions_shows_its_error_instead_of_hiding() -> None:
    js = _WWW / "js" / "pack-submit.js"
    body = _body(js, "async function loadSubmissions(")
    catch = body[body.index("} catch (_e) {") :]
    assert "states.submissionsError" in catch
    assert "classList.add('hidden')" not in catch


def test_an_unreachable_config_is_not_feature_off() -> None:
    js = _WWW / "js" / "pack-submit.js"
    init = _body(js, "async function init(")
    assert init.count("showUnavailable(section)") == 2
    # …while `enabled: false` still keeps the section hidden.
    assert "if (!cfg || !cfg.enabled) { return; }" in init

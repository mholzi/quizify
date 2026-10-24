"""#1127 — pack requests work out of the box.

Every install had the in-app pack request/submission flow switched off,
because the worker URL option was empty by default and nothing filled it in.
Three pieces fix that, and each has a test here:

1. ``resolve_submit_target`` — an empty URL option means the built-in worker
   (``DEFAULT_COMMUNITY_SUBMIT_URL``), and then **no** secret: a host's secret
   belongs to the host's own worker and never travels to the maintainer's.
2. The wish tile — ``pack-submit.js`` closes the hero pack grid with a dashed
   "Missing a topic? / Ask for a pack" tile, under the same rule as the card
   (config ``enabled: true``). Tapping it switches to "Adjust settings" and
   opens the card in request mode. ``buildHeroPackChips`` in ``admin.js``
   re-adds it after every rebuild.
3. The worker's access gate — installs on the default URL send no secret, so
   the worker has an opt-in anonymous path held by a per-IP burst limit and
   per-IP + global daily caps. A deploy without the opt-in stays fail-closed
   (#292), and a wrong secret is a 401, never a fallback to anonymous.

The JS parts run the real files under node (``tests/fixtures/dom_stub.js`` for
the page, an ESM copy of the worker with a fake env for the worker).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from custom_components.quizify.const import DEFAULT_COMMUNITY_SUBMIT_URL
from custom_components.quizify.server.pack_submission import resolve_submit_target

_REPO = Path(__file__).resolve().parent.parent
_WWW = _REPO / "custom_components" / "quizify" / "www"
_JS = _WWW / "js"
_I18N = _WWW / "i18n"
_STUB = Path(__file__).resolve().parent / "fixtures" / "dom_stub.js"
_WORKER = _REPO / "cf-workers" / "quizify-api.js"

_REQUIRE_NODE = os.environ.get("QUIZIFY_REQUIRE_NODE") == "1"


def _require_node() -> None:
    """Skip locally without node; fail in CI (``QUIZIFY_REQUIRE_NODE=1``),
    the same rule the worker contract check follows (#313)."""
    if shutil.which("node") is not None:
        return
    msg = "node not available"
    if _REQUIRE_NODE:
        pytest.fail(f"{msg} (QUIZIFY_REQUIRE_NODE=1 requires it)")
    pytest.skip(msg)


# ---------------------------------------------------------------------------
# 1. Default URL fallback
# ---------------------------------------------------------------------------


def test_default_url_is_the_maintainers_worker() -> None:
    assert DEFAULT_COMMUNITY_SUBMIT_URL == "https://quizify-api.mholzi.workers.dev"


@pytest.mark.parametrize("url", [None, "", "   ", 42])
def test_empty_option_falls_back_to_the_default(url: object) -> None:
    assert resolve_submit_target(url, None) == (DEFAULT_COMMUNITY_SUBMIT_URL, None)


def test_secret_is_dropped_with_the_default_url() -> None:
    """The secret is for the host's own worker; the default gets none."""
    assert resolve_submit_target("", "s3cr3t") == (DEFAULT_COMMUNITY_SUBMIT_URL, None)


def test_option_overrides_the_default_and_keeps_its_secret() -> None:
    assert resolve_submit_target(
        "  https://worker.example.test/submit ", " s3cr3t "
    ) == ("https://worker.example.test/submit", "s3cr3t")


def test_override_without_secret_sends_none() -> None:
    assert resolve_submit_target("https://w.example.test", "  ") == (
        "https://w.example.test",
        None,
    )


# ---------------------------------------------------------------------------
# 2. The wish tile in the hero pack picker
# ---------------------------------------------------------------------------


def _js_function(source: str, signature: str) -> str:
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
                return source[start : i + 1]
    raise AssertionError(f"unbalanced braces after {signature!r}")


_TILE_SCRIPT = """
require({stub});
QZ.serveI18n({i18n});
var i18nFetch = global.fetch;
var ENABLED = {enabled};
global.fetch = async function (url, opts) {{
    if (String(url).indexOf('/api/quizify/pack-submit/config') !== -1) {{
        return {{ ok: true, status: 200, json: async function () {{
            return {{ enabled: ENABLED, limits: {{}} }};
        }} }};
    }}
    if (String(url).indexOf('/api/quizify/pack-submit/submissions') !== -1) {{
        return {{ ok: false, status: 401, json: async function () {{ return {{}}; }} }};
    }}
    return i18nFetch(url, opts);
}};
QZ.load({i18njs});

// The page elements pack-submit.js and the hero grid touch.
QZ.els(['pack-submit-section', 'pack-submit-pane', 'pack-request-pane',
        'pack-mode-submit-btn', 'pack-mode-request-btn', 'pack-request-theme',
        'pack-request-language', 'pack-request-btn', 'setup-tweak-btn',
        'hero-pack-chips', 'category-chips']);
var screen = QZ.el('setup-screen');
screen.setAttribute('data-mode', 'hero');
QZ.el('pack-submit-section').classList.add('hidden');
QZ.el('pack-request-pane').classList.add('hidden');
var tweakClicks = 0;
QZ.el('setup-tweak-btn').addEventListener('click', function () {{
    tweakClicks += 1;
    screen.setAttribute('data-mode', 'detail');   // what admin.js does
}});
var focused = false;
QZ.el('pack-request-theme').focus = function () {{ focused = true; }};
var scrolled = false;
QZ.el('pack-submit-section').scrollIntoView = function () {{ scrolled = true; }};

// admin.js's grid builder, run against an empty category list: with no packs
// the only thing it can put on the grid is what the #1127 hook adds.
var els = {{ categoryChips: QZ.el('category-chips'), heroPackChips: QZ.el('hero-pack-chips') }};
var selectedLanguage = 'de';
var selectedCategory = 'mixed';
var selectedCategories = [];
var _CATEGORY_TINT = {{}};
var _FEATURED_PACK_THEME = 'worldcup';
function _categoryIconSvg() {{ return ''; }}
function syncHeroFeatureCardState() {{}}
function updateCategorySummary() {{}}
function _carryI18nKey() {{}}
{grid}
{sync}
{build}

function wishTiles() {{
    return QZ.el('hero-pack-chips').querySelectorAll('.hero-wish-tile');
}}

(async function () {{
    await window.QuizifyI18n.init('de');
    QZ.load({packsubmit});
    // init() runs on load and awaits the config fetch.
    await new Promise(function (r) {{ setTimeout(r, 20); }});
    var afterInit = wishTiles().length;
    window.QuizifyPackSubmit.appendWishTile(QZ.el('hero-pack-chips'));
    var afterSecondAppend = wishTiles().length;

    // A language switch rebuilds the grid from scratch.
    buildHeroPackChips();
    var afterRebuild = wishTiles().length;

    var out = {{
        afterInit: afterInit,
        afterSecondAppend: afterSecondAppend,
        afterRebuild: afterRebuild,
        sectionHidden: QZ.el('pack-submit-section').classList.contains('hidden')
    }};
    var tile = wishTiles()[0];
    if (tile) {{
        out.title = tile.querySelector('.hct-name').textContent;
        out.cta = tile.querySelector('.hct-count').textContent;
        out.titleKey = tile.querySelector('.hct-name').getAttribute('data-i18n');
        out.ctaKey = tile.querySelector('.hct-count').getAttribute('data-i18n');
        out.isPackTile = tile.className.indexOf('hero-cat-tile') !== -1;
        tile.click();
        out.tweakClicks = tweakClicks;
        out.mode = screen.getAttribute('data-mode');
        out.requestPaneHidden = QZ.el('pack-request-pane').classList.contains('hidden');
        out.submitPaneHidden = QZ.el('pack-submit-pane').classList.contains('hidden');
        out.scrolled = scrolled;
        out.focused = focused;
        // A second tap while already in "Adjust settings" must not toggle back.
        tile.click();
        out.tweakClicksAfterSecondTap = tweakClicks;
    }}
    console.log(JSON.stringify(out));
}})();
"""


def _run_tile(enabled: bool) -> dict:
    _require_node()
    admin = (_JS / "admin.js").read_text(encoding="utf-8")
    script = _TILE_SCRIPT.format(
        stub=json.dumps(str(_STUB)),
        i18n=json.dumps(str(_I18N)),
        i18njs=json.dumps(str(_JS / "i18n.js")),
        packsubmit=json.dumps(str(_JS / "pack-submit.js")),
        enabled="true" if enabled else "false",
        grid=_js_function(admin, "function _gridPackChipsForLang("),
        sync=_js_function(admin, "function syncHeroPackChips("),
        build=_js_function(admin, "function buildHeroPackChips("),
    )
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, check=True
    )
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_tile_shows_when_requests_are_enabled() -> None:
    res = _run_tile(enabled=True)
    assert res["sectionHidden"] is False
    assert res["afterInit"] == 1
    assert res["afterSecondAppend"] == 1, "appendWishTile must be idempotent"
    assert res["afterRebuild"] == 1, "buildHeroPackChips must re-add the tile"


def test_tile_hidden_when_requests_are_disabled() -> None:
    """Same rule as the card: no worker, no tile — even after a rebuild."""
    res = _run_tile(enabled=False)
    assert res["sectionHidden"] is True
    assert res["afterInit"] == 0
    assert res["afterSecondAppend"] == 0
    assert res["afterRebuild"] == 0


def test_tile_text_comes_from_the_bundle() -> None:
    res = _run_tile(enabled=True)
    assert res["title"] == "Fehlt ein Thema?"
    assert res["cta"] == "Pack wünschen"
    assert res["titleKey"] == "packSubmit.wishTile.title"
    assert res["ctaKey"] == "packSubmit.wishTile.cta"


def test_tile_is_not_a_pack_tile() -> None:
    """It must never take part in pack selection (``.hero-cat-tile``)."""
    assert _run_tile(enabled=True)["isPackTile"] is False


def test_tap_opens_the_card_in_request_mode() -> None:
    res = _run_tile(enabled=True)
    assert res["tweakClicks"] == 1
    assert res["mode"] == "detail"
    assert res["requestPaneHidden"] is False
    assert res["submitPaneHidden"] is True
    assert res["scrolled"] is True
    assert res["focused"] is True
    assert res["tweakClicksAfterSecondTap"] == 1


@pytest.mark.parametrize("lang", ["de", "en", "es"])
def test_every_bundle_carries_both_tile_lines(lang: str) -> None:
    bundle = json.loads((_I18N / f"{lang}.json").read_text(encoding="utf-8"))
    tile = bundle["packSubmit"]["wishTile"]
    assert tile["title"].strip()
    assert tile["cta"].strip()
    # The second line is the same action as the card's request tab.
    assert tile["cta"] == bundle["packSubmit"]["mode"]["request"]


# ---------------------------------------------------------------------------
# 3. Worker access gate
# ---------------------------------------------------------------------------

_WORKER_SCRIPT = r"""
import worker from %(worker)s;

// What the Workers runtime has and node does not.
crypto.subtle.timingSafeEqual = function (a, b) {
  if (a.byteLength !== b.byteLength) return false;
  let d = 0;
  for (let i = 0; i < a.byteLength; i++) d |= a[i] ^ b[i];
  return d === 0;
};

let ghCalls = [];
let nextIssue = 1000;
globalThis.fetch = async function (url, init) {
  ghCalls.push({ url: String(url), body: JSON.parse(init.body), auth: init.headers.Authorization });
  return new Response(JSON.stringify({ number: ++nextIssue, html_url: 'https://github.com/mholzi/quizify/issues/' + nextIssue }), { status: 201 });
};

function kv() {
  const m = new Map();
  return { m, async get(k) { return m.has(k) ? m.get(k) : null; }, async put(k, v) { m.set(k, v); } };
}
function limiter(allow) {
  return { calls: 0, async limit() { this.calls++; return { success: allow }; } };
}
function env(extra) {
  return Object.assign({ GITHUB_PAT: 'pat' }, extra || {});
}
function anonEnv(extra) {
  return env(Object.assign({ ANONYMOUS_SUBMISSIONS: 'on', ANON_LIMITER: limiter(true), ANON_COUNTS: kv() }, extra || {}));
}
const REQUEST = { request: { theme: 'Formel 1', language: 'de' } };
function post(body, headers) {
  return new Request('https://quizify-api.example/', {
    method: 'POST',
    headers: Object.assign({ 'Content-Type': 'application/json', 'CF-Connecting-IP': '203.0.113.7' }, headers || {}),
    body: typeof body === 'string' ? body : JSON.stringify(body),
  });
}
async function call(e, body, headers) {
  const before = ghCalls.length;
  const res = await worker.fetch(post(body, headers), e);
  return { status: res.status, body: await res.json(), gh: ghCalls.length - before };
}

const out = {};
out.noOptIn = await call(env(), REQUEST);
out.flagOff = await call(anonEnv({ ANONYMOUS_SUBMISSIONS: 'off' }), REQUEST);
out.noKv = await call(anonEnv({ ANON_COUNTS: undefined }), REQUEST);
out.noLimiter = await call(anonEnv({ ANON_LIMITER: undefined }), REQUEST);

const e1 = anonEnv();
out.anonOk = await call(e1, REQUEST);
out.anonLabels = ghCalls[ghCalls.length - 1].body.labels;
out.anonAuth = ghCalls[ghCalls.length - 1].auth;
out.kvKeys = Array.from(e1.ANON_COUNTS.m.keys());

out.burst = await call(anonEnv({ ANON_LIMITER: limiter(false) }), REQUEST);

// Junk never reaches GitHub and never spends the day's budget.
const e2 = anonEnv();
out.junk = await call(e2, { request: { theme: '' } });
out.junkKv = e2.ANON_COUNTS.m.size;

// Per-IP daily cap.
const e3 = anonEnv();
const perIp = [];
for (let i = 0; i < 6; i++) perIp.push((await call(e3, REQUEST)).status);
out.perIp = perIp;
out.otherIp = (await call(e3, REQUEST, { 'CF-Connecting-IP': '198.51.100.9' })).status;

// Global daily cap across many IPs.
const e4 = anonEnv();
const global = [];
for (let i = 0; i < 41; i++) global.push((await call(e4, REQUEST, { 'CF-Connecting-IP': '10.0.' + (i >> 8) + '.' + (i & 255) })).status);
out.globalLast = global[40];
out.globalOk = global.slice(0, 40).every((s) => s === 200);

// Secrets.
const secretEnv = anonEnv({ SHARED_SECRET: 'right' });
out.wrongSecret = await call(secretEnv, REQUEST, { 'X-Quizify-Secret': 'wrong' });
out.rightSecret = await call(secretEnv, REQUEST, { 'X-Quizify-Secret': 'right' });
out.rightSecretKv = secretEnv.ANON_COUNTS.m.size;
out.rightSecretLimiter = secretEnv.ANON_LIMITER.calls;
out.secretOnlyNoAnon = await call(env({ SHARED_SECRET: 'right' }), REQUEST, { 'X-Quizify-Secret': 'right' });
out.noSecretOnSecretWorker = await call(env({ SHARED_SECRET: 'right' }), REQUEST);
out.headerToSecretlessWorker = await call(anonEnv(), REQUEST, { 'X-Quizify-Secret': 'whatever' });

// Submissions go through the same gate and keep their own label.
const pack = JSON.parse(%(pack)s);
const e5 = anonEnv();
out.anonPack = await call(e5, { pack });
out.anonPackLabels = ghCalls[ghCalls.length - 1].body.labels;

out.get = (await worker.fetch(new Request('https://quizify-api.example/'), anonEnv())).status;
console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def worker_run(tmp_path_factory: pytest.TempPathFactory) -> dict:
    _require_node()
    tmp = tmp_path_factory.mktemp("worker1127")
    module = tmp / "quizify-api.mjs"
    module.write_text(_WORKER.read_text(encoding="utf-8"), encoding="utf-8")
    pack = (_REPO / "tests" / "fixtures" / "community_pack.json").read_text(
        encoding="utf-8"
    )
    script = tmp / "run.mjs"
    script.write_text(
        _WORKER_SCRIPT
        % {"worker": json.dumps("./quizify-api.mjs"), "pack": json.dumps(pack)},
        encoding="utf-8",
    )
    out = subprocess.run(
        ["node", str(script)],
        capture_output=True,
        text=True,
        check=True,
        cwd=tmp,
    )
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_worker_without_opt_in_stays_fail_closed(worker_run: dict) -> None:
    """#292 still holds for any deploy that has not opted in to anonymous."""
    for case in ("noOptIn", "flagOff", "noKv", "noLimiter"):
        assert worker_run[case]["status"] == 401, case
        assert worker_run[case]["gh"] == 0, case


def test_anonymous_request_files_a_labelled_issue(worker_run: dict) -> None:
    assert worker_run["anonOk"]["status"] == 200
    assert worker_run["anonOk"]["gh"] == 1
    assert worker_run["anonOk"]["body"]["issue_number"] > 0
    assert worker_run["anonLabels"] == ["pack-request"]
    assert worker_run["anonAuth"] == "Bearer pat"


def test_daily_counters_do_not_store_the_ip(worker_run: dict) -> None:
    keys = worker_run["kvKeys"]
    assert len(keys) == 2
    assert all("203.0.113.7" not in k for k in keys)


def test_burst_limit_stops_before_github(worker_run: dict) -> None:
    assert worker_run["burst"]["status"] == 429
    assert worker_run["burst"]["body"]["code"] == "RATE_LIMITED"
    assert worker_run["burst"]["gh"] == 0


def test_invalid_payload_spends_no_budget(worker_run: dict) -> None:
    assert worker_run["junk"]["status"] == 400
    assert worker_run["junk"]["gh"] == 0
    assert worker_run["junkKv"] == 0


def test_per_ip_daily_cap(worker_run: dict) -> None:
    assert worker_run["perIp"] == [200] * 5 + [429]
    assert worker_run["otherIp"] == 200


def test_global_daily_cap(worker_run: dict) -> None:
    assert worker_run["globalOk"] is True
    assert worker_run["globalLast"] == 429


def test_wrong_secret_is_401_not_anonymous(worker_run: dict) -> None:
    assert worker_run["wrongSecret"]["status"] == 401
    assert worker_run["wrongSecret"]["gh"] == 0


def test_right_secret_skips_the_anonymous_limits(worker_run: dict) -> None:
    assert worker_run["rightSecret"]["status"] == 200
    assert worker_run["rightSecretKv"] == 0
    assert worker_run["rightSecretLimiter"] == 0
    # A secret-only deploy keeps working without any anonymous bindings…
    assert worker_run["secretOnlyNoAnon"]["status"] == 200
    # …and still refuses callers without the secret.
    assert worker_run["noSecretOnSecretWorker"]["status"] == 401


def test_header_to_a_secretless_worker_is_treated_as_anonymous(
    worker_run: dict,
) -> None:
    assert worker_run["headerToSecretlessWorker"]["status"] == 200


def test_anonymous_pack_submission_keeps_its_label(worker_run: dict) -> None:
    assert worker_run["anonPack"]["status"] == 200
    assert worker_run["anonPackLabels"] == ["community-pack"]


def test_get_is_still_refused(worker_run: dict) -> None:
    assert worker_run["get"] == 405

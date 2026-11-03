"""The silent update: no reload on first install, a skipped one caught up (#1185, P9).

Live in the review: the very first visit loaded the page twice, 0.2 s apart —
``sw-update.js`` reloaded on every ``controllerchange``, including the first
install, where there was no old version at all. And an update that arrived
mid-question was dropped until the next natural load.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_SW = Path(__file__).resolve().parent.parent / "custom_components" / "quizify" / "www" / "js" / "sw-update.js"

_HARNESS = r"""
const fs = require('fs');
let listeners = {}, intervals = [], reloads = 0, idle = %(idle)s, visHandlers = [];
global.navigator = undefined;
const sw = {
    controller: %(controller)s,
    addEventListener: (t, fn) => { (listeners[t] = listeners[t] || []).push(fn); },
    register: () => Promise.resolve({ update: () => Promise.resolve() }),
};
Object.defineProperty(global, 'navigator', { value: { serviceWorker: sw }, configurable: true });
global.window = { location: { reload: () => { reloads++; } }, quizifyIsIdleForReload: () => idle };
global.document = { visibilityState: 'visible', addEventListener: (t, fn) => visHandlers.push(fn) };
global.setInterval = (fn) => { intervals.push(fn); return intervals.length; };
global.clearInterval = () => { intervals = []; };
(0, eval)(fs.readFileSync(%(path)s, 'utf8'));
(async () => {
    await Promise.resolve(); await Promise.resolve();
    const out = {};
    (listeners.controllerchange || []).forEach(fn => fn());
    out.afterFirst = reloads;
    (listeners.controllerchange || []).forEach(fn => fn());
    out.afterSecond = reloads;
    idle = true;
    intervals.slice().forEach(fn => fn());
    out.afterCatchUp = reloads;
    process.stdout.write(JSON.stringify(out));
})();
"""


def _run(*, controller: bool, idle: bool) -> dict:
    script = _HARNESS % {
        "idle": "true" if idle else "false",
        "controller": "{}" if controller else "null",
        "path": json.dumps(str(_SW)),
    }
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=False)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


def test_first_install_does_not_reload() -> None:
    """No controller at load: the first controllerchange is the install."""
    out = _run(controller=False, idle=True)
    assert out["afterFirst"] == 0
    # a later one is a real update, and the screen is idle
    assert out["afterSecond"] == 1


def test_an_update_on_an_idle_screen_reloads_once() -> None:
    out = _run(controller=True, idle=True)
    assert out["afterFirst"] == 1
    assert out["afterCatchUp"] == 1


def test_an_update_mid_question_is_caught_up_later() -> None:
    out = _run(controller=True, idle=False)
    assert out["afterFirst"] == 0  # never mid-question
    assert out["afterCatchUp"] == 1  # on the next quiet screen
